"""M6 (T6.1) — fill models and time fields (spec 7.3).

Time fields on every trade (D-038): signal_bar_index, signal_time (close_time
of the signal bar), decision_time (= signal_time unless latency modeled),
fill_bar_index, fill_time; exits: exit_signal_time, exit_decision_time,
exit_fill_time. All timestamps UTC ISO-8601; close_time is the exclusive
upper bound (D-008).

PARITY (D-039): entries and discretionary exits fill at the CLOSE of the
signal/decision bar (TradingView process_orders_on_close=true);
entry_price = close_s; fill_time = signal_time.

REALISTIC_OHLC (R8 default): fill bar = signal bar + 1 + delay_bars, fill at
that bar's OPEN with adverse slippage; the stop is fixed at signal time.

Resting-order semantics (both models): a stop fills at the stop price when
the bar's range crosses it and the bar opens beyond it on the favourable
side; a gap through the stop fills at OPEN (7.3). Targets fill at the limit,
or at OPEN when gapped favourably. Same-bar stop+target: REALISTIC_OHLC =
stop first (7.3); PARITY = the broker-emulator path heuristic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from engine.strategy.models import Execution, FillModel, SlippageModel

Side = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class Fill:
    """One executed fill: price, bar index, time; the fee is computed by the caller."""

    price: float
    bar_index: int
    time: str  # ISO-8601 UTC (close_time semantics)


def fee_on_notional(fee_percent: float, notional: float) -> float:
    """7.1: PERCENT — per side on notional (value is percent)."""
    return notional * (fee_percent / 100.0)


def slippage_adjust(slippage: SlippageModel, side: Side, open_price: float) -> float:
    """7.1: FIXED_BPS — adverse, per fill (value is basis points)."""
    delta = open_price * (slippage.value / 10_000.0)
    return open_price + delta if side == "LONG" else open_price - delta


def entry_fill(
    execution: Execution,
    slippage: SlippageModel,
    side: Side,
    *,
    close_s: float,
    signal_bar_index: int,
    signal_time: str,
    next_bar_open: float | None,
    next_bar_time: str | None,
) -> Fill | None:
    """7.3 entry fill per model. None when no next bar exists (E1)."""
    if execution.fill_model is FillModel.PARITY:
        return Fill(price=close_s, bar_index=signal_bar_index, time=signal_time)
    if next_bar_open is None or next_bar_time is None:
        return None
    return Fill(
        price=slippage_adjust(slippage, side, next_bar_open),
        bar_index=signal_bar_index + 1 + execution.delay_bars,
        time=next_bar_time,
    )


def discretionary_exit_fill(
    execution: Execution,
    slippage: SlippageModel,
    side: Side,
    *,
    close_t: float,
    decision_bar_index: int,
    decision_time: str,
    next_bar_open: float | None,
    next_bar_time: str | None,
) -> Fill | None:
    """7.3 discretionary exits: PARITY fills at the decision bar's close;
    REALISTIC_OHLC fills at the next bar's open (adverse slippage). None ->
    no next bar (E1 end of data).
    """
    if execution.fill_model is FillModel.PARITY:
        return Fill(price=close_t, bar_index=decision_bar_index, time=decision_time)
    if next_bar_open is None or next_bar_time is None:
        return None
    return Fill(
        price=slippage_adjust(slippage, side, next_bar_open),
        bar_index=decision_bar_index + 1 + execution.delay_bars,
        time=next_bar_time,
    )


def stop_fill_price(
    side: Side,
    *,
    stop: float,
    open_t: float,
    high_t: float,
    low_t: float,
) -> float | None:
    """7.3 resting stop: fill at the stop price when the bar's range crosses it
    and the bar opens beyond it on the favourable side; gap through the stop
    fills at OPEN. None when the stop was not touched.
    """
    if side == "LONG":
        if open_t <= stop:  # gap down through the stop
            return open_t
        if low_t <= stop:
            return stop
        return None
    if open_t >= stop:
        return open_t
    if high_t >= stop:
        return stop
    return None


def target_fill_price(
    side: Side,
    *,
    target: float,
    open_t: float,
    high_t: float,
    low_t: float,
) -> float | None:
    """7.3 target (limit): fills at the limit, or at OPEN when gapped
    favourably. None when the target was not touched.
    """
    if side == "LONG":
        if open_t >= target:
            return open_t
        if high_t >= target:
            return target
        return None
    if open_t <= target:
        return open_t
    if low_t <= target:
        return target
    return None


def same_bar_resolution(
    fill_model: FillModel,
    side: Side,
    *,
    open_t: float,
    high_t: float,
    low_t: float,
    stop: float,
    target: float | None,
) -> Literal["STOP", "TARGET"] | None:
    """7.3.1 E5: same-bar stop+target ordering.

    REALISTIC_OHLC = stop first. PARITY = the TradingView broker-emulator
    path heuristic: if the high is closer to the open than the low the bar
    runs O->H->L->C (target first), else O->L->H->C (stop first).
    """
    stop_hit = stop_fill_price(side, stop=stop, open_t=open_t, high_t=high_t, low_t=low_t)
    target_hit = (
        target_fill_price(side, target=target, open_t=open_t, high_t=high_t, low_t=low_t)
        if target is not None
        else None
    )
    if stop_hit is None and target_hit is None:
        return None
    if stop_hit is not None and target_hit is None:
        return "STOP"
    if target_hit is not None and stop_hit is None:
        return "TARGET"
    if fill_model is FillModel.REALISTIC_OHLC:
        return "STOP"
    # PARITY path heuristic (7.3).
    high_first = abs(high_t - open_t) < abs(low_t - open_t)
    return "TARGET" if high_first else "STOP"
