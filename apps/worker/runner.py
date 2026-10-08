"""Job worker: drain ``system.job`` (design note section 5 and 10).

The API enqueues a row and runs it as a background task; this worker claims
the oldest queued row (``claim_next_job``, ``FOR UPDATE SKIP LOCKED``) and
runs it through the very same glue -- ``apps.api.jobs.execute_claimed`` -- so
one job row means one execution path whichever side picks it up.

INTERPRETATIONS (DECISIONS): the spec names no recovery policy, so stale
``running`` rows are requeued only when the operator asks for it with an age
(``--recover-stale SECONDS``); no age is invented here (PD-4).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from apps.api import jobs as job_seams
from apps.api.db import get_engine
from apps.api.settings import get_settings
from engine.jobs import claim_next_job, recover_stale_jobs

log = logging.getLogger("cryptosos.worker")


async def process_one(*, data_dir: Path, clock_ms: int | None = None) -> bool:
    """Claim and run at most one queued job; ``False`` when the queue is empty."""
    with get_engine().connect() as conn:
        job = claim_next_job(conn)
        conn.commit()
        if job is None:
            return False
        current = job_seams.now_ms() if clock_ms is None else clock_ms
        await job_seams.execute_claimed(conn, job, data_dir=data_dir, current_ms=current)
        return True


def requeue_stale(stale_after_seconds: int) -> int:
    """Requeue ``running`` jobs older than the operator's threshold."""
    with get_engine().connect() as conn:
        requeued = recover_stale_jobs(conn, stale_after_seconds=stale_after_seconds)
        conn.commit()
    return requeued


async def run_forever(*, poll_seconds: float, recover_stale_after: int | None = None) -> None:
    """Drain the queue until cancelled; sleep ``poll_seconds`` when it is empty."""
    data_dir = Path(get_settings().data_dir)
    if recover_stale_after is not None:
        count = requeue_stale(recover_stale_after)
        log.info("requeued %d job(s) running longer than %ds", count, recover_stale_after)
    while True:
        if not await process_one(data_dir=data_dir):
            await asyncio.sleep(poll_seconds)
