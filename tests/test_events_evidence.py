"""M5 (T5.3) - ``GET /v1/events/{id}`` Evidence payload (spec 11 line 532, UI 558).

Acceptance evidence produced here:
- full payload shape (event / trigger / context / stats / analogues /
  data_quality / dataset_version) with spec keys;
- stats block reuses the most recent matching stored run (ref_id equality);
- cache-miss recompute persists default-param events and serves them;
- unpersisted custom-param events miss recompute by construction -> 404;
- unknown ids -> 404 (fail-closed, never a substitute event);
- analogues rule: strictly earlier, same detector@version+type, at most 5,
  newest first, fwd_r_10 null when incomplete.
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
from engine.data.instruments import upsert_instruments
from exchanges.base import Kline
from tests.support import BTC, DAY, T0, WIDE_BUDGET, FixtureAdapter, reset_research
from tests.test_api_stats import SMALL_PARAMS

DAY_COUNT = 120
# Spike spacing clears every range_len window (an earlier spike low inside the
# window would break atLo): 108 is the only default-param event (past warmup).
SPIKES = {8, 48, 108}


def _bars() -> list[Kline]:
    """120 flat daily bars with volume+range spikes: flat base keeps the spike
    low the range low under both SMALL and default range_len, so both param
    sets fire CAPIT (defaults only past the 100-bar warmup)."""
    rows: list[Kline] = []
    for i in range(DAY_COUNT):
        open_ms = T0 + i * DAY
        open_ = 100.0
        if i in SPIKES:
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
    adapter = FixtureAdapter({("BTCUSDT", Timeframe.D1): _bars()})
    monkeypatch.setattr("apps.api.jobs.build_adapter", lambda params: adapter)
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
    reset_research(connection)
    upsert_instruments(connection, [BTC])
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
        reset_research(connection)
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
            "to": "2024-04-29T00:00:00Z",
            "storage_budget_gb": WIDE_BUDGET / 1024**3,
        },
    )
    assert response.status_code == 202, response.text
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
    return {k: v for k, v in query.items() if v is not None}


def _capit_ids(client: TestClient, **extra: Any) -> list[str]:
    body = client.get("/v1/events", params=_query(event_type="CAPIT", **extra)).json()
    assert body["events"], "fixture must fire CAPIT events"
    return [e["event_id"] for e in body["events"]]


def _stats_body() -> dict[str, Any]:
    return {
        "filter": {
            "detector": "RVOL_CLIMAX",
            "timeframe": "1D",
            "params": json.dumps(SMALL_PARAMS),
        },
        "instrument_ids": [str(BTC.instrument_id)],
    }


# --------------------------------------------------------------------------
# Persisted path + stats matching
# --------------------------------------------------------------------------


def test_evidence_payload_shape(client: TestClient, conn: Connection, seeded: str) -> None:
    posted = client.post("/v1/stats", json=_stats_body()).json()
    event_id = _capit_ids(client)[-1]  # latest CAPIT: analogues below are non-empty
    response = client.get(f"/v1/events/{event_id}")
    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == {
        "event",
        "trigger",
        "context",
        "stats",
        "analogues",
        "data_quality",
        "dataset_version",
    }
    event = body["event"]
    assert set(event) == {
        "id",
        "detector",
        "type",
        "version",
        "instrument",
        "tf",
        "time",
        "direction",
        "ref_price",
        "invalidation",
        "r_basis",
        "lookback_gap",
    }
    assert event["id"] == event_id
    assert event["detector"] == "RVOL_CLIMAX"
    assert event["type"] == "CAPIT"
    assert event["instrument"] == "BTC/USDT spot"
    assert event["r_basis"] == "atr"

    assert body["trigger"], "checklist must not be empty"
    assert {r["name"] for r in body["trigger"]} >= {"isClimax"}
    for rule in body["trigger"]:
        assert set(rule) == {"name", "passed", "value", "threshold", "provenance"}
        assert rule["provenance"] == "CALCULATED"

    assert set(body["context"]["regime"]) == {"trend_state", "vol_state", "volume_state"}
    assert isinstance(body["context"]["archetype"], str)

    stats = body["stats"]
    assert set(stats) == {
        "ref_id",
        "data_scope",
        "grade",
        "grade_reason",
        "model_sensitive",
        "n_raw",
        "n_eff",
        "horizons",
        "multiple_testing",
    }
    assert stats["ref_id"] == posted["ref_id"], "most recent matching run is reused"
    assert stats["data_scope"] == "DEVELOPMENT"
    assert stats["grade"] == "D"
    assert [h["h"] for h in stats["horizons"]] == [1, 2, 3, 5, 10, 15, 20, 30, 40, 50]
    for entry in stats["horizons"]:
        assert set(entry) == {"h", "median_r", "ci", "win", "win_ci", "is", "oos"}

    analogues = body["analogues"]
    assert 1 <= len(analogues) <= 5
    times = [a["time"] for a in analogues]
    assert times == sorted(times, reverse=True)
    assert all(t < event["time"] for t in times), "analogues are strictly earlier"
    for analogue in analogues:
        assert set(analogue) == {"event_id", "time", "instrument", "fwd_r_10"}
        assert analogue["fwd_r_10"] is not None  # h=10 completes on this fixture

    assert body["data_quality"]["state"] == "GREEN"
    assert body["data_quality"]["completeness"] == 1.0
    assert body["dataset_version"] == seeded


def test_evidence_earliest_event_has_no_analogues(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    client.post("/v1/stats", json=_stats_body())
    earliest = _capit_ids(client)[0]
    body = client.get(f"/v1/events/{earliest}").json()
    assert body["analogues"] == []


def test_evidence_deterministic(client: TestClient, conn: Connection, seeded: str) -> None:
    client.post("/v1/stats", json=_stats_body())
    event_id = _capit_ids(client)[-1]
    first = client.get(f"/v1/events/{event_id}").json()
    second = client.get(f"/v1/events/{event_id}").json()
    assert first == second


# --------------------------------------------------------------------------
# Cache-miss recompute + 404s
# --------------------------------------------------------------------------


def test_evidence_recompute_path(client: TestClient, conn: Connection, seeded: str) -> None:
    default_ids = client.get("/v1/events", params=_query(params=None)).json()["events"]
    assert default_ids, "default params must fire on the 120-bar fixture"
    target = default_ids[-1]["event_id"]
    conn.execute(
        text(
            "TRUNCATE validation.run, strategy.trade, strategy.backtest,"
            " research.event, research.event_outcome, research.stats_result,"
            " research.hypothesis_version, research.hypothesis_relationship,"
            " research.research_ledger"
        )
    )
    conn.commit()
    assert (
        conn.execute(
            text("SELECT COUNT(*) FROM research.event WHERE event_id = :id"), {"id": target}
        ).scalar_one()
        == 0
    )
    response = client.get(f"/v1/events/{target}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["event"]["id"] == target
    assert body["stats"]["ref_id"] is not None  # fresh run computed through POST /v1/stats
    assert (
        conn.execute(
            text("SELECT COUNT(*) FROM research.event WHERE event_id = :id"), {"id": target}
        ).scalar_one()
        == 1
    )


def test_evidence_unknown_id_404(client: TestClient, conn: Connection, seeded: str) -> None:
    response = client.get("/v1/events/" + "0" * 40)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_evidence_unpersisted_custom_params_404(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    custom_id = _capit_ids(client)[-1]  # SMALL_PARAMS event, never persisted here
    conn.execute(
        text(
            "TRUNCATE validation.run, strategy.trade, strategy.backtest,"
            " research.event, research.event_outcome, research.stats_result,"
            " research.hypothesis_version, research.hypothesis_relationship,"
            " research.research_ledger"
        )
    )
    conn.commit()
    response = client.get(f"/v1/events/{custom_id}")
    assert response.status_code == 404, (
        "default-param recompute cannot reproduce a custom-param event id"
    )
