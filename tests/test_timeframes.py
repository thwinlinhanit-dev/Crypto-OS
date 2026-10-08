"""Spec section 4.2/4.3: bar identity, timeframes, alignment and the closed-bar rule."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from engine.data.bars import (
    BAR_COLUMNS,
    BAR_MS,
    NATIVE_TIMEFRAMES,
    Timeframe,
    UnknownTimeframeError,
    align_open_time,
    bar_frame,
    close_time_ms,
    expected_open_times,
    is_closed,
    is_derived,
    is_native,
    last_closed_open_time,
    parse_timeframe,
)

MONDAY_UTC_MS = 1_704_067_200_000  # 2024-01-01 00:00 UTC, a Monday


def test_native_and_derived_vocabulary_matches_the_spec() -> None:
    # section 4.3: "Native: 1D, 4H, 1H"; "1W from 1D".
    assert set(NATIVE_TIMEFRAMES) == {Timeframe.D1, Timeframe.H4, Timeframe.H1}
    assert is_native(Timeframe.D1) and is_native(Timeframe.H4) and is_native(Timeframe.H1)
    assert not is_native(Timeframe.W1)
    assert is_derived(Timeframe.W1)
    assert not is_derived(Timeframe.D1)


def test_unknown_timeframe_is_rejected_rather_than_defaulted() -> None:
    # section 14.1 fail-closed: "unavailable timeframe must error".
    with pytest.raises(UnknownTimeframeError) as exc:
        parse_timeframe("15m")
    assert exc.value.value == "15m"
    with pytest.raises(UnknownTimeframeError):
        parse_timeframe("")


def test_bar_lengths_are_the_documented_ms_values() -> None:
    assert BAR_MS[Timeframe.D1] == 86_400_000
    assert BAR_MS[Timeframe.H4] == 14_400_000
    assert BAR_MS[Timeframe.H1] == 3_600_000
    assert BAR_MS[Timeframe.W1] == 604_800_000


def test_close_time_is_open_plus_one_bar() -> None:
    assert close_time_ms(MONDAY_UTC_MS, Timeframe.D1) == MONDAY_UTC_MS + 86_400_000
    assert close_time_ms(0, Timeframe.H4) == 14_400_000


def test_daily_and_intraday_open_times_are_epoch_aligned() -> None:
    # section 17: "Daily bars align to 00:00 UTC; never local time."
    opens = expected_open_times(MONDAY_UTC_MS, MONDAY_UTC_MS + 3 * 86_400_000, Timeframe.D1)
    assert opens == [
        MONDAY_UTC_MS,
        MONDAY_UTC_MS + 86_400_000,
        MONDAY_UTC_MS + 2 * 86_400_000,
        MONDAY_UTC_MS + 3 * 86_400_000,
    ]
    day = datetime.fromtimestamp(MONDAY_UTC_MS / 1000, tz=UTC)
    assert (day.hour, day.minute, day.second, day.microsecond) == (0, 0, 0, 0)

    hours = expected_open_times(0, 43_200_000, Timeframe.H4)
    assert hours == [0, 14_400_000, 28_800_000, 43_200_000]


def test_weekly_bars_start_on_monday_utc_not_on_the_epoch() -> None:
    # The Unix epoch (1970-01-01) was a Thursday, so weekly boundaries are offset.
    assert expected_open_times(MONDAY_UTC_MS, MONDAY_UTC_MS, Timeframe.W1) == [MONDAY_UTC_MS]
    aligned = align_open_time(MONDAY_UTC_MS + 86_400_000, Timeframe.W1)
    assert aligned == MONDAY_UTC_MS
    previous_monday = datetime.fromtimestamp(aligned / 1000, tz=UTC)
    assert previous_monday.weekday() == 0
    assert (previous_monday.hour, previous_monday.minute) == (0, 0)


def test_expected_open_times_only_lists_bars_that_fit_the_window() -> None:
    # A window shorter than one bar still expects the open that starts inside it.
    assert expected_open_times(0, 1000, Timeframe.D1) == [0]
    assert expected_open_times(1000, 500, Timeframe.D1) == []


def test_the_forming_bar_is_not_closed() -> None:
    open_ms = MONDAY_UTC_MS
    close_ms = close_time_ms(open_ms, Timeframe.D1)
    assert is_closed(open_ms, close_ms, Timeframe.D1) is True
    assert is_closed(open_ms, close_ms - 1, Timeframe.D1) is False
    # A bar is closed at exactly its close time, and the forming bar is excluded
    # from the "last closed" series the guardian expects.
    assert last_closed_open_time(close_ms, Timeframe.D1) == open_ms
    assert last_closed_open_time(close_ms - 1, Timeframe.D1) == open_ms - 86_400_000


def test_bar_frame_enforces_the_spec_column_set() -> None:
    frame = bar_frame(
        [
            {
                "instrument_id": "00000000-0000-0000-0000-000000000001",
                "timeframe": "1D",
                "open_time": MONDAY_UTC_MS,
                "close_time": MONDAY_UTC_MS + 86_400_000,
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 10.0,
                "quote_volume": 15.0,
                "trades": 7,
                "source": "fixture",
            }
        ],
        ingested_at_ms=123,
    )
    assert frame.columns == list(BAR_COLUMNS)
    assert frame.height == 1
    assert frame["ingested_at"][0] == 123


def test_bar_frame_rejects_incomplete_rows_instead_of_filling_them() -> None:
    with pytest.raises(ValueError):
        bar_frame([{"instrument_id": "x"}], ingested_at_ms=0)
