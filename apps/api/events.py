"""M4 (T4.4) - ``GET /v1/events`` (spec sections 6.2, 11; design note section 6).

On-demand compute, no DB table (design D-022/OQ-16): the detector runs over
the same manifest-backed closed-bar window the candles endpoint serves, so
``dataset_version`` / ``data_quality`` provenance matches (PD-6). ``event_id``
is content-addressed, so recompute is idempotent. Fail-closed resolution
(PD-10/PD-11): unknown detector/version 422, SPEC_REQUIRED 422, undocumented
params 422, unknown instrument 404, perpetual 501, no covering manifest 400.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from apps.api.db import connection_scope
from apps.api.market import (
    _identity,
    _knobs,
    _load_window,
    _parse_params,
    _window_view,
)
from engine.config.defaults import ParamError
from engine.errors import ValidationError
from engine.events.registry import resolve_detector

router = APIRouter(prefix="/v1", tags=["events"])


# --------------------------------------------------------------------------
# Response models (section 6.2 envelope)
# --------------------------------------------------------------------------


class TriggerRuleView(BaseModel):
    """One rule in the event's trigger checklist."""

    name: str
    passed: bool
    value: float | None = None
    threshold: float | None = None


class EventFeaturesView(BaseModel):
    """Detection features carried on the envelope (section 6.2)."""

    rvol: float | None = None
    cpos: float | None = None
    upper_wick: float | None = None
    atr: float | None = None
    ema_regime: float | None = None
    archetype: str | None = None


class EventEnvelopeView(BaseModel):
    """Section 6.2 event envelope as served by the API."""

    event_id: str
    detector_id: str
    event_type: str
    version: str
    params_hash: str
    instrument_id: str
    timeframe: str
    event_time: datetime
    direction: str
    ref_price: float
    invalidation_price: float
    regime: dict[str, Any]
    features: dict[str, Any]
    trigger: dict[str, Any]
    lookback_gap: bool
    dataset_version: str
    engine_version: str
    data_quality: dict[str, Any]
    resolved_params: dict[str, Any]
    provenance: dict[str, str]


class EventsView(BaseModel):
    """``GET /v1/events`` body: resolved request echo plus envelopes."""

    detector: str
    version: str
    instrument_id: UUID
    tf: str
    dataset_version: str
    window: dict[str, Any]
    events: list[EventEnvelopeView]


# --------------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------------


def _resolve_event_type(raw: str | None) -> str | None:
    """Unknown event types are 422 (fail-closed, never filtered to empty)."""
    if raw is None:
        return None
    allowed = {"CONT", "CAPIT", "FAIL", "TOP"}
    if raw not in allowed:
        raise ValidationError(
            "unknown event_type",
            {"event_type": raw, "allowed": sorted(allowed)},
        )
    return raw


@router.get("/events", response_model=EventsView, tags=["events"])
async def events(
    instrument_id: UUID,
    tf: str,
    detector: str = "RVOL_CLIMAX",
    version: str | None = None,
    event_type: str | None = None,
    params: str | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    limit: int | None = None,
    as_of: datetime | None = None,
) -> EventsView:
    """Closed-bar events for one (instrument, timeframe) window (section 6.2).

    Detector/version/params resolve through the registry and
    ``config/defaults.yaml`` (PD-11); the window resolution is exactly the
    candles path so provenance matches.
    """
    resolved_type = _resolve_event_type(event_type)
    try:
        det = resolve_detector(detector, version)
    except ValidationError:
        raise
    try:
        resolved_params = det.resolve(_parse_params(params))
    except ParamError as exc:
        raise ValidationError(str(exc), detail=getattr(exc, "detail", {})) from exc

    from_ms, to_ms, as_of_ms, resolved_limit = _knobs(from_, to, limit, as_of)
    with connection_scope() as conn:
        instrument, timeframe = _identity(conn, instrument_id, tf)
        window = _load_window(
            conn,
            instrument=instrument,
            timeframe=timeframe,
            from_ms=from_ms,
            to_ms=to_ms,
            as_of_ms=as_of_ms,
            limit=resolved_limit,
        )

    # The detector takes (start, end) pairs; GapRow carries datetimes.
    exclusion_pairs = [
        (gap.exclusion_start, gap.exclusion_end) for gap in window.exclusions
    ]
    exclusion_intervals = [
        {
            "gap_id": str(gap.gap_id),
            "start": gap.exclusion_start.isoformat(),
            "end": gap.exclusion_end.isoformat(),
            "kind": gap.kind,
        }
        for gap in window.exclusions
    ]
    data_quality = {
        "state": window.quality.state.value,
        "exclusion_intervals": exclusion_intervals,
    }
    detected = det.detect(
        window.frame,
        resolved_params,
        exclusion_pairs,
        instrument_id=str(instrument.instrument_id),
        dataset_version=window.dataset_version,
        data_quality=data_quality,
        timeframe=timeframe,
    )
    if resolved_type is not None:
        detected = [e for e in detected if e.event_type == resolved_type]
    detected.sort(key=lambda e: (e.event_index, e.event_type))

    return EventsView(
        detector=det.detector_id,
        version=det.version,
        instrument_id=instrument.instrument_id,
        tf=timeframe,
        dataset_version=window.dataset_version,
        window=_window_view(
            limit=resolved_limit, from_=from_, to=to, as_of=as_of
        ).model_dump(mode="json", by_alias=True),
        events=[EventEnvelopeView(**e.to_envelope()) for e in detected],
    )
