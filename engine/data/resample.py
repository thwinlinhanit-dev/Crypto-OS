"""Spec section 4.3 / T1.4: weekly (1W) bars derived from stored 1D bars.

Verbatim rules (section 4.3): week = Monday 00:00 UTC … Sunday 23:59:59.999 UTC;
open = first daily open, high = max, low = min, close = last daily close,
volume/quote_volume/trades = SUM. A week is complete only if all 7 daily bars
exist and none of those days is ORANGE/RED (seen here as an OPEN Guardian
exclusion interval); otherwise the bar is flagged ``incomplete`` and never
interpolated or filled. The forming week is never written.
"""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from engine.data.bars import (
    BAR_COLUMNS,
    BAR_DTYPES,
    BAR_MS,
    Timeframe,
)
from engine.data.guardian import ExclusionInterval

#: Weekly rows are the §4.2 bar columns plus the §4.3 completeness flag.
WEEKLY_COLUMNS: tuple[str, ...] = (*BAR_COLUMNS, "incomplete")
WEEKLY_DTYPES: dict[str, pl.DataType | type[pl.DataType]] = {
    **BAR_DTYPES,
    "incomplete": pl.Boolean,
}
#: Passed to the writer as ``extra_columns`` (design note 7.1): the flag is
#: stored and hashed with the rows; forgetting it is refused, not dropped.
WEEKLY_EXTRA_COLUMNS: tuple[str, ...] = ("incomplete",)

_DAY_MS = BAR_MS[Timeframe.D1]
_WEEK_MS = BAR_MS[Timeframe.W1]
_WEEK_OFFSET = 4 * _DAY_MS  # weeks start Monday; epoch was a Thursday (bars.py)
_DAYS_PER_WEEK = 7


def _covered_day_opens(exclusions: Sequence[ExclusionInterval]) -> list[int]:
    """Daily ``open_time`` values inside any exclusion interval (``start <= o < end``)."""
    covered: set[int] = set()
    for interval in exclusions:
        first_day = (interval.start_ms // _DAY_MS) * _DAY_MS
        for open_ms in range(first_day, interval.end_ms, _DAY_MS):
            covered.add(open_ms)
    return sorted(covered)


def _empty_weekly() -> pl.DataFrame:
    return pl.DataFrame(
        {column: [] for column in WEEKLY_COLUMNS},
        schema=WEEKLY_DTYPES,
    )


def resample_weekly(
    daily: pl.DataFrame,
    *,
    now_ms: int,
    ingested_at_ms: int,
    exclusions: Sequence[ExclusionInterval] = (),
) -> pl.DataFrame:
    """Aggregate stored 1D bars into 1W bars (section 4.3).

    Only weeks whose ``close_time <= now_ms`` are emitted (the forming week is
    never used in any historical calculation). ``exclusions`` are the active
    Guardian exclusion intervals of the 1D series: a day covered by one marks its
    week ``incomplete`` even when the day's bars are present. The input contract
    is strict (fail closed, PD-10): exactly the §4.2 columns, only day-aligned
    ``1D`` rows of a single instrument.
    """
    missing = [column for column in BAR_COLUMNS if column not in daily.columns]
    if missing:
        raise ValueError(f"daily frame is missing bar columns: {missing}")
    unexpected = [column for column in daily.columns if column not in BAR_COLUMNS]
    if unexpected:
        raise ValueError(f"daily frame has unexpected columns: {unexpected}")
    if daily.is_empty():
        return _empty_weekly()

    if daily["timeframe"].unique().to_list() != [Timeframe.D1.value]:
        raise ValueError("resample_weekly accepts only 1D daily bars (section 4.3)")
    if daily["instrument_id"].n_unique() != 1:
        raise ValueError("resample_weekly refuses a frame with mixed instrument_id values")
    aligned = daily.filter(
        (pl.col("open_time") % _DAY_MS == 0)
        & (pl.col("close_time") - pl.col("open_time") == _DAY_MS)
    )
    if aligned.height != daily.height:
        raise ValueError("daily frame has bars that are not day-aligned 1D bars")

    ordered = daily.sort("open_time", maintain_order=True)
    covered = _covered_day_opens(exclusions)
    frame = ordered.with_columns(
        pl.col("open_time").is_in(covered).alias("_day_excluded"),
        (
            (pl.col("open_time") - _WEEK_OFFSET) // _WEEK_MS * _WEEK_MS + _WEEK_OFFSET
        ).alias("_week_open"),
    )
    # The forming week drops out before grouping: nothing partial is aggregated.
    frame = frame.filter(pl.col("_week_open") + _WEEK_MS <= now_ms)
    if frame.is_empty():
        return _empty_weekly()

    computed = (
        frame.group_by("_week_open", maintain_order=True)
        .agg(
            pl.col("instrument_id").first(),
            pl.col("open").first(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
            pl.col("volume").sum(),
            pl.col("quote_volume").sum(),
            pl.col("trades").sum(),
            pl.col("source").first(),
            pl.col("_day_excluded").any().alias("_excluded"),
            pl.len().alias("_days"),
        )
        .rename({"_week_open": "open_time"})
        .with_columns(
            pl.lit(Timeframe.W1.value).alias("timeframe"),
            (pl.col("open_time") + _WEEK_MS).alias("close_time"),
            ((pl.col("_days") < _DAYS_PER_WEEK) | pl.col("_excluded")).alias("incomplete"),
            pl.lit(ingested_at_ms, dtype=pl.Int64).alias("ingested_at"),
        )
        .select(WEEKLY_COLUMNS)
    )
    return pl.DataFrame(
        {column: computed[column].to_list() for column in WEEKLY_COLUMNS},
        schema=WEEKLY_DTYPES,
    ).sort("open_time", maintain_order=True)
