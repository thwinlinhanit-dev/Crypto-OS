"""Spec section 4.2/4.3: bar rows, timeframes, alignment, the closed-bar rule.

Bars are carried as polars DataFrames with exactly the columns the spec lists.
Nothing here fetches, stores or repairs data - those live in engine.data.ingest,
engine.data.store and engine.data.guardian.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import polars as pl

BAR_COLUMNS: tuple[str, ...] = (
    "instrument_id",
    "timeframe",
    "open_time",
    "close_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades",
    "source",
    "ingested_at",
)

BAR_DTYPES: dict[str, pl.DataType | type[pl.DataType]] = {
    "instrument_id": pl.String,
    "timeframe": pl.String,
    "open_time": pl.Int64,
    "close_time": pl.Int64,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "quote_volume": pl.Float64,
    "trades": pl.Int64,
    "source": pl.String,
    "ingested_at": pl.Int64,
}


class Timeframe(StrEnum):
    """Native timeframes plus the derived weekly one (section 4.3)."""

    D1 = "1D"
    H4 = "4H"
    H1 = "1H"
    W1 = "1W"


NATIVE_TIMEFRAMES: tuple[Timeframe, ...] = (Timeframe.D1, Timeframe.H4, Timeframe.H1)
DERIVED_TIMEFRAMES: tuple[Timeframe, ...] = (Timeframe.W1,)

BAR_MS: dict[Timeframe, int] = {
    Timeframe.D1: 86_400_000,
    Timeframe.H4: 14_400_000,
    Timeframe.H1: 3_600_000,
    Timeframe.W1: 604_800_000,
}

#: Bar boundaries are multiples of BAR_MS measured from an anchor in UTC.
#: The Unix epoch (1970-01-01) was a Thursday, so weekly bars - which start
#: Monday 00:00 UTC - anchor at 1970-01-05 (section 4.3).
ALIGN_OFFSET_MS: dict[Timeframe, int] = {
    Timeframe.D1: 0,
    Timeframe.H4: 0,
    Timeframe.H1: 0,
    Timeframe.W1: 4 * 86_400_000,
}


class UnknownTimeframeError(ValueError):
    """Raised instead of silently substituting a timeframe (PD-10, section 14.1)."""

    def __init__(self, value: str) -> None:
        super().__init__(
            f"unknown timeframe {value!r}; native: "
            f"{', '.join(tf.value for tf in NATIVE_TIMEFRAMES)}, derived: "
            f"{', '.join(tf.value for tf in DERIVED_TIMEFRAMES)}"
        )
        self.value = value


def parse_timeframe(value: str) -> Timeframe:
    try:
        return Timeframe(value)
    except ValueError as cause:
        raise UnknownTimeframeError(value) from cause


def is_native(timeframe: Timeframe) -> bool:
    return timeframe in NATIVE_TIMEFRAMES


def is_derived(timeframe: Timeframe) -> bool:
    return timeframe in DERIVED_TIMEFRAMES


def close_time_ms(open_time_ms: int, timeframe: Timeframe) -> int:
    return open_time_ms + BAR_MS[timeframe]


def align_open_time(timestamp_ms: int, timeframe: Timeframe) -> int:
    """Floor ``timestamp_ms`` onto a bar boundary of ``timeframe`` (UTC anchors)."""
    offset = ALIGN_OFFSET_MS[timeframe]
    bar = BAR_MS[timeframe]
    return ((timestamp_ms - offset) // bar) * bar + offset


def expected_open_times(start_ms: int, end_ms: int, timeframe: Timeframe) -> list[int]:
    """Expected bar opens inside ``[start_ms, end_ms]`` (section 4.6 completeness).

    The window bounds open times, the same way the exchange's startTime/endTime
    do; whether each of those bars has *closed* is decided by ``is_closed`` /
    ``last_closed_open_time``, never silently dropped here.
    """
    if end_ms < start_ms:
        return []
    bar = BAR_MS[timeframe]
    first = align_open_time(start_ms, timeframe)
    if first < start_ms:
        first += bar
    opens: list[int] = []
    current = first
    while current <= end_ms:
        opens.append(current)
        current += bar
    return opens


def is_closed(open_time_ms: int, now_ms: int, timeframe: Timeframe) -> bool:
    """Only CLOSED bars enter features, events and backtests (section 4.2)."""
    return close_time_ms(open_time_ms, timeframe) <= now_ms


def last_closed_open_time(now_ms: int, timeframe: Timeframe) -> int:
    """Latest open whose bar has closed at ``now_ms``; may predate the epoch."""
    return align_open_time(now_ms - BAR_MS[timeframe], timeframe)


def to_iso(open_time_ms: int) -> str:
    """UTC ISO-8601 with milliseconds; the API's timestamp form (section 11)."""
    return datetime.fromtimestamp(open_time_ms / 1000, tz=UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def bar_frame(rows: Iterable[Mapping[str, Any]], *, ingested_at_ms: int) -> pl.DataFrame:
    """Build a bar frame with the spec column set; incomplete rows are an error.

    Nothing is filled in or defaulted: a row missing a field is a data defect,
    not something to paper over (PD-10).
    """
    data: dict[str, list[Any]] = {column: [] for column in BAR_COLUMNS}
    for index, row in enumerate(rows):
        missing = [c for c in BAR_COLUMNS if c != "ingested_at" and c not in row]
        if missing:
            raise ValueError(f"bar row {index} is missing fields: {missing}")
        unknown = [key for key in row if key not in BAR_COLUMNS]
        if unknown:
            raise ValueError(f"bar row {index} has unknown fields: {unknown}")
        for column in BAR_COLUMNS:
            data[column].append(ingested_at_ms if column == "ingested_at" else row[column])
    return pl.DataFrame(data, schema=BAR_DTYPES)


def sort_bars(frame: pl.DataFrame) -> pl.DataFrame:
    """Ascending by ``open_time`` (deterministic writer order, section 4.4)."""
    if frame.is_empty():
        return frame
    return frame.sort("open_time", maintain_order=True)


def closed_only(frame: pl.DataFrame, now_ms: int) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    return frame.filter(pl.col("close_time") <= now_ms)
