"""M7 (T7.2) — walk-forward, param stability, concentration, regime split
(spec 9.3 Level 2 computations).

These functions compute the inputs the Level 2 tests in ``levels.py``
consume. All take precomputed slices (trades by window/parameter cell/
symbol/regime) so the test suites can assert them without a full
simulator re-run; the runner wires them to the M6 re-run seam.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: 9.3 defaults (config/validation.yaml).
WALK_FORWARD_TRAIN_YEARS = 3
WALK_FORWARD_TEST_YEARS = 1
PARAM_STABILITY_STEPS = (-2, -1, 1, 2)
CONCENTRATION_TOP_SYMBOL_WARN = 0.30
CONCENTRATION_LOO_MIN = 0.80


def walk_forward_positive_share(
    window_expectancies: Sequence[float],
) -> float | None:
    """9.3: rolling windows (default 3y train / 1y test) inside DEVELOPMENT;
    >=60% windows with positive expectancy -> PASS (the level test applies
    the threshold; this returns the share)."""
    if not window_expectancies:
        return None
    positive = sum(1 for value in window_expectancies if value > 0)
    return positive / len(window_expectancies)


def param_stability_neighbor_share(
    center_profit_factor: float,
    neighbor_profit_factors: Sequence[float],
) -> float | None:
    """9.3: grid of +/-1 and +/-2 steps per parameter; >=70% neighbors keep
    PF>=1.0 -> PASS. Returns the share of neighbors with PF >= 1.0."""
    if not neighbor_profit_factors:
        return None
    _ = center_profit_factor  # the level test reports it in the basis
    kept = sum(1 for pf in neighbor_profit_factors if pf >= 1.0)
    return kept / len(neighbor_profit_factors)


def trade_dependency(
    pnls: Sequence[float],
    *,
    top_n: int = 5,
) -> dict[str, Any]:
    """9.3: expectancy after removing the top N trades (or top 5%) plus the
    top-5 share of gross profit."""
    closed = [p for p in pnls if p is not None]
    if not closed:
        return {"expectancy_after_top": None, "top_share": None, "removed": 0}
    ordered = sorted(closed, reverse=True)
    # 9.3: "top 5 trades (or top 5%)" - five trades is the default; the
    # 5% reading matters only for books too small to lose five.
    remove_count = min(top_n, len(closed))
    remaining = closed[:]
    for value in ordered[:remove_count]:
        remaining.remove(value)
    expectancy_after = sum(remaining) / len(remaining) if remaining else 0.0
    gross_profit = sum(p for p in closed if p > 0)
    top_sum = sum(p for p in ordered[:remove_count] if p > 0)
    share = (top_sum / gross_profit) if gross_profit > 0 else None
    return {
        "expectancy_after_top": expectancy_after,
        "top_share": share,
        "removed": remove_count,
    }


def concentration(
    pnl_by_symbol: Mapping[str, float],
) -> dict[str, Any]:
    """9.3: top symbol >30% of profit -> WARN (reported); leave-one-symbol-
    out and leave-one-year-out positive in >=80% -> PASS."""
    profits = {symbol: pnl for symbol, pnl in pnl_by_symbol.items() if pnl > 0}
    total_profit = sum(profits.values())
    top_symbol_share = 0.0
    if total_profit > 0 and profits:
        top_symbol_share = max(profits.values()) / total_profit
    loo_positive = 0.0
    if pnl_by_symbol:
        positive = 0
        for symbol in pnl_by_symbol:
            rest = sum(v for s, v in pnl_by_symbol.items() if s != symbol)
            if rest > 0:
                positive += 1
        loo_positive = positive / len(pnl_by_symbol)
    return {
        "top_symbol_share": top_symbol_share,
        "leave_one_out_positive_share": loo_positive,
        "top_symbol_warn": top_symbol_share > CONCENTRATION_TOP_SYMBOL_WARN,
    }


def leave_one_year_out(
    expectancy_by_year: Mapping[str, float],
) -> float | None:
    """9.3: leave-one-year-out positive share (>=80% -> PASS)."""
    if not expectancy_by_year:
        return None
    positive = 0
    for year in expectancy_by_year:
        rest = sum(v for y, v in expectancy_by_year.items() if y != year)
        if rest > 0:
            positive += 1
    return positive / len(expectancy_by_year)


def regime_split(
    expectancy_by_regime: Mapping[str, float],
    n_by_regime: Mapping[str, int],
    *,
    min_n: int = 20,
) -> dict[str, Any]:
    """9.3: per-regime n and expectancy; any regime with n<20 ->
    INSUFFICIENT."""
    smallest = min(n_by_regime.values()) if n_by_regime else 0
    return {
        "counts": dict(n_by_regime),
        "expectancies": dict(expectancy_by_regime),
        "min_n": smallest,
        "insufficient": smallest < min_n,
    }
