"""M7 (T7.1) — validation runs API (spec section 9, 11).

- ``POST /v1/validation`` (async; requires backtest_id + kinds[]) runs the
  three levels for a stored backtest and persists one ``validation.run``
  row per kind with the 9.5 provenance.
- ``GET /v1/validation/{id}`` reads one run.
- ``POST /v1/baselines/random`` (async; requires backtest_id + seeds) runs
  the 9.2 random-entry baseline (seeds 1..N) and stores the distributions.

Fail-closed (PD-4/PD-10/PD-12): unknown backtest 404, unknown kind 422,
Level 2/3 require DEVELOPMENT scope (9.3).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from apps.api.db import connection_scope
from apps.api.jobs import run_job
from engine.errors import NotFoundError, ValidationError
from engine.jobs import JobKind, enqueue_job

router = APIRouter(prefix="/v1", tags=["validation"])

#: 9.3 kinds (mirrored by tests/test_validation_levels.py).
VALIDATION_KINDS = (
    "edge", "oos", "walk_forward", "clustering_sensitivity", "multiple_testing",
    "random_baseline", "param_stability", "trade_dependency", "concentration",
    "regime_split", "cost_stress", "delay_stress", "gap_risk", "monte_carlo",
    "liquidity_funding",
)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValidationCreate(Model):
    backtest_id: UUID
    kinds: list[str]


class BaselineCreate(Model):
    backtest_id: UUID
    seeds: int = 1000


class RunView(Model):
    run_id: UUID
    backtest_id: UUID
    experiment_id: UUID | None
    level: int
    kind: str
    status: str
    result: dict[str, Any]
    seed: int | None
    locked: bool
    created_at: str


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat().replace("+00:00", "Z")


@router.post("/validation", response_model=list[RunView], status_code=202, tags=["validation"])
async def create_validation(body: ValidationCreate, background: BackgroundTasks) -> list[RunView]:
    """Queue a validation run (spec 11: async)."""
    unknown = [kind for kind in body.kinds if kind not in VALIDATION_KINDS]
    if unknown:
        raise ValidationError("unknown validation kind",
                              {"unknown": unknown, "allowed": list(VALIDATION_KINDS)})
    with connection_scope() as conn:
        backtest = conn.execute(
            text("SELECT backtest_id, experiment_id, data_scope FROM strategy.backtest"
                 " WHERE backtest_id = :id"),
            {"id": body.backtest_id},
        ).first()
        if backtest is None:
            raise NotFoundError("backtest not found", {"backtest_id": str(body.backtest_id)})
        if backtest.data_scope != "DEVELOPMENT":
            raise ValidationError(
                "Level 2/3 validation runs in DEVELOPMENT scope only (9.3)",
                {"data_scope": backtest.data_scope},
            )
        run_ids: list[str] = []
        for kind in body.kinds:
            run_id = uuid.uuid4()
            run_ids.append(str(run_id))
            conn.execute(
                text(
                    "INSERT INTO validation.run (run_id, experiment_id, backtest_id, level,"
                    " kind, status) VALUES (:id, :exp, :bid, :level, :kind, 'queued')"
                ),
                {
                    "id": run_id,
                    "exp": backtest.experiment_id,
                    "bid": backtest.backtest_id,
                    "level": _level_for(kind),
                    "kind": kind,
                },
            )
        job = enqueue_job(
            conn,
            kind=JobKind.VALIDATION,
            params={"run_ids": run_ids, "backtest_id": str(body.backtest_id),
                    "kinds": body.kinds},
        )
        rows = conn.execute(
            text("SELECT * FROM validation.run WHERE run_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": run_ids},
        ).all()
        background.add_task(run_job, job.job_id)
        return [_run_view(row) for row in rows]


@router.get("/validation/{run_id}", response_model=RunView, tags=["validation"])
async def get_validation(run_id: UUID) -> RunView:
    with connection_scope() as conn:
        row = conn.execute(
            text("SELECT * FROM validation.run WHERE run_id = :id"), {"id": run_id}
        ).first()
    if row is None:
        raise NotFoundError("validation run not found", {"run_id": str(run_id)})
    return _run_view(row)


@router.post("/baselines/random", response_model=RunView, status_code=202, tags=["validation"])
async def create_random_baseline(body: BaselineCreate, background: BackgroundTasks) -> RunView:
    """Queue the 9.2 random-entry baseline (seeds 1..N)."""
    if body.seeds <= 0 or body.seeds > 100_000:
        raise ValidationError("seeds must be between 1 and 100000", {"seeds": body.seeds})
    with connection_scope() as conn:
        backtest = conn.execute(
            text("SELECT backtest_id, experiment_id FROM strategy.backtest"
                 " WHERE backtest_id = :id"),
            {"id": body.backtest_id},
        ).first()
        if backtest is None:
            raise NotFoundError("backtest not found", {"backtest_id": str(body.backtest_id)})
        run_id = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO validation.run (run_id, experiment_id, backtest_id, level, kind,"
                " params, status) VALUES (:id, :exp, :bid, 2, 'random_baseline',"
                " CAST(:params AS jsonb), 'queued')"
            ),
            {
                "id": run_id,
                "exp": backtest.experiment_id,
                "bid": backtest.backtest_id,
                "params": json.dumps({"seeds": body.seeds}),
            },
        )
        job = enqueue_job(
            conn,
            kind=JobKind.BASELINE,
            params={"run_id": str(run_id), "backtest_id": str(body.backtest_id),
                    "seeds": body.seeds},
        )
        row = conn.execute(
            text("SELECT * FROM validation.run WHERE run_id = :id"), {"id": run_id}
        ).first()
        assert row is not None
        background.add_task(run_job, job.job_id)
        return _run_view(row)


def _level_for(kind: str) -> int:
    if kind in {"edge", "oos", "walk_forward", "clustering_sensitivity",
                "multiple_testing", "random_baseline", "param_stability",
                "trade_dependency", "concentration", "regime_split"}:
        return 2
    if kind in {"cost_stress", "delay_stress", "gap_risk", "monte_carlo",
                "liquidity_funding"}:
        return 3
    return 1


def _run_view(row: Any) -> RunView:
    result = row.result
    if isinstance(result, str):
        result = json.loads(result)
    return RunView(
        run_id=row.run_id,
        backtest_id=row.backtest_id,
        experiment_id=row.experiment_id,
        level=row.level,
        kind=row.kind,
        status=row.status,
        result=result,
        seed=row.seed,
        locked=bool(row.locked),
        created_at=row.created_at.isoformat().replace("+00:00", "Z"),
    )
