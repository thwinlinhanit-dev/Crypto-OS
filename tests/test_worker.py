"""``apps.worker``: the queue drain shares the API's execution glue.

Acceptance evidence produced here (design note section 10):
- ``process_one`` claims the *oldest* queued job (SKIP LOCKED) and runs it
  through ``apps.api.jobs.execute_claimed`` -- one job row, one execution
  path regardless of which side picks it up;
- an empty queue answers ``False`` (the poll loop sleeps, it does not spin);
- a job whose adapter cannot be built is recorded ``failed`` with the spec
  error envelope instead of crashing the loop.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, create_engine, text

from apps.api.settings import get_settings
from apps.worker.runner import process_one
from engine.data.bars import Timeframe
from engine.data.instruments import upsert_instruments
from engine.errors import NotImplementedMarketError
from engine.jobs import JobKind, JobStatus, enqueue_job, get_job
from tests.support import BTC, DAY, INGEST_AT, NOW, T0, WIDE_BUDGET, FixtureAdapter, daily


def _ingest_params(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "venue": "BINANCE",
        "market_type": "SPOT",
        "instrument_ids": [str(BTC.instrument_id)],
        "timeframes": ["1D"],
        "start_ms": T0,
        "end_ms": T0 + 9 * DAY,
        "storage_budget_bytes": WIDE_BUDGET,
    }
    params.update(overrides)
    return params


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(get_settings().database_url, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def conn(engine: Engine) -> Iterator[Connection]:
    """Committed connection with a clean slate (the worker claims in its own)."""
    connection = engine.connect()
    connection.execute(text("DELETE FROM system.job"))
    connection.execute(text("DELETE FROM system.gap"))
    connection.execute(text("DELETE FROM system.data_quality"))
    connection.execute(text("DELETE FROM market.dataset_manifest"))
    connection.execute(text("DELETE FROM system.audit_log"))
    upsert_instruments(connection, [BTC])
    connection.commit()
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "data"
    directory.mkdir()
    monkeypatch.setenv("DATA_DIR", str(directory))
    return directory


@pytest.fixture()
def venue(monkeypatch: pytest.MonkeyPatch) -> FixtureAdapter:
    adapter = FixtureAdapter({("BTCUSDT", Timeframe.D1): daily()})
    monkeypatch.setattr("apps.api.jobs.build_adapter", lambda params: adapter)
    monkeypatch.setattr("apps.api.jobs.now_ms", lambda: NOW)
    return adapter


async def test_process_one_runs_the_oldest_queued_job(
    conn: Connection, data_dir: Path, venue: FixtureAdapter
) -> None:
    first = enqueue_job(conn, kind=JobKind.INGEST, params=_ingest_params())
    # both rows share one transaction's now(), so age the first explicitly:
    # claim_next_job orders by created_at and uuid4 ties are not an order.
    conn.execute(
        text(
            "UPDATE system.job SET created_at = created_at - interval '1 hour' "
            "WHERE job_id = :job_id"
        ),
        {"job_id": first.job_id},
    )
    second = enqueue_job(conn, kind=JobKind.RESAMPLE, params=_ingest_params(timeframes=["1W"]))
    conn.commit()

    assert await process_one(data_dir=data_dir, clock_ms=INGEST_AT) is True

    done = get_job(conn, first.job_id)
    assert done is not None
    assert done.status == JobStatus.SUCCEEDED
    assert done.result_ref is not None
    assert any(entry.startswith("succeeded") for entry in done.logs)
    # exactly one row per call -- the second job stays queued for the next poll
    waiting = get_job(conn, second.job_id)
    assert waiting is not None
    assert waiting.status == JobStatus.QUEUED


async def test_process_one_answers_false_on_an_empty_queue(
    conn: Connection, data_dir: Path, venue: FixtureAdapter
) -> None:
    assert await process_one(data_dir=data_dir, clock_ms=NOW) is False


async def test_worker_records_a_failed_job_without_raising(
    conn: Connection, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(params: dict[str, Any]) -> FixtureAdapter:
        raise NotImplementedMarketError("fixture refuses this venue", {"venue": "X"})

    monkeypatch.setattr("apps.api.jobs.build_adapter", _boom)
    job = enqueue_job(conn, kind=JobKind.INGEST, params=_ingest_params())
    conn.commit()

    assert await process_one(data_dir=data_dir, clock_ms=NOW) is True

    row = get_job(conn, job.job_id)
    assert row is not None
    assert row.status == JobStatus.FAILED
    assert row.error is not None
    envelope = json.loads(row.error)
    assert envelope["code"] == "NOT_IMPLEMENTED"
