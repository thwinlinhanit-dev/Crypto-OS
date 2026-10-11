"""HF Phase 6 — the robustness & falsification suite tests (spec §13/§14.2).

The adversarial workflows, composed from the M5 result (never re-deriving
a statistic), and the validation-status rules: a promising candidate cannot
be PASSED while a mandatory check is missing or failed.
"""

from __future__ import annotations

from typing import Any

from engine.hypothesis.robustness import (
    check_asset_consistency,
    check_negative_control,
    check_outlier_dependence,
    check_parameter_robustness,
    check_regime_robustness,
    check_time_robustness,
    run_robustness,
    validation_decision,
)


def _result(
    *,
    symbols_agree: float | None = 0.8,
    oos_agrees: bool | None = True,
    ci_excludes: bool = True,
) -> dict[str, Any]:
    base = {"n": 100, "mean_r": 0.4, "ci_excludes_zero": ci_excludes}
    return {
        "headline_model": "CALENDAR_DAY",
        "cluster_models": {
            "CALENDAR_DAY": {
                "n_eff": 100,
                "grade": "B",
                "oos_sign_agrees": oos_agrees,
                "ci_median_r": (0.1, 0.7),
                "ci_excludes_zero": ci_excludes,
            }
        },
        "cross_asset": {
            "n_symbols": 4,
            "pooled_median_r": 0.4,
            "pooled_sign": 1,
            "symbols_agree_share": symbols_agree,
        },
        "horizons": [{"h": 5, "base": base}],
    }


# --------------------------------------------------------------------------
# Asset robustness (spec §13: concentration is a finding)
# --------------------------------------------------------------------------


def test_asset_agreement_passes_at_the_threshold() -> None:
    check = check_asset_consistency(_result(symbols_agree=0.8))
    assert check.passed is True
    assert check.mandatory is True


def test_asset_concentration_fails() -> None:
    check = check_asset_consistency(_result(symbols_agree=0.2))
    assert check.passed is False


def test_a_missing_cross_asset_block_is_reported_not_passed() -> None:
    """A check that cannot run is reported, never assumed to pass."""
    result = _result()
    del result["cross_asset"]
    check = check_asset_consistency(result)
    assert check.passed is False
    assert check.detail


# --------------------------------------------------------------------------
# Time robustness (spec §13)
# --------------------------------------------------------------------------


def test_time_robustness_passes_when_signs_agree() -> None:
    check = check_time_robustness(_result(oos_agrees=True))
    assert check.passed is True


def test_time_robustness_fails_when_signs_disagree() -> None:
    check = check_time_robustness(_result(oos_agrees=False))
    assert check.passed is False


def test_an_unopened_holdout_is_missing_not_passed() -> None:
    check = check_time_robustness(_result(oos_agrees=None))
    assert check.passed is False
    assert "null" in check.detail


# --------------------------------------------------------------------------
# Regime robustness (spec §13; PD-2: contemporaneous labels only)
# --------------------------------------------------------------------------


def test_regime_robustness_passes_when_regimes_agree() -> None:
    check = check_regime_robustness(
        stats_result=_result(),
        by_regime={"trend_up": [0.5, 0.6, 0.4], "trend_down": [0.3, 0.2, 0.4]},
    )
    assert check.passed is True


def test_regime_robustness_fails_when_regimes_disagree() -> None:
    check = check_regime_robustness(
        stats_result=_result(),
        by_regime={"trend_up": [0.5, 0.6], "trend_down": [-0.5, -0.6]},
    )
    assert check.passed is False


def test_no_regime_labels_is_missing_not_passed() -> None:
    check = check_regime_robustness(stats_result=_result(), by_regime={})
    assert check.passed is False


# --------------------------------------------------------------------------
# Parameter robustness (spec §13: the surface, not the best point)
# --------------------------------------------------------------------------


def test_parameter_robustness_passes_when_neighbours_survive() -> None:
    neighbours = [_result(), _result(), _result(ci_excludes=False)]
    check = check_parameter_robustness(neighbor_results=neighbours)
    assert check.passed is True  # 2 of 3 survive


def test_parameter_robustness_fails_when_neighbours_collapse() -> None:
    neighbours = [_result(ci_excludes=False), _result(ci_excludes=False)]
    check = check_parameter_robustness(neighbor_results=neighbours)
    assert check.passed is False


def test_no_neighbours_is_missing_not_passed() -> None:
    check = check_parameter_robustness(neighbor_results=[])
    assert check.passed is False


# --------------------------------------------------------------------------
# Outlier dependence (spec §13: the original result stays visible)
# --------------------------------------------------------------------------


def test_no_outlier_dependence_passes() -> None:
    check = check_outlier_dependence(
        outcomes=[0.1] * 100, top1_share=0.05, top5_share=0.1, top10_share=0.2
    )
    assert check.passed is True


def test_a_single_outlier_carrying_the_effect_is_flagged() -> None:
    """The check FLAGS dependence; it never rewrites the original result."""
    check = check_outlier_dependence(
        outcomes=[0.0] * 99 + [10.0],
        top1_share=0.9,
        top5_share=0.95,
        top10_share=0.98,
    )
    assert check.passed is False
    assert "top-1%" in check.detail
    # the original result stays visible: the shares are reported as evidence
    assert check.evidence["top1_share"] == 0.9


# --------------------------------------------------------------------------
# Negative controls & placebo (spec §13)
# --------------------------------------------------------------------------


def test_a_null_control_passes() -> None:
    check = check_negative_control(_result(ci_excludes=False))
    assert check.passed is True


def test_a_significant_control_is_a_leaking_pipeline() -> None:
    """A control that "works" means the pipeline is leaking - a failed
    experiment, never a success."""
    check = check_negative_control(_result(ci_excludes=True))
    assert check.passed is False
    assert "leaking" in check.detail


def test_a_missing_control_is_missing_not_passed() -> None:
    check = check_negative_control(None)
    assert check.passed is False


# --------------------------------------------------------------------------
# The validation-status rules (spec §14.2)
# --------------------------------------------------------------------------


def _full_pass() -> Any:
    return run_robustness(
        stats_result=_result(),
        by_regime={"a": [0.4, 0.5], "b": [0.3, 0.4]},
        neighbor_results=[_result(), _result()],
        outlier_shares=(0.05, 0.1, 0.2),
        outcomes=[0.1] * 100,
        control_result=_result(ci_excludes=False),
    )


def test_a_fully_passing_suite_passes_research_validation() -> None:
    report = _full_pass()
    assert report.passed is True
    assert validation_decision(report) == "PASSED_RESEARCH_VALIDATION"


def test_a_failed_mandatory_check_blocks_promotion() -> None:
    """Spec §13: a promising candidate cannot be PASSED while mandatory
    checks are missing or failed."""
    report = run_robustness(
        stats_result=_result(symbols_agree=0.1),  # asset concentration
        by_regime={"a": [0.4, 0.5], "b": [0.3, 0.4]},
        neighbor_results=[_result(), _result()],
        outlier_shares=(0.05, 0.1, 0.2),
        outcomes=[0.1] * 100,
        control_result=_result(ci_excludes=False),
    )
    assert report.mandatory_failed
    assert validation_decision(report) == "FAILED_ROBUSTNESS"


def test_a_missing_check_blocks_promotion_to_human_review() -> None:
    report = run_robustness(
        stats_result=_result(),
        by_regime={},  # missing
        neighbor_results=[],  # missing
        outlier_shares=None,  # not run
        control_result=None,  # missing
    )
    assert report.mandatory_missing or report.mandatory_failed
    assert validation_decision(report) in ("REQUIRES_HUMAN_REVIEW", "FAILED_ROBUSTNESS")


def test_an_empty_suite_requires_human_review() -> None:
    from engine.hypothesis.robustness import RobustnessReport

    assert validation_decision(RobustnessReport()) == "REQUIRES_HUMAN_REVIEW"
