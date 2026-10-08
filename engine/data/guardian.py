"""Spec section 4.6: Data Guardian checks, states, completeness and exclusions.

Every check and threshold below is quoted from section 4.6 of the spec; the two
places the spec is silent are marked INTERPRETATION and carried as open
questions (design note section 13).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

import polars as pl
from sqlalchemy import Connection, text

from engine.data.bars import BAR_MS, Timeframe, expected_open_times, parse_timeframe

#: Section 4.6 stale check: "no new closed bar within 2 bar-lengths + grace".
#: The spec gives no value for the grace; ASSUMPTION carried as an open question.
STALE_GRACE_BAR_LENGTHS = 1

#: Section 4.6 staleness also must not fire for a range whose ingestion itself
#: happened long after the last bar (a frozen historical range, not a live feed).
STALE_INGEST_WINDOW_BAR_LENGTHS = 3


class QualityState(StrEnum):
    """Section 4.6 states per (instrument, timeframe)."""

    GREEN = "GREEN"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    RED = "RED"


class ExclusionKind(StrEnum):
    """Section 4.6 defines exactly two kinds of exclusion range."""

    GAP = "gap"  # missing expected open_time
    INVALID = "invalid"  # OHLC violation / duplicate / non-monotonic bar


@dataclass(frozen=True, slots=True)
class ExclusionInterval:
    """[first missing/invalid bar open_time, last such bar close_time] (4.6)."""

    start_ms: int
    end_ms: int
    kind: ExclusionKind


@dataclass(frozen=True, slots=True)
class Assessment:
    """The guardian's verdict for one (instrument, timeframe) over one range."""

    state: QualityState
    timeframe: str
    completeness: float
    expected_bars: int
    present_bars: int
    zero_volume_fraction: float
    stale: bool
    exclusions: tuple[ExclusionInterval, ...]
    detail: dict[str, Any]


def _runs_to_intervals(opens: Sequence[int], bar_ms: int, kind: ExclusionKind) -> list[ExclusionInterval]:
    """Merge sorted missing/invalid opens into contiguous [open, last close] runs."""
    if not opens:
        return []
    ordered = sorted(set(opens))
    intervals: list[ExclusionInterval] = []
    start = previous = ordered[0]
    for open_ms in ordered[1:]:
        if open_ms == previous + bar_ms:
            previous = open_ms
            continue
        intervals.append(ExclusionInterval(start, previous + bar_ms, kind))
        start = previous = open_ms
    intervals.append(ExclusionInterval(start, previous + bar_ms, kind))
    return intervals


def assess(
    frame: pl.DataFrame,
    *,
    timeframe: Timeframe | str,
    window_start_ms: int,
    window_end_ms: int,
    now_ms: int,
    ingested_at_ms: int | None = None,
) -> Assessment:
    """Run every section 4.6 check over ``frame`` for the requested window.

    The frame is inspected **as given** - the non-monotonic check must see the
    order rows actually arrived in; nothing is sorted or repaired here.
    """
    tf = timeframe if isinstance(timeframe, Timeframe) else parse_timeframe(str(timeframe))
    bar_ms = BAR_MS[tf]
    rows = frame.height

    opens: list[int] = frame["open_time"].to_list() if rows else []
    present = set(opens)

    expected = [open_ms for open_ms in expected_open_times(window_start_ms, window_end_ms, tf) if open_ms + bar_ms <= now_ms]
    matched = [open_ms for open_ms in expected if open_ms in present]
    missing = [open_ms for open_ms in expected if open_ms not in present]
    completeness = (len(matched) / len(expected)) if expected else 1.0

    # --- check: OHLC sanity, exactly section 4.6's three relations -----------
    ohlc_opens: list[int] = []
    if rows:
        invalid = frame.filter(
            ~(
                (pl.col("low") <= pl.col("open"))
                & (pl.col("close") <= pl.col("high"))
                & (pl.col("low") <= pl.col("high"))
            )
        )
        ohlc_opens = invalid["open_time"].to_list()

    # --- check: duplicate bars ---------------------------------------------
    duplicate_opens: list[int] = []
    if rows:
        counts = frame.group_by("open_time").len()
        duplicate_opens = counts.filter(pl.col("len") > 1)["open_time"].to_list()

    # --- check: non-monotonic time (row order as received) ------------------
    opens_values = opens
    monotonic_opens: list[int] = [
        opens_values[i] for i in range(1, len(opens_values)) if opens_values[i] <= opens_values[i - 1]
    ]

    # --- check: zero-volume anomalies (closed bars only) --------------------
    closed = frame.filter(pl.col("close_time") <= now_ms) if rows else frame
    closed_bars = closed.height
    zero_volume_bars = int(closed.filter(pl.col("volume") == 0).height) if closed_bars else 0
    zero_volume_fraction = (zero_volume_bars / closed_bars) if closed_bars else 0.0

    # --- check: stale feed ---------------------------------------------------
    stale = False
    last_close_ms: int | None = None
    ingested_ms = ingested_at_ms
    if rows:
        last_close_ms = int(max(frame["close_time"].to_list()))
        if ingested_ms is None:
            ingested_ms = int(max(frame["ingested_at"].to_list()))
        stale = (now_ms - last_close_ms) > (2 + STALE_GRACE_BAR_LENGTHS) * bar_ms and (
            ingested_ms - last_close_ms
        ) <= STALE_INGEST_WINDOW_BAR_LENGTHS * bar_ms

    # --- exclusions (4.6): gap ranges and invalid ranges ---------------------
    invalid_opens = set(ohlc_opens) | set(duplicate_opens) | set(monotonic_opens)
    exclusions = tuple(
        _runs_to_intervals(missing, bar_ms, ExclusionKind.GAP)
        + _runs_to_intervals(list(invalid_opens), bar_ms, ExclusionKind.INVALID)
    )

    # --- state precedence: RED > ORANGE > YELLOW > GREEN ---------------------
    # ``causes`` records every condition actually observed; the state picks the
    # most severe one, so a RED range still reports the open gaps it also has.
    causes: list[str] = []
    if ohlc_opens:
        causes.append("ohlc_violations")
    if duplicate_opens:
        causes.append("duplicate_bars")
    if monotonic_opens:
        causes.append("non_monotonic_time")
    if missing:
        causes.append("open_gaps")
    if zero_volume_fraction > 0.0:
        # Section 4.6 documents YELLOW for "zero-volume bars <0.1%" and gives no
        # state for >=0.1% (OQ-5): the fraction is reported in detail either way.
        causes.append("zero_volume_bars")
    if stale:
        causes.append("stale_feed")

    if {"ohlc_violations", "duplicate_bars", "non_monotonic_time"} & set(causes):
        state = QualityState.RED
    elif missing:
        state = QualityState.ORANGE
    elif zero_volume_fraction > 0.0 or stale:
        state = QualityState.YELLOW
    else:
        state = QualityState.GREEN

    detail: dict[str, Any] = {
        "causes": causes,
        "window": {"start_ms": window_start_ms, "end_ms": window_end_ms, "now_ms": now_ms},
        "completeness": {
            "present": len(matched),
            "expected": len(expected),
            "fraction": completeness,
        },
        "checks": {
            "duplicates": {"violations": len(duplicate_opens), "opens": sorted(duplicate_opens)},
            "gaps": {"missing": len(missing), "opens": sorted(missing)},
            "non_monotonic": {"violations": len(monotonic_opens), "opens": sorted(monotonic_opens)},
            "ohlc": {"violations": len(ohlc_opens), "opens": sorted(ohlc_opens)},
            "zero_volume": {"bars": zero_volume_bars, "closed_bars": closed_bars},
            "stale": {
                "stale": stale,
                "last_close_time": last_close_ms,
                "threshold_ms": (2 + STALE_GRACE_BAR_LENGTHS) * bar_ms,
                "ingested_at": ingested_ms,
            },
        },
        "exclusions": [
            {"kind": interval.kind.value, "start_ms": interval.start_ms, "end_ms": interval.end_ms}
            for interval in exclusions
        ],
    }

    return Assessment(
        state=state,
        timeframe=tf.value,
        completeness=completeness,
        expected_bars=len(expected),
        present_bars=len(matched),
        zero_volume_fraction=zero_volume_fraction,
        stale=stale,
        exclusions=exclusions,
        detail=detail,
    )


def _ms_to_dt(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)


_QUALITY_SELECT = (
    "id, instrument_id, timeframe, state, completeness, detail, computed_at"
)


def save_assessment(
    conn: Connection,
    instrument_id: UUID,
    assessment: Assessment,
    *,
    computed_at: datetime | None = None,
) -> None:
    """Append an assessment row (section 10 ``system.data_quality`` is a history)."""
    conn.execute(
        text(
            "INSERT INTO system.data_quality (instrument_id, timeframe, state, "
            "completeness, detail, computed_at) VALUES (:instrument_id, :timeframe, "
            ":state, :completeness, CAST(:detail AS jsonb), :computed_at)"
        ),
        {
            "instrument_id": instrument_id,
            "timeframe": assessment.timeframe,
            "state": assessment.state.value,
            "completeness": assessment.completeness,
            "detail": json.dumps(assessment.detail),
            "computed_at": computed_at if computed_at is not None else datetime.now(UTC),
        },
    )


@dataclass(frozen=True, slots=True)
class QualityRecord:
    id: int
    instrument_id: UUID
    timeframe: str
    state: QualityState
    completeness: float
    detail: dict[str, Any]
    computed_at: datetime


def latest_assessments(
    conn: Connection,
    *,
    instrument_id: UUID | None = None,
    timeframe: str | None = None,
) -> list[QualityRecord]:
    """Latest row per (instrument, timeframe) - the /v1/data/health view."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if instrument_id is not None:
        clauses.append("instrument_id = :instrument_id")
        params["instrument_id"] = instrument_id
    if timeframe is not None:
        clauses.append("timeframe = :timeframe")
        params["timeframe"] = timeframe
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        text(
            f"SELECT {_QUALITY_SELECT} FROM (SELECT {_QUALITY_SELECT}, "
            "row_number() OVER (PARTITION BY instrument_id, timeframe "
            "ORDER BY computed_at DESC, id DESC) AS rn "
            f"FROM system.data_quality {where}) ranked WHERE rn = 1 "
            "ORDER BY timeframe, instrument_id"
        ),
        params,
    ).all()
    return [
        QualityRecord(
            id=row.id,
            instrument_id=row.instrument_id,
            timeframe=row.timeframe,
            state=QualityState(row.state),
            completeness=float(row.completeness),
            detail=dict(row.detail),
            computed_at=row.computed_at,
        )
        for row in rows
    ]


@dataclass(frozen=True, slots=True)
class GapRow:
    """One stored exclusion interval (section 10 ``system.gap``)."""

    gap_id: UUID
    instrument_id: UUID
    timeframe: str
    exclusion_start: datetime
    exclusion_end: datetime
    status: str
    kind: str
    source: str
    detected_at: datetime
    repaired_at: datetime | None = None
    repaired_dataset_version: str | None = None


_GAP_SELECT = (
    "gap_id, instrument_id, timeframe, exclusion_start, exclusion_end, status, "
    "kind, source, detected_at, repaired_at, repaired_dataset_version"
)


def save_exclusions(
    conn: Connection,
    *,
    instrument_id: UUID,
    timeframe: str,
    intervals: Iterable[ExclusionInterval],
    source: str = "guardian",
    detected_at: datetime | None = None,
) -> list[UUID]:
    """Persist OPEN exclusion rows; identical OPEN intervals are not duplicated.

    A range repaired earlier (status REPAIRED) does not block a *new* detection:
    re-detection inserts a fresh OPEN row, while the repaired one stays listed
    for reports (section 4.6 "Reports list all exclusion intervals").
    """
    detected = detected_at if detected_at is not None else datetime.now(UTC)
    inserted: list[UUID] = []
    for interval in intervals:
        start = _ms_to_dt(interval.start_ms)
        end = _ms_to_dt(interval.end_ms)
        existing = conn.execute(
            text(
                "SELECT 1 FROM system.gap WHERE instrument_id = :instrument_id "
                "AND timeframe = :timeframe AND exclusion_start = :start "
                "AND exclusion_end = :end AND status = 'OPEN'"
            ),
            {"instrument_id": instrument_id, "timeframe": timeframe, "start": start, "end": end},
        ).first()
        if existing is not None:
            continue
        gap_id = uuid4()
        conn.execute(
            text(
                f"INSERT INTO system.gap ({_GAP_SELECT}) VALUES (:gap_id, :instrument_id, "
                ":timeframe, :exclusion_start, :exclusion_end, 'OPEN', :kind, :source, "
                ":detected_at, NULL, NULL)"
            ),
            {
                "gap_id": gap_id,
                "instrument_id": instrument_id,
                "timeframe": timeframe,
                "exclusion_start": start,
                "exclusion_end": end,
                "kind": interval.kind.value,
                "source": source,
                "detected_at": detected,
            },
        )
        inserted.append(gap_id)
    return inserted


def load_exclusions(
    conn: Connection,
    *,
    instrument_id: UUID | None = None,
    timeframe: str | None = None,
    status: str | None = None,
    active_only: bool = False,
) -> list[GapRow]:
    """List exclusion intervals; ``active_only`` = still excluding (design note 6.1).

    Active is ``OPEN`` (pending repair) **or** ``UNREPAIRABLE`` (section 4.6:
    a gap that cannot be refetched "stays excluded"); only ``REPAIRED`` ranges
    stop excluding while remaining listed forever for reports.
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if instrument_id is not None:
        clauses.append("instrument_id = :instrument_id")
        params["instrument_id"] = instrument_id
    if timeframe is not None:
        clauses.append("timeframe = :timeframe")
        params["timeframe"] = timeframe
    if status is not None:
        clauses.append("status = :status")
        params["status"] = status
    if active_only:
        clauses.append("status IN ('OPEN', 'UNREPAIRABLE')")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        text(f"SELECT {_GAP_SELECT} FROM system.gap{where} ORDER BY exclusion_start"),
        params,
    ).all()
    return [_to_gap(row) for row in rows]


def get_gap(conn: Connection, gap_id: UUID) -> GapRow | None:
    """One stored exclusion by id; None when it does not exist (404 at the edge)."""
    row = conn.execute(
        text(f"SELECT {_GAP_SELECT} FROM system.gap WHERE gap_id = :gap_id"),
        {"gap_id": gap_id},
    ).first()
    return _to_gap(row) if row is not None else None


def set_gap_status(
    conn: Connection,
    gap_id: UUID,
    *,
    status: str,
    repaired_at: datetime | None = None,
    repaired_dataset_version: str | None = None,
) -> bool:
    """Transition a gap row (repair flow, section 4.6); False when unknown."""
    result = conn.execute(
        text(
            "UPDATE system.gap SET status = :status, repaired_at = :repaired_at, "
            "repaired_dataset_version = :repaired_dataset_version WHERE gap_id = :gap_id"
        ),
        {
            "gap_id": gap_id,
            "status": status,
            "repaired_at": repaired_at,
            "repaired_dataset_version": repaired_dataset_version,
        },
    )
    return result.rowcount == 1


def _to_gap(row: Any) -> GapRow:
    return GapRow(
        gap_id=row.gap_id,
        instrument_id=row.instrument_id,
        timeframe=row.timeframe,
        exclusion_start=row.exclusion_start,
        exclusion_end=row.exclusion_end,
        status=row.status,
        kind=row.kind,
        source=row.source,
        detected_at=row.detected_at,
        repaired_at=row.repaired_at,
        repaired_dataset_version=row.repaired_dataset_version,
    )
