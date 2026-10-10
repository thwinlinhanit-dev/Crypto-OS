"""M6 T6.1 — fill-model tests (spec 7.3, 7.3.1 E5).

PARITY fills at the signal bar's CLOSE (D-039); REALISTIC_OHLC at the next
bar's OPEN with adverse slippage. Resting stops fill at the stop price, or
at OPEN on a gap. Same-bar stop+target: REALISTIC_OHLC = stop first; PARITY
= the broker-emulator path heuristic.
"""

from __future__ import annotations

import pytest

from engine.strategy import fills
from engine.strategy.models import Execution, FillModel, SlippageModel


def _execution(**kw: object) -> Execution:
    return Execution.model_validate({"fill_model": kw.pop("fill_model", "REALISTIC_OHLC"),
                                     **kw})


def _slippage(value: float = 5.0) -> SlippageModel:
    return SlippageModel.model_validate({"kind": "FIXED_BPS", "value": value})


def test_parity_entry_fills_at_signal_close() -> None:
    """7.3/D-039: PARITY entry_price = close_s; fill_time = signal_time."""
    fill = fills.entry_fill(_execution(fill_model="PARITY"), _slippage(), "LONG",
                            close_s=100.0, signal_bar_index=5, signal_time="2026-01-01T00:00:00Z",
                            next_bar_open=101.0, next_bar_time="2026-01-02T00:00:00Z")
    assert fill is not None
    assert fill.price == 100.0
    assert fill.bar_index == 5
    assert fill.time == "2026-01-01T00:00:00Z"


def test_realistic_entry_fills_at_next_open_with_adverse_slippage() -> None:
    """7.3: REALISTIC_OHLC fill bar = signal+1+delay, OPEN + adverse slippage."""
    fill = fills.entry_fill(_execution(fill_model="REALISTIC_OHLC", delay_bars=0), _slippage(5),
                            "LONG", close_s=100.0, signal_bar_index=5,
                            signal_time="2026-01-01T00:00:00Z",
                            next_bar_open=100.0, next_bar_time="2026-01-02T00:00:00Z")
    assert fill is not None
    assert fill.price == pytest.approx(100.05)  # 100 + 5bps
    assert fill.bar_index == 6
    assert fill.time == "2026-01-02T00:00:00Z"


def test_realistic_entry_short_slippage_is_adverse() -> None:
    fill = fills.entry_fill(_execution(fill_model="REALISTIC_OHLC"), _slippage(5), "SHORT",
                            close_s=100.0, signal_bar_index=5, signal_time="t0",
                            next_bar_open=100.0, next_bar_time="t1")
    assert fill is not None
    assert fill.price == pytest.approx(99.95)


def test_no_next_bar_returns_none_e1() -> None:
    """E1: no next bar -> None (caller records NOT_FILLED/NO_NEXT_BAR)."""
    fill = fills.entry_fill(_execution(fill_model="REALISTIC_OHLC"), _slippage(), "LONG",
                            close_s=100.0, signal_bar_index=5, signal_time="t0",
                            next_bar_open=None, next_bar_time=None)
    assert fill is None


def test_delay_bars_shift_the_fill_bar() -> None:
    fill = fills.entry_fill(_execution(fill_model="REALISTIC_OHLC", delay_bars=2), _slippage(),
                            "LONG", close_s=100.0, signal_bar_index=5, signal_time="t0",
                            next_bar_open=100.0, next_bar_time="t1")
    assert fill is not None
    assert fill.bar_index == 8


def test_parity_discretionary_exit_at_decision_close() -> None:
    fill = fills.discretionary_exit_fill(_execution(fill_model="PARITY"), _slippage(), "LONG",
                                         close_t=110.0, decision_bar_index=9,
                                         decision_time="t9", next_bar_open=111.0,
                                         next_bar_time="t10")
    assert fill is not None
    assert fill.price == 110.0
    assert fill.bar_index == 9


def test_realistic_discretionary_exit_at_next_open() -> None:
    """A long exit at the next open takes adverse slippage UP (7.1)."""
    fill = fills.discretionary_exit_fill(_execution(fill_model="REALISTIC_OHLC"), _slippage(5),
                                         "LONG", close_t=110.0, decision_bar_index=9,
                                         decision_time="t9", next_bar_open=110.0,
                                         next_bar_time="t10")
    assert fill is not None
    assert fill.price == pytest.approx(110.055)
    assert fill.bar_index == 10


def test_stop_fill_at_stop_price_when_touched() -> None:
    price = fills.stop_fill_price("LONG", stop=95.0, open_t=100.0, high_t=101.0, low_t=94.0)
    assert price == pytest.approx(95.0)


def test_stop_gap_through_fills_at_open() -> None:
    """7.3: gap through the stop -> fill at OPEN, not the stop."""
    price = fills.stop_fill_price("LONG", stop=95.0, open_t=93.0, high_t=94.0, low_t=92.0)
    assert price == pytest.approx(93.0)


def test_stop_not_touched_returns_none() -> None:
    assert fills.stop_fill_price("LONG", stop=90.0, open_t=100.0, high_t=101.0,
                                 low_t=99.0) is None


def test_stop_mirrored_for_short() -> None:
    assert fills.stop_fill_price("SHORT", stop=105.0, open_t=100.0, high_t=106.0,
                                 low_t=99.0) == pytest.approx(105.0)
    assert fills.stop_fill_price("SHORT", stop=105.0, open_t=106.0, high_t=107.0,
                                 low_t=100.0) == pytest.approx(106.0)  # gap up


def test_target_fills_at_limit_or_open() -> None:
    assert fills.target_fill_price("LONG", target=110.0, open_t=100.0, high_t=111.0,
                                   low_t=99.0) == pytest.approx(110.0)
    assert fills.target_fill_price("LONG", target=110.0, open_t=112.0, high_t=113.0,
                                   low_t=111.0) == pytest.approx(112.0)  # gapped favourably


def test_e5_realistic_stop_first() -> None:
    """7.3.1 E5: REALISTIC_OHLC same-bar stop+target -> STOP first."""
    resolution = fills.same_bar_resolution(
        FillModel.REALISTIC_OHLC, "LONG", open_t=100.0, high_t=112.0, low_t=94.0,
        stop=95.0, target=110.0,
    )
    assert resolution == "STOP"


def test_e5_parity_path_heuristic() -> None:
    """7.3: PARITY — high closer to open than low -> O->H->L->C (target first)."""
    # |high-open| = 2 < |low-open| = 8 -> target first.
    assert fills.same_bar_resolution(
        FillModel.PARITY, "LONG", open_t=100.0, high_t=102.0, low_t=92.0,
        stop=95.0, target=101.0,
    ) == "TARGET"
    # low closer (|low-open|=3 < |high-open|=12) -> O->L->H->C (stop first);
    # both levels touched on the bar.
    assert fills.same_bar_resolution(
        FillModel.PARITY, "LONG", open_t=100.0, high_t=112.0, low_t=94.0,
        stop=96.0, target=110.0,
    ) == "STOP"


def test_e5_neither_touched_returns_none() -> None:
    assert fills.same_bar_resolution(
        FillModel.PARITY, "LONG", open_t=100.0, high_t=101.0, low_t=99.0,
        stop=95.0, target=110.0,
    ) is None


def test_fee_on_notional() -> None:
    """7.1: PERCENT fee — per side on notional (value is percent)."""
    assert fills.fee_on_notional(0.10, 10000.0) == pytest.approx(10.0)

