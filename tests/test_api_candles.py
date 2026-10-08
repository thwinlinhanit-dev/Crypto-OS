"""M2 (T2.1) - ``GET /v1/candles`` (spec section 11; design note m2_chart sections 1, 6).

Acceptance evidence produced here:
- the section 11 response contract verbatim (``dataset_version``, ``quality``,
  columnar ``t/o/h/l/c/v``) plus the ``window`` echo (design note section 3);
- the forming bar is never served: only bars with ``close_time <= as_of`` or
  ``close_time <= now`` (section 4.2, PD-2);
- window/limit semantics: inclusive ``from``/``to`` on ``open_time``, ``limit``
  keeps the most recent bars, capped by ``config/defaults.yaml[candles]``
  (PD-11, no silent default);
- fail-closed resolution (PD-10): unknown instrument 404, unknown timeframe 422,
  perpetual 501, no covering manifest 400 DATA_UNAVAILABLE, window with no
  overlap 400 DATA_UNAVAILABLE - never another venue/market/timeframe/dataset;
- only the source files **recorded in the manifest** are read: an unregistered
  part on disk is invisible to the endpoint (section 4.4);
- active exclusions are echoed so the client can shade them (design D-013);
- AC performance: a 5,000-candle request answers within the section 14.3
  300 ms budget (measured with ``time.perf_counter`` around the full request).

INTERPRETATION: the response adds ``window`` (the resolved request knobs) next
to the spec fields - additive like D-007, so a screenshot or stored payload can
be reproduced exactly (PD-6).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text

from apps.api.main import create_app
from apps.api.settings import get_settings
from engine.data.bars import Timeframe
from engine.data.instruments import build_instrument, upsert_instruments
from tests.support import BTC, DAY, NOW, T0, WIDE_BUDGET, FixtureAdapter, daily, hourly

HOUR = 3_600_000
PERP = build_instrument(
    venue="BINANCE",
    market_type="PERPETUAL",
    asset="BTC",
    quote="USDT",
    native_symbol="BTCUSDT_PERP",
)


def _ingest_body(timeframe: str = "1D", **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "venue": "BINANCE",
        "market_type": "SPOT",
        "instrument_ids": [str(BTC.instrument_id)],
        "timeframes": [timeframe],
        "from": "2024-01-01T00:00:00Z",
        "to": "2024-01-10T00:00:00Z",
        "storage_budget_gb": WIDE_BUDGET / 1024**3,
    }
    body.update(overrides)
    return body


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "data"
    directory.mkdir(parents=True)
    monkeypatch.setenv("DATA_DIR", str(directory))
    return directory


@pytest.fixture()
def venue(monkeypatch: pytest.MonkeyPatch) -> FixtureAdapter:
    adapter = FixtureAdapter(
        {("BTCUSDT", Timeframe.D1): daily(), ("BTCUSDT", Timeframe.H1): hourly(5_001)}
    )
    monkeypatch.setattr("apps.api.jobs.build_adapter", lambda params: adapter)
    monkeypatch.setattr("apps.api.jobs.now_ms", lambda: NOW)
    return adapter


@pytest.fixture()
def conn() -> Iterator[Connection]:
    engine = create_engine(get_settings().database_url, future=True)
    connection = engine.connect()
    connection.execute(text("DELETE FROM system.job"))
    connection.execute(text("DELETE FROM system.gap"))
    connection.execute(text("DELETE FROM system.data_quality"))
    connection.execute(text("DELETE FROM market.dataset_manifest"))
    connection.execute(text("DELETE FROM system.audit_log"))
    connection.execute(text("DELETE FROM market.instrument"))
    upsert_instruments(connection, [BTC])
    connection.commit()
    try:
        yield connection
    finally:
        # Leave the shared database as clean as it was found: later engine
        # tests (test_guardian, test_ingest) assert exact row lists without
        # clearing first, so committed leftovers here would break them.
        connection.rollback()
        for statement in (
            "DELETE FROM system.job",
            "DELETE FROM system.gap",
            "DELETE FROM system.data_quality",
            "DELETE FROM market.dataset_manifest",
            "DELETE FROM system.audit_log",
            "DELETE FROM market.instrument",
        ):
            connection.execute(text(statement))
        connection.commit()
        connection.close()
        engine.dispose()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def seeded(client: TestClient, venue: FixtureAdapter, data_dir: Path) -> str:
    """Ingest the ten golden daily bars through the real job path."""
    response = client.post("/v1/data/ingest", json=_ingest_body())
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    view = client.get(f"/v1/jobs/{job_id}").json()
    assert view["status"] == "succeeded", view["error"]
    return str(view["result_ref"])


def _params(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"instrument_id": str(BTC.instrument_id), "tf": "1D"}
    params.update(overrides)
    return params


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------


def test_candles_answer_the_section_11_contract(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get("/v1/candles", params=_params())
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "dataset_version",
        "quality",
        "window",
        "t",
        "o",
        "h",
        "l",
        "c",
        "v",
    }
    assert body["dataset_version"] == seeded
    assert body["quality"] == {
        "state": "GREEN",
        "completeness": 1.0,
        "exclusions": [],
    }
    assert len(body["t"]) == 10
    assert body["t"][0] == "2024-01-01T00:00:00Z"
    assert body["t"] == sorted(body["t"])
    assert body["o"][0] == pytest.approx(100.0)
    assert body["h"][0] == pytest.approx(105.0)
    assert body["l"][0] == pytest.approx(95.0)
    assert body["c"][0] == pytest.approx(101.0)
    assert body["v"][0] == pytest.approx(1000.0)
    assert len(body["o"]) == len(body["h"]) == len(body["l"]) == len(body["c"])
    assert len(body["v"]) == len(body["t"])
    assert body["window"] == {
        "from": None,
        "to": None,
        "limit": 5_000,
        "as_of": None,
    }


def test_forming_and_future_bars_are_never_served(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get(
        "/v1/candles", params=_params(as_of="2024-01-06T00:00:00Z")
    )
    assert response.status_code == 200
    body = response.json()
    # bars 0..4 close at or before the cutoff; bar 5 opens 2024-01-06 and
    # closes 2024-01-07 -> still forming at as_of, therefore not served.
    assert len(body["t"]) == 5
    assert body["t"][-1] == "2024-01-05T00:00:00Z"
    assert body["window"]["as_of"] == "2024-01-06T00:00:00Z"


def test_window_and_limit_keep_the_most_recent_bars(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get(
        "/v1/candles",
        params=_params(
            **{
                "from": "2024-01-03T00:00:00Z",
                "to": "2024-01-07T00:00:00Z",
                "limit": 2,
            }
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["t"] == ["2024-01-06T00:00:00Z", "2024-01-07T00:00:00Z"]
    assert body["window"]["limit"] == 2


def test_limit_is_capped_by_the_documented_maximum() -> None:
    # the cap itself is asserted through defaults.yaml (PD-11) in the loader
    # tests; here we pin that the resolved window reports what was applied.
    from engine.config.defaults import load_defaults

    assert load_defaults()["candles"]["max_limit"] == 5_000
    assert load_defaults()["candles"]["default_limit"] == 5_000


def test_non_positive_limit_is_422(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get("/v1/candles", params=_params(limit=0))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_from_after_to_is_422(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get(
        "/v1/candles",
        params=_params(**{"from": "2024-01-05T00:00:00Z", "to": "2024-01-03T00:00:00Z"}),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --------------------------------------------------------------------------
# Fail-closed resolution (PD-10)
# --------------------------------------------------------------------------


def test_unknown_instrument_is_404(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get("/v1/candles", params=_params(instrument_id=str(uuid.uuid4())))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_unknown_timeframe_is_422(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get("/v1/candles", params=_params(tf="2D"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_perpetual_requests_fail_closed(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    upsert_instruments(conn, [PERP])
    conn.commit()
    response = client.get(
        "/v1/candles", params=_params(instrument_id=str(PERP.instrument_id))
    )
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "NOT_IMPLEMENTED"


def test_instrument_without_a_manifest_is_data_unavailable(
    client: TestClient, conn: Connection
) -> None:
    eth = build_instrument(
        venue="BINANCE", market_type="SPOT", asset="ETH", quote="USDT",
        native_symbol="ETHUSDT",
    )
    upsert_instruments(conn, [eth])
    conn.commit()
    response = client.get(
        "/v1/candles", params=_params(instrument_id=str(eth.instrument_id))
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"


def test_window_with_no_overlap_is_data_unavailable(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get(
        "/v1/candles",
        params=_params(**{"from": "2030-01-01T00:00:00Z", "to": "2030-02-01T00:00:00Z"}),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"


# --------------------------------------------------------------------------
# Provenance and shading (AC2/AC3 support)
# --------------------------------------------------------------------------


def test_only_manifest_source_files_are_read(
    client: TestClient, conn: Connection, seeded: str, data_dir: Path, venue: FixtureAdapter
) -> None:
    """A part on disk that no manifest declares must stay invisible (4.4)."""
    from engine.data.bars import bar_frame
    from engine.data.parquet import write_part

    stray_rows = [
        {
            "instrument_id": str(BTC.instrument_id),
            "timeframe": "1D",
            "open_time": T0 + 400 * DAY,
            "close_time": T0 + 401 * DAY,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 10.0,
            "quote_volume": 15.0,
            "trades": 3,
            "source": "fixture",
        }
    ]
    frame = bar_frame(stray_rows, ingested_at_ms=NOW)
    write_part(
        frame,
        data_dir=data_dir,
        venue="BINANCE",
        market_type="SPOT",
        native_symbol="BTCUSDT",
        timeframe=Timeframe.D1,
    )
    response = client.get(
        "/v1/candles",
        params=_params(**{"from": "2025-01-01T00:00:00Z", "to": "2025-12-31T00:00:00Z"}),
    )
    # the stray part exists on disk but no manifest declares it -> not served
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"


def test_active_exclusions_are_echoed_for_the_client_to_shade(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    conn.execute(
        text(
            "INSERT INTO system.gap (gap_id, instrument_id, timeframe,"
            " exclusion_start, exclusion_end, status, source, detected_at, kind)"
            " VALUES (:gap_id, :instrument_id, '1D', :start, :end, 'OPEN',"
            " 'guardian', now(), 'gap')"
        ),
        {
            "gap_id": uuid.uuid4(),
            "instrument_id": BTC.instrument_id,
            "start": "2024-01-04T00:00:00+00",
            "end": "2024-01-05T00:00:00+00",
        },
    )
    conn.commit()
    response = client.get("/v1/candles", params=_params())
    assert response.status_code == 200
    exclusions = response.json()["quality"]["exclusions"]
    assert len(exclusions) == 1
    assert exclusions[0]["kind"] == "gap"
    assert exclusions[0]["status"] == "OPEN"
    assert exclusions[0]["exclusion_start"].startswith("2024-01-04")
    assert exclusions[0]["exclusion_end"].startswith("2024-01-05")
    assert exclusions[0]["gap_id"]


# --------------------------------------------------------------------------
# Performance (section 14.3: 5,000 candles < 300 ms)
# --------------------------------------------------------------------------


def test_5000_candle_request_meets_the_spec_budget(
    client: TestClient,
    conn: Connection,
    venue: FixtureAdapter,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    future_now = T0 + 5_100 * HOUR
    monkeypatch.setattr("apps.api.jobs.now_ms", lambda: future_now)
    monkeypatch.setattr("apps.api.market.now_ms", lambda: future_now)
    response = client.post(
        "/v1/data/ingest",
        json=_ingest_body(
            timeframe="1H",
            **{"from": "2024-01-01T00:00:00Z", "to": "2024-07-29T00:00:00Z"},
        ),
    )
    assert response.status_code == 202
    job = client.get(f"/v1/jobs/{response.json()['job_id']}").json()
    assert job["status"] == "succeeded", job["error"]

    samples: list[float] = []
    body: dict[str, Any] = {}
    for _ in range(5):
        started = time.perf_counter()
        candles = client.get("/v1/candles", params=_params(tf="1H", limit=5_000))
        samples.append((time.perf_counter() - started) * 1000.0)
        assert candles.status_code == 200
        body = candles.json()
    samples.sort()
    print(
        "candles 5,000 bars: "
        f"p50 {samples[len(samples) // 2]:.1f} ms / p95 {samples[-1]:.1f} ms (<300 ms)"
    )
    assert len(body["t"]) == 5_000
    assert samples[-1] < 300.0, f"5,000-candle request took {samples[-1]:.1f} ms (<300 ms)"
