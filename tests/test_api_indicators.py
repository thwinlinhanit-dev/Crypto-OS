"""M2 (T2.1) - ``GET /v1/indicators`` (spec sections 5, 11; design note m2_chart section 2).

Acceptance evidence produced here:
- the response contract decided in D-011 (``name``, resolved ``params``,
  ``instrument_id``, ``tf``, ``dataset_version``, ``window``, ``t``,
  keyed ``series``) with ``null`` warm-up values (section 5);
- one dataset provenance: ``dataset_version`` and ``t`` equal the candles
  response for the same window (PD-6);
- PD-11 fail-closed parameter handling through the 422 envelope: unknown name,
  undocumented parameter, missing documented default, invalid window size,
  unparsable ``params`` JSON - nothing is defaulted or guessed;
- PD-10: unknown instrument 404, unknown timeframe 422, perpetual 501,
  no manifest / empty window 400 DATA_UNAVAILABLE;
- AC performance (section 14.3): ema, bb and rsi each over 5,000 bars answer
  within 150 ms, measured with ``time.perf_counter`` around the request.
"""

from __future__ import annotations

import json
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
from engine.config.defaults import ParamErrorCode
from engine.data.bars import Timeframe
from engine.data.instruments import build_instrument, upsert_instruments
from tests.support import BTC, NOW, T0, WIDE_BUDGET, FixtureAdapter, daily, hourly

HOUR = 3_600_000
PERP = build_instrument(
    venue="BINANCE",
    market_type="PERPETUAL",
    asset="BTC",
    quote="USDT",
    native_symbol="BTCUSDT_PERP",
)


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
def seeded(client: TestClient, conn: Connection, venue: FixtureAdapter, data_dir: Path) -> str:
    response = client.post(
        "/v1/data/ingest",
        json={
            "venue": "BINANCE",
            "market_type": "SPOT",
            "instrument_ids": [str(BTC.instrument_id)],
            "timeframes": ["1D"],
            "from": "2024-01-01T00:00:00Z",
            "to": "2024-01-10T00:00:00Z",
            "storage_budget_gb": WIDE_BUDGET / 1024**3,
        },
    )
    assert response.status_code == 202
    view = client.get(f"/v1/jobs/{response.json()['job_id']}").json()
    assert view["status"] == "succeeded", view["error"]
    return str(view["result_ref"])


def _params(name: str, params: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    query: dict[str, Any] = {"instrument_id": str(BTC.instrument_id), "tf": "1D", "name": name}
    if params is not None:
        query["params"] = json.dumps(params)
    query.update(extra)
    return query


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------


def test_indicator_answer_the_d011_contract_with_null_warmup(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get("/v1/indicators", params=_params("sma", {"length": 3}))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "name",
        "params",
        "instrument_id",
        "tf",
        "dataset_version",
        "window",
        "t",
        "series",
    }
    assert body["name"] == "sma"
    assert body["params"] == {"length": 3}
    assert body["instrument_id"] == str(BTC.instrument_id)
    assert body["tf"] == "1D"
    assert body["dataset_version"] == seeded
    assert len(body["t"]) == 10
    assert len(body["series"]) == 1
    series = body["series"][0]
    assert series["key"] == "sma"
    assert series["values"][:2] == [None, None]  # warm-up, never filled
    # daily() closes are 101.0, 102.0, ... -> first sma(3) = 102.0
    assert series["values"][2] == pytest.approx(102.0)
    assert body["window"]["limit"] == 5_000


def test_indicator_series_align_with_the_candles_response(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    candles = client.get(
        "/v1/candles",
        params={
            "instrument_id": str(BTC.instrument_id),
            "tf": "1D",
            "from": "2024-01-02T00:00:00Z",
            "to": "2024-01-09T00:00:00Z",
        },
    ).json()
    indicator = client.get(
        "/v1/indicators",
        params=_params("ema", {"length": 2}, **{
            "from": "2024-01-02T00:00:00Z",
            "to": "2024-01-09T00:00:00Z",
        }),
    ).json()
    assert indicator["dataset_version"] == candles["dataset_version"]
    assert indicator["t"] == candles["t"]


def test_bollinger_returns_keyed_upper_middle_lower(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    body = client.get("/v1/indicators", params=_params("bb")).json()
    assert [entry["key"] for entry in body["series"]] == ["upper", "middle", "lower"]
    assert body["params"] == {"length": 20, "mult": 2}


def test_vwap_needs_no_parameters(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    body = client.get("/v1/indicators", params=_params("vwap")).json()
    assert body["params"] == {}
    assert all(value is not None for value in body["series"][0]["values"])


# --------------------------------------------------------------------------
# PD-11: parameters resolve from the request or config/defaults.yaml only
# --------------------------------------------------------------------------


def test_missing_required_length_is_422(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get("/v1/indicators", params=_params("sma"))
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["detail"]["reason"] == ParamErrorCode.MISSING_PARAM


def test_unknown_indicator_is_422(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get("/v1/indicators", params=_params("macd"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_undocumented_parameter_is_422(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get("/v1/indicators", params=_params("sma", {"length": 2, "x": 1}))
    assert response.status_code == 422
    assert response.json()["error"]["detail"]["reason"] == ParamErrorCode.UNDOCUMENTED_PARAM


def test_window_size_domain_violation_is_422(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get("/v1/indicators", params=_params("sma", {"length": 0}))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unparsable_params_json_is_422(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get(
        "/v1/indicators",
        params={"instrument_id": str(BTC.instrument_id), "tf": "1D", "name": "sma", "params": "{"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --------------------------------------------------------------------------
# PD-10 fail-closed resolution
# --------------------------------------------------------------------------


def test_unknown_instrument_is_404(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get(
        "/v1/indicators", params=_params("sma", {"length": 2}, instrument_id=str(uuid.uuid4()))
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_unknown_timeframe_is_422(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get("/v1/indicators", params=_params("sma", {"length": 2}, tf="2D"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_perpetual_requests_fail_closed(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    upsert_instruments(conn, [PERP])
    conn.commit()
    response = client.get(
        "/v1/indicators",
        params=_params("sma", {"length": 2}, instrument_id=str(PERP.instrument_id)),
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
        "/v1/indicators",
        params=_params("sma", {"length": 2}, instrument_id=str(eth.instrument_id)),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"


# --------------------------------------------------------------------------
# Performance (section 14.3: indicators, 5,000 bars < 150 ms)
# --------------------------------------------------------------------------


def test_indicators_over_5000_bars_meet_the_spec_budget(
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
        json={
            "venue": "BINANCE",
            "market_type": "SPOT",
            "instrument_ids": [str(BTC.instrument_id)],
            "timeframes": ["1H"],
            "from": "2024-01-01T00:00:00Z",
            "to": "2024-07-29T00:00:00Z",
            "storage_budget_gb": WIDE_BUDGET / 1024**3,
        },
    )
    assert response.status_code == 202
    job = client.get(f"/v1/jobs/{response.json()['job_id']}").json()
    assert job["status"] == "succeeded", job["error"]

    cases: list[tuple[str, dict[str, Any] | None]] = [
        ("ema", {"length": 20}),
        ("bb", None),
        ("rsi", {"length": 14}),
    ]
    for name, params in cases:
        samples: list[float] = []
        body: dict[str, Any] = {}
        for _ in range(5):
            started = time.perf_counter()
            result = client.get(
                "/v1/indicators", params=_params(name, params, tf="1H", limit=5_000)
            )
            samples.append((time.perf_counter() - started) * 1000.0)
            assert result.status_code == 200, result.text
            body = result.json()
        samples.sort()
        print(
            f"{name} over 5,000 bars: "
            f"p50 {samples[len(samples) // 2]:.1f} ms / p95 {samples[-1]:.1f} ms (<150 ms)"
        )
        assert len(body["t"]) == 5_000
        assert samples[-1] < 150.0, f"{name} over 5,000 bars took {samples[-1]:.1f} ms (<150 ms)"
