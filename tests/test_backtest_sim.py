"""M6 T6.1 — simulator tests (spec 7.4 order of operations, 7.5 trade record,
7.3.1 edge cases E1-E6, §17 traps).

Synthetic bars + signals; the simulator is pure (D-042), so these run with
no database. Every outcome is recorded — no signal disappears (M6 AC).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict

import pytest

from engine.strategy.hash import canonical_hash
from engine.strategy.models import StrategyDefinition
from engine.strategy.simulator import BacktestResult, Bar, Signal, simulate


def _strategy(**overrides: object) -> StrategyDefinition:
    body: dict[str, object] = {
        "strategy_id": "test",
        "version": "1.0",
        "entry": {"detector": "RVOL_CLIMAX@5.0", "params": {}, "signals": ["CONT"],
                  "side": "LONG_ONLY"},
        "sizing": {"mode": "RISK", "risk_pct": 1.0, "max_alloc_pct": 100.0},
        "stop": {"kind": "ATR", "atr_len": 14, "atr_mult": 2.0},
        "costs": {"fee_model": {"kind": "PERCENT", "value": 0.10},
                  "slippage_model": {"kind": "FIXED_BPS", "value": 0}},
        "execution": {"fill_model": "REALISTIC_OHLC", "initial_capital": 10000,
                      "delay_bars": 0},
    }
    body.update(overrides)
    return StrategyDefinition.model_validate(body)


def _bars(
    prices: list[tuple[float, float, float, float]], *, exclude: set[int] | None = None
) -> list[Bar]:
    """Build bars from (open, high, low, close) tuples; index starts at 0."""
    exclude = exclude or set()
    return [
        Bar(index=i, time=f"2026-01-{i + 1:02d}T00:00:00Z", open=o, high=h, low=lo,
            close=c, excluded=i in exclude)
        for i, (o, h, lo, c) in enumerate(prices)
    ]


def _atr(n: int, value: float | None) -> list[float | None]:
    return [value] * n


def _run(
    strategy: StrategyDefinition,
    bars: Sequence[Bar],
    signals: Sequence[Signal],
    *,
    tick_size: float = 1e-8,
    lot_step: float = 1e-8,
    structure_level: Sequence[float | None] | None = None,
    ema: Sequence[float | None] | None = None,
    donchian_lo: Sequence[float | None] | None = None,
    donchian_hi: Sequence[float | None] | None = None,
    pivot_low: Sequence[float | None] | None = None,
    pivot_high: Sequence[float | None] | None = None,
) -> BacktestResult:
    return simulate(strategy, "BTCUSDT", bars, signals,
                    atr=_atr(len(bars), 5.0),
                    structure_level=structure_level,
                    ema=ema,
                    donchian_lo=donchian_lo,
                    donchian_hi=donchian_hi,
                    pivot_low=pivot_low,
                    pivot_high=pivot_high,
                    tick_size=tick_size,
                    lot_step=lot_step)


# ---------------------------------------------------------------------------
# 7.4 per-bar order + 7.5 trade record
# ---------------------------------------------------------------------------


def test_entry_stop_and_exit_produce_one_trade() -> None:
    """A signal -> entry next bar -> stop hit -> one closed trade (STOP)."""
    strategy = _strategy()
    bars = _bars([
        (100, 101, 99, 100),    # 0 signal bar (close 100)
        (100, 100.5, 95, 96),   # 1 entry at open 100; stop = 100-2*5=90 not hit... adjust
    ])
    # stop = close_s - atr_mult*ATR = 100 - 2*5 = 90. Make bar 1 dip to 89.
    bars = _bars([
        (100, 101, 99, 100),
        (100, 100.5, 89, 90),   # entry at 100, stop 90 -> low 89 < 90 -> stop fills 90
        (90, 91, 88, 89),
    ])
    signals = [Signal(bar_index=0, event_type="CONT", direction="LONG")]
    result = _run(strategy, bars, signals)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "STOP"
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.exit_price == pytest.approx(90.0)
    assert trade.entry_tag == "CONT"
    assert trade.fill_model == "REALISTIC_OHLC"
    assert trade.signal_time == bars[0].time
    assert trade.signal_bar_index == 0
    assert trade.fill_bar_index == 1


def test_trade_carries_all_time_fields() -> None:
    """7.3: signal/fill/exit time fields are all present (D-038)."""
    strategy = _strategy()
    bars = _bars([
        (100, 101, 99, 100),
        (100, 100.5, 89, 90),
    ])
    signals = [Signal(bar_index=0, event_type="CONT", direction="LONG")]
    result = _run(strategy, bars, signals)
    trade = result.trades[0]
    assert trade.signal_bar_index == 0
    assert trade.fill_bar_index == 1
    assert trade.signal_time == "2026-01-01T00:00:00Z"
    assert trade.fill_time == "2026-01-02T00:00:00Z"
    assert trade.exit_signal_time is not None
    assert trade.exit_fill_time is not None
    assert trade.exit_decision_time is not None


def test_pnl_and_r_multiple_computed() -> None:
    """7.5: pnl, pnl_pct, r_multiple, mfe_r, mae_r, bars_held recorded."""
    strategy = _strategy()
    bars = _bars([
        (100, 101, 99, 100),
        (100, 100.5, 99, 100.4),  # entry 100; management starts here
        (100, 101, 89, 90),       # stop 90 hit -> exit
    ])
    signals = [Signal(bar_index=0, event_type="CONT", direction="LONG")]
    result = _run(strategy, bars, signals)
    trade = result.trades[0]
    # entry 100, stop 90 -> R = 10/unit; loss of 10/unit gross, minus fees.
    assert trade.r_multiple is not None
    assert trade.r_multiple < 0
    assert trade.pnl < 0
    assert trade.bars_held is not None
    assert trade.mae_r is not None  # saw at least one management bar


def test_long_only_filters_short_signals() -> None:
    """7.1: side filters live in the strategy layer."""
    strategy = _strategy()
    bars = _bars([(100, 101, 99, 100), (100, 101, 99, 100)])
    signals = [Signal(bar_index=0, event_type="FAIL", direction="SHORT")]
    result = _run(strategy, bars, signals)
    assert result.trades == []
    assert result.skipped == []  # filtered at the strategy layer, not recorded as skipped


def test_same_bar_reentry_after_stop() -> None:
    """§17 trap: a stop hit at t leaves the account flat at close of t, so a
    signal at t can re-enter."""
    strategy = _strategy()
    bars = _bars([
        (100, 101, 99, 100),    # 0: signal 1
        (100, 100.5, 89, 90),   # 1: entry at 100; stop 90 touched -> exit STOP
        (90, 95, 89, 94),       # 2: signal 2 at close 94 -> entry next bar
        (94, 94.5, 92, 93),     # 3: entry at 94
    ])
    signals = [Signal(bar_index=0, event_type="CONT", direction="LONG"),
               Signal(bar_index=2, event_type="CONT", direction="LONG")]
    result = _run(strategy, bars, signals)
    assert len(result.trades) >= 1
    assert result.trades[0].exit_reason == "STOP"


# ---------------------------------------------------------------------------
# 7.3.1 edge cases — no signal disappears
# ---------------------------------------------------------------------------


def test_e1_no_next_bar_not_filled() -> None:
    """E1: no next bar -> NOT_FILLED/NO_NEXT_BAR; never filled later."""
    strategy = _strategy()
    bars = _bars([(100, 101, 99, 100)])
    signals = [Signal(bar_index=0, event_type="CONT", direction="LONG")]
    result = _run(strategy, bars, signals)
    assert result.trades == []
    assert len(result.skipped) == 1
    assert result.skipped[0].outcome == "NOT_FILLED"
    assert result.skipped[0].reason == "NO_NEXT_BAR"


def test_e2_entry_data_gap_skipped() -> None:
    """E2: fill bar excluded -> SKIPPED/ENTRY_DATA_GAP; never filled later."""
    strategy = _strategy()
    bars = _bars([
        (100, 101, 99, 100),
        (100, 100.5, 95, 96),   # excluded
        (96, 97, 90, 91),
    ], exclude={1})
    signals = [Signal(bar_index=0, event_type="CONT", direction="LONG")]
    result = _run(strategy, bars, signals)
    assert result.trades == []
    assert len(result.skipped) == 1
    assert result.skipped[0].outcome == "SKIPPED"
    assert result.skipped[0].reason == "ENTRY_DATA_GAP"


def test_e1_open_position_at_end_of_data() -> None:
    """E1: open position at end of data is marked to market, reported OPEN,
    excluded from closed-trade stats."""
    strategy = _strategy()
    bars = _bars([
        (100, 101, 99, 100),
        (100, 100.5, 99, 100.4),  # entry 100, no stop hit (stop 90)
    ])
    signals = [Signal(bar_index=0, event_type="CONT", direction="LONG")]
    result = _run(strategy, bars, signals)
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "END_OF_DATA"


def test_qty_zero_skipped() -> None:
    """7.2: qty<=0 -> skip (lot rounding)."""
    strategy = _strategy(sizing={"mode": "RISK", "risk_pct": 0.0001, "max_alloc_pct": 0.001})
    bars = _bars([
        (100, 101, 99, 100),
        (100, 101, 99, 100),
    ])
    signals = [Signal(bar_index=0, event_type="CONT", direction="LONG")]
    result = _run(strategy, bars, signals, lot_step=1.0)
    assert result.trades == []
    assert len(result.skipped) == 1
    assert result.skipped[0].reason == "QTY_ZERO"


def test_target_exit_recorded() -> None:
    """7.4 step 2: target fill at the limit."""
    strategy = _strategy(target={"R": 1.0})
    # entry 100, R=10 -> target 110. stop 90.
    bars = _bars([
        (100, 101, 99, 100),
        (100, 100.5, 99, 100.4),   # entry at 100; target armed at 110
        (100, 111, 100, 110.5),    # high 111 >= 110 -> target fills 110
    ])
    signals = [Signal(bar_index=0, event_type="CONT", direction="LONG")]
    result = _run(strategy, bars, signals)
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "TARGET"
    assert result.trades[0].exit_price == pytest.approx(110.0)


def test_time_stop_counts_from_signal_bar() -> None:
    """§17 trap: the time stop counts from the signal bar, not the fill."""
    strategy = _strategy(time_stop={"enabled": True, "max_bars": 2})
    bars = _bars([
        (100, 101, 99, 100),    # 0 signal
        (100, 100.5, 99, 100),  # 1 entry
        (100, 101, 99, 100),    # 2 bars_since_signal=2 -> time stop
        (100, 101, 99, 100),
    ])
    signals = [Signal(bar_index=0, event_type="CONT", direction="LONG")]
    result = _run(strategy, bars, signals)
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "TIME"


def test_breakeven_moves_stop_to_entry() -> None:
    """7.4: breakeven at trigger_R moves the stop to entry (ratchet)."""
    strategy = _strategy(breakeven={"enabled": True, "trigger_R": 1.0})
    bars = _bars([
        (100, 101, 99, 100),     # 0 signal; stop = 90, R = 10
        (100, 110, 99.5, 109),   # 1 entry 100; close 109 -> gain_R ~0.9 (below 1)
        (109, 111, 100, 110),    # 2 gain_R = 1.0 -> breakeven -> stop 100
        (110, 110.5, 99, 100.5), # 3 low 100 <= stop 100 -> exit STOP at 100
    ])
    signals = [Signal(bar_index=0, event_type="CONT", direction="LONG")]
    result = _run(strategy, bars, signals)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "STOP"
    # stop ratcheted to entry 100 -> exit near breakeven, not -1R.
    assert trade.exit_price == pytest.approx(100.0)
    assert trade.r_multiple is not None
    assert trade.r_multiple > -0.2


def test_atr_chandelier_trail_tightens_stop() -> None:
    """7.4: ATR_CHANDELIER ratchets the stop up only."""
    strategy = _strategy(
        trail={"enabled": True, "kind": "ATR_CHANDELIER", "atr_mult": 1.0},
    )
    # stop starts 90; hi_since tracks the max high; chandelier = hi - 1*ATR(5).
    bars = _bars([
        (100, 101, 99, 100),     # 0 signal
        (100, 105, 99.5, 104),   # 1 entry 100; hi=105 -> chand 100 -> stop 100
        (104, 108, 103, 107),    # 2 hi=108 -> chand 103 -> stop 103
        (107, 107.5, 102, 103),  # 3 low 102 < 103 -> exit STOP at 103
    ])
    signals = [Signal(bar_index=0, event_type="CONT", direction="LONG")]
    result = _run(strategy, bars, signals)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "STOP"
    assert trade.exit_price == pytest.approx(103.0)  # ratcheted, not the initial 90


def test_data_gap_during_hold_flags_trade() -> None:
    """E4: a gap while the position is open flags the trade."""
    strategy = _strategy()
    bars = _bars([
        (100, 101, 99, 100),     # 0 signal
        (100, 100.5, 99, 100.4), # 1 entry 100
        (100, 100.5, 99, 100.2), # 2 excluded bar
        (100, 100.5, 99, 100.1), # 3 valid; stop 90 not hit
        (100, 100.5, 89, 90),    # 4 stop fills 90
    ], exclude={2})
    signals = [Signal(bar_index=0, event_type="CONT", direction="LONG")]
    result = _run(strategy, bars, signals)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.data_gap_during_hold is True
    assert result.flagged_trade_count == 1


def test_equity_curve_has_one_point_per_bar() -> None:
    """7.4 step 6: mark-to-market equity per bar."""
    strategy = _strategy()
    bars = _bars([(100, 101, 99, 100)] * 4)
    signals = [Signal(bar_index=0, event_type="CONT", direction="LONG")]
    result = _run(strategy, bars, signals)
    assert len(result.equity_curve) == 4
    assert all(len(point) == 2 for point in result.equity_curve)


# ---------------------------------------------------------------------------
# PARITY fill model
# ---------------------------------------------------------------------------


def test_parity_entry_fills_at_signal_close() -> None:
    """D-039/§17: PARITY entries fill at the signal bar's CLOSE."""
    strategy = _strategy(execution={"fill_model": "PARITY", "initial_capital": 10000})
    bars = _bars([
        (100, 101, 99, 100),     # 0 signal; entry fills at close 100
        (100, 100.5, 89, 90),    # 1 stop 90 touched -> exit
    ])
    signals = [Signal(bar_index=0, event_type="CONT", direction="LONG")]
    result = _run(strategy, bars, signals)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.fill_bar_index == 0  # filled on the signal bar itself
    assert trade.fill_model == "PARITY"


# ---------------------------------------------------------------------------
# reproducibility (M6 AC)
# ---------------------------------------------------------------------------


def test_same_inputs_byte_identical_trades() -> None:
    """M6 AC: reproducibility — same inputs => byte-identical trade JSON."""
    strategy = _strategy()
    bars = _bars([
        (100, 101, 99, 100),
        (100, 100.5, 99, 100.4),
        (100, 111, 99, 110),
    ])
    signals = [Signal(bar_index=0, event_type="CONT", direction="LONG")]
    r1 = _run(strategy, bars, signals)
    r2 = _run(strategy, bars, signals)
    assert canonical_hash([asdict(t) for t in r1.trades]) == canonical_hash(
        [asdict(t) for t in r2.trades]
    )
