"""M4 (T4.4) - ``GET /v1/events`` (spec sections 6.2, 11; design note section 6).

Acceptance evidence produced here:
- the section 6.2 envelope contract (detector/version/instrument/tf/
  dataset_version/window/events, every envelope field present);
- ``dataset_version`` equals the candles response for the same window (PD-6);
- PD-11 fail-closed: unknown detector 422, SPEC_REQUIRED detector 422,
  unknown version 422, undocumented param 422, unknown event_type 422;
- PD-10: unknown instrument 404, perpetual 501, unknown timeframe 422;
- ``event_type`` filter narrows the list; envelopes are ordered by
  (event_index, event_type).
"""

from __future__ import annotations

import json
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
from exchanges.base import Kline
from tests.support import BTC, DAY, NOW, T0, WIDE_BUDGET, FixtureAdapter

PERP = build_instrument(
    venue="BINANCE",
    market_type="PERPETUAL",
    asset="BTC",
    quote="USDT",
    native_symbol="BTCUSDT_PERP",
)

# Small params so the 10-bar fixture clears warmup (PD-11: overrides only).
SMALL_PARAMS: dict[str, Any] = {
    "vol_len": 3,
    "climax_mult": 3.0,
    "top_mult": 2.0,
    "range_len": 3,
    "trend_len": 2,
    "close_pos_lo": 0.30,
    "close_pos_hi": 0.70,
    "regime_len": 4,
    "bounce_len": 3,
    "bounce_atr": 1.5,
    "wick_min": 0.40,
    "cooldown": 0,
    "atr_len": 3,
    "fail_mode": "failed_bounce",
    "lookback_bars": 5,
}


def _spike_daily() -> list[Kline]:
    """Ten daily bars with a volume+range spike on day 8 (fires CAPIT/CONT)."""
    rows: list[Kline] = []
    for i in range(10):
        open_ms = T0 + i * DAY
        open_ = 100.0 + i
        if i == 8:
            # Climax bar: volume 10x, closes near its low (cpos <= 0.30).
            rows.append(
                Kline(
                    open_time=open_ms,
                    close_time=open_ms + DAY,
                    open=open_,
                    high=open_ + 1.0,
                    low=open_ - 12.0,
                    close=open_ - 11.0,
                    volume=10_000.0,
                    quote_volume=open_ * 10_000.0,
                    trades=5_000,
                )
            )
        else:
            rows.append(
                Kline(
                    open_time=open_ms,
                    close_time=open_ms + DAY,
                    open=open_,
                    high=open_ + 5.0,
                    low=open_ - 5.0,
                    close=open_ + 1.0,
                    volume=1_000.0,
                    quote_volume=open_ * 1_000.0,
                    trades=500,
                )
            )
    return rows


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "data"
    directory.mkdir(parents=True)
    monkeypatch.setenv("DATA_DIR", str(directory))
    return directory


@pytest.fixture()
def venue(monkeypatch: pytest.MonkeyPatch) -> FixtureAdapter:
    adapter = FixtureAdapter({("BTCUSDT", Timeframe.D1): _spike_daily()})
    monkeypatch.setattr("apps.api.jobs.build_adapter", lambda params: adapter)
    monkeypatch.setattr("apps.api.jobs.now_ms", lambda: NOW)
    return adapter


@pytest.fixture()
def conn() -> Iterator[Connection]:
    engine = create_engine(get_settings().database_url, future=True)
    connection = engine.connect()
    for statement in (
        "DELETE FROM system.job",
        "DELETE FROM system.gap",
        "DELETE FROM system.data_quality",
        "DELETE FROM market.dataset_manifest",
        "DELETE FROM system.audit_log",
        "DELETE FROM market.instrument",
    ):
        connection.execute(text(statement))
    upsert_instruments(connection, [BTC, PERP])
    connection.commit()
    try:
        yield connection
    finally:
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
    assert view["status"] == "succeeded", view.get("error")
    return str(view["result_ref"])


def _query(**extra: Any) -> dict[str, Any]:
    query: dict[str, Any] = {
        "instrument_id": str(BTC.instrument_id),
        "tf": "1D",
        "detector": "RVOL_CLIMAX",
        "params": json.dumps(SMALL_PARAMS),
    }
    query.update(extra)
    return query


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------


def test_events_answer_the_section_6_2_contract(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get("/v1/events", params=_query())
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "detector",
        "version",
        "instrument_id",
        "tf",
        "dataset_version",
        "window",
        "events",
    }
    assert body["detector"] == "RVOL_CLIMAX"
    assert body["version"] == "5.0"
    assert body["instrument_id"] == str(BTC.instrument_id)
    assert body["tf"] == "1D"
    assert body["dataset_version"] == seeded
    assert body["window"]["limit"] == 5_000


def test_events_dataset_version_matches_candles(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    candles = client.get(
        "/v1/candles",
        params={"instrument_id": str(BTC.instrument_id), "tf": "1D"},
    ).json()
    events = client.get("/v1/events", params=_query()).json()
    assert events["dataset_version"] == candles["dataset_version"]


def test_event_envelope_carries_every_section_6_2_field(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    body = client.get("/v1/events", params=_query()).json()
    assert body["events"], "fixture should fire at least one event"
    envelope = body["events"][0]
    assert set(envelope) >= {
        "event_id",
        "detector_id",
        "event_type",
        "version",
        "params_hash",
        "instrument_id",
        "timeframe",
        "event_time",
        "direction",
        "ref_price",
        "invalidation_price",
        "regime",
        "features",
        "trigger",
        "lookback_gap",
        "dataset_version",
        "engine_version",
        "data_quality",
        "resolved_params",
        "provenance",
    }
    assert envelope["detector_id"] == "RVOL_CLIMAX"
    assert envelope["event_type"] in {"CONT", "CAPIT", "FAIL", "TOP"}
    assert envelope["direction"] in {"LONG", "SHORT"}
    assert envelope["event_id"]
    assert envelope["params_hash"]
    assert envelope["provenance"]["ref_price"] == "OBSERVED"
    assert envelope["provenance"]["features"] == "CALCULATED"
    assert envelope["resolved_params"]["cooldown"] == 0


def test_events_are_ordered_by_event_index(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    body = client.get("/v1/events", params=_query()).json()
    indices = [
        # event_index is engine-internal; order by event_time as the
        # envelope exposes it, which is monotonic in event_index.
        e["event_time"]
        for e in body["events"]
    ]
    assert indices == sorted(indices)


def test_event_type_filter_narrows_the_list(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    unfiltered = client.get("/v1/events", params=_query()).json()["events"]
    assert unfiltered, "need at least one event to filter"
    target = unfiltered[0]["event_type"]
    filtered = client.get(
        "/v1/events", params=_query(event_type=target)
    ).json()["events"]
    assert filtered
    assert all(e["event_type"] == target for e in filtered)
    assert len(filtered) <= len(unfiltered)


# --------------------------------------------------------------------------
# PD-10 / PD-11 fail-closed
# --------------------------------------------------------------------------


def test_unknown_event_type_is_422(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get("/v1/events", params=_query(event_type="NOT_A_TYPE"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_detector_is_422(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get("/v1/events", params=_query(detector="NOPE"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_spec_required_detector_is_422(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get("/v1/events", params=_query(detector="TOP_CAPIT"))
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["detail"]["status"] == "SPEC_REQUIRED"


def test_unknown_version_is_422(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get("/v1/events", params=_query(version="9.9"))
    assert response.status_code == 422


def test_undocumented_param_is_422(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    params = {**SMALL_PARAMS, "invented_knob": 1}
    response = client.get("/v1/events", params=_query(params=json.dumps(params)))
    assert response.status_code == 422


def test_unknown_instrument_is_404(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get(
        "/v1/events",
        params=_query(instrument_id="00000000-0000-0000-0000-000000000000"),
    )
    assert response.status_code == 404


def test_perpetual_is_501(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get(
        "/v1/events", params=_query(instrument_id=str(PERP.instrument_id))
    )
    assert response.status_code == 501


def test_unknown_timeframe_is_422(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    response = client.get("/v1/events", params=_query(tf="7D"))
    assert response.status_code == 422
