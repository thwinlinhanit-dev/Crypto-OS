"""M7 (T7.1) — three-level validation tests (spec 9.3).

Every test returns a status with a numeric basis; the trust rule (Level 1
FAIL/NOT_RUN -> Level 2/3 UNTRUSTED) is asserted end to end.
"""

from __future__ import annotations

from engine.validation.levels import (
    TestResult,
    is_trusted,
    level1_tests,
    level2_tests,
    level3_tests,
)

THRESHOLDS: dict[str, object] = {}


def _by_name(results: list[TestResult], name: str) -> TestResult:
    return next(r for r in results if r.name == name)


# ---------------------------------------------------------------------------
# Level 1
# ---------------------------------------------------------------------------


def test_parity_pass() -> None:
    results = level1_tests(parity_status="EXACT", truncation_passed=True,
                           replay_reproduced=True, accounting_ok=True)
    assert _by_name(results, "tradingview_parity").status == "PASS"
    assert is_trusted(results) is True


def test_parity_na_without_export_still_trusted() -> None:
    """9.3: parity is N/A only when no reference export exists."""
    results = level1_tests(parity_status=None, truncation_passed=True,
                           replay_reproduced=True, accounting_ok=True)
    assert _by_name(results, "tradingview_parity").status == "NOT_RUN"
    assert is_trusted(results) is True  # N/A parity does not untrust


def test_parity_failure_untrusts() -> None:
    results = level1_tests(parity_status="ENGINE_BUG", truncation_passed=True,
                           replay_reproduced=True, accounting_ok=True)
    assert _by_name(results, "tradingview_parity").status == "FAIL"
    assert is_trusted(results) is False


def test_truncation_failure_untrusts() -> None:
    results = level1_tests(parity_status=None, truncation_passed=False,
                           replay_reproduced=True, accounting_ok=True)
    assert is_trusted(results) is False


def test_replay_not_run_untrusts() -> None:
    """9.3: NOT_RUN (other than N/A parity) also untrusts."""
    results = level1_tests(parity_status=None, truncation_passed=True,
                           replay_reproduced=None, accounting_ok=True)
    assert _by_name(results, "deterministic_replay").status == "NOT_RUN"
    assert is_trusted(results) is False


def test_every_test_carries_numeric_basis() -> None:
    results = level1_tests(parity_status="EXACT", truncation_passed=True,
                           replay_reproduced=True, accounting_ok=True)
    for result in results:
        payload = result.as_dict()
        assert payload["status"] in {"PASS", "WARN", "FAIL", "INSUFFICIENT", "NOT_RUN"}
        assert isinstance(payload["basis"], dict)


# ---------------------------------------------------------------------------
# Level 2
# ---------------------------------------------------------------------------


def test_edge_pass_when_ci_excludes_zero() -> None:
    results = level2_tests(
        expectancy_ci=(0.05, 0.3), trade_count=100, is_expectancy_r=0.2,
        oos_expectancy_r=0.15, walk_forward_positive_share=0.7,
        clustering_grades_differ=False, selection_method="PRE_SPECIFIED",
        baseline_percentile=97.0, param_stability_neighbor_share=0.8,
        expectancy_after_top5_removed=0.1, top5_share=0.2, concentration=None,
        regime_counts={"trend": 50, "range": 30}, thresholds=THRESHOLDS,
    )
    assert _by_name(results, "edge").status == "PASS"


def test_edge_insufficient_below_thirty_trades() -> None:
    results = level2_tests(
        expectancy_ci=(0.05, 0.3), trade_count=10, is_expectancy_r=0.2,
        oos_expectancy_r=0.15, walk_forward_positive_share=0.7,
        clustering_grades_differ=False, selection_method="PRE_SPECIFIED",
        baseline_percentile=97.0, param_stability_neighbor_share=0.8,
        expectancy_after_top5_removed=0.1, top5_share=0.2, concentration=None,
        regime_counts=None, thresholds=THRESHOLDS,
    )
    assert _by_name(results, "edge").status == "INSUFFICIENT"
    assert _by_name(results, "edge").basis["min_trades"] == 30


def test_edge_fail_when_ci_includes_zero() -> None:
    results = level2_tests(
        expectancy_ci=(-0.1, 0.3), trade_count=100, is_expectancy_r=0.2,
        oos_expectancy_r=0.15, walk_forward_positive_share=0.7,
        clustering_grades_differ=False, selection_method="PRE_SPECIFIED",
        baseline_percentile=97.0, param_stability_neighbor_share=0.8,
        expectancy_after_top5_removed=0.1, top5_share=0.2, concentration=None,
        regime_counts=None, thresholds=THRESHOLDS,
    )
    assert _by_name(results, "edge").status == "FAIL"


def test_oos_sign_flip_is_fail() -> None:
    results = level2_tests(
        expectancy_ci=(0.05, 0.3), trade_count=100, is_expectancy_r=0.2,
        oos_expectancy_r=-0.05, walk_forward_positive_share=0.7,
        clustering_grades_differ=False, selection_method="PRE_SPECIFIED",
        baseline_percentile=97.0, param_stability_neighbor_share=0.8,
        expectancy_after_top5_removed=0.1, top5_share=0.2, concentration=None,
        regime_counts=None, thresholds=THRESHOLDS,
    )
    assert _by_name(results, "oos").status == "FAIL"


def test_multiple_testing_warns_when_searched() -> None:
    results = level2_tests(
        expectancy_ci=None, trade_count=0, is_expectancy_r=None,
        oos_expectancy_r=None, walk_forward_positive_share=None,
        clustering_grades_differ=None, selection_method="SWEEP_BEST",
        baseline_percentile=None, param_stability_neighbor_share=None,
        expectancy_after_top5_removed=None, top5_share=None, concentration=None,
        regime_counts=None, thresholds=THRESHOLDS,
    )
    assert _by_name(results, "multiple_testing").status == "WARN"


def test_trade_dependency_fail_when_top5_removed_negative() -> None:
    results = level2_tests(
        expectancy_ci=None, trade_count=0, is_expectancy_r=None,
        oos_expectancy_r=None, walk_forward_positive_share=None,
        clustering_grades_differ=None, selection_method="PRE_SPECIFIED",
        baseline_percentile=None, param_stability_neighbor_share=None,
        expectancy_after_top5_removed=-0.05, top5_share=0.5, concentration=None,
        regime_counts=None, thresholds=THRESHOLDS,
    )
    assert _by_name(results, "trade_dependency").status == "FAIL"


def test_regime_insufficient_below_min_n() -> None:
    results = level2_tests(
        expectancy_ci=None, trade_count=0, is_expectancy_r=None,
        oos_expectancy_r=None, walk_forward_positive_share=None,
        clustering_grades_differ=None, selection_method="PRE_SPECIFIED",
        baseline_percentile=None, param_stability_neighbor_share=None,
        expectancy_after_top5_removed=None, top5_share=None, concentration=None,
        regime_counts={"trend": 5}, thresholds=THRESHOLDS,
    )
    assert _by_name(results, "regime_split").status == "INSUFFICIENT"


# ---------------------------------------------------------------------------
# Level 3
# ---------------------------------------------------------------------------


def test_cost_stress_pass() -> None:
    results = level3_tests(
        cost_stress={"profit_factor": 1.2, "expectancy_r": 0.1, "oos_expectancy_r": 0.05},
        delay_stress={"profit_factor": 1.1, "expectancy_r": 0.08, "oos_expectancy_r": 0.02},
        gap_risk={"count": 0}, monte_carlo={"p5_final_equity": 10500},
        thresholds=THRESHOLDS,
    )
    assert _by_name(results, "cost_stress").status == "PASS"
    assert _by_name(results, "delay_stress").status == "PASS"


def test_cost_stress_fail_when_pf_below_one() -> None:
    results = level3_tests(
        cost_stress={"profit_factor": 0.9, "expectancy_r": -0.02, "oos_expectancy_r": 0.05},
        delay_stress=None, gap_risk=None, monte_carlo=None,
        thresholds=THRESHOLDS,
    )
    assert _by_name(results, "cost_stress").status == "FAIL"


def test_liquidity_funding_is_not_run_before_m10() -> None:
    results = level3_tests(cost_stress=None, delay_stress=None, gap_risk=None,
                           monte_carlo=None, thresholds=THRESHOLDS)
    assert _by_name(results, "liquidity_funding").status == "NOT_RUN"


def test_gap_risk_warns_above_threshold() -> None:
    results = level3_tests(
        cost_stress=None, delay_stress=None,
        gap_risk={"count": 5, "worst_gap_loss_r": -2.0},
        monte_carlo=None, thresholds=THRESHOLDS,
    )
    assert _by_name(results, "gap_risk").status == "WARN"
    assert _by_name(results, "gap_risk").basis["count"] == 5
