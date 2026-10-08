"""Spec section 10 ``system.job`` + section 11 jobs contract: the M1 job queue.

Acceptance evidence produced here:
- enqueue -> ``queued``; claim -> ``running`` exactly once (section 11:
  ``POST`` returns ``{"job_id","status":"queued"}``);
- ``execute_job`` dispatches INGEST / REPAIR / RESAMPLE through the *same*
  engine routines ``test_ingest`` gates, records ``succeeded`` with the
  ``dataset_version`` as ``result_ref``, and records a failed job with the
  spec error envelope in ``error`` without raising (design note section 5);
- stale ``running`` jobs are requeued by ``recover_stale_jobs``.

INTERPRETATIONS (design note section 5, DECISIONS): the spec names only
``queued``, so the lifecycle is ``queued -> running -> succeeded|failed``;
``error`` stores the envelope JSON ``{code,message,detail}``; progress is
coarse (0 claimed, 1 on success).  ``execute_job`` never commits: the caller
owns the transaction, and on failure it rolls back only its own writes (the
claim must already be committed, hence this module's committed fixtures).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, create_engine, text

from apps.api.settings import get_settings
from engine.data.bars import Timeframe
from engine.data.guardian import (
    ExclusionInterval,
    ExclusionKind,
    get_gap,
    load_exclusions,
    save_exclusions,
)
from engine.data.ingest import run_ingest
from engine.data.instruments import upsert_instruments
from engine.data.manifest import get_manifest
from engine.errors import ValidationError
from engine.jobs import (
    JobKind,
    JobStatus,
    claim_job,
    claim_next_job,
    enqueue_job,
    execute_job,
    get_job,
    recover_stale_jobs,
    resolve_job_kind,
    scope_from_params,
)
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


def _adapter() -> FixtureAdapter:
    return FixtureAdapter({("BTCUSDT", Timeframe.D1): daily()})


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(get_settings().database_url, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def conn(engine: Engine) -> Iterator[Connection]:
    """Committed connection with a clean slate for the tables jobs write.

    ``execute_job`` rolls back mid-job, so enqueue/claim must be durable
    before it runs; committed rows from a previous run would otherwise leak
    into assertions, hence the explicit deletes (the data files live under
    ``tmp_path`` and are thrown away with it).
    """
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
def data_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "data"
    directory.mkdir(parents=True)
    return directory


# --------------------------------------------------------------------------
# Queue lifecycle: enqueue / claim / claim_next / get
# --------------------------------------------------------------------------


def test_enqueue_returns_a_queued_job(conn: Connection) -> None:
    job = enqueue_job(conn, kind=JobKind.INGEST, params=_ingest_params())
    conn.commit()

    assert job.status is JobStatus.QUEUED
    assert job.kind is JobKind.INGEST
    assert job.progress == 0.0
    assert job.logs == []
    assert job.error is None and job.result_ref is None
    assert job.started_at is None and job.finished_at is None
    stored = get_job(conn, job.job_id)
    assert stored is not None
    assert stored.params == _ingest_params()


def test_claim_marks_running_exactly_once(conn: Connection) -> None:
    job = enqueue_job(conn, kind=JobKind.INGEST, params=_ingest_params())
    conn.commit()

    claimed = claim_job(conn, job.job_id)
    conn.commit()
    assert claimed is not None
    assert claimed.status is JobStatus.RUNNING
    assert claimed.started_at is not None
    assert claimed.logs == ["claimed"]
    assert claim_job(conn, job.job_id) is None
    running = get_job(conn, job.job_id)
    assert running is not None
    assert running.status is JobStatus.RUNNING


def test_claim_next_takes_oldest_queued_then_returns_none(conn: Connection) -> None:
    first = enqueue_job(conn, kind=JobKind.INGEST, params=_ingest_params())
    conn.commit()
    second = enqueue_job(conn, kind=JobKind.REPAIR, params={"gap_id": str(uuid.uuid4())})
    conn.commit()

    one = claim_next_job(conn)
    conn.commit()
    two = claim_next_job(conn)
    conn.commit()
    assert one is not None and two is not None
    assert one.job_id == first.job_id
    assert two.job_id == second.job_id
    assert claim_next_job(conn) is None


def test_get_job_unknown_id_is_none(conn: Connection) -> None:
    assert get_job(conn, uuid.uuid4()) is None


# --------------------------------------------------------------------------
# execute_job dispatch: INGEST / REPAIR / RESAMPLE / failure
# --------------------------------------------------------------------------


async def test_execute_ingest_job_succeeds_and_records_the_dataset(
    conn: Connection, data_dir: Path
) -> None:
    job = enqueue_job(conn, kind=JobKind.INGEST, params=_ingest_params())
    conn.commit()
    claimed = claim_job(conn, job.job_id)
    conn.commit()
    assert claimed is not None

    record = await execute_job(
        conn,
        claimed,
        adapter_factory=_adapter,
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
    )
    conn.commit()

    assert record.status is JobStatus.SUCCEEDED
    assert record.progress == 1.0
    assert record.result_ref is not None
    assert get_manifest(conn, record.result_ref) is not None
    assert record.error is None
    assert any(entry.startswith("succeeded dataset_version=") for entry in record.logs)
    stored = get_job(conn, job.job_id)
    assert stored is not None
    assert stored.status is JobStatus.SUCCEEDED
    assert stored.finished_at is not None


async def test_execute_job_records_engine_errors_without_raising(
    conn: Connection, data_dir: Path
) -> None:
    unknown = str(uuid.uuid4())
    job = enqueue_job(conn, kind=JobKind.INGEST, params=_ingest_params(instrument_ids=[unknown]))
    conn.commit()
    claimed = claim_job(conn, job.job_id)
    conn.commit()
    assert claimed is not None

    record = await execute_job(
        conn,
        claimed,
        adapter_factory=FixtureAdapter,
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
    )
    conn.commit()

    assert record.status is JobStatus.FAILED
    assert record.error is not None
    error = json.loads(record.error)
    assert error["code"] == "NOT_FOUND"
    assert error["detail"]["instrument_id"] == unknown
    assert record.result_ref is None
    assert record.logs[-1].startswith("failed NOT_FOUND:")
    assert not list(data_dir.rglob("*.parquet"))
    stored = get_job(conn, job.job_id)
    assert stored is not None and stored.status is JobStatus.FAILED


async def test_execute_repair_job_closes_an_open_gap(conn: Connection, data_dir: Path) -> None:
    gap_ids = save_exclusions(
        conn,
        instrument_id=BTC.instrument_id,
        timeframe="1D",
        intervals=[
            ExclusionInterval(start_ms=T0 + 4 * DAY, end_ms=T0 + 5 * DAY, kind=ExclusionKind.GAP)
        ],
        source="test",
    )
    conn.commit()
    gap_id = gap_ids[0]

    job = enqueue_job(
        conn,
        kind=JobKind.REPAIR,
        params={"gap_id": str(gap_id), "storage_budget_bytes": WIDE_BUDGET},
    )
    conn.commit()
    claimed = claim_job(conn, job.job_id)
    conn.commit()
    assert claimed is not None

    record = await execute_job(
        conn,
        claimed,
        adapter_factory=_adapter,
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
    )
    conn.commit()

    assert record.status is JobStatus.SUCCEEDED
    assert record.result_ref is not None
    gap = get_gap(conn, gap_id)
    assert gap is not None
    assert gap.status == "REPAIRED"
    assert gap.repaired_dataset_version == record.result_ref
    assert record.logs[-1].startswith("succeeded outcome=REPAIRED")
    active = load_exclusions(
        conn, instrument_id=BTC.instrument_id, timeframe="1D", active_only=True
    )
    assert active == []


async def test_execute_resample_job_derives_weekly(conn: Connection, data_dir: Path) -> None:
    await run_ingest(
        conn,
        scope_from_params(_ingest_params()),
        _adapter(),
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
    )
    conn.commit()

    job = enqueue_job(conn, kind=JobKind.RESAMPLE, params=_ingest_params(timeframes=["1W"]))
    conn.commit()
    claimed = claim_job(conn, job.job_id)
    conn.commit()
    assert claimed is not None

    record = await execute_job(
        conn,
        claimed,
        adapter_factory=_adapter,
        data_dir=data_dir,
        now_ms=NOW,
        ingested_at_ms=INGEST_AT,
    )
    conn.commit()

    assert record.status is JobStatus.SUCCEEDED
    assert record.result_ref is not None
    manifest = get_manifest(conn, record.result_ref)
    assert manifest is not None
    assert manifest.timeframes == ("1W",)


# --------------------------------------------------------------------------
# Params + kind resolution (PD-11 fail-closed) and stale recovery
# --------------------------------------------------------------------------


def test_scope_from_params_requires_every_parameter() -> None:
    with pytest.raises(ValidationError) as first:
        scope_from_params({})
    assert first.value.detail["param"] == "venue"

    with pytest.raises(ValidationError) as bad_uuid:
        scope_from_params(_ingest_params(instrument_ids=["not-a-uuid"]))
    assert bad_uuid.value.detail["param"] == "instrument_ids"

    with pytest.raises(ValidationError) as bad_tf:
        scope_from_params(_ingest_params(timeframes=["5m"]))
    assert bad_tf.value.detail["param"] == "timeframes"


def test_resolve_job_kind_dispatches_ingest_and_resample() -> None:
    assert resolve_job_kind(["1D"]) is JobKind.INGEST
    assert resolve_job_kind(["1D", "4H"]) is JobKind.INGEST
    assert resolve_job_kind(["1W"]) is JobKind.RESAMPLE
    for broken in ([], ["1D", "1W"], ["5m"]):
        with pytest.raises(ValidationError):
            resolve_job_kind(broken)


def test_recover_stale_jobs_requeues_running(conn: Connection) -> None:
    job = enqueue_job(conn, kind=JobKind.INGEST, params=_ingest_params())
    conn.commit()
    assert claim_job(conn, job.job_id) is not None
    conn.commit()

    recovered = recover_stale_jobs(conn, stale_after_seconds=0)
    conn.commit()

    assert recovered == 1
    again = get_job(conn, job.job_id)
    assert again is not None
    assert again.status is JobStatus.QUEUED
    assert again.started_at is None
    assert again.logs[-1] == "requeued after stale lease"
    assert recover_stale_jobs(conn, stale_after_seconds=3600) == 0
