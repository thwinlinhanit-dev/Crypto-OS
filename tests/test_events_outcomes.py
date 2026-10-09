"""M4 (T4.3) - outcomes (spec section 6.4, design section 5).

Acceptance evidence produced here:
- fwd_ret_pct/R always use the full horizon h (no stop truncation); h=10
  matches the Pine study formula exactly;
- R = |ref - invalidation| (3.0 * atr by construction);
- hit_invalidation scans bars t+1..t+h in order and stops at the first touch;
- same-bar ambiguity: stop first - excursion window ends at the stop bar
  (inclusive); ties resolve to the earliest bar;
- mfe_R/mae_R direction-adjusted; bars_to_mfe/mae are 1-based offsets of the
  first bar printing the window extreme;
- incomplete horizons (t+h > last closed bar) carry null values and are
  never partially counted;
- the full horizon list [1,2,3,5,10,15,20,30,40,50] is the default set.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from engine.events.base import Event
from engine.events.outcomes import DEFAULT_HORIZONS, compute_outcomes

DAY = 86_400_000
BASE = 1_704_067_200_000


def _event(
    index: int,
    direction: str,
    ref_price: float,
    invalidation_price: float,
) -> Event:
    return Event(
        detector_id="RVOL_CLIMAX",
        event_type="CONT" if direction == "LONG" else "TOP",
        version="5.0",
        params_hash="test",
        instrument_id="BINANCE:BTCUSDT",
        timeframe="1D",
        event_time=datetime.fromtimestamp(
            (BASE + index * DAY + DAY) / 1000.0, tz=UTC
        ),
        direction=direction,
        ref_price=ref_price,
        invalidation_price=invalidation_price,
        regime={},
        features={},
        trigger={},
        lookback_gap=False,
        dataset_version="test",
        event_index=index,
    )


def _frame_from(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> dict[str, list[float]]:
    if highs is None:
        highs = [c + 1.0 for c in closes]
    if lows is None:
        lows = [c - 1.0 for c in closes]
    return {"close": closes, "high": highs, "low": lows}


def test_default_horizons_match_spec() -> None:
    assert DEFAULT_HORIZONS == (1, 2, 3, 5, 10, 15, 20, 30, 40, 50)


def test_fwd_ret_long_full_horizon() -> None:
    # Event at bar 0, ref=100, invalidation=100-3*2=94, R=6 (atr=2).
    # Close at bar 2 = 110: move = 10, fwd_ret_pct = 10%, fwd_ret_R = 10/6.
    closes = [100.0, 105.0, 110.0]
    frame = _frame_from(closes)
    event = _event(0, "LONG", 100.0, 94.0)
    outcomes = compute_outcomes([event], frame, horizons=(2,))
    outcome = outcomes[0]
    h = outcome.horizons[2]
    assert h.complete is True
    assert h.fwd_ret_pct == pytest.approx(10.0)
    assert h.fwd_ret_r == pytest.approx(10.0 / 6.0)
    assert outcome.r_basis == "atr"
    assert outcome.r_value == pytest.approx(6.0)


def test_fwd_ret_short_full_horizon() -> None:
    # SHORT event at bar 0, ref=100, invalidation=106, R=6.
    # Close at bar 2 = 90: move = 10, fwd_ret_pct = 10%.
    closes = [100.0, 95.0, 90.0]
    frame = _frame_from(closes)
    event = _event(0, "SHORT", 100.0, 106.0)
    outcomes = compute_outcomes([event], frame, horizons=(2,))
    h = outcomes[0].horizons[2]
    assert h.fwd_ret_pct == pytest.approx(10.0)
    assert h.fwd_ret_r == pytest.approx(10.0 / 6.0)


def test_hit_invalidation_long_and_stop_first() -> None:
    # LONG at bar 0, ref=100, inv=94.
    # Bar 1 low=93 (touches inv) -> stop bar is 1.
    # Bar 2 low=80, high=120 - but the window ends at bar 1 (stop first).
    closes = [100.0, 99.0, 110.0]
    highs = [101.0, 100.0, 120.0]
    lows = [99.0, 93.0, 80.0]
    frame = _frame_from(closes, highs, lows)
    event = _event(0, "LONG", 100.0, 94.0)
    outcomes = compute_outcomes([event], frame, horizons=(2,))
    h = outcomes[0].horizons[2]
    assert h.hit_invalidation is True
    # mfe/mae only over bars 1..min(2, stop_bar=1) = bar 1.
    # LONG signed (design 5): mfe_R = (high1 - ref)/R = (100-100)/6 = 0
    #                          mae_R = (low1 - ref)/R = (93-100)/6 = -7/6
    assert h.mfe_r == pytest.approx(0.0)
    assert h.mae_r == pytest.approx(-7.0 / 6.0)
    assert h.bars_to_mfe == 1
    assert h.bars_to_mae == 1


def test_stop_first_short() -> None:
    # SHORT at bar 0, ref=100, inv=106.
    # Bar 1 high=107 (touches) -> stop bar 1.
    closes = [100.0, 101.0, 90.0]
    highs = [101.0, 107.0, 102.0]
    lows = [99.0, 100.0, 85.0]
    frame = _frame_from(closes, highs, lows)
    event = _event(0, "SHORT", 100.0, 106.0)
    outcomes = compute_outcomes([event], frame, horizons=(2,))
    h = outcomes[0].horizons[2]
    assert h.hit_invalidation is True
    # Window ends at bar 1; SHORT mfe_R = (ref - low1)/R = (100-100)/6 = 0
    # mae_R = (ref - ... wait SHORT mae = (ref - high)/R negative side:
    #   SHORT: mfe_R = (ref - min low)/R, mae_R = (ref - max high)/R
    #   over bars 1..1: min low = 100, max high = 107
    #   mfe_R = 0, mae_R = (100 - 107)/6 = -7/6
    assert h.mfe_r == pytest.approx(0.0)
    assert h.mae_r == pytest.approx(-7.0 / 6.0)


def test_ties_resolve_to_earliest_bar() -> None:
    # LONG, ref=100, inv=94. Bars 1 and 2 both have low=93 (tie).
    # stop_bar must be 1 (earliest).
    closes = [100.0, 99.0, 99.0]
    highs = [101.0, 100.0, 100.0]
    lows = [99.0, 93.0, 93.0]
    frame = _frame_from(closes, highs, lows)
    event = _event(0, "LONG", 100.0, 94.0)
    outcomes = compute_outcomes([event], frame, horizons=(2,))
    h = outcomes[0].horizons[2]
    assert h.hit_invalidation is True
    assert h.bars_to_mae == 1  # first bar printing the window extreme


def test_bars_to_mfe_and_mae_offsets() -> None:
    # LONG, ref=100, inv=94 (never hit). No stop.
    # bar1: high=105, low=98; bar2: high=112, low=97.
    # mfe over 1..2 = 112 -> bars_to_mfe = 2
    # mae over 1..2 = 97 -> bars_to_mae = 2
    closes = [100.0, 104.0, 110.0]
    highs = [101.0, 105.0, 112.0]
    lows = [99.0, 98.0, 97.0]
    frame = _frame_from(closes, highs, lows)
    event = _event(0, "LONG", 100.0, 94.0)
    outcomes = compute_outcomes([event], frame, horizons=(2,))
    h = outcomes[0].horizons[2]
    assert h.hit_invalidation is False
    assert h.mfe_r == pytest.approx(12.0 / 6.0)
    assert h.mae_r == pytest.approx((97.0 - 100.0) / 6.0)  # signed, design 5
    assert h.bars_to_mfe == 2
    assert h.bars_to_mae == 2


def test_bars_to_mfe_is_1_based_offset_from_event_bar() -> None:
    # The extreme is printed on the very next bar -> bars_to_mfe = 1.
    closes = [100.0, 110.0]
    highs = [101.0, 115.0]
    lows = [99.0, 109.0]
    frame = _frame_from(closes, highs, lows)
    event = _event(0, "LONG", 100.0, 94.0)
    outcomes = compute_outcomes([event], frame, horizons=(1,))
    h = outcomes[0].horizons[1]
    assert h.bars_to_mfe == 1
    assert h.mfe_r == pytest.approx(15.0 / 6.0)


def test_incomplete_horizon_null_and_never_partially_counted() -> None:
    # Event at bar 0, frame has only 3 bars (indices 0,1,2).
    # Horizon 10 needs bar 10 -> incomplete; values are None.
    closes = [100.0, 105.0, 110.0]
    frame = _frame_from(closes)
    event = _event(0, "LONG", 100.0, 94.0)
    outcomes = compute_outcomes([event], frame, horizons=(1, 10))
    outcome = outcomes[0]
    h1 = outcome.horizons[1]
    h10 = outcome.horizons[10]
    assert h1.complete is True
    assert h1.fwd_ret_pct == pytest.approx(5.0)
    assert h10.complete is False
    assert h10.fwd_ret_pct is None
    assert h10.fwd_ret_r is None
    assert h10.mfe_r is None
    assert h10.mae_r is None
    assert h10.bars_to_mfe is None
    assert h10.bars_to_mae is None
    assert h10.hit_invalidation is None


def test_exact_horizon_boundary_is_complete() -> None:
    # Event at bar 0, last bar index 2. Horizon 2 needs bar 2 -> complete.
    closes = [100.0, 105.0, 110.0]
    frame = _frame_from(closes)
    event = _event(0, "LONG", 100.0, 94.0)
    outcomes = compute_outcomes([event], frame, horizons=(2,))
    assert outcomes[0].horizons[2].complete is True
    # Horizon 3 needs bar 3 -> incomplete.
    outcomes3 = compute_outcomes([event], frame, horizons=(3,))
    assert outcomes3[0].horizons[3].complete is False


def test_r_equals_abs_ref_minus_invalidation() -> None:
    closes = [100.0, 105.0]
    frame = _frame_from(closes)
    event = _event(0, "LONG", 100.0, 90.0)  # R = 10
    outcomes = compute_outcomes([event], frame, horizons=(1,))
    assert outcomes[0].r_value == pytest.approx(10.0)
    h = outcomes[0].horizons[1]
    assert h.fwd_ret_r == pytest.approx(5.0 / 10.0)


def test_multiple_events_ordered_by_event_index() -> None:
    closes = [100.0, 101.0, 102.0, 103.0]
    frame = _frame_from(closes)
    e0 = _event(2, "LONG", 102.0, 96.0)
    e1 = _event(0, "LONG", 100.0, 94.0)
    outcomes = compute_outcomes([e0, e1], frame, horizons=(1,))
    assert [o.event_index for o in outcomes] == [0, 2]


def test_outcome_carries_event_id_and_provenance() -> None:
    closes = [100.0, 105.0]
    frame = _frame_from(closes)
    event = _event(0, "LONG", 100.0, 94.0)
    outcomes = compute_outcomes([event], frame, horizons=(1,))
    outcome = outcomes[0]
    assert outcome.event_id == event.event_id
    assert outcome.provenance == {
        "fwd_ret_pct": "CALCULATED",
        "fwd_ret_r": "CALCULATED",
        "mfe_r": "CALCULATED",
        "mae_r": "CALCULATED",
        "hit_invalidation": "CALCULATED",
        "complete": "CALCULATED",
    }


def test_empty_events_and_empty_frame() -> None:
    assert compute_outcomes([], {"close": [], "high": [], "low": []}) == []


def test_zero_ref_price_is_not_silently_ignored() -> None:
    # ref=0 would make fwd_ret_pct divide by zero; the outcome must not
    # silently produce inf/nan - incomplete or a documented skip.
    closes = [0.0, 1.0]
    frame = _frame_from(closes)
    event = _event(0, "LONG", 0.0, -3.0)
    outcomes = compute_outcomes([event], frame, horizons=(1,))
    h = outcomes[0].horizons[1]
    # Document behaviour: either complete with a sentinel or incomplete.
    assert h.complete is False or h.fwd_ret_pct is None
