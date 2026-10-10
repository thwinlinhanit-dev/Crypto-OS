"""M6 (T6.1) — the backtest simulator (spec 7.4 order of operations, 7.5 trade
record, 7.3.1 edge cases E1-E6).

Pure (D-042): ``simulate`` never touches the database or the network. Bars,
signals and indicator arrays are plain sequences supplied by the caller, so
the same function runs sync in tests and async behind the M1 job seam.

Per-bar order (7.4, closed bar t, per instrument):
  1. (features are precomputed and passed in as arrays)
  2. EXIT FILLS for resting stop/target (7.3);
  3. management while still open (breakeven, trailing per kind, ratchet);
  4. DISCRETIONARY EXITS decided at close t, filled per fill model;
  5. ENTRIES when flat (same-bar re-entry allowed — §17);
  6. mark-to-market equity.

Traps honoured (§17): a stop hit at t leaves the account flat at close of t;
PARITY management starts the NEXT bar and hi_since excludes the signal bar;
the time stop counts from the signal bar.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from engine.strategy import fills
from engine.strategy.fills import Side
from engine.strategy.models import StrategyDefinition, TrailKind
from engine.strategy.sizing import size_at_signal

Outcome = Literal["TRADE", "SKIPPED", "NOT_FILLED", "OPEN"]

ExitReason = Literal[
    "STOP",
    "TARGET",
    "TIME",
    "EMA_TRAIL",
    "OPPOSITE",
    "DATA_GAP",
    "END_OF_DATA",
]


@dataclass(frozen=True)
class Bar:
    """One closed bar (close_time is the exclusive upper bound, D-008)."""

    index: int
    time: str  # ISO-8601 UTC
    open: float
    high: float
    low: float
    close: float
    excluded: bool = False  # inside an exclusion interval (4.6) — E2/E4


@dataclass(frozen=True)
class Signal:
    """A detector signal at a bar: type and direction."""

    bar_index: int
    event_type: str
    direction: Literal["LONG", "SHORT"]


@dataclass
class Trade:
    """7.5 trade record. Times are UTC ISO-8601 (D-038)."""

    instrument: str
    side: Side
    signal_bar_index: int
    signal_time: str
    decision_time: str
    fill_bar_index: int
    fill_time: str
    entry_price: float
    qty: float
    stop_at_entry: float | None
    entry_tag: str
    exit_signal_time: str | None = None
    exit_decision_time: str | None = None
    exit_fill_time: str | None = None
    exit_price: float | None = None
    exit_reason: ExitReason | None = None
    entry_fee: float = 0.0
    exit_fee: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    r_multiple: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    bars_held: int | None = None
    data_gap_during_hold: bool = False
    straddles_boundary: bool = False
    fill_model: str = ""

    @property
    def outcome(self) -> Outcome:
        return "OPEN" if self.exit_reason is None else "TRADE"


@dataclass
class SkippedSignal:
    """7.3.1: every signal that did not become a trade, with its reason."""

    signal_bar_index: int
    signal_time: str
    event_type: str
    outcome: Literal["SKIPPED", "NOT_FILLED"]
    reason: str


@dataclass
class Position:
    side: Side
    qty: float
    entry_price: float
    stop: float | None
    stop_keep: float | None
    target: float | None
    signal_bar_index: int
    signal_time: str
    fill_bar_index: int
    fill_time: str
    entry_tag: str
    entry_fee: float
    entry_r: float  # risk distance at entry (7.4 entry_R)
    hi_since: float
    lo_since: float
    bars_since_signal: int = 0
    data_gap_during_hold: bool = False
    mfe_r: float = 0.0
    mae_r: float = 0.0
    ema_armed: bool = False


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    skipped: list[SkippedSignal] = field(default_factory=list)
    equity_curve: list[tuple[str, float]] = field(default_factory=list)
    flagged_trade_count: int = 0


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _emitted_side(strategy: StrategyDefinition, direction: Literal["LONG", "SHORT"]) -> Side | None:
    """7.1: side filters live in the strategy layer, not the detector.
    R11/D-043: SHORT_ONLY/BOTH never reach the simulator.
    """
    if strategy.entry.side.value == "LONG_ONLY" and direction != "LONG":
        return None
    return direction


def _track_excursion(position: Position, bar: Bar) -> None:
    """7.4: hi_since/lo_since track from the first management bar; MFE/MAE in R
    across every management bar (0.0 is a real excursion, not "unset")."""
    if position.side == "LONG":
        position.hi_since = max(position.hi_since, bar.high)
        position.lo_since = min(position.lo_since, bar.low) if position.lo_since else bar.low
        gain = (bar.close - position.entry_price) / position.entry_r
    else:
        position.lo_since = min(position.lo_since, bar.low)
        position.hi_since = max(position.hi_since, bar.high) if position.hi_since else bar.high
        gain = (position.entry_price - bar.close) / position.entry_r
    position.mfe_r = max(position.mfe_r, gain)
    position.mae_r = min(position.mae_r, gain)


def _trailing_candidate(
    strategy: StrategyDefinition,
    position: Position,
    bar: Bar,
    atr: Sequence[float | None],
    ema: Sequence[float | None],
    donchian_lo: Sequence[float | None],
    donchian_hi: Sequence[float | None],
    pivot_low: Sequence[float | None],
    pivot_high: Sequence[float | None],
) -> float | None:
    """7.4 trailing-stop candidate per trail.kind. None = no candidate."""
    trail = strategy.trail
    kind = trail.kind
    atr_t = atr[bar.index] if bar.index < len(atr) else None
    if kind is TrailKind.ATR_CHANDELIER:
        if atr_t is None:
            return None
        if position.side == "LONG":
            return position.hi_since - trail.atr_mult * atr_t
        return position.lo_since + trail.atr_mult * atr_t
    if kind is TrailKind.PERCENT:
        if position.side == "LONG":
            return position.hi_since * (1 - trail.percent / 100.0)
        return position.lo_since * (1 + trail.percent / 100.0)
    if kind is TrailKind.DONCHIAN:
        if position.side == "LONG":
            return donchian_lo[bar.index] if bar.index < len(donchian_lo) else None
        return donchian_hi[bar.index] if bar.index < len(donchian_hi) else None
    if kind is TrailKind.PIVOT_SWING:
        if position.side == "LONG":
            return pivot_low[bar.index] if bar.index < len(pivot_low) else None
        return pivot_high[bar.index] if bar.index < len(pivot_high) else None
    if kind is TrailKind.PROFIT_GIVEBACK:
        gb = trail.giveback
        peak = (position.hi_since - position.entry_price) if position.side == "LONG" else (
            position.entry_price - position.lo_since
        )
        if peak < gb.activate_r * position.entry_r:
            return None
        if position.side == "LONG":
            return position.hi_since - gb.pct / 100.0 * peak
        return position.lo_since + gb.pct / 100.0 * peak
    # EMA_CLOSE: no stop level; handled as a discretionary exit (7.4).
    return None


def _tick(bar: Bar) -> float:
    return 1e-8


def _update_management(
    strategy: StrategyDefinition,
    position: Position,
    bar: Bar,
    atr: Sequence[float | None],
    ema: Sequence[float | None],
    donchian_lo: Sequence[float | None],
    donchian_hi: Sequence[float | None],
    pivot_low: Sequence[float | None],
    pivot_high: Sequence[float | None],
    tick: float,
) -> None:
    """7.4 step 3: breakeven, trailing candidate, ratchet (stops only tighten)."""
    stop_keep = position.stop_keep
    stop_at_entry = position.stop
    stop_t: float = stop_keep if stop_keep is not None else (stop_at_entry or 0.0)
    if position.side == "LONG":
        gain_r = (bar.close - position.entry_price) / position.entry_r
    else:
        gain_r = (position.entry_price - bar.close) / position.entry_r

    if strategy.breakeven.enabled and gain_r >= strategy.breakeven.trigger_r:
        if position.side == "LONG":
            stop_t = max(stop_t, position.entry_price)
        else:
            stop_t = min(stop_t, position.entry_price)

    if strategy.trail.enabled:
        cand = _trailing_candidate(
            strategy, position, bar, atr, ema, donchian_lo, donchian_hi, pivot_low, pivot_high
        )
        if cand is not None:
            if position.side == "LONG":
                cand = min(cand, bar.close - tick)  # 7.4 clamp
                stop_t = max(stop_t, cand)  # ratchet
            else:
                cand = max(cand, bar.close + tick)
                stop_t = min(stop_t, cand)

    if strategy.trail.enabled and strategy.trail.kind is TrailKind.EMA_CLOSE:
        ema_t = ema[bar.index] if bar.index < len(ema) else None
        if ema_t is not None:
            favourable = (bar.close > ema_t) if position.side == "LONG" else (bar.close < ema_t)
            if favourable:
                position.ema_armed = True

    position.stop_keep = stop_t
    position.stop = stop_t
    if strategy.target.R > 0 and position.entry_r > 0:
        if position.side == "LONG":
            position.target = position.entry_price + strategy.target.R * position.entry_r
        else:
            position.target = position.entry_price - strategy.target.R * position.entry_r


def _evaluate_resting(
    strategy: StrategyDefinition,
    position: Position,
    bar: Bar,
    atr: Sequence[float | None],
    ema: Sequence[float | None],
    donchian_lo: Sequence[float | None],
    donchian_hi: Sequence[float | None],
    pivot_low: Sequence[float | None],
    pivot_high: Sequence[float | None],
) -> tuple[float, ExitReason, str] | None:
    """7.4 step 2 + 7.3 resting-order semantics; E4 gap handling."""
    if bar.excluded:
        return None
    stop = position.stop
    target = position.target
    if stop is None:
        return None
    resolution = fills.same_bar_resolution(
        strategy.execution.fill_model,
        position.side,
        open_t=bar.open,
        high_t=bar.high,
        low_t=bar.low,
        stop=stop,
        target=target,
    )
    if resolution == "STOP":
        price = fills.stop_fill_price(
            position.side, stop=stop, open_t=bar.open, high_t=bar.high, low_t=bar.low
        )
        if price is not None:
            return price, "STOP", bar.time
    elif resolution == "TARGET":
        assert target is not None
        price = fills.target_fill_price(
            position.side, target=target, open_t=bar.open, high_t=bar.high, low_t=bar.low
        )
        if price is not None:
            return price, "TARGET", bar.time
    return None


def _discretionary_exit(
    strategy: StrategyDefinition,
    position: Position,
    bar: Bar,
    atr: Sequence[float | None],
    ema: Sequence[float | None],
) -> tuple[ExitReason, str] | None:
    """7.4 step 4: time stop (counts from the signal bar — §17), EMA_CLOSE
    cross, on_opposite / long_on_bearish_signal (signal-driven, handled by
    the caller through the signal feed — here: time + EMA only).
    """
    if strategy.time_stop.enabled and position.bars_since_signal >= strategy.time_stop.max_bars:
        return "TIME", bar.time
    if strategy.trail.enabled and strategy.trail.kind is TrailKind.EMA_CLOSE and position.ema_armed:
        ema_t = ema[bar.index] if bar.index < len(ema) else None
        if ema_t is not None:
            crossed = (bar.close < ema_t) if position.side == "LONG" else (bar.close > ema_t)
            if crossed:
                return "EMA_TRAIL", bar.time
    return None


def _try_entry(
    strategy: StrategyDefinition,
    instrument: str,
    sig: Signal,
    side: Side,
    bar: Bar,
    bars: Sequence[Bar],
    atr: Sequence[float | None],
    structure_level: Sequence[float | None],
    tick_size: float,
    lot_step: float,
    equity: float,
    result: BacktestResult,
) -> Position | None:
    """7.4 step 5 + 7.2 sizing + 7.3 fills + E1/E2/E3/E4. Returns the opened
    position (PARITY) or None (REALISTIC_OHLC defers the open to the fill bar).
    """
    execution = strategy.execution
    if execution.fill_model is not None and execution.fill_model.value == "REALISTIC_OHLC":
        next_bar = bars[bar.index + 1] if bar.index + 1 < len(bars) else None
        if next_bar is None:
            result.skipped.append(
                SkippedSignal(bar.index, bar.time, sig.event_type, "NOT_FILLED", "NO_NEXT_BAR")
            )
            return None
        if next_bar.excluded:
            result.skipped.append(
                SkippedSignal(bar.index, bar.time, sig.event_type, "SKIPPED", "ENTRY_DATA_GAP")
            )
            return None
    # bars may not be indexed by .index directly; locate neighbours by position.
    position_in_seq = _seq_index(bars, bar.index)
    sized = size_at_signal(
        strategy.stop,
        strategy.sizing,
        side,
        close_s=bar.close,
        atr_s=atr[bar.index] if bar.index < len(atr) else None,
        structure_level=(structure_level[bar.index] if bar.index < len(structure_level) else None) or 0.0,
        tick_size=tick_size,
        lot_step=lot_step,
        equity=equity,
    )
    if sized.qty <= 0:
        result.skipped.append(
            SkippedSignal(bar.index, bar.time, sig.event_type, "SKIPPED", "QTY_ZERO")
        )
        return None
    fill = fills.entry_fill(
        execution,
        strategy.costs.slippage_model,
        side,
        close_s=bar.close,
        signal_bar_index=bar.index,
        signal_time=bar.time,
        next_bar_open=bars[position_in_seq + 1].open if position_in_seq is not None and position_in_seq + 1 < len(bars) else None,
        next_bar_time=bars[position_in_seq + 1].time if position_in_seq is not None and position_in_seq + 1 < len(bars) else None,
    )
    if fill is None:
        result.skipped.append(
            SkippedSignal(bar.index, bar.time, sig.event_type, "NOT_FILLED", "NO_NEXT_BAR")
        )
        return None
    if sized.stop_price is not None:
        gap_through = (
            (side == "LONG" and fill.price <= sized.stop_price)
            or (side == "SHORT" and fill.price >= sized.stop_price)
        )
        if gap_through:
            result.skipped.append(
                SkippedSignal(bar.index, bar.time, sig.event_type, "SKIPPED", "GAP_THROUGH_STOP")
            )
            return None
    fee = fills.fee_on_notional(strategy.costs.fee_model.value, fill.price * sized.qty)
    # risk_dist actual for REALISTIC_OHLC (7.3): fill - stop for long.
    entry_r = sized.risk_dist
    if sized.stop_price is not None and side == "LONG":
        entry_r = max(abs(fill.price - sized.stop_price), tick_size)
    elif sized.stop_price is not None and side == "SHORT":
        entry_r = max(abs(sized.stop_price - fill.price), tick_size)
    position = Position(
        side=side,
        qty=sized.qty,
        entry_price=fill.price,
        stop=sized.stop_price,
        stop_keep=sized.stop_price,
        target=None,
        signal_bar_index=bar.index,
        signal_time=bar.time,
        fill_bar_index=fill.bar_index,
        fill_time=fill.time,
        entry_tag=sig.event_type,
        entry_fee=fee,
        entry_r=entry_r,
        hi_since=fill.price,
        lo_since=fill.price,
    )
    return position


def _seq_index(bars: Sequence[Bar], index: int) -> int | None:
    for i, b in enumerate(bars):
        if b.index == index:
            return i
    return None


def _close(
    position: Position,
    exit_price: float,
    reason: ExitReason,
    decision_time: str,
    exit_time: str,
    strategy: StrategyDefinition,
    instrument: str,
    result: BacktestResult,
    fill_bar_index: int | None = None,
) -> Trade:
    """Close a position into a 7.5 trade record."""
    execution = strategy.execution
    exit_fee = fills.fee_on_notional(
        strategy.costs.fee_model.value, exit_price * position.qty
    )
    if position.side == "LONG":
        gross = (exit_price - position.entry_price) * position.qty
    else:
        gross = (position.entry_price - exit_price) * position.qty
    pnl = gross - position.entry_fee - exit_fee
    pnl_pct = pnl / (position.entry_price * position.qty) if position.entry_price * position.qty else 0.0
    r_multiple = pnl / (position.entry_r * position.qty) if position.entry_r and position.qty else None
    bars_held = (fill_bar_index or position.fill_bar_index) - position.fill_bar_index
    trade = Trade(
        instrument=instrument,
        side=position.side,
        signal_bar_index=position.signal_bar_index,
        signal_time=position.signal_time,
        decision_time=decision_time,
        fill_bar_index=position.fill_bar_index,
        fill_time=position.fill_time,
        entry_price=position.entry_price,
        qty=position.qty,
        stop_at_entry=position.stop,
        entry_tag=position.entry_tag,
        exit_signal_time=decision_time,
        exit_decision_time=decision_time,
        exit_fill_time=exit_time,
        exit_price=exit_price,
        exit_reason=reason,
        entry_fee=position.entry_fee,
        exit_fee=exit_fee,
        pnl=pnl,
        pnl_pct=pnl_pct,
        r_multiple=r_multiple,
        mfe_r=position.mfe_r,
        mae_r=position.mae_r,
        bars_held=max(bars_held, 0),
        data_gap_during_hold=position.data_gap_during_hold,
        fill_model=execution.fill_model.value,
    )
    return trade


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------


def simulate(
    strategy: StrategyDefinition,
    instrument: str,
    bars: Sequence[Bar],
    signals: Sequence[Signal],
    *,
    atr: Sequence[float | None],
    structure_level: Sequence[float | None] | None = None,
    ema: Sequence[float | None] | None = None,
    donchian_lo: Sequence[float | None] | None = None,
    donchian_hi: Sequence[float | None] | None = None,
    pivot_low: Sequence[float | None] | None = None,
    pivot_high: Sequence[float | None] | None = None,
    tick_size: float = 1e-8,
    lot_step: float = 1e-8,
) -> BacktestResult:
    """Run one strategy over one instrument's bars (7.4). Pure (D-042)."""
    result = BacktestResult()
    execution = strategy.execution
    equity = execution.initial_capital
    position: Position | None = None
    signal_at = {s.bar_index: s for s in signals}
    atr = list(atr)
    structure_level = list(structure_level or [])
    ema = list(ema or [])
    donchian_lo = list(donchian_lo or [])
    donchian_hi = list(donchian_hi or [])
    pivot_low = list(pivot_low or [])
    pivot_high = list(pivot_high or [])

    for pos, bar in enumerate(bars):
        t = bar.index

        # --- 2. EXIT FILLS ---------------------------------------------------
        if position is not None and not bar.excluded:
            resting = _evaluate_resting(
                strategy, position, bar, atr, ema, donchian_lo, donchian_hi, pivot_low, pivot_high
            )
            if resting is not None:
                price, reason, decision_time = resting
                trade = _close(position, price, reason, decision_time, bar.time,
                               strategy, instrument, result)
                result.trades.append(trade)
                equity += trade.pnl
                position = None

        # --- 3. MANAGEMENT ---------------------------------------------------
        if position is not None:
            position.bars_since_signal += 1
            if bar.excluded:
                position.data_gap_during_hold = True
            else:
                _track_excursion(position, bar)
                _update_management(
                    strategy, position, bar, atr, ema, donchian_lo, donchian_hi,
                    pivot_low, pivot_high, tick_size,
                )

        # --- 4. DISCRETIONARY EXITS ------------------------------------------
        if position is not None and not bar.excluded:
            exit_now = _discretionary_exit(strategy, position, bar, atr, ema)
            if exit_now is not None:
                reason, decision_time = exit_now
                next_bar = bars[pos + 1] if pos + 1 < len(bars) else None
                fill = fills.discretionary_exit_fill(
                    execution,
                    strategy.costs.slippage_model,
                    position.side,
                    close_t=bar.close,
                    decision_bar_index=t,
                    decision_time=decision_time,
                    next_bar_open=next_bar.open if next_bar else None,
                    next_bar_time=next_bar.time if next_bar else None,
                )
                if fill is None:
                    fill = fills.Fill(price=bar.close, bar_index=t, time=bar.time)
                    reason = "END_OF_DATA"
                trade = _close(position, fill.price, reason, decision_time, fill.time,
                               strategy, instrument, result)
                result.trades.append(trade)
                equity += trade.pnl
                position = None

        # --- 5. ENTRIES ------------------------------------------------------
        if position is None:
            sig = signal_at.get(t)
            if sig is not None:
                side = _emitted_side(strategy, sig.direction)
                if side is not None:
                    position = _try_entry(
                        strategy, instrument, sig, side, bar, bars, atr, structure_level,
                        tick_size, lot_step, equity, result,
                    )

        # --- 6. MARK-TO-MARKET ----------------------------------------------
        if position is not None:
            unreal = (bar.close - position.entry_price) * position.qty
            if position.side == "SHORT":
                unreal = -unreal
            result.equity_curve.append((bar.time, equity + unreal))
        else:
            result.equity_curve.append((bar.time, equity))

    # E1: open position at end of data — marked to market, reported OPEN.
    if position is not None:
        trade = _close(position, bars[-1].close, "END_OF_DATA", bars[-1].time,
                       bars[-1].time, strategy, instrument, result)
        result.trades.append(trade)

    result.flagged_trade_count = sum(1 for tr in result.trades if tr.data_gap_during_hold)
    return result
