"""M6 (T6.1-T6.2) — strategy versions + backtests (spec section 7, 11).

- ``/v1/strategies`` CRUD; versions are append-only (7.1: edits create a new
  version, never an in-place mutation), each content-hashed (PD-6).
- ``POST /v1/backtests`` queues a BACKTEST job (async, spec 11); the job
  runs ``engine.strategy.runner.run_backtest_job`` through the M1 seam.
  Every backtest requires a registered ``experiment_id`` (D-041, PD-12).
- ``GET /v1/backtests/{id}[/trades|/equity|/metrics|/skipped]`` reads.

Fail-closed (PD-4/PD-10/PD-12): unknown experiment/strategy-version 404,
unknown dataset_version 422, unknown fill_model 422, SHORT_ONLY/BOTH 422
until M10 (R11, D-043), RANDOM_BASELINE 422 until M7 (7.6).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, text

from apps.api.db import connection_scope
from apps.api.jobs import run_job
from engine.errors import NotFoundError, ValidationError
from engine.jobs import JobKind, enqueue_job
from engine.strategy.hash import canonical_hash
from engine.strategy.models import Side, StrategyDefinition

router = APIRouter(prefix="/v1", tags=["strategy"])

#: 7.3 fill models (R8: REALISTIC_INTRABAR reserved, NOT_IMPLEMENTED).
FILL_MODELS = ("PARITY", "REALISTIC_OHLC")

#: Backtest status vocabulary (migration 0006 CHECK).
BACKTEST_STATUSES = ("queued", "running", "succeeded", "failed")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrategyCreate(Model):
    """``POST /v1/strategies`` body: the 7.1 definition JSON."""

    strategy_id: str
    version: str
    definition: dict[str, Any]


class StrategyView(Model):
    strategy_id: str
    version: str
    definition: dict[str, Any]
    definition_hash: str


class BacktestCreate(Model):
    """``POST /v1/backtests`` body (spec 11: async; experiment_id required)."""

    experiment_id: UUID
    strategy_id: str
    version: str
    universe: list[UUID]
    timeframe: str
    fill_model: str = "REALISTIC_OHLC"
    data_scope: str = "DEVELOPMENT"
    dataset_version: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    seed: int | None = None
    # T6.3 portfolio mode (spec 7.4): shared equity with caps. When false
    # (default) each instrument runs independently (R9 single-symbol mode).
    portfolio: bool = False
    max_concurrent_positions: int | None = None
    max_portfolio_heat: float | None = None
    per_asset_cap_pct: float = 25.0


class BacktestView(Model):
    backtest_id: UUID
    experiment_id: UUID
    strategy_id: str
    version: str
    universe: list[str]
    data_scope: str
    fill_model: str
    dataset_version: str
    dataset_schema_version: str
    engine_version: str
    code_version: str
    seed: int | None
    status: str
    supersedes: UUID | None
    locked: bool
    created_at: str


class TradeView(Model):
    backtest_id: UUID
    trade_no: int
    instrument_id: str
    side: str
    signal_time: str
    decision_time: str
    fill_time: str
    exit_signal_time: str | None
    exit_decision_time: str | None
    exit_fill_time: str | None
    entry_price: float
    exit_price: float | None
    qty: float
    fees: float
    pnl: float | None
    pnl_pct: float | None
    r_multiple: float | None
    mfe_r: float | None
    mae_r: float | None
    bars_held: int | None
    entry_tag: str
    exit_reason: str | None
    data_gap_during_hold: bool
    straddles_boundary: bool


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat().replace("+00:00", "Z")


def _require_text(value: str, field: str) -> str:
    if not value.strip():
        raise ValidationError(f"{field} must be a non-empty string", {field: value})
    return value


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"invalid ISO-8601 timestamp: {value}") from exc


# --------------------------------------------------------------------------
# Strategies
# --------------------------------------------------------------------------


@router.post("/strategies", response_model=StrategyView, status_code=201, tags=["strategy"])
async def create_strategy(body: StrategyCreate) -> StrategyView:
    """Create a strategy version (7.1: edits create a new version)."""
    strategy_id = _require_text(body.strategy_id, "strategy_id")
    version = _require_text(body.version, "version")
    try:
        StrategyDefinition.model_validate(body.definition)
    except ValueError as exc:
        raise ValidationError("strategy definition is invalid", {"error": str(exc)}) from exc
    definition_hash = canonical_hash(body.definition)
    with connection_scope() as conn:
        existing = conn.execute(
            text(
                "SELECT strategy_id FROM strategy.strategy_version"
                " WHERE strategy_id = :s AND version = :v"
            ),
            {"s": strategy_id, "v": version},
        ).first()
        if existing is not None:
            raise ValidationError(
                "strategy version already exists; edits create a NEW version (7.1)",
                {"strategy_id": strategy_id, "version": version},
            )
        conn.execute(
            text(
                "INSERT INTO strategy.strategy_version (strategy_id, version, definition,"
                " definition_hash) VALUES (:s, :v, CAST(:d AS jsonb), :h)"
            ),
            {
                "s": strategy_id,
                "v": version,
                "d": json.dumps(body.definition, sort_keys=True),
                "h": definition_hash,
            },
        )
        return StrategyView(
            strategy_id=strategy_id,
            version=version,
            definition=body.definition,
            definition_hash=definition_hash,
        )


@router.get("/strategies", response_model=list[StrategyView], tags=["strategy"])
async def list_strategies() -> list[StrategyView]:
    with connection_scope() as conn:
        rows = conn.execute(
            text("SELECT * FROM strategy.strategy_version ORDER BY strategy_id, version")
        ).all()
        return [_strategy_view(row) for row in rows]


@router.get(
    "/strategies/{strategy_id}/{version}", response_model=StrategyView, tags=["strategy"]
)
async def get_strategy(strategy_id: str, version: str) -> StrategyView:
    with connection_scope() as conn:
        row = conn.execute(
            text(
                "SELECT * FROM strategy.strategy_version"
                " WHERE strategy_id = :s AND version = :v"
            ),
            {"s": strategy_id, "v": version},
        ).first()
    if row is None:
        raise NotFoundError("strategy version not found", {"strategy_id": strategy_id,
                                                           "version": version})
    return _strategy_view(row)


def _strategy_view(row: Any) -> StrategyView:
    definition = row.definition
    if isinstance(definition, str):
        definition = json.loads(definition)
    return StrategyView(
        strategy_id=row.strategy_id,
        version=row.version,
        definition=definition,
        definition_hash=row.definition_hash,
    )


# --------------------------------------------------------------------------
# Backtests
# --------------------------------------------------------------------------


@router.post("/backtests", response_model=BacktestView, status_code=202, tags=["strategy"])
async def create_backtest(body: BacktestCreate, background: BackgroundTasks) -> BacktestView:
    """Queue an async backtest (spec 11). Every backtest belongs to an
    experiment (D-041)."""
    if body.fill_model not in FILL_MODELS:
        raise ValidationError(
            "unknown fill_model",
            {"fill_model": body.fill_model, "allowed": list(FILL_MODELS)},
        )
    if body.data_scope not in {"DEVELOPMENT", "HOLDOUT"}:
        raise ValidationError(
            "data_scope must be DEVELOPMENT or HOLDOUT", {"data_scope": body.data_scope}
        )
    if not body.universe:
        raise ValidationError("universe is required and must not be empty (PD-12)",
                              {"universe": []})
    universe = [str(i) for i in dict.fromkeys(str(i) for i in body.universe)]
    period_start = _parse_iso(body.period_start)
    period_end = _parse_iso(body.period_end)

    with connection_scope() as conn:
        strategy = conn.execute(
            text(
                "SELECT definition FROM strategy.strategy_version"
                " WHERE strategy_id = :s AND version = :v"
            ),
            {"s": body.strategy_id, "v": body.version},
        ).first()
        if strategy is None:
            raise NotFoundError(
                "strategy version not found",
                {"strategy_id": body.strategy_id, "version": body.version},
            )
        definition = StrategyDefinition.model_validate(strategy.definition)
        if definition.entry.side in (Side.SHORT_ONLY, Side.BOTH):
            raise ValidationError(
                "short-side strategies are disabled until M10 (R11)",
                {"side": definition.entry.side.value},
            )
        if definition.entry.mode != "SIGNAL":
            raise ValidationError(
                "only entry.mode=SIGNAL is implemented; RANDOM_BASELINE lands with M7",
                {"mode": definition.entry.mode},
            )
        experiment = conn.execute(
            text(
                "SELECT experiment_id, dataset_version FROM research.experiment"
                " WHERE experiment_id = :id"
            ),
            {"id": body.experiment_id},
        ).first()
        if experiment is None:
            raise NotFoundError(
                "experiment not found", {"experiment_id": str(body.experiment_id)}
            )
        dataset_version = body.dataset_version or experiment.dataset_version
        manifest = conn.execute(
            text(
                "SELECT dataset_version FROM market.dataset_manifest"
                " WHERE dataset_version = :dv"
            ),
            {"dv": dataset_version},
        ).first()
        if manifest is None:
            raise ValidationError(
                "dataset_version is not a registered manifest",
                {"dataset_version": dataset_version},
            )

        backtest_id = uuid.uuid4()
        params = {
            "experiment_id": str(body.experiment_id),
            "strategy_id": body.strategy_id,
            "version": body.version,
            "universe": universe,
            "timeframe": body.timeframe,
            "fill_model": body.fill_model,
            "data_scope": body.data_scope,
            "dataset_version": dataset_version,
            "period_start": _iso(period_start),
            "period_end": _iso(period_end),
            "seed": body.seed,
            "portfolio": body.portfolio,
            "max_concurrent_positions": body.max_concurrent_positions,
            "max_portfolio_heat": body.max_portfolio_heat,
            "per_asset_cap_pct": body.per_asset_cap_pct,
            "backtest_id": str(backtest_id),
        }
        job = enqueue_job(conn, kind=JobKind.BACKTEST, params=params)
        # tstzrange(:start, :end, '[]') accepts NULL bounds natively (open range).
        row = conn.execute(
            text(
                "INSERT INTO strategy.backtest (backtest_id, experiment_id, strategy_id, version,"
                " universe, period, data_scope, fill_model, dataset_version,"
                " dataset_schema_version, engine_version, code_version, resolved_params, seed,"
                " status)"
                " VALUES (:id, :exp, :s, :v, CAST(:u AS jsonb), tstzrange(:start, :end, '[]'),"
                " :scope,"
                " :fill, :dv, '1', '0.1.0', :cv, CAST(:rp AS jsonb), :seed, 'queued')"
                " RETURNING *"
            ),
            {
                "id": backtest_id,
                "exp": body.experiment_id,
                "s": body.strategy_id,
                "v": body.version,
                "u": json.dumps(universe),
                "start": _iso(period_start),
                "end": _iso(period_end),
                "scope": body.data_scope,
                "fill": body.fill_model,
                "dv": dataset_version,
                "cv": "unknown",
                "rp": json.dumps(definition.model_dump(by_alias=True)),
                "seed": body.seed,
            },
        ).first()
        assert row is not None
        background.add_task(run_job, job.job_id)
        return _backtest_view(row)


@router.get("/backtests", response_model=list[BacktestView], tags=["strategy"])
async def list_backtests() -> list[BacktestView]:
    with connection_scope() as conn:
        rows = conn.execute(
            text("SELECT * FROM strategy.backtest ORDER BY created_at DESC, backtest_id ASC")
        ).all()
        return [_backtest_view(row) for row in rows]


@router.get("/backtests/{backtest_id}", response_model=BacktestView, tags=["strategy"])
async def get_backtest(backtest_id: UUID) -> BacktestView:
    with connection_scope() as conn:
        row = conn.execute(
            text("SELECT * FROM strategy.backtest WHERE backtest_id = :id"), {"id": backtest_id}
        ).first()
    if row is None:
        raise NotFoundError("backtest not found", {"backtest_id": str(backtest_id)})
    return _backtest_view(row)


@router.get("/backtests/{backtest_id}/trades", response_model=list[TradeView], tags=["strategy"])
async def get_backtest_trades(backtest_id: UUID) -> list[TradeView]:
    """7.5 trade records (spec 11 ``GET /v1/backtests/{id}/trades``)."""
    with connection_scope() as conn:
        _require_backtest(conn, backtest_id)
        rows = conn.execute(
            text("SELECT * FROM strategy.trade WHERE backtest_id = :id ORDER BY trade_no"),
            {"id": backtest_id},
        ).all()
        return [_trade_view(row) for row in rows]


@router.get("/backtests/{backtest_id}/metrics", tags=["strategy"])
async def get_backtest_metrics(backtest_id: UUID) -> dict[str, Any]:
    with connection_scope() as conn:
        row = _require_backtest(conn, backtest_id)
        return dict(row.metrics or {})


@router.get("/backtests/{backtest_id}/skipped", tags=["strategy"])
async def get_backtest_skipped(backtest_id: UUID) -> list[dict[str, Any]]:
    """7.3.1: every skipped/not-filled signal with its reason (M6 AC)."""
    with connection_scope() as conn:
        row = _require_backtest(conn, backtest_id)
        return list(row.skipped or [])


@router.get("/backtests/{backtest_id}/equity", tags=["strategy"])
async def get_backtest_equity(backtest_id: UUID) -> list[dict[str, Any]]:
    """Per-bar equity curve (stored inside metrics; the Parquet artifact is
    M7 with the validation suite)."""
    with connection_scope() as conn:
        row = _require_backtest(conn, backtest_id)
        return list((row.metrics or {}).get("equity_curve") or [])


def _require_backtest(conn: Connection, backtest_id: UUID) -> Any:
    row = conn.execute(
        text("SELECT * FROM strategy.backtest WHERE backtest_id = :id"), {"id": backtest_id}
    ).first()
    if row is None:
        raise NotFoundError("backtest not found", {"backtest_id": str(backtest_id)})
    return row


def _backtest_view(row: Any) -> BacktestView:
    universe = row.universe
    if isinstance(universe, str):
        universe = json.loads(universe)
    return BacktestView(
        backtest_id=row.backtest_id,
        experiment_id=row.experiment_id,
        strategy_id=row.strategy_id,
        version=row.version,
        universe=[str(u) for u in universe],
        data_scope=row.data_scope,
        fill_model=row.fill_model,
        dataset_version=row.dataset_version,
        dataset_schema_version=row.dataset_schema_version,
        engine_version=row.engine_version,
        code_version=row.code_version,
        seed=row.seed,
        status=row.status,
        supersedes=row.supersedes,
        locked=bool(row.locked),
        created_at=row.created_at.isoformat().replace("+00:00", "Z"),
    )


def _trade_view(row: Any) -> TradeView:
    return TradeView(
        backtest_id=row.backtest_id,
        trade_no=row.trade_no,
        instrument_id=row.instrument_id,
        side=row.side,
        signal_time=row.signal_time.isoformat().replace("+00:00", "Z"),
        decision_time=row.decision_time.isoformat().replace("+00:00", "Z"),
        fill_time=row.fill_time.isoformat().replace("+00:00", "Z"),
        exit_signal_time=_iso(row.exit_signal_time),
        exit_decision_time=_iso(row.exit_decision_time),
        exit_fill_time=_iso(row.exit_fill_time),
        entry_price=float(row.entry_price),
        exit_price=None if row.exit_price is None else float(row.exit_price),
        qty=float(row.qty),
        fees=float(row.fees),
        pnl=None if row.pnl is None else float(row.pnl),
        pnl_pct=None if row.pnl_pct is None else float(row.pnl_pct),
        r_multiple=None if row.r_multiple is None else float(row.r_multiple),
        mfe_r=None if row.mfe_r is None else float(row.mfe_r),
        mae_r=None if row.mae_r is None else float(row.mae_r),
        bars_held=row.bars_held,
        entry_tag=row.entry_tag,
        exit_reason=row.exit_reason,
        data_gap_during_hold=bool(row.data_gap_during_hold),
        straddles_boundary=bool(row.straddles_boundary),
    )
