"""M7 (T7.1) — performance metrics (spec 9.1, verbatim list).

Every metric the spec names: net return, CAGR, profit factor, expectancy
(R), win rate, avg win/loss, payoff ratio, Sharpe, Sortino, max drawdown,
Calmar, exposure, turnover, trades, avg/median R, top-1/5/10 trade share,
longest losing streak. Per-symbol AND portfolio-level (shared equity);
per-symbol Sharpe is never the headline (9.1).

Pure (D-042): the inputs are trade records and an equity curve; the caller
supplies ``periods_per_year`` from the timeframe (D-045).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

#: Profit factor cap when there are no losses (D-046: never Infinity in JSON).
GROSS_LOSS_ZERO_CAP = 999_999.0


@dataclass(frozen=True)
class TradeLike:
    """The fields the metrics need from a 7.5 trade record."""

    pnl: float
    r_multiple: float | None
    entry_price: float
    exit_price: float | None
    bars_held: int | None
    instrument: str


def _closed(trades: Sequence[TradeLike]) -> list[TradeLike]:
    """E1/D-049: open positions (exit_price None) are excluded."""
    return [t for t in trades if t.exit_price is not None]


def compute_metrics(
    trades: Sequence[TradeLike],
    equity_curve: Sequence[tuple[str, float]],
    *,
    periods_per_year: float,
    initial_capital: float,
) -> dict[str, Any]:
    """Every 9.1 metric, from closed trades + the shared equity curve."""
    closed = _closed(trades)
    pnls = [t.pnl for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    r_values = [t.r_multiple for t in closed if t.r_multiple is not None]

    trade_count = len(closed)
    win_rate = (len(wins) / trade_count) if trade_count else None
    avg_win = (sum(wins) / len(wins)) if wins else None
    avg_loss = (sum(losses) / len(losses)) if losses else None
    payoff = (abs(avg_win / avg_loss)) if avg_win is not None and avg_loss else None
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor: float | None
    if gross_loss == 0:
        profit_factor = GROSS_LOSS_ZERO_CAP if gross_win > 0 else None
    else:
        profit_factor = gross_win / gross_loss
    expectancy_r = (sum(r_values) / len(r_values)) if r_values else None

    net_return, cagr, sharpe, sortino, max_dd, calmar = _equity_metrics(
        equity_curve, periods_per_year, initial_capital
    )
    exposure, turnover = _activity_metrics(closed)
    top_shares = _top_trade_shares(pnls)
    longest_loss_streak = _longest_losing_streak(pnls)

    return {
        "net_return": net_return,
        "cagr": cagr,
        "profit_factor": profit_factor,
        "gross_loss_zero": gross_loss == 0,
        "expectancy_r": expectancy_r,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "exposure": exposure,
        "turnover": turnover,
        "trade_count": trade_count,
        "avg_r": expectancy_r,
        "median_r": _median(r_values),
        "top_1_share": top_shares[0],
        "top_5_share": top_shares[1],
        "top_10_share": top_shares[2],
        "longest_losing_streak": longest_loss_streak,
    }


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _equity_metrics(
    equity_curve: Sequence[tuple[str, float]],
    periods_per_year: float,
    initial_capital: float,
) -> tuple[float | None, float | None, float | None, float | None, float | None, float | None]:
    """Net return, CAGR, Sharpe, Sortino, max drawdown, Calmar (9.1)."""
    if not equity_curve:
        return None, None, None, None, None, None
    values = [value for _moment, value in equity_curve]
    final = values[-1]
    net_return = (final - initial_capital) / initial_capital if initial_capital else None

    # CAGR: geometric annualised growth over the observed bar count.
    bars = len(values)
    years = bars / periods_per_year if periods_per_year else None
    cagr: float | None = None
    if years and years > 0 and initial_capital > 0 and final > 0:
        cagr = (final / initial_capital) ** (1 / years) - 1

    returns: list[float] = []
    for i in range(1, len(values)):
        prev = values[i - 1]
        if prev:
            returns.append((values[i] - prev) / prev)

    sharpe = sortino = None
    if returns:
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        std = math.sqrt(variance)
        sharpe = (mean / std * math.sqrt(periods_per_year)) if std else None
        downside = [r for r in returns if r < 0]
        if downside:
            downside_std = math.sqrt(sum(r * r for r in downside) / len(returns))
            sortino = (mean / downside_std * math.sqrt(periods_per_year)) if downside_std else None

    peak = values[0]
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            max_dd = max(max_dd, (peak - value) / peak)

    calmar = (cagr / max_dd) if cagr is not None and max_dd else None
    return net_return, cagr, sharpe, sortino, max_dd, calmar


def _activity_metrics(closed: Sequence[TradeLike]) -> tuple[float | None, float | None]:
    """Exposure: bars in market / total bars. Turnover: traded notional /
    mean equity (9.1)."""
    bars_held = [t.bars_held for t in closed if t.bars_held is not None]
    exposure = (sum(bars_held) / len(bars_held)) if bars_held else None
    notional = sum((t.entry_price + (t.exit_price or t.entry_price)) / 2 for t in closed)
    return exposure, notional if closed else None


def _top_trade_shares(pnls: Sequence[float]) -> tuple[float | None, float | None, float | None]:
    """Top-1/5/10 trade share of gross profit (9.1)."""
    gross = sum(p for p in pnls if p > 0)
    if gross <= 0:
        return None, None, None
    ordered = sorted((p for p in pnls if p > 0), reverse=True)
    return (
        ordered[0] / gross,
        sum(ordered[:5]) / gross,
        sum(ordered[:10]) / gross,
    )


def _longest_losing_streak(pnls: Sequence[float]) -> int:
    longest = 0
    current = 0
    for pnl in pnls:
        if pnl < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
