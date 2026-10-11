"""HF Phase 4/5 — the falsification evaluator + the transition decision
(spec §13/§14, the master prompt's negative-finding preservation rules).

The tests cover the seven synthetic cases plus the decision rules:
a missing result is INVALID (never a promotion), a falsified result is a
preserved negative, a statistical-only result never reaches economic, and
the ladder advances one rung at a time.
"""

from __future__ import annotations

from typing import Any

from engine.hypothesis.evaluate import (
    MIN_HORIZON_N,
    decide_transition,
    evaluate,
    horizon_summaries,
    transition_is_legal,
)


def _m5_result(
    *,
    mean_r: float = 0.5,
    win_rate: float = 0.6,
    ci_excludes_zero: bool = True,
    n: int = 200,
    horizons: tuple[int, ...] = (5,),
) -> dict[str, Any]:
    """An M5-shaped ``compute_stats`` result (the contract this module
    consumes - it never fabricates the statistics itself)."""
    entries = []
    for h in horizons:
        entries.append(
            {
                "horizon": h,
                "n": n,
                "mean_pct": mean_r,
                "mean_r": mean_r,
                "median_r": mean_r,
                "win_rate": win_rate,
                "ci_median_r": (0.1, 0.9) if ci_excludes_zero else (-0.2, 0.3),
                "ci_excludes_zero": ci_excludes_zero,
            }
        )
    return {
        "n_raw": n,
        "n_eff": n,
        "grade": "B",
        "horizons": entries,
        "multiple_testing": {"family_id": "fam", "variant_count": 1,
                             "selection_method": "PRE_SPECIFIED"},
    }


# --------------------------------------------------------------------------
# The horizon extraction
# --------------------------------------------------------------------------


def test_horizons_are_extracted_keyed_by_horizon() -> None:
    result = _m5_result(horizons=(1, 5, 10))
    summaries = horizon_summaries(result)
    assert sorted(summaries) == [1, 5, 10]
    assert summaries[5]["mean_r"] == 0.5


def test_a_result_without_horizons_is_empty() -> None:
    assert horizon_summaries({"horizons": []}) == {}


def test_a_malformed_horizon_is_skipped() -> None:
    result: dict[str, Any] = {"horizons": [{"horizon": "x"}, {"horizon": 5}]}
    assert list(horizon_summaries(result)) == [5]


# --------------------------------------------------------------------------
# A missing / broken result is INVALID, never a promotion (spec §22)
# --------------------------------------------------------------------------


def test_a_missing_result_is_invalid_not_falsified() -> None:
    evaluation = evaluate(
        hypothesis_id="h",
        falsification_criteria=["effect vanishes after fees"],
        stats_result=None,
        economic_threshold=0.2,
    )
    assert evaluation.invalid is True
    assert evaluation.falsified is False
    assert decide_transition(evaluation) == "INVALID_EXPERIMENT"


def test_a_result_with_no_horizons_is_invalid() -> None:
    evaluation = evaluate(
        hypothesis_id="h",
        falsification_criteria=["ci includes zero"],
        stats_result={"horizons": []},
    )
    assert evaluation.invalid is True
    assert decide_transition(evaluation) == "INVALID_EXPERIMENT"


def test_a_missing_declared_horizon_is_invalid() -> None:
    evaluation = evaluate(
        hypothesis_id="h",
        falsification_criteria=["ci includes zero"],
        stats_result=_m5_result(horizons=(5,)),
        declared_horizons=(5, 20),
    )
    assert evaluation.invalid is True
    assert "20" in str(evaluation.reject_reason)


# --------------------------------------------------------------------------
# The falsification criteria (spec §13)
# --------------------------------------------------------------------------


def test_the_effect_vanishes_after_fees() -> None:
    """A statistical effect below the economic threshold is falsified by the
    'effect vanishes after fees' criterion (spec §11.3)."""
    evaluation = evaluate(
        hypothesis_id="h",
        falsification_criteria=["effect vanishes after fees"],
        stats_result=_m5_result(mean_r=0.05),
        economic_threshold=0.2,
    )
    assert evaluation.falsified is True
    assert decide_transition(evaluation) == "FAILED_STATISTICAL_TEST"


def test_a_ci_including_zero_falsifies() -> None:
    evaluation = evaluate(
        hypothesis_id="h",
        falsification_criteria=["ci includes zero"],
        stats_result=_m5_result(ci_excludes_zero=False),
    )
    assert evaluation.falsified is True


def test_a_win_rate_at_chance_falsifies() -> None:
    evaluation = evaluate(
        hypothesis_id="h",
        falsification_criteria=["win rate not above chance"],
        stats_result=_m5_result(win_rate=0.5),
    )
    assert evaluation.falsified is True


def test_a_small_sample_is_inconclusive_not_positive() -> None:
    """Insufficient observations never promote a hypothesis (spec §13)."""
    evaluation = evaluate(
        hypothesis_id="h",
        falsification_criteria=["insufficient observations"],
        stats_result=_m5_result(n=10),
    )
    assert evaluation.falsified is False
    assert "inconclusive" in str(evaluation.criteria[0].detail)


def test_an_unrecognized_criterion_requires_human_review() -> None:
    """A criterion outside the closed vocabulary is recorded, never assumed
    to pass (PD-10 fail-closed)."""
    evaluation = evaluate(
        hypothesis_id="h",
        falsification_criteria=["vibes are off"],
        stats_result=_m5_result(),
    )
    assert evaluation.falsified is False
    assert "UNRECOGNIZED" in evaluation.criteria[0].detail


# --------------------------------------------------------------------------
# The statistical vs economic split (spec §11.3)
# --------------------------------------------------------------------------


def test_a_statistical_only_result_is_not_economic() -> None:
    evaluation = evaluate(
        hypothesis_id="h",
        falsification_criteria=[],  # no criteria falsify it
        stats_result=_m5_result(mean_r=0.5),
        economic_threshold=100.0,  # unreachable economically
    )
    assert evaluation.statistical_support is True
    assert evaluation.economic_only is False
    assert decide_transition(evaluation) == "FAILED_ROBUSTNESS"


def test_a_supporting_result_is_exploratory_not_validated() -> None:
    """A result that passes the statistical AND economic checks is
    EXPLORATORY - the ladder advances one rung at a time (spec §14)."""
    evaluation = evaluate(
        hypothesis_id="h",
        falsification_criteria=[],
        stats_result=_m5_result(mean_r=0.5),
        economic_threshold=0.2,
    )
    assert evaluation.statistical_support is True
    assert evaluation.economic_only is True
    assert decide_transition(evaluation) == "EXPLORATORY"


def test_no_ci_excludes_zero_means_no_effect() -> None:
    evaluation = evaluate(
        hypothesis_id="h",
        falsification_criteria=[],
        stats_result=_m5_result(ci_excludes_zero=False),
    )
    assert evaluation.statistical_support is False
    assert decide_transition(evaluation) == "FAILED_STATISTICAL_TEST"


def test_a_hypothesis_without_falsification_criteria_cannot_promote() -> None:
    """Spec §1.2.4: all hypotheses must be falsifiable - an undeclared
    criterion set is recorded as a blocker."""
    evaluation = evaluate(
        hypothesis_id="h",
        falsification_criteria=[],
        stats_result=_m5_result(),
    )
    assert any("falsifiable" in note for note in evaluation.notes)


# --------------------------------------------------------------------------
# The transition table legality
# --------------------------------------------------------------------------


def test_the_transition_decision_is_legal_from_not_tested() -> None:
    # a supporting result from an untested hypothesis reaches EXPLORATORY
    evaluation = evaluate(
        hypothesis_id="h",
        falsification_criteria=[],
        stats_result=_m5_result(),
        economic_threshold=0.2,
    )
    target = decide_transition(evaluation)
    assert target == "EXPLORATORY"
    assert transition_is_legal("NOT_TESTED", target) is True


def test_a_statistical_only_decision_needs_the_exploratory_rung_first() -> None:
    """A statistical-only result reaches FAILED_ROBUSTNESS, which is only
    legal from EXPLORATORY - the ladder advances one rung at a time (spec
    §14.2)."""
    evaluation = evaluate(
        hypothesis_id="h",
        falsification_criteria=[],
        stats_result=_m5_result(),
        economic_threshold=100.0,
    )
    assert decide_transition(evaluation) == "FAILED_ROBUSTNESS"
    assert transition_is_legal("EXPLORATORY", "FAILED_ROBUSTNESS") is True
    # NOT_TESTED cannot jump straight there
    assert transition_is_legal("NOT_TESTED", "FAILED_ROBUSTNESS") is False


def test_the_invalid_transition_is_legal_from_not_tested() -> None:
    evaluation = evaluate(
        hypothesis_id="h",
        falsification_criteria=[],
        stats_result=None,
    )
    assert transition_is_legal("NOT_TESTED", decide_transition(evaluation))


def test_an_illegal_transition_is_rejected() -> None:
    assert transition_is_legal("NOT_TESTED", "PASSED_RESEARCH_VALIDATION") is False


def test_the_min_horizon_n_is_the_spec_threshold() -> None:
    assert MIN_HORIZON_N == 30
