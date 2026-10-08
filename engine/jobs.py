"""Spec section 10 ``system.job`` + section 11 jobs contract: the M1 job queue.

``enqueue_job`` records work, ``claim_job``/``claim_next_job`` move it to
``running`` (the worker uses ``FOR UPDATE SKIP LOCKED``), and ``execute_job``
dispatches it through the same engine routines ``run_ingest`` / ``run_repair``
/ ``run_resample`` that the ingest tests gate (design note section 5).

INTERPRETATIONS (design note section 5, DECISIONS):
- section 11 names only ``queued``, so the lifecycle is
  ``queued -> running -> succeeded | failed``;
- ``error`` stores the spec error envelope as JSON ``{code,message,detail}``
  for ``GET /v1/jobs/{id}``, while ``logs`` stays a list of short strings;
- progress is coarse: 0 when claimed, 1.0 on success;
- a ``running`` job whose ``started_at`` is older than the caller's threshold
  is requeued (``recover_stale_jobs``): its partial work was rolled back with
  the dead process, and re-running resumes at the chunk-reuse step (AC1).

``execute_job`` never commits. The caller owns the transaction and must have
committed the claim first: on failure only this dispatch's writes are rolled
back, the FAILED outcome is recorded, and the caller commits that.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from engine.data.bars import Timeframe, UnknownTimeframeError, is_derived, parse_timeframe
from engine.data.ingest import IngestScope, run_ingest, run_repair, run_resample
from engine.data.instruments import MarketType
from engine.errors import EngineError, ValidationError
from exchanges.base import ExchangeAdapter

_SELECT = (
    "job_id, kind, status, progress, params, error, result_ref, logs, "
    "created_at, started_at, finished_at"
)


class JobKind(StrEnum):
    """Job kinds M1 enqueues (design note section 10)."""

    INGEST = "INGEST"
    REPAIR = "REPAIR"
    RESAMPLE = "RESAMPLE"


class JobStatus(StrEnum):
    """Lifecycle statuses (INTERPRETATION: section 11 names only 'queued')."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class JobRecord:
    """One ``system.job`` row (section 11 ``GET /v1/jobs/{id}`` payload source)."""

    job_id: UUID
    kind: JobKind
    status: JobStatus
    progress: float
    params: dict[str, Any]
    error: str | None
    result_ref: str | None
    logs: list[str]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _json_field(raw: Any) -> Any:
    return json.loads(raw) if isinstance(raw, str) else raw


def _to_job(row: Any) -> JobRecord:
    return JobRecord(
        job_id=row.job_id,
        kind=JobKind(row.kind),
        status=JobStatus(row.status),
        progress=float(row.progress),
        params=dict(_json_field(row.params)),
        error=row.error,
        result_ref=row.result_ref,
        logs=list(_json_field(row.logs) or []),
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def enqueue_job(conn: Connection, *, kind: JobKind, params: dict[str, Any]) -> JobRecord:
    """Record work as ``queued`` (section 11: POST answers ``job_id,status``)."""
    row = conn.execute(
        text(
            f"INSERT INTO system.job (job_id, kind, status, progress, params, logs) "
            f"VALUES (:job_id, :kind, '{JobStatus.QUEUED.value}', 0, "
            "CAST(:params AS jsonb), '[]'::jsonb) "
            f"RETURNING {_SELECT}"
        ),
        {"job_id": uuid4(), "kind": JobKind(kind).value, "params": json.dumps(params)},
    ).one()
    return _to_job(row)


def get_job(conn: Connection, job_id: UUID) -> JobRecord | None:
    row = conn.execute(
        text(f"SELECT {_SELECT} FROM system.job WHERE job_id = :job_id"), {"job_id": job_id}
    ).first()
    return None if row is None else _to_job(row)


def claim_job(conn: Connection, job_id: UUID) -> JobRecord | None:
    """``queued -> running`` for one id; None when it is not queued (taken)."""
    row = conn.execute(
        text(
            f"UPDATE system.job SET status = '{JobStatus.RUNNING.value}', started_at = now(), "
            "progress = 0, error = NULL, logs = logs || '[\"claimed\"]'::jsonb "
            "WHERE job_id = :job_id AND status = 'queued' "
            f"RETURNING {_SELECT}"
        ),
        {"job_id": job_id},
    ).first()
    return None if row is None else _to_job(row)


def claim_next_job(conn: Connection) -> JobRecord | None:
    """Claim the oldest queued job without locking concurrent workers (SKIP LOCKED)."""
    row = conn.execute(
        text(
            f"UPDATE system.job SET status = '{JobStatus.RUNNING.value}', started_at = now(), "
            "progress = 0, error = NULL, logs = logs || '[\"claimed\"]'::jsonb "
            "WHERE job_id = (SELECT job_id FROM system.job WHERE status = 'queued' "
            "ORDER BY created_at, job_id FOR UPDATE SKIP LOCKED LIMIT 1) "
            f"RETURNING {_SELECT}"
        )
    ).first()
    return None if row is None else _to_job(row)


def recover_stale_jobs(conn: Connection, *, stale_after_seconds: int) -> int:
    """Requeue ``running`` jobs older than the threshold (INTERPRETATION)."""
    result = conn.execute(
        text(
            f"UPDATE system.job SET status = '{JobStatus.QUEUED.value}', started_at = NULL, "
            "error = NULL, logs = logs || '[\"requeued after stale lease\"]'::jsonb "
            "WHERE status = 'running' "
            "AND started_at < now() - CAST(:seconds AS double precision) * interval '1 second'"
        ),
        {"seconds": stale_after_seconds},
    )
    return int(result.rowcount or 0)


def _finish(
    conn: Connection,
    job_id: UUID,
    *,
    status: JobStatus,
    result_ref: str | None,
    error: str | None,
    message: str,
) -> JobRecord:
    row = conn.execute(
        text(
            "UPDATE system.job SET status = :status, "
            "progress = CASE WHEN :success = 1 THEN 1 ELSE progress END, "
            "finished_at = now(), result_ref = :result_ref, error = :error, "
            "logs = logs || CAST(:entry AS jsonb) "
            "WHERE job_id = :job_id "
            f"RETURNING {_SELECT}"
        ),
        {
            "status": JobStatus(status).value,
            "success": 1 if status is JobStatus.SUCCEEDED else 0,
            "result_ref": result_ref,
            "error": error,
            "entry": json.dumps([message]),
            "job_id": job_id,
        },
    ).one()
    return _to_job(row)


def _require(params: dict[str, Any], key: str) -> Any:
    """PD-11: a parameter that is not in the job payload is rejected, never defaulted."""
    if key not in params:
        raise ValidationError("job params are missing a required parameter", {"param": key})
    return params[key]


def _int_param(params: dict[str, Any], key: str) -> int:
    raw = _require(params, key)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError("job param is not an integer", {"param": key, key: raw}) from exc


def scope_from_params(params: dict[str, Any]) -> IngestScope:
    """Build the PD-12 scope an INGEST/RESAMPLE job was queued with (fail closed)."""
    venue_raw = _require(params, "venue")
    market_raw = _require(params, "market_type")
    try:
        market_type = MarketType(str(market_raw))
    except ValueError as exc:
        raise ValidationError(
            "job param is not a known market_type",
            {"param": "market_type", "market_type": market_raw},
        ) from exc

    raw_ids = _require(params, "instrument_ids")
    instrument_ids: list[UUID] = []
    for item in raw_ids if isinstance(raw_ids, (list, tuple)) else [raw_ids]:
        try:
            instrument_ids.append(UUID(str(item)))
        except ValueError as exc:
            raise ValidationError(
                "job param holds a non-uuid instrument_id",
                {"param": "instrument_ids", "instrument_ids": [str(item)]},
            ) from exc

    raw_timeframes = _require(params, "timeframes")
    timeframes: list[Timeframe] = []
    for item in raw_timeframes if isinstance(raw_timeframes, (list, tuple)) else [raw_timeframes]:
        try:
            timeframes.append(parse_timeframe(str(item)))
        except UnknownTimeframeError as exc:
            raise ValidationError(
                "job param holds an unknown timeframe",
                {"param": "timeframes", "timeframes": [str(item)]},
            ) from exc

    return IngestScope(
        venue=str(venue_raw),
        market_type=market_type,
        instrument_ids=tuple(instrument_ids),
        timeframes=tuple(timeframes),
        start_ms=_int_param(params, "start_ms"),
        end_ms=_int_param(params, "end_ms"),
        storage_budget_bytes=_int_param(params, "storage_budget_bytes"),
    )


def repair_gap_id(params: dict[str, Any]) -> UUID:
    """The gap a REPAIR job targets (fail closed on a malformed id)."""
    raw = _require(params, "gap_id")
    try:
        return UUID(str(raw))
    except ValueError as exc:
        raise ValidationError("job param is not a uuid gap_id", {"param": "gap_id"}) from exc


def resolve_job_kind(timeframes: Sequence[str]) -> JobKind:
    """API-side dispatch (design note section 10): INGEST or RESAMPLE, else 422."""
    if not timeframes:
        raise ValidationError("scope names no timeframes", {"timeframes": []})
    derived: list[str] = []
    for raw in timeframes:
        try:
            parsed = parse_timeframe(str(raw))
        except UnknownTimeframeError as exc:
            raise ValidationError("unknown timeframe", {"timeframes": [str(raw)]}) from exc
        if is_derived(parsed):
            derived.append(parsed.value)
    if derived and len(derived) == len(timeframes):
        return JobKind.RESAMPLE
    if derived:
        raise ValidationError(
            "a resample scope may contain only derived timeframes",
            {"timeframes": [str(item) for item in timeframes]},
        )
    return JobKind.INGEST


async def execute_job(
    conn: Connection,
    job: JobRecord,
    *,
    adapter_factory: Callable[[], ExchangeAdapter],
    data_dir: Path,
    now_ms: int,
    ingested_at_ms: int,
) -> JobRecord:
    """Dispatch a claimed job and record its outcome on the job row.

    Success leaves the dispatch transaction open for the caller to commit
    together with the SUCCEEDED row.  ``EngineError`` rolls back only this
    dispatch's writes, records FAILED with the envelope JSON in ``error`` and
    returns without raising; an unexpected exception records FAILED too and
    re-raises (the caller commits the record, then reports the bug).

    The venue adapter is built *inside* this scope from ``adapter_factory``
    (only INGEST/REPAIR need one), so a construction failure - a non-BINANCE
    venue, a market_type the adapter refuses - is recorded on the job row like
    any other ``EngineError`` instead of escaping the runner; an adapter this
    function built is closed again before returning.
    """
    adapter: ExchangeAdapter | None = None
    try:
        if job.kind is JobKind.INGEST:
            adapter = adapter_factory()
            ingest_result = await run_ingest(
                conn,
                scope_from_params(job.params),
                adapter,
                data_dir=data_dir,
                now_ms=now_ms,
                ingested_at_ms=ingested_at_ms,
            )
            return _finish(
                conn,
                job.job_id,
                status=JobStatus.SUCCEEDED,
                result_ref=ingest_result.dataset_version,
                error=None,
                message=f"succeeded dataset_version={ingest_result.dataset_version}",
            )
        if job.kind is JobKind.REPAIR:
            adapter = adapter_factory()
            repair_result = await run_repair(
                conn,
                gap_id=repair_gap_id(job.params),
                adapter=adapter,
                data_dir=data_dir,
                now_ms=now_ms,
                ingested_at_ms=ingested_at_ms,
                storage_budget_bytes=_int_param(job.params, "storage_budget_bytes"),
            )
            reference = (
                None
                if repair_result.dataset_version is None
                else str(repair_result.dataset_version)
            )
            return _finish(
                conn,
                job.job_id,
                status=JobStatus.SUCCEEDED,
                result_ref=reference,
                error=None,
                message=(
                    f"succeeded outcome={repair_result.status} dataset_version={reference or 'none'}"
                ),
            )
        if job.kind is JobKind.RESAMPLE:
            resample_result = run_resample(
                conn,
                scope_from_params(job.params),
                data_dir=data_dir,
                now_ms=now_ms,
                ingested_at_ms=ingested_at_ms,
            )
            return _finish(
                conn,
                job.job_id,
                status=JobStatus.SUCCEEDED,
                result_ref=resample_result.dataset_version,
                error=None,
                message=f"succeeded dataset_version={resample_result.dataset_version}",
            )
        raise RuntimeError(f"unknown job kind {job.kind!r}")
    except EngineError as exc:
        if conn.in_transaction():
            conn.rollback()
        return _finish(
            conn,
            job.job_id,
            status=JobStatus.FAILED,
            result_ref=None,
            error=json.dumps({"code": exc.code, "message": exc.message, "detail": exc.detail}),
            message=f"failed {exc.code}: {exc.message}",
        )
    except Exception as exc:
        if conn.in_transaction():
            conn.rollback()
        _finish(
            conn,
            job.job_id,
            status=JobStatus.FAILED,
            result_ref=None,
            error=json.dumps(
                {
                    "code": "INTERNAL_ERROR",
                    "message": str(exc) or type(exc).__name__,
                    "detail": {"type": type(exc).__name__},
                }
            ),
            message=f"failed INTERNAL_ERROR: {type(exc).__name__}",
        )
        raise
    finally:
        if adapter is not None:
            await adapter.aclose()
