"""Spec section 4.3 / T1.4: weekly (1W) resampling from native 1D bars.

Covers: aggregation rules, COMPLETE/INCOMPLETE flagging (missing day, Guardian
exclusion intervals), forming-week exclusion, input contract, order invariance
and the truncation-invariance property required by section 4.3/14.1.
"""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.data.bars import BAR_COLUMNS, BAR_DTYPES, Timeframe, align_open_time, bar_frame
from engine.data.guardian import ExclusionInterval, ExclusionKind
from engine.data.instruments import MarketType, instrument_id_for
from engine.data.resample import WEEKLY_COLUMNS, WEEKLY_DTYPES, resample_weekly

DAY_MS = 86_400_000
WEEK_MS = 7 * DAY_MS
W0 = 1_704_067_200_000  # 2024-01-01, a Monday
INGESTED_AT_MS = 1_710_000_000_000
CUT_MAX = W0 + 3 * WEEK_MS  # every day of the 3-week span is closed here

INSTRUMENT_ID = str(instrument_id_for("BINANCE", MarketType.SPOT, "BTCUSDT"))


def _day(offset: int, **overrides: Any) -> dict[str, Any]:
    """A 1D bar ``offset`` days after 2024-01-01 (day 0 = Monday of week 0)."""
    row: dict[str, Any] = {
        "instrument_id": INSTRUMENT_ID,
        "timeframe": "1D",
        "open_time": W0 + offset * DAY_MS,
        "close_time": W0 + (offset + 1) * DAY_MS,
        "open": 10.0 + offset,
        "high": 20.0 + offset,
        "low": 5.0 + offset,
        "close": 15.0 + offset,
        "volume": 100.0 + offset,
        "quote_volume": 1_000.0 + offset,
        "trades": 7 + offset,
        "source": "fixture",
    }
    row.update(overrides)
    return row


def _daily(days: list[dict[str, Any]], *, ingested_at_ms: int = INGESTED_AT_MS) -> pl.DataFrame:
    return bar_frame(days, ingested_at_ms=ingested_at_ms)


def _resample(
    frame: pl.DataFrame,
    *,
    now_ms: int,
    exclusions: list[ExclusionInterval] | None = None,
) -> pl.DataFrame:
    return resample_weekly(
        frame,
        now_ms=now_ms,
        ingested_at_ms=INGESTED_AT_MS,
        exclusions=exclusions or [],
    )


def test_seven_days_aggregate_by_the_section_4_3_rules() -> None:
    weekly = _resample(_daily([_day(i) for i in range(7)]), now_ms=W0 + WEEK_MS)
    assert weekly.height == 1
    row = weekly.row(named=True)
    assert row["open_time"] == W0
    assert row["close_time"] == W0 + WEEK_MS
    assert row["timeframe"] == "1W"
    assert row["instrument_id"] == INSTRUMENT_ID
    assert row["source"] == "fixture"
    assert row["ingested_at"] == INGESTED_AT_MS
    assert row["open"] == 10.0  # first daily open (day 0)
    assert row["close"] == 15.0 + 6  # last daily close (day 6)
    assert row["high"] == 20.0 + 6  # max over days, not the first day's
    assert row["low"] == 5.0  # min over days, not the last day's
    assert row["volume"] == pytest.approx(sum(100.0 + i for i in range(7)))
    assert row["quote_volume"] == pytest.approx(sum(1_000.0 + i for i in range(7)))
    assert row["trades"] == sum(7 + i for i in range(7))


def test_complete_week_has_incomplete_false() -> None:
    weekly = _resample(_daily([_day(i) for i in range(7)]), now_ms=W0 + WEEK_MS)
    assert weekly.columns == list(WEEKLY_COLUMNS)
    assert weekly.schema == WEEKLY_DTYPES
    assert weekly["incomplete"].to_list() == [False]


def test_weekly_alignment_puts_week_open_on_the_monday() -> None:
    assert align_open_time(W0, Timeframe.W1) == W0
    assert align_open_time(W0 + 3 * DAY_MS, Timeframe.W1) == W0
    assert align_open_time(W0 + WEEK_MS, Timeframe.W1) == W0 + WEEK_MS


def test_forming_week_is_never_written() -> None:
    days = [_day(i) for i in range(7)]
    # Mid-week (Thursday): the week has not closed, nothing may be emitted.
    assert _resample(_daily(days), now_ms=W0 + 3 * DAY_MS).height == 0
    # The instant the Sunday bar closes, the week appears.
    assert _resample(_daily(days), now_ms=W0 + WEEK_MS).height == 1
    # Six closed days, Sunday not closed yet: still forming, still nothing.
    assert _resample(_daily([_day(i) for i in range(6)]), now_ms=W0 + WEEK_MS - 1).height == 0


def test_missing_day_flags_the_week_incomplete() -> None:
    days = [_day(i) for i in range(7) if i != 3]
    weekly = _resample(_daily(days), now_ms=W0 + WEEK_MS)
    assert weekly.height == 1
    assert weekly["incomplete"].to_list() == [True]
    # Never interpolated: no synthetic bars were added to reach 7 days.
    assert weekly["volume"].to_list() == [pytest.approx(sum(100.0 + i for i in range(7) if i != 3))]


def test_invalid_day_from_a_guardian_exclusion_flags_the_week_incomplete() -> None:
    interval = ExclusionInterval(
        kind=ExclusionKind.INVALID,
        start_ms=W0 + 2 * DAY_MS,
        end_ms=W0 + 3 * DAY_MS,
    )
    weekly = _resample(_daily([_day(i) for i in range(7)]), now_ms=W0 + WEEK_MS,
                       exclusions=[interval])
    assert weekly.height == 1
    assert weekly["incomplete"].to_list() == [True]


def test_gap_exclusion_over_an_absent_day_flags_the_week_incomplete() -> None:
    interval = ExclusionInterval(
        kind=ExclusionKind.GAP,
        start_ms=W0 + 4 * DAY_MS,
        end_ms=W0 + 5 * DAY_MS,
    )
    days = [_day(i) for i in range(7) if i != 4]
    weekly = _resample(_daily(days), now_ms=W0 + WEEK_MS, exclusions=[interval])
    assert weekly["incomplete"].to_list() == [True]


def test_exclusions_outside_the_resampled_days_do_not_flag_anything() -> None:
    interval = ExclusionInterval(
        kind=ExclusionKind.INVALID,
        start_ms=W0 - DAY_MS,  # the Sunday before week 0: not part of this frame
        end_ms=W0,
    )
    weekly = _resample(_daily([_day(i) for i in range(7)]), now_ms=W0 + WEEK_MS,
                       exclusions=[interval])
    assert weekly["incomplete"].to_list() == [False]


def test_partial_week_at_the_start_is_incomplete_but_written() -> None:
    days = [_day(i) for i in (3, 4, 5)]  # Wed..Fri of week 0
    weekly = _resample(_daily(days), now_ms=W0 + WEEK_MS)
    assert weekly.height == 1
    assert weekly["incomplete"].to_list() == [True]
    assert weekly["open_time"].to_list() == [W0]
    assert weekly["open"].to_list() == [10.0 + 3]  # first *present* daily open


def test_two_weeks_do_not_bleed_into_each_other() -> None:
    days = [_day(i) for i in range(7)] + [_day(i) for i in range(7, 10)]
    weekly = _resample(_daily(days), now_ms=W0 + 2 * WEEK_MS)
    assert weekly["open_time"].to_list() == [W0, W0 + WEEK_MS]
    assert weekly["incomplete"].to_list() == [False, True]
    assert weekly["close_time"].to_list() == [W0 + WEEK_MS, W0 + 2 * WEEK_MS]


def test_close_time_equals_the_sunday_bar_close_time() -> None:
    days = [_day(i) for i in range(7)]
    sunday_close = days[6]["close_time"]
    weekly = _resample(_daily(days), now_ms=W0 + WEEK_MS)
    assert weekly["close_time"].to_list() == [sunday_close]


def test_input_order_does_not_change_the_result() -> None:
    days = [_day(i) for i in range(7)]
    forward = _resample(_daily(days), now_ms=W0 + WEEK_MS)
    backward = _resample(_daily(list(reversed(days))), now_ms=W0 + WEEK_MS)
    assert forward.equals(backward)


def test_empty_input_returns_an_empty_well_formed_weekly_frame() -> None:
    weekly = _resample(_daily([]), now_ms=W0 + WEEK_MS)
    assert weekly.is_empty()
    assert weekly.columns == list(WEEKLY_COLUMNS)
    assert weekly.schema == WEEKLY_DTYPES


def test_non_daily_bars_are_refused() -> None:
    rows = [_day(0, timeframe="4H", close_time=W0 + 4 * 3_600_000)]
    with pytest.raises(ValueError, match="only 1D"):
        _resample(_daily(rows), now_ms=W0 + WEEK_MS)


def test_misaligned_daily_bars_are_refused() -> None:
    rows = [_day(0, open_time=W0 + 1, close_time=W0 + DAY_MS + 1)]
    with pytest.raises(ValueError, match="aligned"):
        _resample(_daily(rows), now_ms=W0 + WEEK_MS)


def test_daily_bars_must_span_exactly_one_day() -> None:
    rows = [_day(0, close_time=W0 + 2 * DAY_MS)]
    with pytest.raises(ValueError, match="aligned"):
        _resample(_daily(rows), now_ms=W0 + WEEK_MS)


def test_mixed_instruments_are_refused() -> None:
    other = str(instrument_id_for("BINANCE", MarketType.SPOT, "ETHUSDT"))
    rows = [_day(0), _day(1, instrument_id=other)]
    with pytest.raises(ValueError, match="instrument"):
        _resample(_daily(rows), now_ms=W0 + WEEK_MS)


def test_non_bar_columns_are_refused() -> None:
    frame = _daily([_day(0)]).with_columns(pl.lit(1).alias("junk"))
    with pytest.raises(ValueError, match="unexpected columns"):
        _resample(frame, now_ms=W0 + WEEK_MS)


def test_bar_frame_schema_is_the_writer_contract() -> None:
    # Guard the assumptions resample_weekly builds on (column set, dtypes).
    frame = _daily([_day(0)])
    assert frame.columns == list(BAR_COLUMNS)
    assert frame.schema == BAR_DTYPES


@settings(deadline=None, max_examples=60)
@given(
    present=st.lists(st.integers(min_value=0, max_value=20), unique=True),
    cut=st.integers(min_value=W0 - DAY_MS, max_value=CUT_MAX + DAY_MS),
)
def test_truncation_invariance(present: list[int], cut: int) -> None:
    """resample(data cut at t) == restrict(resample(full data) to bars closed <= t)."""
    days = [_day(i) for i in sorted(set(present))]
    full = _resample(_daily(days), now_ms=CUT_MAX)
    cut_days = [day for day in days if day["close_time"] <= cut]
    left = _resample(_daily(cut_days), now_ms=cut)
    right = full.filter(pl.col("close_time") <= cut)
    assert left.equals(right)
