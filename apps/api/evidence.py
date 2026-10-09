"""M5 (T5.3) - ``GET /v1/events/{id}`` Evidence payload (spec 11 line 532, UI 558).

``event_id`` is a sha1, not reversible, so lookup is by stored PK:

1. ``SELECT`` from ``research.event`` (+ outcomes).
2. On miss: cache-miss recompute - run every registered detector over the
   dataset family's ``(instrument, timeframe)`` grid with default params,
   insert anything found, re-select. Windows wider than the served bar
   budget are sliced (never silently truncated); detectors/cells without
   coverage are skipped. Events first computed with custom params only
   reproduce under those params, so unpersisted custom-param events miss
   here by construction (persist them with POST /v1/stats first).
3. Still absent -> 404 ``NOT_FOUND`` (fail-closed, never a substitute event).

The ``stats`` block carries the most recent stored StatsResult whose filter
covers the event (same detector+version params hash, event type, direction,
instrument and timeframe, same dataset_version, DEVELOPMENT scope); when no
stored run matches, one is computed through the POST /v1/stats path and
reused. Analogues are the 5 most recent same-detector@version+type events
with strictly earlier event_time (nearest-in-time peers, no outcome
conditioning), each with h=10 ``fwd_ret_r`` (null when incomplete).
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import Connection, text

from apps.api.db import connection_scope
from apps.api.market import _load_window
from apps.api.stats import StatsFilter, StatsRequest, post_stats
from engine.config.defaults import ParamError, load_defaults
from engine.data.bars import BAR_MS, UnknownTimeframeError, parse_timeframe
from engine.data.guardian import latest_assessments
from engine.errors import DataUnavailableError, NotFoundError
from engine.events.base import CALCULATED, params_hash
from engine.events.outcomes import DEFAULT_HORIZONS, compute_outcomes
from engine.events.registry import known_detectors, resolve_detector
from engine.scopes import load_boundaries
from engine.stats.persist import persist_events

router = APIRouter(prefix="/v1", tags=["events"])


class TriggerRowView(BaseModel):
    """One checklist row: the stored trigger block plus its provenance."""

    name: str
    passed: bool
    value: Any | None = None
    threshold: Any | None = None
    provenance: str = CALCULATED


class EvidenceEventView(BaseModel):
    """The ``event`` block (spec 532-536 keys)."""

    id: str
    detector: str
    type: str
    version: str
    instrument: str
    tf: str
    time: datetime
    direction: str
    ref_price: float
    invalidation: float
    r_basis: str = "atr"
    lookback_gap: bool


class EvidenceStatsView(BaseModel):
    """The ``stats`` block: grade triple plus horizons and multiple-testing."""

    ref_id: str
    data_scope: str
    grade: str | None
    grade_reason: str | None = None
    model_sensitive: bool | None
    n_raw: int
    n_eff: int
    # Horizon entries projected to the spec line-539 shape
    # {h, median_r, ci, win, win_ci, is, oos} (plain dicts: "is" is a keyword).
    horizons: list[dict[str, Any]]
    multiple_testing: dict[str, Any]


class AnalogueView(BaseModel):
    """One nearest-in-time peer event."""

    event_id: str
    time: datetime
    instrument: str
    fwd_r_10: float | None


class DataQualityView(BaseModel):
    """Guardian state for the event's instrument/timeframe."""

    state: str
    completeness: float


class EvidenceView(BaseModel):
    """``GET /v1/events/{id}`` body (spec 532-544)."""

    event: EvidenceEventView
    trigger: list[TriggerRowView]
    context: dict[str, Any]
    stats: EvidenceStatsView
    analogues: list[AnalogueView]
    data_quality: DataQualityView
    dataset_version: str


@router.get("/events/{event_id}", response_model=EvidenceView, tags=["events"])
async def event_evidence(event_id: str) -> EvidenceView:
    """Evidence payload for one stored (or recomputed) event."""
    with connection_scope() as conn:
        row = _select_event(conn, event_id)
        if row is None:
            _recompute_grid(conn)
            row = _select_event(conn, event_id)
        if row is None:
            raise NotFoundError("event not found", {"event_id": event_id})
        stats = _matching_stats(conn, row)
        analogues = _analogues(conn, row)
        quality = _quality(conn, row)
        display = _display_refs(conn, [row.instrument_id])
    if stats is None:
        stats = await _compute_stats_for(row)
    horizons = [
        {
            "h": entry["h"],
            "median_r": entry["median_r"],
            "ci": entry["ci_median_r"],
            "win": entry["win_rate"],
            "win_ci": entry["win_ci"],
            "is": entry["is"],
            "oos": entry["oos"],
        }
        for entry in stats["result"]["horizons"]
    ]
    trigger_block = dict(row.trigger) if isinstance(row.trigger, dict) else {}
    rules = trigger_block.get("rules", [])
    if not isinstance(rules, list):
        rules = []
    return EvidenceView(
        event=EvidenceEventView(
            id=row.event_id,
            detector=row.detector_id,
            type=row.event_type,
            version=row.version,
            instrument=display.get(row.instrument_id, row.instrument_id),
            tf=row.timeframe,
            time=row.event_time,
            direction=row.direction,
            ref_price=float(row.ref_price),
            invalidation=float(row.invalidation_price),
            lookback_gap=bool(row.lookback_gap),
        ),
        trigger=[
            TriggerRowView(
                name=str(rule["name"]),
                passed=bool(rule["passed"]),
                value=rule.get("value"),
                threshold=rule.get("threshold"),
            )
            for rule in rules
        ],
        context={
            "regime": dict(row.regime) if isinstance(row.regime, dict) else {},
            "archetype": dict(row.features).get("archetype") if isinstance(row.features, dict) else None,
        },
        stats=EvidenceStatsView(
            ref_id=str(stats["ref_id"]),
            data_scope=stats["data_scope"],
            grade=stats["result"]["grade"],
            grade_reason=stats["result"]["grade_reason"],
            model_sensitive=stats["result"]["model_sensitive"],
            n_raw=stats["result"]["n_raw"],
            n_eff=stats["result"]["n_eff"],
            horizons=horizons,
            multiple_testing=dict(stats["result"]["multiple_testing"]),
        ),
        analogues=analogues,
        data_quality=quality,
        dataset_version=row.dataset_version,
    )


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _select_event(conn: Connection, event_id: str) -> Any | None:
    return conn.execute(
        text("SELECT * FROM research.event WHERE event_id = :id"), {"id": event_id}
    ).first()


def _recompute_grid(conn: Connection) -> None:
    """Run every registered detector over the scope family grid (default params).

    Cells without data (unknown instrument, uncovered timeframe) are skipped;
    anything found is inserted idempotently. Windows wider than the served bar
    budget are sliced so no window is ever silently truncated.
    """
    boundaries = load_boundaries()
    max_bars = int(load_defaults()["candles"]["max_limit"])
    for detector_id, version in known_detectors():
        detector = resolve_detector(detector_id, version)
        try:
            resolved = detector.resolve(None)
        except ParamError:
            continue
        for ref in boundaries.universe:
            venue, _, native = ref.partition(":")
            instrument_rows = conn.execute(
                text(
                    "SELECT instrument_id FROM market.instrument"
                    " WHERE venue = :venue AND native_symbol = :native"
                ),
                {"venue": venue, "native": native},
            ).all()
            for (instrument_id,) in instrument_rows:
                for timeframe in boundaries.timeframes:
                    try:
                        bar_ms = BAR_MS[parse_timeframe(timeframe)]
                    except UnknownTimeframeError:
                        continue
                    for start_ms, end_ms in _slices(
                        boundaries.s_start, boundaries.s_end, bar_ms, max_bars
                    ):
                        try:
                            window = _load_window(
                                conn,
                                instrument=_instrument(conn, instrument_id),
                                timeframe=timeframe,
                                from_ms=start_ms,
                                to_ms=end_ms,
                                as_of_ms=None,
                                limit=max_bars + 1,
                            )
                        except DataUnavailableError:
                            continue
                        detected = detector.detect(
                            window.frame,
                            resolved,
                            [
                                (gap.exclusion_start, gap.exclusion_end)
                                for gap in window.exclusions
                            ],
                            instrument_id=str(instrument_id),
                            dataset_version=window.dataset_version,
                            data_quality={"state": window.quality.state.value},
                            timeframe=timeframe,
                        )
                        detected.sort(key=lambda e: (e.event_index, e.event_type))
                        persist_events(
                            conn,
                            detected,
                            {
                                o.event_id: o
                                for o in compute_outcomes(
                                    detected, window.frame, list(DEFAULT_HORIZONS)
                                )
                            },
                        )


def _slices(start_ms: int, end_ms: int, bar_ms: int, max_bars: int) -> list[tuple[int, int]]:
    """Split [start, end] so every slice holds at most ``max_bars`` bars."""
    total = (end_ms - start_ms) // bar_ms + 1
    count = max(1, math.ceil(total / max_bars))
    width = (end_ms - start_ms) / count
    bounds = [start_ms + round(k * width) for k in range(count)] + [end_ms]
    return [(bounds[k], bounds[k + 1]) for k in range(count) if bounds[k] < bounds[k + 1]]


def _instrument(conn: Connection, instrument_id: UUID) -> Any:
    from engine.data.instruments import get_instrument

    instrument = get_instrument(conn, instrument_id)
    if instrument is None:  # pragma: no cover - row was just selected above
        raise NotFoundError("instrument not found", {"instrument_id": str(instrument_id)})
    return instrument


def _matching_stats(conn: Connection, event: Any) -> dict[str, Any] | None:
    """Most recent stored run covering this event (filter, scope, params, version)."""
    rows = conn.execute(
        text(
            "SELECT * FROM research.stats_result"
            " WHERE data_scope = 'DEVELOPMENT' AND dataset_version = :dv"
            " AND filter @> CAST(:need AS jsonb)"
            " AND (filter->>'event_type' IS NULL OR filter->>'event_type' = :etype)"
            " AND (filter->>'direction' IS NULL OR filter->>'direction' = :direction)"
            " AND (filter->'instrument_ids' ? :instrument_id)"
            " ORDER BY created_at DESC LIMIT 5"
        ),
        {
            "dv": event.dataset_version,
            "need": json.dumps({"detector": event.detector_id, "timeframe": event.timeframe}),
            "etype": event.event_type,
            "direction": event.direction,
            "instrument_id": event.instrument_id,
        },
    ).all()
    for row in rows:
        resolved = dict(row.resolved_params).get("detector")
        if isinstance(resolved, dict) and params_hash(resolved) == event.params_hash:
            return {
                "ref_id": row.ref_id,
                "data_scope": row.data_scope,
                "result": dict(row.result),
            }
    return None


async def _compute_stats_for(event: Any) -> dict[str, Any]:
    """No stored run matches: compute once through the POST /v1/stats path."""
    view = await post_stats(
        StatsRequest(
            filter=StatsFilter(
                detector=event.detector_id,
                version=event.version,
                event_type=event.event_type,
                timeframe=event.timeframe,
            ),
            instrument_ids=[UUID(event.instrument_id)],
            data_scope="DEVELOPMENT",
        )
    )
    return {"ref_id": view.ref_id, "data_scope": view.data_scope, "result": view.result}


def _analogues(conn: Connection, event: Any) -> list[AnalogueView]:
    """Up to 5 most recent same detector@version+type peers, strictly earlier."""
    boundaries = load_boundaries()
    rows = conn.execute(
        text(
            "SELECT e.event_id, e.event_time, e.instrument_id,"
            " CASE WHEN o.complete THEN o.fwd_ret_r ELSE NULL END AS fwd_r_10"
            " FROM research.event e LEFT JOIN research.event_outcome o"
            " ON o.event_id = e.event_id AND o.horizon = 10"
            " WHERE e.detector_id = :det AND e.version = :ver AND e.event_type = :type"
            " AND e.event_time < :at"
            " AND e.instrument_id IN (SELECT instrument_id::text FROM market.instrument"
            " WHERE (venue || ':' || native_symbol) = ANY(:universe))"
            " ORDER BY e.event_time DESC LIMIT 5"
        ),
        {
            "det": event.detector_id,
            "ver": event.version,
            "type": event.event_type,
            "at": event.event_time,
            "universe": list(boundaries.universe),
        },
    ).all()
    display = _display_refs(conn, [r.instrument_id for r in rows])
    return [
        AnalogueView(
            event_id=r.event_id,
            time=r.event_time,
            instrument=display.get(r.instrument_id, r.instrument_id),
            fwd_r_10=None if r.fwd_r_10 is None else float(r.fwd_r_10),
        )
        for r in rows
    ]


def _display_refs(conn: Connection, instrument_ids: list[str]) -> dict[str, str]:
    """UUID strings -> ``BTC/USDT spot`` display refs (fallback: the raw id)."""
    if not instrument_ids:
        return {}
    try:
        ids = [UUID(i) for i in instrument_ids]
    except ValueError:
        return {}
    rows = conn.execute(
        text(
            "SELECT instrument_id, asset, quote, market_type FROM market.instrument"
            " WHERE instrument_id = ANY(:ids)"
        ),
        {"ids": ids},
    ).all()
    return {
        str(r.instrument_id): f"{r.asset}/{r.quote} {r.market_type.lower()}" for r in rows
    }


def _quality(conn: Connection, event: Any) -> DataQualityView:
    try:
        instrument_id = UUID(event.instrument_id)
    except ValueError:
        instrument_id = None
    records = (
        latest_assessments(conn, instrument_id=instrument_id, timeframe=event.timeframe)
        if instrument_id is not None
        else []
    )
    if not records:
        # No assessment exists (rows are only wiped, never decayed): explicit
        # unknown, never a fabricated GREEN.
        return DataQualityView(state="UNKNOWN", completeness=0.0)
    return DataQualityView(
        state=records[0].state.value, completeness=float(records[0].completeness)
    )
