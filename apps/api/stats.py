"""M5 (T5.2d) - ``POST /v1/stats`` and ``GET /v1/stats/{ref_id}`` (spec 8.2, 11).

Flow: validate -> load scope boundaries (fail-closed) -> resolve detector
params (PD-11) -> compute events per instrument over the clipped window ->
persist events/outcomes idempotently (INSERT-only tables, ``ON CONFLICT DO
NOTHING``) -> embargo/purge/segment -> pure ``compute_stats`` -> insert one
``stats_result`` row -> return the view. Reads only, no order surface (PD-1).

Fail-closed resolution (PD-10/PD-11): missing instrument_ids 422, unknown
detector/version 422, SPEC_REQUIRED 422, undocumented params 422, unknown
event_type/direction/timeframe 422, CUSTOM cluster model 422, instrument
outside the scope family universe 422, mixed dataset versions 422, unknown
instrument 404, perpetual 501, HOLDOUT without an opened hypothesis 409,
second HOLDOUT run 409. Windows wider than the served bar budget are 422,
never silently truncated.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import Connection, text

from apps.api.db import connection_scope
from apps.api.experiments import create_singleton_family, record_variant
from apps.api.market import _identity, _load_window, _parse_params
from engine.config.defaults import ParamError, load_defaults
from engine.config.validation import load_evidence_grade
from engine.data.bars import BAR_MS, UnknownTimeframeError, parse_timeframe
from engine.data.instruments import Instrument
from engine.errors import HoldoutLockedError, NotFoundError, ValidationError
from engine.events.base import Event, params_hash
from engine.events.outcomes import Outcome, compute_outcomes
from engine.events.registry import resolve_detector
from engine.scopes import (
    DEVELOPMENT,
    HOLDOUT,
    SCOPES,
    ScopeBoundaries,
    clip_window,
    embargo_purge,
    load_boundaries,
)
from engine.stats.clusters import CLUSTER_MODELS, CUSTOM_M5_MESSAGE
from engine.stats.compute import Observation, compute_stats
from engine.stats.persist import persist_events
from engine.versions import DATASET_SCHEMA_VERSION, ENGINE_VERSION, code_version

router = APIRouter(prefix="/v1", tags=["stats"])

EVENT_TYPES = ("CONT", "CAPIT", "FAIL", "TOP")
DIRECTIONS = ("LONG", "SHORT")


# --------------------------------------------------------------------------
# Request / response models (spec section 11)
# --------------------------------------------------------------------------


class StatsFilter(BaseModel):
    """Event selection for the run. ``timeframe`` is required: every analysis
    in 8.1 uses one instrument timeframe (gap math needs ``bar_ms``)."""

    detector: str = "RVOL_CLIMAX"
    version: str | None = None
    event_type: str | None = None
    direction: str | None = None
    timeframe: str
    params: str | None = None
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None


class StatsRequest(BaseModel):
    """``POST /v1/stats`` body (spec line 516 + hypothesis_id for the HOLDOUT gate)."""

    filter: StatsFilter
    instrument_ids: list[UUID]
    data_scope: str = DEVELOPMENT
    cluster_models: list[str] | None = None
    family_id: str | None = None
    bootstrap_seed: int | None = None
    hypothesis_id: UUID | None = None


class ScopeView(BaseModel):
    """Requested vs applied window (spec 8.1: clipped requests are flagged, never silent)."""

    requested_from: datetime | None
    requested_to: datetime | None
    applied_from: datetime
    applied_to: datetime
    clipped: bool
    flag: str | None


class StatsResultView(BaseModel):
    """``POST /v1/stats`` / ``GET /v1/stats/{ref_id}`` body: the stored row."""

    ref_id: UUID
    filter: dict[str, Any]
    data_scope: str
    boundaries: dict[str, str]
    dataset_version: str
    dataset_schema_version: str
    engine_version: str
    code_version: str
    resolved_params: dict[str, Any]
    seed: int | None
    result: dict[str, Any]
    grade: str | None
    n_eff: int
    model_sensitive: bool | None
    scope: ScopeView
    created_at: datetime


# --------------------------------------------------------------------------
# Pure parameter resolution (PD-11; unit-testable without a database)
# --------------------------------------------------------------------------


def resolve_stats_params(
    *,
    detector_id: str,
    cluster_models: Sequence[str] | None,
    bootstrap_seed: int | None,
    n_instruments: int,
    defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolved stats parameters: request values win, config fills the rest.

    - ``horizons``: the frozen spec list (no request override in M5).
    - ``bootstrap_resamples`` / ``bootstrap_seed``: config, seed overridable.
    - ``embargo_bars``: derived, default = H = max horizon in view (8.1 step 2).
    - ``market_event_k``: derived, max(3, ceil(10% of universe)) (8.2; a
      fractional-symbol threshold rounds up to whole symbols).
    - ``cluster_models``: request, else the detector's documented default
      ASSUMPTION from config (8.2); CUSTOM fails fast here with the same
      message ``compute_stats`` would raise after the detector work.
    """
    doc = defaults if defaults is not None else load_defaults()
    stats = doc.get("stats") if isinstance(doc, Mapping) else None
    if not isinstance(stats, Mapping):
        raise ValidationError(
            "stats defaults are not configured in config/defaults.yaml",
            {"section": "stats"},
        )
    for key in ("horizons", "bootstrap_resamples", "bootstrap_seed", "cluster_models_by_detector"):
        if key not in stats:
            raise ValidationError(
                "stats defaults are incomplete in config/defaults.yaml",
                {"missing": key},
            )
    horizons = stats["horizons"]
    if (
        not isinstance(horizons, list)
        or not horizons
        or any(type(h) is not int or h < 1 for h in horizons)
    ):
        raise ValidationError(
            "stats.horizons must be a non-empty list of positive integers",
            {"horizons": horizons},
        )
    resamples = stats["bootstrap_resamples"]
    if type(resamples) is not int or resamples < 1:
        raise ValidationError(
            "stats.bootstrap_resamples must be a positive integer",
            {"bootstrap_resamples": resamples},
        )
    seed_default = stats["bootstrap_seed"]
    if type(seed_default) is not int:
        raise ValidationError(
            "stats.bootstrap_seed must be an integer", {"bootstrap_seed": seed_default}
        )
    seed = bootstrap_seed if bootstrap_seed is not None else seed_default
    if type(seed) is not int:
        raise ValidationError("bootstrap_seed must be an integer", {"bootstrap_seed": seed})
    if n_instruments < 1:
        raise ValidationError("at least one instrument is required", {})
    if cluster_models is not None:
        models = list(cluster_models)
        unknown = [m for m in models if m not in CLUSTER_MODELS]
        if unknown:
            raise ValidationError(
                "unknown cluster model",
                {"unknown": unknown, "allowed": list(CLUSTER_MODELS)},
            )
        if "CUSTOM" in models:
            raise ValidationError(CUSTOM_M5_MESSAGE, {"cluster_models": models})
    else:
        by_detector = stats["cluster_models_by_detector"]
        if not isinstance(by_detector, Mapping) or detector_id not in by_detector:
            raise ValidationError(
                "no default cluster_models documented for this detector",
                {"detector": detector_id},
            )
        configured = by_detector[detector_id]
        if not isinstance(configured, list) or not configured:
            raise ValidationError(
                "default cluster_models for this detector is not a non-empty list",
                {"detector": detector_id, "cluster_models": configured},
            )
        models = list(configured)
    horizon_h = max(horizons)
    return {
        "horizons": sorted(set(horizons)),
        "bootstrap_resamples": resamples,
        "bootstrap_seed": seed,
        "embargo_bars": horizon_h,
        "market_event_k": max(3, math.ceil(0.1 * n_instruments)),
        "cluster_models": models,
    }


# --------------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------------


def _resolve_event_type(raw: str | None) -> str | None:
    if raw is None:
        return None
    if raw not in EVENT_TYPES:
        raise ValidationError(
            "unknown event_type", {"event_type": raw, "allowed": sorted(EVENT_TYPES)}
        )
    return raw


def _resolve_direction(raw: str | None) -> str | None:
    if raw is None:
        return None
    if raw not in DIRECTIONS:
        raise ValidationError(
            "unknown direction", {"direction": raw, "allowed": sorted(DIRECTIONS)}
        )
    return raw


def _resolve_scope(raw: str) -> str:
    if raw not in SCOPES:
        raise ValidationError(
            f"data_scope must be one of {list(SCOPES)}", {"data_scope": raw}
        )
    return raw


def _norm(dt: datetime | None) -> datetime | None:
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


def _ms(dt: datetime | None) -> int | None:
    normalized = _norm(dt)
    return None if normalized is None else int(normalized.timestamp() * 1000)


def _scope_view(
    requested_from_ms: int | None,
    requested_to_ms: int | None,
    *,
    boundaries: ScopeBoundaries,
    data_scope: str,
) -> ScopeView:
    """Requested vs applied window; clipping is flagged, never silent (8.1)."""
    if data_scope == DEVELOPMENT:
        scope_low, scope_high = boundaries.s_start, boundaries.holdout_start
    else:
        scope_low, scope_high = boundaries.holdout_start, boundaries.s_end
    start_ms = requested_from_ms if requested_from_ms is not None else scope_low
    end_ms = requested_to_ms if requested_to_ms is not None else scope_high
    clipped_window = clip_window(start_ms, end_ms, boundaries=boundaries, scope=data_scope)
    return ScopeView(
        requested_from=(
            datetime.fromtimestamp(requested_from_ms / 1000, tz=UTC)
            if requested_from_ms is not None
            else None
        ),
        requested_to=(
            datetime.fromtimestamp(requested_to_ms / 1000, tz=UTC)
            if requested_to_ms is not None
            else None
        ),
        applied_from=datetime.fromtimestamp(clipped_window.applied_start / 1000, tz=UTC),
        applied_to=datetime.fromtimestamp(clipped_window.applied_end / 1000, tz=UTC),
        clipped=clipped_window.clipped,
        flag=clipped_window.flag,
    )


def _row_to_view(row: Any, scope: ScopeView) -> StatsResultView:
    result = dict(row.result)
    return StatsResultView(
        ref_id=row.ref_id,
        filter=dict(row.filter),
        data_scope=row.data_scope,
        boundaries={
            "oos_start": result["boundaries"]["oos_start"],
            "holdout_start": result["boundaries"]["holdout_start"],
        },
        dataset_version=row.dataset_version,
        dataset_schema_version=row.dataset_schema_version,
        engine_version=row.engine_version,
        code_version=row.code_version,
        resolved_params=dict(row.resolved_params),
        seed=row.seed,
        result=result,
        grade=result["grade"],
        n_eff=result["n_eff"],
        model_sensitive=result["model_sensitive"],
        scope=scope,
        created_at=row.created_at,
    )


@router.post("/stats", response_model=StatsResultView, tags=["stats"])
async def post_stats(body: StatsRequest) -> StatsResultView:
    """Run an event study and store the StatsResult (spec 8.2, 11)."""
    filt = body.filter
    data_scope = _resolve_scope(body.data_scope)
    resolved_type = _resolve_event_type(filt.event_type)
    resolved_direction = _resolve_direction(filt.direction)
    if not body.instrument_ids:
        raise ValidationError(
            "instrument_ids is required and must not be empty (PD-12)",
            {"instrument_ids": body.instrument_ids},
        )
    detector = resolve_detector(filt.detector, filt.version)
    try:
        detector_params = detector.resolve(_parse_params(filt.params))
    except ParamError as exc:
        raise ValidationError(str(exc), detail=getattr(exc, "detail", {})) from exc
    stats_params = resolve_stats_params(
        detector_id=detector.detector_id,
        cluster_models=body.cluster_models,
        bootstrap_seed=body.bootstrap_seed,
        n_instruments=len(body.instrument_ids),
    )
    try:
        timeframe_enum = parse_timeframe(filt.timeframe)
    except UnknownTimeframeError as exc:
        raise ValidationError("unknown timeframe", {"timeframe": filt.timeframe}) from exc
    timeframe = timeframe_enum.value
    bar_ms = BAR_MS[timeframe_enum]

    boundaries = load_boundaries()
    scope_view = _scope_view(_ms(filt.from_), _ms(filt.to), boundaries=boundaries, data_scope=data_scope)
    applied_start = int(scope_view.applied_from.timestamp() * 1000)
    applied_end = int(scope_view.applied_to.timestamp() * 1000)

    with connection_scope() as conn:
        # Ad-hoc runs never bypass recording (spec 8.4): a family_id the request
        # names must exist; otherwise a singleton family id is minted here and
        # written only once the run succeeds (a failed request leaves no rows).
        # The stored result embeds the REQUEST's family identity only, so two
        # identical requests stay byte-identical (the minted singleton id is an
        # audit row that points at the result, never the other way round).
        if body.family_id is not None:
            family_row = conn.execute(
                text(
                    "SELECT family_id, variant_count FROM research.experiment_family"
                    " WHERE family_id = :id"
                ),
                {"id": body.family_id},
            ).first()
            if family_row is None:
                raise NotFoundError("family not found", {"family_id": str(body.family_id)})
            result_family_id: str | None = str(body.family_id)
            family_id = uuid.UUID(str(body.family_id))
            variant_count = int(family_row.variant_count) + 1
        else:
            result_family_id = None
            variant_count = 1
            family_id = uuid.uuid4()
        if data_scope == HOLDOUT:
            _gate_holdout(conn, body.hypothesis_id)
        instruments: list[Instrument] = []
        for instrument_id in body.instrument_ids:
            instrument, _ = _identity(conn, instrument_id, timeframe)
            ref = f"{instrument.venue}:{instrument.native_symbol}"
            if ref not in boundaries.universe:
                raise ValidationError(
                    "instrument is outside the scope family universe",
                    {"instrument_id": str(instrument_id), "ref": ref, "family": boundaries.family},
                )
            instruments.append(instrument)

        max_bars = int(load_defaults()["candles"]["max_limit"])
        all_events: list[Event] = []
        all_outcomes: dict[str, Outcome] = {}
        dataset_versions: set[str] = set()
        for instrument in instruments:
            window = _load_window(
                conn,
                instrument=instrument,
                timeframe=timeframe,
                from_ms=applied_start,
                to_ms=applied_end,
                as_of_ms=None,
                limit=max_bars + 1,
            )
            if window.frame.height > max_bars:
                raise ValidationError(
                    "window holds more bars than the served budget; narrow from/to",
                    {
                        "instrument_id": str(instrument.instrument_id),
                        "max_bars": max_bars,
                    },
                )
            dataset_versions.add(window.dataset_version)
            exclusion_pairs = [
                (gap.exclusion_start, gap.exclusion_end) for gap in window.exclusions
            ]
            detected = detector.detect(
                window.frame,
                detector_params,
                exclusion_pairs,
                instrument_id=str(instrument.instrument_id),
                dataset_version=window.dataset_version,
                # Same detector inputs as GET /v1/events so persisted events match it.
                data_quality={
                    "state": window.quality.state.value,
                    "exclusion_intervals": [
                        {
                            "gap_id": str(gap.gap_id),
                            "start": gap.exclusion_start.isoformat(),
                            "end": gap.exclusion_end.isoformat(),
                            "kind": gap.kind,
                        }
                        for gap in window.exclusions
                    ],
                },
                timeframe=timeframe,
            )
            if resolved_type is not None:
                detected = [e for e in detected if e.event_type == resolved_type]
            if resolved_direction is not None:
                detected = [e for e in detected if e.direction == resolved_direction]
            detected.sort(key=lambda e: (e.event_index, e.event_type))
            outcomes = {
                o.event_id: o
                for o in compute_outcomes(detected, window.frame, stats_params["horizons"])
            }
            persist_events(conn, detected, outcomes)
            all_events.extend(detected)
            all_outcomes.update(outcomes)
        if len(dataset_versions) != 1:
            raise ValidationError(
                "instruments span multiple dataset versions; one run covers one version",
                {"dataset_versions": sorted(dataset_versions)},
            )
        dataset_version = next(iter(dataset_versions))

        origins = [_origin_ms(e, bar_ms) for e in all_events]
        n_raw = len(all_events)
        horizon_h = max(stats_params["horizons"])
        report = embargo_purge(
            origins,
            boundaries=boundaries,
            horizon_h=horizon_h,
            embargo_e=int(stats_params["embargo_bars"]),
            bar_ms=bar_ms,
        )
        segment_by_origin = {a.origin_ms: a.segment for a in report.kept}
        observations = [
            Observation(
                event_id=e.event_id,
                instrument_id=str(e.instrument_id),
                event_type=e.event_type,
                origin_ms=origin,
                horizons=dict(all_outcomes[e.event_id].horizons),
            )
            for e, origin in zip(all_events, origins, strict=True)
            if origin in segment_by_origin
        ]
        thresholds = load_evidence_grade()
        result = compute_stats(
            observations,
            boundaries=boundaries,
            data_scope=data_scope,
            event_filter={
                "detector": detector.detector_id,
                "version": detector.version,
                "event_type": resolved_type,
                "direction": resolved_direction,
                "timeframe": timeframe,
                "params": filt.params,
                "from": filt.from_.isoformat() if filt.from_ is not None else None,
                "to": filt.to.isoformat() if filt.to is not None else None,
                "instrument_ids": [str(i) for i in body.instrument_ids],
            },
            cluster_models=stats_params["cluster_models"],
            seed=int(stats_params["bootstrap_seed"]),
            params={
                "horizons": stats_params["horizons"],
                "bootstrap_resamples": stats_params["bootstrap_resamples"],
                "market_event_k": stats_params["market_event_k"],
            },
            grade_thresholds=thresholds,
            n_raw=n_raw,
            bar_ms=bar_ms,
            family_id=result_family_id,
            variant_count=variant_count,
        )
        resolved_params = {
            "detector": dict(detector_params),
            "stats": {
                "horizons": stats_params["horizons"],
                "bootstrap_resamples": stats_params["bootstrap_resamples"],
                "bootstrap_seed": stats_params["bootstrap_seed"],
                "embargo_bars": stats_params["embargo_bars"],
                "market_event_k": stats_params["market_event_k"],
                "cluster_models": stats_params["cluster_models"],
            },
            "family_id": result_family_id,
        }
        ref_id = uuid.uuid4()
        conn.execute(
            text(
                'INSERT INTO research.stats_result (ref_id, experiment_id, "filter",'
                " data_scope, dataset_version, dataset_schema_version, engine_version,"
                " code_version, resolved_params, result, seed, supersedes, locked)"
                " VALUES (:ref_id, NULL, CAST(:filter AS jsonb), :data_scope,"
                " :dataset_version, :dataset_schema_version, :engine_version,"
                " :code_version, CAST(:resolved AS jsonb), CAST(:result AS jsonb),"
                " :seed, NULL, false)"
            ),
            {
                "ref_id": ref_id,
                "filter": json.dumps(result["filter"]),
                "data_scope": data_scope,
                "dataset_version": dataset_version,
                "dataset_schema_version": DATASET_SCHEMA_VERSION,
                "engine_version": ENGINE_VERSION,
                "code_version": code_version(),
                "resolved": json.dumps(resolved_params),
                "result": json.dumps(result),
                "seed": int(stats_params["bootstrap_seed"]),
            },
        )
        if data_scope == HOLDOUT:
            assert body.hypothesis_id is not None
            _consume_holdout(
                conn,
                hypothesis_id=body.hypothesis_id,
                ref_id=ref_id,
                result=result,
                resolved_params=resolved_params,
            )
        if body.family_id is None:
            create_singleton_family(
                conn,
                family_id=family_id,
                hypothesis_ids=(
                    [str(body.hypothesis_id)] if body.hypothesis_id is not None else []
                ),
            )
        record_variant(
            conn,
            family_id=family_id,
            stats_ref_id=ref_id,
            params_hash_value=params_hash(resolved_params),
            resolved_params=resolved_params,
        )
        stored = conn.execute(
            text("SELECT * FROM research.stats_result WHERE ref_id = :ref"),
            {"ref": ref_id},
        ).first()
        assert stored is not None
        return _row_to_view(stored, scope_view)


@router.get("/stats/{ref_id}", response_model=StatsResultView, tags=["stats"])
async def get_stats(ref_id: UUID) -> StatsResultView:
    """Return the stored StatsResult row verbatim (spec 11; 404 unknown).

    The scope echo is rebuilt from the stored filter: the row is immutable, so
    the recomputed echo matches the POST response exactly.
    """
    with connection_scope() as conn:
        row = conn.execute(
            text("SELECT * FROM research.stats_result WHERE ref_id = :ref"),
            {"ref": ref_id},
        ).first()
    if row is None:
        raise NotFoundError("stats result not found", {"ref_id": str(ref_id)})
    result = dict(row.result)
    stored_filter = dict(row.filter)
    bounds = load_boundaries()
    scope_view = _scope_view(
        _ms(datetime.fromisoformat(stored_filter["from"].replace("Z", "+00:00")))
        if stored_filter.get("from") is not None
        else None,
        _ms(datetime.fromisoformat(stored_filter["to"].replace("Z", "+00:00")))
        if stored_filter.get("to") is not None
        else None,
        boundaries=bounds,
        data_scope=row.data_scope,
    )
    return StatsResultView(
        ref_id=row.ref_id,
        filter=stored_filter,
        data_scope=row.data_scope,
        boundaries={
            "oos_start": result["boundaries"]["oos_start"],
            "holdout_start": result["boundaries"]["holdout_start"],
        },
        dataset_version=row.dataset_version,
        dataset_schema_version=row.dataset_schema_version,
        engine_version=row.engine_version,
        code_version=row.code_version,
        resolved_params=dict(row.resolved_params),
        seed=row.seed,
        result=result,
        grade=result["grade"],
        n_eff=result["n_eff"],
        model_sensitive=result["model_sensitive"],
        scope=scope_view,
        created_at=row.created_at,
    )


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _origin_ms(event: Event, bar_ms: int) -> int:
    """Bar open_time of the event: ``event_time`` is the bar CLOSE (spec 6.2)."""
    moment = event.event_time
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000) - bar_ms


def _gate_holdout(conn: Connection, hypothesis_id: UUID | None) -> None:
    """HOLDOUT preconditions (spec 8.1 line 373, design section 6.3)."""
    if hypothesis_id is None:
        raise ValidationError(
            "data_scope HOLDOUT requires hypothesis_id (open the holdout first)",
            {"data_scope": HOLDOUT},
        )
    row = conn.execute(
        text(
            "SELECT hyp_id, holdout_opened_at, holdout_result FROM research.hypothesis"
            " WHERE hyp_id = :id"
        ),
        {"id": hypothesis_id},
    ).first()
    if row is None:
        raise NotFoundError("hypothesis not found", {"hypothesis_id": str(hypothesis_id)})
    if row.holdout_opened_at is None:
        raise HoldoutLockedError(
            "holdout has not been opened for this hypothesis",
            {"hypothesis_id": str(hypothesis_id)},
        )
    if row.holdout_result is not None:
        raise HoldoutLockedError(
            "holdout already consumed by a previous stats run (one run per hypothesis)",
            {"hypothesis_id": str(hypothesis_id)},
        )


def _consume_holdout(
    conn: Connection,
    *,
    hypothesis_id: UUID,
    ref_id: UUID,
    result: Mapping[str, Any],
    resolved_params: Mapping[str, Any],
) -> None:
    """Record the single HOLDOUT run, atomically (rowcount guards the race)."""
    holdout_result = {
        "ref_id": str(ref_id),
        "grade": result["grade"],
        "n_eff": result["n_eff"],
        "at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "resolved_params": dict(resolved_params),
    }
    updated = conn.execute(
        text(
            "UPDATE research.hypothesis SET holdout_result = CAST(:result AS jsonb)"
            " WHERE hyp_id = :id AND holdout_result IS NULL"
        ),
        {"result": json.dumps(holdout_result), "id": hypothesis_id},
    ).rowcount
    if updated != 1:
        raise HoldoutLockedError(
            "holdout already consumed by a previous stats run (one run per hypothesis)",
            {"hypothesis_id": str(hypothesis_id)},
        )
    conn.execute(
        text(
            "INSERT INTO system.audit_log (actor, action, target, detail)"
            " VALUES (:actor, :action, :target, CAST(:detail AS jsonb))"
        ),
        {
            "actor": "api",
            "action": "stats.holdout_run",
            "target": str(hypothesis_id),
            "detail": json.dumps(
                {
                    "ref_id": str(ref_id),
                    "grade": result["grade"],
                    "n_eff": result["n_eff"],
                    "resolved_params": dict(resolved_params),
                }
            ),
        },
    )
