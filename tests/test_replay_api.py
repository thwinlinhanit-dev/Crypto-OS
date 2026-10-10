"""M8 — replay no-future-leak against the real database (spec M8 AC).

With ``as_of`` set to bar k, the events returned contain no event with a
later ``event_time`` (PD-2, the truncation-invariance spirit). This is the
SQL-level assertion; tests/test_replay.py pins the unit contract.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text

from apps.api.main import create_app
from apps.api.settings import get_settings
from engine.data.instruments import upsert_instruments
from tests.support import BTC, WIDE_BUDGET, daily


@pytest.fixture()
def conn() -> Iterator[Connection]:
    engine = create_engine(
        get_settings().database_url, future=True, connect_args={"connect_timeout": 3}
    )
    try:
        connection = engine.connect()
    except Exception as exc:
        engine.dispose()
        pytest.skip(f"database unreachable: {exc}")
    try:
        yield connection
    finally:
        connection.close()
        engine.dispose()


def _require_db(conn: Connection) -> None:
    """Skip (not fail) when the scratch cluster is unreachable - the AC's hard
    gate is the unit suite in tests/test_replay.py; this suite is the
    live-database evidence."""
    try:
        conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"database unreachable: {exc}")


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def clean(conn: Connection) -> Iterator[None]:
    _require_db(conn)
    conn.execute(text("SET session_replication_role = replica"))
    for table in (
        "validation.run", "strategy.trade", "strategy.backtest",
        "strategy.strategy_version", "market.watchlist_item", "market.watchlist",
        "market.dataset_manifest", "market.instrument",
        "research.event", "research.event_outcome", "research.stats_result",
        "research.hypothesis", "research.note", "research.variant",
        "research.experiment", "research.experiment_family",
    ):
        conn.execute(text(f"DELETE FROM {table}"))
    conn.execute(text("SET session_replication_role = DEFAULT"))
    conn.commit()
    try:
        yield
    finally:
        conn.execute(text("SET session_replication_role = replica"))
        for table in (
            "validation.run", "strategy.trade", "strategy.backtest",
            "strategy.strategy_version", "market.watchlist_item", "market.watchlist",
            "market.dataset_manifest", "market.instrument",
            "research.event", "research.event_outcome", "research.stats_result",
            "research.hypothesis", "research.note", "research.variant",
            "research.experiment", "research.experiment_family",
        ):
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("SET session_replication_role = DEFAULT"))
        conn.commit()


@pytest.fixture()
def seeded_with_events(client: TestClient, conn: Connection, tmp_path: Path,
                       monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Ingest BTC, then run POST /v1/stats so events are persisted; returns
    the instrument id and the bar grid."""
    from engine.data.bars import Timeframe
    from tests.support import FixtureAdapter

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "apps.api.jobs.build_adapter",
        lambda params: FixtureAdapter({("BTCUSDT", Timeframe.D1): daily()}),
    )
    upsert_instruments(conn, [BTC])
    conn.commit()
    response = client.post(
        "/v1/data/ingest",
        json={
            "venue": "BINANCE", "market_type": "SPOT",
            "instrument_ids": [str(BTC.instrument_id)], "timeframes": ["1D"],
            "from": "2024-01-01T00:00:00Z", "to": "2024-03-01T00:00:00Z",
            "storage_budget_gb": WIDE_BUDGET / 1024**3,
        },
    )
    assert response.status_code == 202, response.text
    view = client.get(f"/v1/jobs/{response.json()['job_id']}").json()
    assert view["status"] == "succeeded", view.get("error")
    stats = client.post(
        "/v1/stats",
        json={
            "filter": {"detector": "RVOL_CLIMAX", "timeframe": "1D"},
            "instrument_ids": [str(BTC.instrument_id)],
            "data_scope": "DEVELOPMENT",
        },
    )
    assert stats.status_code == 200, stats.text
    return {"instrument_id": str(BTC.instrument_id)}


def test_as_of_events_never_leak_the_future(client: TestClient, conn: Connection,
                                            seeded_with_events: dict[str, Any]) -> None:
    """M8 AC: with as_of at bar k, no event after k is visible."""
    from engine.scanner.replay import replay_events

    events = replay_events(
        conn, instrument_id=seeded_with_events["instrument_id"],
        detector="RVOL_CLIMAX", as_of=None,
    )
    if not events:
        pytest.skip("no events emitted on the fixture window")
    as_of_ms_value = int(
        events[0]["event_time"].replace("Z", "+00:00").replace("+00:00", "")[:19]
        .replace("-", "").replace("T", "").replace(":", "")
    ) if False else None
    # use the first event's own time as the cutoff
    from datetime import datetime

    first = datetime.fromisoformat(events[0]["event_time"].replace("Z", "+00:00"))
    as_of_ms_value = int(first.timestamp() * 1000)
    visible = replay_events(
        conn, instrument_id=seeded_with_events["instrument_id"],
        detector="RVOL_CLIMAX", as_of=as_of_ms_value,
    )
    for row in visible:
        assert row["event_time"] <= events[0]["event_time"]
    assert len(visible) <= len(events)


def test_as_of_bars_never_leak_the_future(conn: Connection,
                                          seeded_with_events: dict[str, Any]) -> None:
    """M8 AC: bars stop at as_of (close_time <= as_of, D-008)."""
    from datetime import datetime

    import polars as pl

    from engine.scanner.replay import replay_bars
    from tests.support import T0

    frame = pl.DataFrame(
        {
            "open_time": [T0 + i * 86_400_000 for i in range(10)],
            "close_time": [T0 + (i + 1) * 86_400_000 for i in range(10)],
        }
    )
    as_of = T0 + 5 * 86_400_000
    visible = replay_bars(frame, as_of)
    assert visible.height == 5
    assert visible["close_time"].max() <= as_of
    # a future as_of shows everything
    assert replay_bars(frame, T0 + 100 * 86_400_000).height == 10
    _ = datetime  # imported for the event-time assertion above
