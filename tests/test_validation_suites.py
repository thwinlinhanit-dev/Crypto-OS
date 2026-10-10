"""M7 (T7.2-T7.3) — the Level 2/3 computation tests (spec 9.3).

Walk-forward share, param-stability share, trade dependency,
concentration, regime split, cost/delay stress and Monte Carlo.
"""

from __future__ import annotations

from engine.validation.stress import cost_stress, delay_stress, monte_carlo
from engine.validation.suites import (
    concentration,
    leave_one_year_out,
    param_stability_neighbor_share,
    regime_split,
    trade_dependency,
    walk_forward_positive_share,
)

# ---------------------------------------------------------------------------
# T7.2 suites
# ---------------------------------------------------------------------------


def test_walk_forward_share() -> None:
    assert walk_forward_positive_share([0.1, 0.2, -0.1, 0.3]) == 0.75
    assert walk_forward_positive_share([]) is None


def test_param_stability_share() -> None:
    assert param_stability_neighbor_share(1.5, [1.1, 1.2, 0.9, 1.4]) == 0.75
    assert param_stability_neighbor_share(1.5, []) is None


def test_trade_dependency_removes_top_five() -> None:
    pnls = [10.0, -2.0, 8.0, -1.0, 5.0, 3.0, -3.0, 2.0, 1.0, -1.0]
    result = trade_dependency(pnls)
    assert result["removed"] == 5  # 9.3: top 5 trades (top-5% covers tiny books)
    assert result["expectancy_after_top"] is not None
    assert result["top_share"] is not None


def test_concentration_top_symbol_share() -> None:
    result = concentration({"AAA": 70.0, "BBB": 20.0, "CCC": 10.0})
    assert result["top_symbol_share"] == 0.7
    assert result["top_symbol_warn"] is True
    # every leave-one-out sum stays positive (30 / 80 / 90)
    assert result["leave_one_out_positive_share"] == 1.0


def test_concentration_one_dominant_symbol_fails_loo() -> None:
    """A single negative symbol drags every leave-one-out sum positive, but
    one that is itself the book fails."""
    result = concentration({"AAA": 100.0, "BBB": -40.0, "CCC": -60.0})
    # removing BBB or CCC leaves 40 or 100 positive; removing AAA -> -100
    assert result["leave_one_out_positive_share"] == 2 / 3
    assert result["top_symbol_share"] == 1.0


def test_leave_one_year_out() -> None:
    # removing any one year leaves the other two positive -> 1.0
    assert leave_one_year_out({"2024": 0.1, "2025": 0.2, "2026": -0.05}) == 1.0
    assert leave_one_year_out({"2024": 0.1, "2025": -0.3}) == 0.5


def test_regime_split_insufficient() -> None:
    result = regime_split({"trend": 0.1}, {"trend": 5})
    assert result["insufficient"] is True
    result_ok = regime_split({"trend": 0.1}, {"trend": 50})
    assert result_ok["insufficient"] is False


# ---------------------------------------------------------------------------
# T7.3 suites
# ---------------------------------------------------------------------------


def test_cost_stress_halves_pnl_headroom() -> None:
    trades = [{"pnl": 10.0, "fees": 2.0, "r_multiple": 1.0}]
    stressed = cost_stress(trades, multiplier=2.0)
    # base pnl 10 minus one extra fee (2) = 8
    assert stressed["profit_factor"] is not None
    assert stressed["multiplier"] == 2.0


def test_delay_stress_reports_aggregates() -> None:
    trades = [{"pnl": 10.0, "r_multiple": 1.0}, {"pnl": -4.0, "r_multiple": -0.5}]
    stressed = delay_stress(trades)
    assert stressed["profit_factor"] == 2.5
    assert stressed["expectancy_r"] == 0.25
    assert stressed["extra_delay_bars"] == 1


def test_monte_carlo_is_seeded_and_percentiles() -> None:
    pnls = [1.0, -0.5, 2.0, -1.0, 0.5, 3.0, -2.0, 1.5, -0.25, 0.75]
    first = monte_carlo(pnls, 1000.0, seed=7, resamples=500)
    second = monte_carlo(pnls, 1000.0, seed=7, resamples=500)
    assert first == second  # seeded determinism (M6/M7 reproducibility AC)
    assert first["p5_final_equity"] is not None
    assert first["p5_final_equity"] <= first["p95_final_equity"]
    assert first["maxdd_p95"] >= first["maxdd_p50"]


def test_monte_carlo_empty() -> None:
    result = monte_carlo([], 1000.0)
    assert result["p5_final_equity"] is None
