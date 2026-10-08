"""Spec section 11 (M1 subset) + design note section 10: the data endpoints.

Acceptance evidence produced here:
- AC1 through the API: a repeated scope requeues an *identical* job while the
  fixture venue is never asked to refetch (the engine reuses stored chunks);
- AC2: ``GET /v1/datasets/{version}`` answers "what exactly did this use?"
  (manifest with source_files/hashes, row_counts, version fields; 404 when the
  version is unknown);
- AC3 through the API: an incomplete ingest opens a gap, ``GET /v1/data/gaps``
  lists it OPEN, ``POST /v1/data/repair`` closes it to REPAIRED;
- AC4: ``market_type=PERPETUAL`` -> 501 ``NOT_IMPLEMENTED`` with *no* job
  queued; a non-BINANCE venue likewise (PD-10: never fetch the wrong venue);
- AC5: an over-budget request is refused at POST with 422
  ``reason:"storage_budget_exceeded"`` and no job queued (OD-5);
- the job contract: POST -> 202 ``{job_id,status:"queued"}``, the background
  task runs it to ``succeeded`` (progress 1.0, result_ref set) or ``failed``
  (the envelope JSON in ``error``), ``GET /v1/jobs/{id}`` shows the outcome;
- ``/v1/data/health`` reports the latest quality per (instrument, timeframe)
  plus the exclusions still active; unknown ids are 404 envelopes.

INTERPRETATIONS: response ``status``/``kind`` mirror the ``system.job``
vocabulary (``queued|running|succeeded|failed``); invalid path/enum input is
422 (FastAPI validation), unknown *entities* are 404.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text

from apps.api.errors import ErrorCode, install_error_handlers
from apps.api.main import create_app
from apps.api.settings import get_settings
from engine.data.bars import Timeframe
from engine.data.instruments import upsert_instruments
from engine.errors import EngineError
from tests.support import BTC, NOW, FixtureAdapter, daily


def _ingest_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "venue": "BINANCE",
        "market_type": "SPOT",
        "instrument_ids": [str(BTC.instrument_id)],
        "timeframes": ["1D"],
        "from": "2024-01-01T00:00:00Z",
        "to": "2024-01-10T00:00:00Z",
    }
    body.update(overrides)
    return body


def _job_count(conn: Connection) -> int:
    return int(conn.execute(text("SELECT count(*) FROM system.job")).scalar_one())


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "data"
    directory.mkdir(parents=True)
    monkeypatch.setenv("DATA_DIR", str(directory))
    return directory


@pytest.fixture()
def venue(monkeypatch: pytest.MonkeyPatch) -> FixtureAdapter:
    adapter = FixtureAdapter({("BTCUSDT", Timeframe.D1): daily()})
    monkeypatch.setattr("apps.api.jobs.build_adapter", lambda params: adapter)
    monkeypatch.setattr("apps.api.jobs.now_ms", lambda: NOW)
    return adapter


@pytest.fixture()
def conn() -> Iterator[Connection]:
    """Committed connection with a clean slate for the tables M1 writes."""
    engine = create_engine(get_settings().database_url, future=True)
    connection = engine.connect()
    connection.execute(text("DELETE FROM system.job"))
    connection.execute(text("DELETE FROM system.gap"))
    connection.execute(text("DELETE FROM system.data_quality"))
    connection.execute(text("DELETE FROM market.dataset_manifest"))
    connection.execute(text("DELETE FROM system.audit_log"))
    # The registry too: `make seed-fixtures` commits BTC/ETH/SOL, and several
    # assertions here assume only what this fixture upserts (gap/data_quality
    # go first -- they hold the FK to market.instrument).
    connection.execute(text("DELETE FROM market.instrument"))
    upsert_instruments(connection, [BTC])
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()
        engine.dispose()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


# --------------------------------------------------------------------------
# Reads: instruments, jobs, datasets, health, gaps
# --------------------------------------------------------------------------


def test_instruments_endpoint_lists_the_registry(client: TestClient, conn: Connection) -> None:
    response = client.get("/v1/instruments", params={"venue": "BINANCE", "asset": "BTC"})
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["instrument_id"] == str(BTC.instrument_id)
    assert rows[0]["native_symbol"] == "BTCUSDT"
    assert rows[0]["market_type"] == "SPOT"
    assert rows[0]["tick_size"] is None

    empty = client.get("/v1/instruments", params={"asset": "ETH"})
    assert empty.status_code == 200
    assert empty.json() == []


def test_ingest_endpoint_queues_and_the_job_runs_to_success(
    client: TestClient, conn: Connection, venue: FixtureAdapter, data_dir: Path
) -> None:
    response = client.post("/v1/data/ingest", json=_ingest_body())
    assert response.status_code == 202
    queued: dict[str, Any] = response.json()
    assert set(queued) == {"job_id", "status"}
    assert queued["status"] == "queued"
    assert _job_count(conn) == 1

    view = client.get(f"/v1/jobs/{queued['job_id']}")
    assert view.status_code == 200
    job = view.json()
    assert job["kind"] == "INGEST"
    assert job["status"] == "succeeded"
    assert job["progress"] == 1.0
    assert job["error"] is None
    assert job["result_ref"]  # dataset_version
    assert job["logs"][0] == "claimed"
    assert any(line.startswith("succeeded dataset_version=") for line in job["logs"])
    assert job["timestamps"]["started_at"] is not None
    assert job["timestamps"]["finished_at"] is not None

    # AC2: the manifest answers "what exactly did this use?"
    manifest = client.get(f"/v1/datasets/{job['result_ref']}")
    assert manifest.status_code == 200
    body = manifest.json()
    assert body["dataset_version"] == job["result_ref"]
    assert body["instruments"] == [str(BTC.instrument_id)]
    assert body["timeframes"] == ["1D"]
    assert body["source_files"][0]["content_sha256"]
    assert body["row_counts"]
    assert body["dataset_schema_version"] and body["bar_semantics_version"]
    assert body["timezone_policy_version"] and body["instrument_metadata_version"]

    health = client.get("/v1/data/health", params={"market_type": "SPOT"})
    assert health.status_code == 200
    health_body = health.json()
    series = [row for row in health_body["series"] if row["timeframe"] == "1D"]
    assert series and series[0]["state"] == "GREEN"
    assert series[0]["completeness"] == 1.0
    assert health_body["exclusions"] == []
    assert client.get("/v1/data/gaps").json() == []


def test_repeated_scope_requeues_without_refetching(
    client: TestClient, conn: Connection, venue: FixtureAdapter, data_dir: Path
) -> None:
    first = client.post("/v1/data/ingest", json=_ingest_body()).json()
    fetched_after_first = len(venue.fetch_calls)
    assert fetched_after_first == 1

    second = client.post("/v1/data/ingest", json=_ingest_body()).json()
    assert second["job_id"] != first["job_id"]

    first_job = client.get(f"/v1/jobs/{first['job_id']}").json()
    second_job = client.get(f"/v1/jobs/{second['job_id']}").json()
    assert second_job["status"] == "succeeded"
    # AC1: same scope -> same dataset_version, and no second fetch of the venue.
    assert second_job["result_ref"] == first_job["result_ref"]
    assert len(venue.fetch_calls) == fetched_after_first


def test_jobs_endpoint_unknown_id_is_a_404_envelope(client: TestClient, conn: Connection) -> None:
    response = client.get(f"/v1/jobs/{uuid.uuid4()}")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == ErrorCode.NOT_FOUND
    assert "job_id" in error["detail"]


def test_datasets_unknown_version_is_a_404_envelope(client: TestClient, conn: Connection) -> None:
    response = client.get("/v1/datasets/does-not-exist")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == ErrorCode.NOT_FOUND
    assert error["detail"]["dataset_version"] == "does-not-exist"


# --------------------------------------------------------------------------
# Fail-closed POSTs (AC4, AC5, PD-10/PD-11)
# --------------------------------------------------------------------------


def test_perpetual_ingest_fails_closed_without_queueing(
    client: TestClient, conn: Connection, venue: FixtureAdapter, data_dir: Path
) -> None:
    response = client.post("/v1/data/ingest", json=_ingest_body(market_type="PERPETUAL"))
    assert response.status_code == 501
    error = response.json()["error"]
    assert error["code"] == ErrorCode.NOT_IMPLEMENTED
    assert error["detail"]["market_type"] == "PERPETUAL"
    assert _job_count(conn) == 0


def test_non_binance_venue_fails_closed_without_queueing(
    client: TestClient, conn: Connection, venue: FixtureAdapter, data_dir: Path
) -> None:
    response = client.post("/v1/data/ingest", json=_ingest_body(venue="KRKEN"))
    assert response.status_code == 501
    error = response.json()["error"]
    assert error["code"] == ErrorCode.NOT_IMPLEMENTED
    assert error["detail"]["venue"] == "KRKEN"
    assert _job_count(conn) == 0


def test_bad_timeframe_is_422_before_queueing(
    client: TestClient, conn: Connection, venue: FixtureAdapter, data_dir: Path
) -> None:
    unknown = client.post("/v1/data/ingest", json=_ingest_body(timeframes=["5m"]))
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR

    mixed = client.post("/v1/data/ingest", json=_ingest_body(timeframes=["1D", "1W"]))
    assert mixed.status_code == 422
    assert mixed.json()["error"]["detail"]["timeframes"] == ["1D", "1W"]
    assert _job_count(conn) == 0


def test_bad_time_window_is_422_before_queueing(
    client: TestClient, conn: Connection, venue: FixtureAdapter, data_dir: Path
) -> None:
    reversed_window = client.post(
        "/v1/data/ingest", json=_ingest_body(**{"from": "2024-01-10T00:00:00Z"})
    )
    assert reversed_window.status_code == 422
    assert reversed_window.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR

    naive = client.post("/v1/data/ingest", json=_ingest_body(**{"from": "2024-01-01T00:00:00"}))
    assert naive.status_code == 422
    assert naive.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR
    assert _job_count(conn) == 0


def test_over_budget_request_is_refused_before_queueing(
    client: TestClient, conn: Connection, venue: FixtureAdapter, data_dir: Path
) -> None:
    (data_dir / "filler.bin").write_bytes(b"x" * 4096)
    response = client.post("/v1/data/ingest", json=_ingest_body(storage_budget_gb=1e-6))
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == ErrorCode.VALIDATION_ERROR
    assert error["detail"]["reason"] == "storage_budget_exceeded"
    assert error["detail"]["used_gb"] >= error["detail"]["budget_gb"]
    assert _job_count(conn) == 0


def test_failed_job_exposes_the_envelope_json(
    client: TestClient, conn: Connection, venue: FixtureAdapter, data_dir: Path
) -> None:
    unknown = str(uuid.uuid4())
    response = client.post("/v1/data/ingest", json=_ingest_body(instrument_ids=[unknown]))
    assert response.status_code == 202

    job = client.get(f"/v1/jobs/{response.json()['job_id']}").json()
    assert job["status"] == "failed"
    assert job["progress"] == 0.0
    assert job["result_ref"] is None
    error = json.loads(job["error"])
    assert error["code"] == "NOT_FOUND"
    assert error["detail"]["instrument_id"] == unknown
    assert any(line.startswith("failed NOT_FOUND:") for line in job["logs"])


# --------------------------------------------------------------------------
# Repair round trip (AC3) and resample dispatch
# --------------------------------------------------------------------------


def test_repair_endpoint_closes_an_opened_gap(
    client: TestClient, conn: Connection, venue: FixtureAdapter, data_dir: Path
) -> None:
    venue.bars[("BTCUSDT", Timeframe.D1)] = daily(skip=(4,))
    assert client.post("/v1/data/ingest", json=_ingest_body()).status_code == 202

    gaps = client.get("/v1/data/gaps", params={"status": "OPEN"}).json()
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["status"] == "OPEN"
    assert gap["kind"] == "gap"
    assert gap["instrument_id"] == str(BTC.instrument_id)
    assert gap["timeframe"] == "1D"

    health = client.get("/v1/data/health", params={"timeframe": "1D"}).json()
    assert health["series"][0]["state"] == "ORANGE"
    assert [row["gap_id"] for row in health["exclusions"]] == [gap["gap_id"]]

    venue.bars[("BTCUSDT", Timeframe.D1)] = daily()
    repair = client.post("/v1/data/repair", json={"gap_id": gap["gap_id"]})
    assert repair.status_code == 202
    assert repair.json()["status"] == "queued"

    job = client.get(f"/v1/jobs/{repair.json()['job_id']}").json()
    assert job["status"] == "succeeded"
    assert job["result_ref"]
    assert any(line.startswith("succeeded outcome=REPAIRED") for line in job["logs"])

    after = client.get("/v1/data/gaps").json()
    assert [row["status"] for row in after] == ["REPAIRED"]
    assert client.get("/v1/data/gaps", params={"status": "OPEN"}).json() == []
    health_after = client.get("/v1/data/health", params={"timeframe": "1D"}).json()
    assert health_after["exclusions"] == []
    assert health_after["series"][0]["state"] == "GREEN"


def test_weekly_resample_endpoint_queues_a_resample_job(
    client: TestClient, conn: Connection, venue: FixtureAdapter, data_dir: Path
) -> None:
    assert client.post("/v1/data/ingest", json=_ingest_body()).status_code == 202
    response = client.post("/v1/data/ingest", json=_ingest_body(timeframes=["1W"]))
    assert response.status_code == 202

    job = client.get(f"/v1/jobs/{response.json()['job_id']}").json()
    assert job["kind"] == "RESAMPLE"
    assert job["status"] == "succeeded"
    manifest = client.get(f"/v1/datasets/{job['result_ref']}").json()
    assert manifest["timeframes"] == ["1W"]


# --------------------------------------------------------------------------
# Filters, health refusal, handler registration
# --------------------------------------------------------------------------


def test_health_rejects_perpetual_and_unknown_filters(client: TestClient, conn: Connection) -> None:
    perpetual = client.get("/v1/data/health", params={"market_type": "PERPETUAL"})
    assert perpetual.status_code == 501
    assert perpetual.json()["error"]["code"] == ErrorCode.NOT_IMPLEMENTED

    bad_market = client.get("/v1/instruments", params={"market_type": "PERPS"})
    assert bad_market.status_code == 422
    assert bad_market.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR

    bad_status = client.get("/v1/data/gaps", params={"status": "WEIRD"})
    assert bad_status.status_code == 422
    error = bad_status.json()["error"]
    assert error["code"] == ErrorCode.VALIDATION_ERROR
    assert "allowed" in error["detail"]


def test_engine_error_handler_is_registered_on_a_bare_app() -> None:
    app = FastAPI()
    install_error_handlers(app)
    assert EngineError in app.exception_handlers
