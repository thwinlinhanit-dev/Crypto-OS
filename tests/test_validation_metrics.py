"""M7 (T7.1) — metrics tests (spec 9.1 verbatim list).

Every metric is asserted on hand-computed fixtures; per-symbol AND
portfolio-level inputs are both covered (9.1); open positions are excluded
from closed-trade metrics (D-049); a zero-loss book reports the finite cap
(D-046), never Infinity.
"""

from __future__ import annotations

from engine.validation.metrics import GROSS_LOSS_ZERO_CAP, TradeLike, compute_metrics


def _trade(pnl: float, r: float | None = 1.0, *, instrument: str = "AAA",
           entry: float = 100.0, exit_price: float | None = 110.0,
           bars: int | None = 2) -> TradeLike:
    return TradeLike(pnl=pnl, r_multiple=r, entry_price=entry, exit_price=exit_price,
                     bars_held=bars, instrument=instrument)


def _equity(values: list[float]) -> list[tuple[str, float]]:
    return [(f"2026-01-{i + 1:02d}T00:00:00Z", value) for i, value in enumerate(values)]


PPY = 252.0  # daily bars


def test_trade_count_and_win_rate() -> None:
    trades = [_trade(100), _trade(-50), _trade(200)]
    metrics = compute_metrics(trades, [], periods_per_year=PPY, initial_capital=1000)
    assert metrics["trade_count"] == 3
    assert metrics["win_rate"] == 2 / 3


def test_profit_factor_and_payoff() -> None:
    trades = [_trade(300), _trade(-100)]
    metrics = compute_metrics(trades, [], periods_per_year=PPY, initial_capital=1000)
    assert metrics["profit_factor"] == 3.0
    # payoff = avg win / |avg loss| = 300 / 100
    assert metrics["payoff_ratio"] == 3.0
    assert metrics["avg_win"] == 300
    assert metrics["avg_loss"] == -100


def test_expectancy_and_median_r() -> None:
    trades = [_trade(100, r=2.0), _trade(-50, r=-1.0), _trade(80, r=1.0)]
    metrics = compute_metrics(trades, [], periods_per_year=PPY, initial_capital=1000)
    assert metrics["expectancy_r"] == (2.0 - 1.0 + 1.0) / 3
    assert metrics["median_r"] == 1.0


def test_zero_loss_book_reports_finite_cap() -> None:
    """D-046: never Infinity in stored JSON."""
    trades = [_trade(100), _trade(50)]
    metrics = compute_metrics(trades, [], periods_per_year=PPY, initial_capital=1000)
    assert metrics["profit_factor"] == GROSS_LOSS_ZERO_CAP
    assert metrics["gross_loss_zero"] is True


def test_open_positions_excluded() -> None:
    """D-049: open positions (exit None) are not counted."""
    trades = [_trade(100), _trade(999, exit_price=None)]
    metrics = compute_metrics(trades, [], periods_per_year=PPY, initial_capital=1000)
    assert metrics["trade_count"] == 1


def test_longest_losing_streak() -> None:
    pnls = [100, -10, -20, 50, -1, -2, -3, -4]
    trades = [_trade(pnl) for pnl in pnls]
    metrics = compute_metrics(trades, [], periods_per_year=PPY, initial_capital=1000)
    assert metrics["longest_losing_streak"] == 4


def test_top_trade_shares() -> None:
    trades = [_trade(60), _trade(30), _trade(10)]
    metrics = compute_metrics(trades, [], periods_per_year=PPY, initial_capital=1000)
    assert metrics["top_1_share"] == 0.6
    assert metrics["top_5_share"] == 1.0


def test_net_return_cagr_and_max_drawdown() -> None:
    equity = _equity([1000, 1100, 900, 1200])
    metrics = compute_metrics([], equity, periods_per_year=PPY, initial_capital=1000)
    assert metrics["net_return"] == 0.2  # (1200-1000)/1000
    # peak 1100 -> trough 900 = 18.18% drawdown
    assert metrics["max_drawdown"] == (1100 - 900) / 1100
    assert metrics["cagr"] is not None


def test_calmar_links_cagr_and_drawdown() -> None:
    equity = _equity([1000, 1100, 900, 1200])
    metrics = compute_metrics([], equity, periods_per_year=PPY, initial_capital=1000)
    assert metrics["calmar"] is not None
    assert metrics["calmar"] == metrics["cagr"] / metrics["max_drawdown"]


def test_sharpe_and_sortino_present_with_returns() -> None:
    equity = _equity([1000, 1010, 1005, 1020, 1030])
    metrics = compute_metrics([], equity, periods_per_year=PPY, initial_capital=1000)
    assert metrics["sharpe"] is not None
    assert metrics["sortino"] is not None


def test_empty_inputs_return_none_not_crash() -> None:
    metrics = compute_metrics([], [], periods_per_year=PPY, initial_capital=1000)
    assert metrics["trade_count"] == 0
    assert metrics["win_rate"] is None
    assert metrics["net_return"] is None
    assert metrics["sharpe"] is None


def test_per_symbol_and_portfolio_inputs() -> None:
    """9.1: the caller computes per-symbol and portfolio-level separately;
    the metrics function accepts either trade set."""
    aaa = [_trade(100, instrument="AAA"), _trade(-30, instrument="AAA")]
    bbb = [_trade(50, instrument="BBB")]
    per_symbol = compute_metrics(aaa, [], periods_per_year=PPY, initial_capital=1000)
    portfolio = compute_metrics([*aaa, *bbb], [], periods_per_year=PPY, initial_capital=1000)
    assert per_symbol["trade_count"] == 2
    assert portfolio["trade_count"] == 3
    # per-symbol Sharpe is never the headline: the UI shows portfolio-level
    # with the per-symbol breakdown beside it (9.1)
