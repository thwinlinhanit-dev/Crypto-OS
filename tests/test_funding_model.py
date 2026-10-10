"""M10 — the funding cost model tests (spec T10.3/7.7, design note
m10_perp_live section 4, D-070).

Funding accrues every 8h while a position is open; a positive rate means
longs pay shorts (the venue convention).
"""

from __future__ import annotations

import pytest

from engine.strategy.funding import (
    FUNDING_INTERVAL_MS,
    FundingResult,
    funding_charges,
    funding_cost,
    next_funding_ts,
)

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
T0 = 1_767_225_600_000  # 2026-01-01T00:00:00Z

# a funding every 8h at +0.01% (a typical positive rate)
SCHEDULE = [
    (T0 + 8 * HOUR_MS, 0.0001),
    (T0 + 16 * HOUR_MS, 0.0001),
    (T0 + 24 * HOUR_MS, 0.0001),
]


def test_a_long_pays_at_a_positive_rate() -> None:
    result = funding_cost(
        side="LONG",
        entry_ms=T0,
        exit_ms=T0 + 24 * HOUR_MS,
        notional=10_000.0,
        schedule=SCHEDULE,
    )
    # three accruals at +0.01% of 10k = 3 * 1.0
    assert result.total_paid == pytest.approx(3.0)
    assert len(result.charges) == 3


def test_a_short_receives_at_a_positive_rate() -> None:
    """D-070: positive rate = longs pay shorts — a short receives."""
    result = funding_cost(
        side="SHORT",
        entry_ms=T0,
        exit_ms=T0 + 24 * HOUR_MS,
        notional=10_000.0,
        schedule=SCHEDULE,
    )
    assert result.total_paid == pytest.approx(-3.0)


def test_a_negative_rate_flips_the_payer() -> None:
    """A negative rate: shorts pay longs."""
    negative = [(ts, -0.0002) for ts, _ in SCHEDULE]
    result = funding_cost(
        side="LONG",
        entry_ms=T0,
        exit_ms=T0 + 24 * HOUR_MS,
        notional=10_000.0,
        schedule=negative,
    )
    assert result.total_paid == pytest.approx(-6.0)


def test_an_event_at_entry_is_not_charged() -> None:
    """The position opens at entry_ms; a funding event at that instant is
    not charged to it."""
    at_entry = [(T0, 0.0001)]
    result = funding_cost(
        side="LONG",
        entry_ms=T0,
        exit_ms=T0 + 8 * HOUR_MS,
        notional=10_000.0,
        schedule=at_entry,
    )
    assert result.total_paid == 0.0
    assert result.charges == []


def test_an_event_at_exit_is_charged() -> None:
    """A funding event at exit_ms IS charged (the position was open through
    it)."""
    at_exit = [(T0 + 8 * HOUR_MS, 0.0001)]
    result = funding_cost(
        side="LONG",
        entry_ms=T0,
        exit_ms=T0 + 8 * HOUR_MS,
        notional=10_000.0,
        schedule=at_exit,
    )
    assert result.total_paid == pytest.approx(1.0)


def test_events_outside_the_window_are_ignored() -> None:
    result = funding_cost(
        side="LONG",
        entry_ms=T0 + 24 * HOUR_MS,
        exit_ms=T0 + 32 * HOUR_MS,
        notional=10_000.0,
        schedule=SCHEDULE,
    )
    assert result.total_paid == 0.0


def test_an_empty_window_charges_nothing() -> None:
    result = funding_cost(
        side="LONG",
        entry_ms=T0,
        exit_ms=T0,
        notional=10_000.0,
        schedule=SCHEDULE,
    )
    assert result.total_paid == 0.0


def test_an_unknown_side_fails_closed() -> None:
    with pytest.raises(ValueError):
        funding_charges(
            side="FLAT",
            entry_ms=T0,
            exit_ms=T0 + DAY_MS,
            notional=10_000.0,
            schedule=SCHEDULE,
        )


def test_an_unsorted_schedule_is_sorted_before_accrual() -> None:
    """The venue may deliver out of order; the charge is order-independent."""
    shuffled = [SCHEDULE[2], SCHEDULE[0], SCHEDULE[1]]
    result = funding_cost(
        side="LONG",
        entry_ms=T0,
        exit_ms=T0 + 24 * HOUR_MS,
        notional=10_000.0,
        schedule=shuffled,
    )
    assert result.total_paid == pytest.approx(3.0)


def test_next_funding_ts_snaps_to_the_8h_grid() -> None:
    assert next_funding_ts(T0 + 8 * HOUR_MS) == T0 + 8 * HOUR_MS  # on-grid
    assert next_funding_ts(T0 + 8 * HOUR_MS + 1) == T0 + 16 * HOUR_MS
    assert next_funding_ts(T0 + 9 * HOUR_MS) == T0 + 16 * HOUR_MS


def test_funding_interval_is_8h() -> None:
    assert FUNDING_INTERVAL_MS == 8 * HOUR_MS


def test_funding_result_shape() -> None:
    result = funding_cost(
        side="LONG",
        entry_ms=T0,
        exit_ms=T0 + 8 * HOUR_MS,
        notional=1_000.0,
        schedule=SCHEDULE[:1],
    )
    assert isinstance(result, FundingResult)
    charge = result.charges[0]
    assert charge.ts == T0 + 8 * HOUR_MS
    assert charge.rate == 0.0001
    assert charge.notional == 1_000.0
    assert charge.paid == pytest.approx(0.1)
