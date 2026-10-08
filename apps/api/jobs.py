"""Background execution of queued jobs (design note section 5 and 10).

The API enqueues a ``system.job`` row and schedules ``run_job`` as an ASGI
background task; the worker CLI (``apps.worker.runner``) claims jobs instead.
Both paths share this glue so one job row means one execution path: claim
(committed) -> ``execute_job`` (never commits) -> commit the outcome.

INTERPRETATIONS (design note section 5, DECISIONS):
- ``build_adapter`` and ``now_ms`` are module-level seams so tests swap in
  the in-memory fixture venue and the golden time without patching the engine;
- ``run_job`` never raises: a crash in a background task is invisible to the
  HTTP caller, so every failure path is logged and the job row carries the
  outcome (``execute_job`` records the envelope JSON on the row itself);
- only BINANCE SPOT exists in M1, but the venue/market_type are still taken
  from the job params (PD-11), never assumed: anything else is refused by
  ``build_adapter`` with NOT_IMPLEMENTED instead of silently fetching the
  wrong venue (PD-10).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Connection

from apps.api.db import get_engine
from apps.api.settings import get_settings
from engine.data.instruments import MarketType
from engine.errors import NotImplementedMarketError, ValidationError
from engine.jobs import JobRecord, claim_job, execute_job
from exchanges.base import ExchangeAdapter

log = logging.getLogger("cryptosos.jobs")


def now_ms() -> int:
    """Wall clock in epoch milliseconds (tests freeze it to the golden NOW)."""
    return int(time.time() * 1000)


def build_adapter(params: dict[str, Any]) -> ExchangeAdapter:
    """Live venue for the job's market (tests swap this for the fixture venue)."""
    venue = str(params.get("venue", ""))
    if venue != "BINANCE":
        raise NotImplementedMarketError("M1 ingests only the BINANCE venue", {"venue": venue})
    market_raw = str(params.get("market_type", ""))
    try:
        market_type = MarketType(market_raw)
    except ValueError as exc:
        raise ValidationError(
            "job param is not a known market_type", {"market_type": market_raw}
        ) from exc
    from exchanges.binance.adapter import BinanceAdapter

    return BinanceAdapter(market_type=market_type)


async def run_job(job_id: UUID) -> None:
    """Claim ``job_id``, run it, commit the outcome; never raises."""
    try:
        await _run_job(job_id)
    except Exception:
        log.exception("job runner crashed for %s", job_id)


async def execute_claimed(
    conn: Connection, job: JobRecord, *, data_dir: Path, current_ms: int
) -> None:
    """Run an already-claimed job on ``conn`` and commit its outcome.

    The claim itself is committed by the caller first (SKIP LOCKED rows must
    be visible to other workers); ``execute_job`` never commits, so the
    outcome is committed here -- including a failure it recorded on the row.
    The API background task and ``apps.worker`` both go through this one glue.
    """
    try:
        await execute_job(
            conn,
            job,
            adapter_factory=lambda: build_adapter(job.params),
            data_dir=data_dir,
            now_ms=current_ms,
            ingested_at_ms=current_ms,
        )
        conn.commit()
    except Exception:
        # execute_job already recorded FAILED for anything it handled and
        # rolled back its own writes; commit that record, report the bug.
        conn.commit()
        log.exception("job %s failed", job.job_id)


async def _run_job(job_id: UUID) -> None:
    data_dir = Path(get_settings().data_dir)
    with get_engine().connect() as conn:
        job = claim_job(conn, job_id)
        conn.commit()
        if job is None:
            return
        await execute_claimed(conn, job, data_dir=data_dir, current_ms=now_ms())
