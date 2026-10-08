"""M2 market reads (spec section 11): ``GET /v1/candles`` and ``GET /v1/indicators``.

Design note specs/design/m2_chart.md sections 1-3. Fail-closed resolution
(PD-10): unknown instrument 404, perpetual 501 before any read, unknown
timeframe 422, no covering manifest / no assessment / empty window 400
DATA_UNAVAILABLE - never another venue, market type, timeframe or dataset.
Bars come only from the source files the manifest recorded (section 4.4) and
never include the forming bar: ``close_time <= as_of`` or ``<= now`` (PD-2).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import polars as pl
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import Connection, text

from apps.api.db import connection_scope
from apps.api.settings import get_settings
from engine.config.defaults import load_defaults
from engine.data.bars import UnknownTimeframeError, parse_timeframe
from engine.data.guardian import GapRow, QualityRecord, latest_assessments, load_exclusions
from engine.data.instruments import Instrument, MarketType, get_instrument
from engine.data.manifest import get_manifest
from engine.data.parquet import read_part
from engine.errors import (
    DataUnavailableError,
    NotFoundError,
    NotImplementedMarketError,
    ValidationError,
)
from engine.features import resolve_indicator

router = APIRouter(prefix="/v1", tags=["market"])


def now_ms() -> int:
    """Clock seam: tests patch ``apps.api.market.now_ms`` (M1 pattern)."""
    return int(time.time() * 1000)


# --------------------------------------------------------------------------
# Response models (section 11 contract + D-011)
# --------------------------------------------------------------------------


class ExclusionView(BaseModel):
    """One active exclusion interval; the chart shades it (design D-013/D-014)."""

    gap_id: UUID
    exclusion_start: datetime
    exclusion_end: datetime
    kind: str
    status: str


class CandleQualityView(BaseModel):
    """``quality`` block of the section 11 candle response."""

    state: str
    completeness: float
    exclusions: list[ExclusionView]


class WindowView(BaseModel):
    """Resolved window knobs echoed so every served payload is reproducible."""

    from_: datetime | None = Field(default=None, serialization_alias="from")
    to: datetime | None = None
    limit: int
    as_of: datetime | None = None


class CandlesView(BaseModel):
    """Section 11 candle window, columnar, plus the ``window`` echo (D-007 style)."""

    dataset_version: str
    quality: CandleQualityView
    window: WindowView
    t: list[str]
    o: list[float]
    h: list[float]
    l: list[float]  # noqa: E741 - the section 11 contract names this column "l"
    c: list[float]
    v: list[float]


class SeriesView(BaseModel):
    """One keyed indicator output; warm-up entries are ``null`` (section 5)."""

    key: str
    values: list[float | None]


class IndicatorsView(BaseModel):
    """``GET /v1/indicators`` body (design D-011)."""

    name: str
    params: dict[str, Any]
    instrument_id: UUID
    tf: str
    dataset_version: str
    window: WindowView
    t: list[str]
    series: list[SeriesView]


# --------------------------------------------------------------------------
# Request resolution (shared by both endpoints)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Window:
    """One resolved candle window: bars plus the provenance they answer to."""

    frame: pl.DataFrame
    dataset_version: str
    quality: QualityRecord
    exclusions: tuple[GapRow, ...]


def _norm(dt: datetime | None) -> datetime | None:
    """Naive instants are read as UTC (API convention, section 11)."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


def _ms(dt: datetime | None) -> int | None:
    normalized = _norm(dt)
    return None if normalized is None else int(normalized.timestamp() * 1000)


def _iso(moment_ms: int) -> str:
    """ISO-8601 UTC for bar timestamps (whole seconds; bars are second-aligned)."""
    return datetime.fromtimestamp(moment_ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _year(moment_ms: int) -> int:
    return datetime.fromtimestamp(moment_ms / 1000, tz=UTC).year


def _knobs(
    from_: datetime | None,
    to: datetime | None,
    limit: int | None,
    as_of: datetime | None,
) -> tuple[int | None, int | None, int | None, int]:
    """Resolve the window knobs from the request or config/defaults.yaml (PD-11)."""
    documented = load_defaults()["candles"]
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValidationError("limit must be an integer >= 1", {"limit": limit})
    resolved_limit = documented["default_limit"] if limit is None else limit
    resolved_limit = min(resolved_limit, documented["max_limit"])
    from_ms, to_ms, as_of_ms = _ms(from_), _ms(to), _ms(as_of)
    if from_ms is not None and to_ms is not None and from_ms > to_ms:
        raise ValidationError("'from' must not be after 'to'", {"from": _iso(from_ms), "to": _iso(to_ms)})
    return from_ms, to_ms, as_of_ms, resolved_limit


def _identity(conn: Connection, instrument_id: UUID, tf: str) -> tuple[Instrument, str]:
    """Instrument 404 -> perpetual 501 -> timeframe 422 (design note section 1)."""
    instrument = get_instrument(conn, instrument_id)
    if instrument is None:
        raise NotFoundError("instrument not found", {"instrument_id": str(instrument_id)})
    if instrument.market_type is MarketType.PERPETUAL:
        raise NotImplementedMarketError(
            "perpetual candles are not served before M10",
            {"market_type": MarketType.PERPETUAL.value},
        )
    try:
        timeframe = parse_timeframe(tf)
    except UnknownTimeframeError as exc:
        raise ValidationError("unknown timeframe", {"timeframe": tf}) from exc
    return instrument, timeframe.value


def _covering_manifest(conn: Connection, instrument: Instrument, timeframe: str) -> str:
    """Latest manifest covering the request; the manifest is the authority (4.4)."""
    row = conn.execute(
        text(
            "SELECT dataset_version FROM market.dataset_manifest"
            " WHERE instruments @> CAST(:instruments AS jsonb)"
            " AND timeframes @> CAST(:timeframes AS jsonb)"
            " ORDER BY created_at DESC LIMIT 1"
        ),
        {
            "instruments": json.dumps([str(instrument.instrument_id)]),
            "timeframes": json.dumps([timeframe]),
        },
    ).first()
    if row is None:
        raise DataUnavailableError(
            "no registered dataset covers this instrument and timeframe",
            {"instrument_id": str(instrument.instrument_id), "timeframe": timeframe},
        )
    return str(row.dataset_version)


def _assessment(conn: Connection, instrument: Instrument, timeframe: str) -> QualityRecord:
    """Unassessed data is not served (design note section 1, step 6)."""
    records = latest_assessments(
        conn, instrument_id=instrument.instrument_id, timeframe=timeframe
    )
    if not records:
        raise DataUnavailableError(
            "no guardian assessment for this instrument and timeframe",
            {"instrument_id": str(instrument.instrument_id), "timeframe": timeframe},
        )
    return records[0]


def _covers(
    path: str,
    native_symbol: str,
    timeframe: str,
    from_ms: int | None,
    to_ms: int | None,
) -> bool:
    """Match a manifest source path to the request, pruning whole years early."""
    if f"/symbol={native_symbol}/" not in f"/{path}":
        return False
    if f"/tf={timeframe}/" not in f"/{path}":
        return False
    if (from_ms is None and to_ms is None) or "year=" not in path:
        return True
    year = int(path.split("year=", 1)[1].split("/", 1)[0])
    if from_ms is not None and year < _year(from_ms):
        return False
    return to_ms is None or year <= _year(to_ms)


def _load_window(
    conn: Connection,
    *,
    instrument: Instrument,
    timeframe: str,
    from_ms: int | None,
    to_ms: int | None,
    as_of_ms: int | None,
    limit: int,
) -> _Window:
    """Manifest-backed bars for the window: files, quality, cutoff, limit."""
    dataset_version = _covering_manifest(conn, instrument, timeframe)
    quality = _assessment(conn, instrument, timeframe)
    manifest = get_manifest(conn, dataset_version)
    if manifest is None:
        raise DataUnavailableError(
            "manifest recorded but unavailable",
            {"dataset_version": dataset_version},
        )
    data_dir = Path(get_settings().data_dir)
    frames = [
        read_part(data_dir, source.path)
        for source in manifest.source_files
        if _covers(source.path, instrument.native_symbol, timeframe, from_ms, to_ms)
    ]
    if not frames:
        raise DataUnavailableError(
            "no manifest source files fall in the requested window",
            {"dataset_version": dataset_version, "timeframe": timeframe},
        )
    frame = pl.concat(frames).sort("open_time")
    if from_ms is not None:
        frame = frame.filter(pl.col("open_time") >= from_ms)
    if to_ms is not None:
        frame = frame.filter(pl.col("open_time") <= to_ms)
    cutoff = as_of_ms if as_of_ms is not None else now_ms()
    frame = frame.filter(pl.col("close_time") <= cutoff)
    if frame.is_empty():
        raise DataUnavailableError(
            "window contains no closed bars",
            {"dataset_version": dataset_version, "timeframe": timeframe},
        )
    frame = frame.tail(limit)
    exclusions = load_exclusions(
        conn,
        instrument_id=instrument.instrument_id,
        timeframe=timeframe,
        active_only=True,
    )
    return _Window(
        frame=frame,
        dataset_version=dataset_version,
        quality=quality,
        exclusions=tuple(exclusions),
    )


def _parse_params(raw: str | None) -> dict[str, Any] | None:
    """``params`` is a JSON object string; anything else is 422 (PD-11)."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError("params must be a JSON object", {"params": raw[:64]}) from exc
    if not isinstance(parsed, dict):
        raise ValidationError("params must be a JSON object", {"params": type(parsed).__name__})
    return parsed


def _window_view(
    *, limit: int, from_: datetime | None, to: datetime | None, as_of: datetime | None
) -> WindowView:
    return WindowView(from_=_norm(from_), to=_norm(to), limit=limit, as_of=_norm(as_of))


def _exclusion_view(gap: GapRow) -> ExclusionView:
    return ExclusionView(
        gap_id=gap.gap_id,
        exclusion_start=gap.exclusion_start,
        exclusion_end=gap.exclusion_end,
        kind=gap.kind,
        status=gap.status,
    )


def _times(frame: pl.DataFrame) -> list[str]:
    return [_iso(int(moment)) for moment in frame["open_time"].to_list()]


def _numbers(frame: pl.DataFrame, column: str) -> list[float]:
    return [float(value) for value in frame[column].to_list()]


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@router.get("/candles", response_model=CandlesView, tags=["market"])
async def candles(
    instrument_id: UUID,
    tf: str,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    limit: int | None = None,
    as_of: datetime | None = None,
) -> CandlesView:
    """Closed bars for one (instrument, timeframe) window (spec section 11)."""
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
    frame = window.frame
    return CandlesView(
        dataset_version=window.dataset_version,
        quality=CandleQualityView(
            state=window.quality.state.value,
            completeness=float(window.quality.completeness),
            exclusions=[_exclusion_view(gap) for gap in window.exclusions],
        ),
        window=_window_view(
            limit=resolved_limit, from_=from_, to=to, as_of=as_of
        ),
        t=_times(frame),
        o=_numbers(frame, "open"),
        h=_numbers(frame, "high"),
        l=_numbers(frame, "low"),
        c=_numbers(frame, "close"),
        v=_numbers(frame, "volume"),
    )


@router.get("/indicators", response_model=IndicatorsView, tags=["market"])
async def indicators(
    instrument_id: UUID,
    tf: str,
    name: str,
    params: str | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    limit: int | None = None,
    as_of: datetime | None = None,
) -> IndicatorsView:
    """Server-computed indicator over the same window as /v1/candles (D-011).

    Name and params fail fast (422) before any I/O: unknown names,
    undocumented keys and missing documented defaults are never guessed.
    """
    spec, resolved = resolve_indicator(name, _parse_params(params))
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
    frame = window.frame
    values = spec.compute(frame, resolved)
    return IndicatorsView(
        name=spec.name,
        params=resolved,
        instrument_id=instrument.instrument_id,
        tf=timeframe,
        dataset_version=window.dataset_version,
        window=_window_view(
            limit=resolved_limit, from_=from_, to=to, as_of=as_of
        ),
        t=_times(frame),
        series=[
            SeriesView(key=key, values=values[key]) for key in spec.outputs
        ],
    )
