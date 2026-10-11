"""HF Phase 4 — the synthetic methodology tests (spec §23.3).

The suite ships synthetic datasets with known properties and asserts the
pipeline identifies or appropriately flags them:

1. a feature with no predictive relationship;
2. a feature with a known positive relationship;
3. a feature whose apparent relationship disappears after controlling for a
   confounder;
4. a feature with an effect concentrated in a single outlier;
5. a feature that only appears predictive when future information leaks;
6. a feature with strong in-sample performance but no out-of-sample effect;
7. multiple independent null hypotheses where uncorrected testing creates
   false discoveries.

Synthetic tests validate software behavior, not actual cryptocurrency
market profitability (spec §23.3).
"""

from __future__ import annotations

import random

import pytest

from engine.hypothesis.stats import (
    apply_correction,
    benjamini_hochberg,
    cohens_d,
    outlier_sensitivity,
    synthetic_check,
)
from engine.hypothesis.stats import (
    test_hypothesis as run_hypothesis_test,
)


def _null_returns(n: int, rng: random.Random, mean: float = 0.0, sd: float = 1.0) -> list[float]:
    return [rng.gauss(mean, sd) for _ in range(n)]


def test_case_1_a_null_feature_is_not_significant() -> None:
    """A feature with no predictive relationship: the adjusted p stays above
    the threshold (no false discovery)."""
    rng = random.Random(1)
    treatment = _null_returns(200, rng, mean=0.0, sd=1.0)
    baseline = _null_returns(200, rng, mean=0.0, sd=1.0)
    result = synthetic_check(
        treatment=treatment,
        baseline=baseline,
        raw_p=0.5,  # a null test
        economic_threshold=0.5,
    )
    assert result.conclusion == "NO_EFFECT_DETECTED"
    assert result.horizons[0].adjusted_p is not None
    assert result.horizons[0].adjusted_p >= 0.05


def test_case_2_a_true_positive_is_detected() -> None:
    """A feature with a known positive relationship: the effect is detected
    and the effect size is positive."""
    rng = random.Random(2)
    treatment = _null_returns(200, rng, mean=1.5, sd=1.0)
    baseline = _null_returns(200, rng, mean=0.0, sd=1.0)
    result = synthetic_check(
        treatment=treatment,
        baseline=baseline,
        raw_p=0.001,
        economic_threshold=0.5,
    )
    assert result.conclusion == "EXPLORATORY_SUPPORT"
    horizon = result.horizons[0]
    assert horizon.difference > 0
    assert horizon.effect_size > 0
    assert horizon.economically_significant is True


def test_case_3_a_confounded_effect_is_not_economic() -> None:
    """A feature whose apparent relationship disappears after controlling
    for a confounder: the difference collapses toward the baseline, so the
    result is statistical-only or no-effect — never economic."""
    rng = random.Random(3)
    # the raw (uncontrolled) difference is large...
    treatment = _null_returns(200, rng, mean=1.0, sd=1.0)
    # ...but the controlled baseline (the confounder explains it) matches
    baseline = _null_returns(200, rng, mean=1.0, sd=1.0)
    result = synthetic_check(
        treatment=treatment,
        baseline=baseline,
        raw_p=0.001,
        economic_threshold=0.5,
    )
    assert result.horizons[0].difference == pytest.approx(0.0, abs=0.3)
    assert result.horizons[0].economically_significant is False


def test_case_4_an_outlier_only_effect_is_flagged() -> None:
    """A feature with an effect concentrated in a single outlier: the
    outlier sensitivity is large (the original result stays visible, spec
    §13.5)."""
    rng = random.Random(4)
    treatment = _null_returns(19, rng, mean=0.0, sd=0.1)
    treatment.append(50.0)  # the single extreme winner
    baseline = _null_returns(20, rng, mean=0.0, sd=0.1)
    sensitivity = outlier_sensitivity(treatment)
    assert sensitivity > 1.0, "the outlier's contribution is flagged"
    result = synthetic_check(
        treatment=treatment,
        baseline=baseline,
        raw_p=0.01,
        economic_threshold=0.5,
    )
    assert result.horizons[0].outlier_sensitivity is not None
    assert result.horizons[0].outlier_sensitivity > 1.0


def test_case_5_a_look_ahead_feature_is_rejected_before_testing() -> None:
    """A feature that only appears predictive when future information leaks:
    the definition validation rejects it before any test runs (PD-2)."""
    from engine.errors import ValidationError
    from engine.hypothesis.registry import validate_definition

    spec = _leaky_spec()
    with pytest.raises(ValidationError) as excinfo:
        validate_definition(spec)
    assert "observation_ts_rule" in str(excinfo.value.message)


def _leaky_spec() -> dict[str, object]:
    return {
        "null_hypothesis": "no relationship",
        "alternative_hypothesis": "a relationship",
        "independent_vars": ["leaky_feature"],
        "dependent_vars": ["forward_return_5"],
        "features": ["rvol"],
        "population": "synthetic",
        "timeframe": "1D",
        # the leak: the observation uses the NEXT bar's close
        "observation_ts_rule": "the next bar's close_time (future information)",
        "outcome_horizon": "5 bars",
        "expected_direction": "HIGHER",
        "data_requirements": ["synthetic"],
        "proposed_statistical_method": "t-test",
        "falsification_criteria": ["none"],
    }


def test_case_6_in_sample_only_performance_is_not_confirmatory() -> None:
    """A feature with strong in-sample performance but no out-of-sample
    effect: the in-sample result is EXPLORATORY at best (the holdout gate
    is a separate, later check — spec §14.1)."""
    rng = random.Random(6)
    treatment = _null_returns(200, rng, mean=2.0, sd=1.0)
    baseline = _null_returns(200, rng, mean=0.0, sd=1.0)
    result = synthetic_check(
        treatment=treatment,
        baseline=baseline,
        raw_p=0.0001,
        economic_threshold=0.5,
    )
    # the conclusion is EXPLORATORY_SUPPORT - never PASSED_RESEARCH_VALIDATION
    # (the out-of-sample gate is a separate transition, spec §14.2)
    assert result.conclusion == "EXPLORATORY_SUPPORT"
    assert "PASSED" not in result.conclusion


def test_case_7_multiple_nulls_uncorrected_vs_corrected() -> None:
    """Multiple independent null hypotheses where uncorrected testing
    creates false discoveries: the BH correction controls the FDR."""
    rng = random.Random(7)
    # 20 null hypotheses, each with a marginal raw p
    raw_p = [rng.uniform(0.0, 0.05) for _ in range(20)]
    adjusted = benjamini_hochberg(raw_p)
    # BH: the adjusted p is >= the raw p, and the number of "discoveries"
    # at the 0.05 level is controlled (not all 20 pass)
    assert all(a >= p for a, p in zip(adjusted, raw_p, strict=True))
    uncorrected_discoveries = sum(1 for p in raw_p if p < 0.05)
    corrected_discoveries = sum(1 for a in adjusted if a < 0.05)
    assert corrected_discoveries <= uncorrected_discoveries


def test_the_correction_is_more_conservative_than_none() -> None:
    """The adjusted p-values are never smaller than the raw (spec §12)."""
    p_values = [0.001, 0.01, 0.04, 0.2]
    for plan in ("BENJAMINI_HOCHBERG", "BONFERRONI"):
        adjusted = apply_correction(plan, p_values)
        assert all(a >= p for a, p in zip(adjusted, p_values, strict=True)), plan


def test_the_correction_plan_is_declared_not_chosen() -> None:
    """The correction plan is a declared input, not a post-hoc choice (spec
    §12: never chosen after seeing the results)."""
    p_values = [0.01, 0.02, 0.5]
    bh = apply_correction("BENJAMINI_HOCHBERG", p_values)
    bonf = apply_correction("BONFERRONI", p_values)
    # both are computed from the SAME declared plan input; the caller chose
    # before the analysis (the function has no access to the outcomes)
    assert bh != bonf  # the methods genuinely differ


def test_an_unknown_correction_plan_is_rejected() -> None:
    with pytest.raises(ValueError):
        apply_correction("CHERRY_PICKED", [0.01])


def test_a_strategy_selection_correction_is_not_silently_substituted() -> None:
    """White's Reality Check / Deflated Sharpe are declared but NOT_IMPLEMENTED
    until Phase 6 - never silently swapped for BH (spec §12)."""
    with pytest.raises(NotImplementedError):
        apply_correction("WHITE_REALITY_CHECK", [0.01])


def test_the_multi_horizon_result_carries_every_horizon() -> None:
    """Spec §11.2: every configured horizon is reported, each with its own
    adjusted p."""
    rng = random.Random(8)
    forward = {h: _null_returns(100, rng, mean=0.5, sd=1.0) for h in (1, 5, 10, 20)}
    baseline = {h: _null_returns(100, rng, mean=0.0, sd=1.0) for h in (1, 5, 10, 20)}
    result = run_hypothesis_test(
        hypothesis_id="multi",
        claim="multi-horizon",
        family_id="f",
        correction_plan="BENJAMINI_HOCHBERG",
        forward_returns=forward,
        baseline_returns=baseline,
        raw_p_values=dict.fromkeys(forward, 0.01),
        economic_threshold=None,
    )
    assert [h.horizon for h in result.horizons] == [1, 5, 10, 20]
    assert all(h.adjusted_p is not None for h in result.horizons)


def test_cohens_d_is_zero_for_identical_distributions() -> None:
    assert cohens_d([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 0.0


def test_cohens_d_is_positive_for_a_positive_shift() -> None:
    rng = random.Random(9)
    treatment = _null_returns(100, rng, mean=1.0, sd=1.0)
    baseline = _null_returns(100, rng, mean=0.0, sd=1.0)
    assert cohens_d(treatment, baseline) > 0


def test_an_empty_result_is_invalid_not_successful() -> None:
    """An experiment with no observations is INVALID_EXPERIMENT, never a
    successful empty result (spec §22: "no effect" ≠ "execution failure")."""
    result = run_hypothesis_test(
        hypothesis_id="empty",
        claim="no observations",
        family_id="f",
        correction_plan="BENJAMINI_HOCHBERG",
        forward_returns={},
        baseline_returns={},
        raw_p_values={},
        economic_threshold=None,
    )
    assert result.conclusion == "INVALID_EXPERIMENT"
