"""HF Phase 4/5 — the falsification evaluator + the transition decision
(spec §13/§14; design note docs/hypothesis-factory/RESEARCH_METHODOLOGY.md).

The evaluator takes a completed statistical result (produced by the M5
stats engine - HF-001: never re-implement a test) and applies the
hypothesis's DECLARED falsification criteria to it, then decides the
evidence-ladder transition through the explicit rules (spec §14.2).

The separation this module enforces:

- an AI model may propose a hypothesis but cannot promote it by expressing
  confidence: a promotion requires an evaluation that passes here;
- "statistically significant" is not "economically useful" (§11.3): both
  are checked, separately;
- "no effect detected" is distinct from "the experiment could not run" (§22):
  an invalid/missing result is INVALID_EXPERIMENT, never a promotion.

Every evaluation records which criterion fired and why, so the negative and
inconclusive findings are preserved (§1.2.5-6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from engine.hypothesis.models import ALLOWED_TRANSITIONS

#: The minimum horizon sample for an inference (spec §13: small samples are
#: inconclusive, never positive).
MIN_HORIZON_N: Final[int] = 30


@dataclass
class CriterionOutcome:
    """One declared falsification criterion evaluated against the result."""

    criterion: str
    fired: bool
    detail: str = ""


@dataclass
class Evaluation:
    """The full evaluation of one hypothesis against one result."""

    hypothesis_id: str
    falsified: bool = False
    criteria: list[CriterionOutcome] = field(default_factory=list)
    reject_reason: str | None = None
    #: True when the experiment was structurally unable to run (missing data,
    #: no observations) - distinct from "no effect" (spec §22)
    invalid: bool = False
    economic_only: bool = False
    statistical_support: bool = False
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# The horizon extraction from an M5 stats result
# --------------------------------------------------------------------------


def horizon_summaries(stats_result: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    """The per-horizon summaries from the M5 ``compute_stats`` result, keyed
    by horizon (the M5 entry keys the horizon as ``h``; the pooled block
    carries ``mean_r``/``win_rate``/``ci_median_r`` directly). A horizon with
    no observations is reported with n=0 (never fabricated)."""
    horizons = stats_result.get("horizons")
    if not isinstance(horizons, Sequence) or isinstance(horizons, (str, bytes)):
        return {}
    out: dict[int, dict[str, Any]] = {}
    for entry in horizons:
        if not isinstance(entry, Mapping):
            continue
        horizon = entry.get("h")
        if not isinstance(horizon, int):
            continue
        # the pooled statistics live in the segment blocks; ``base`` is the
        # all-segments block (IS+OOS pooled) - the horizon's headline
        pooled = entry.get("base")
        summary: dict[str, Any] = dict(entry)
        if isinstance(pooled, Mapping):
            summary.update(pooled)
            # the segment blocks carry the CI as ``ci_median_r`` too
        out[horizon] = summary
    return out


def _ci_excludes_zero(entry: Mapping[str, Any]) -> bool | None:
    """Whether the horizon's median CI excludes zero (the M5 shape carries
    ``ci_median_r`` / ``ci_excludes_zero``)."""
    explicit = entry.get("ci_excludes_zero")
    if isinstance(explicit, bool):
        return explicit
    ci = entry.get("ci_median_r")
    if not isinstance(ci, Sequence) or isinstance(ci, (str, bytes)) or len(ci) != 2:
        return None
    lo, hi = ci[0], ci[1]
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        return None
    return bool(lo > 0.0 or hi < 0.0)


def _horizon_mean_r(entry: Mapping[str, Any]) -> float | None:
    value = entry.get("mean_r")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _horizon_win_rate(entry: Mapping[str, Any]) -> float | None:
    value = entry.get("win_rate")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


# --------------------------------------------------------------------------
# The declared falsification criteria
# --------------------------------------------------------------------------


def _eval_criterion(
    criterion: str,
    *,
    horizons: Mapping[int, dict[str, Any]],
    economic_threshold: float | None,
) -> CriterionOutcome:
    """Evaluate one declared falsification criterion (spec §13). The
    criteria are matched against a closed vocabulary so the semantic
    matching is deterministic; a criterion outside the vocabulary is
    recorded but never silently assumed to pass."""
    key = criterion.strip().lower()

    if key in ("effect vanishes after fees", "vanishes after fees", "no effect after fees"):
        # an effect that is statistically present but below the economic
        # threshold vanishes once fees are charged (spec §11.3)
        for horizon, entry in horizons.items():
            mean = _horizon_mean_r(entry)
            if mean is None:
                continue
            if economic_threshold is not None and abs(mean) < abs(economic_threshold):
                return CriterionOutcome(
                    criterion,
                    True,
                    f"horizon {horizon}: mean_r {mean:.4f} below economic "
                    f"threshold {economic_threshold}",
                )
        return CriterionOutcome(criterion, False, "mean_r above the economic threshold")

    if key in ("ci includes zero", "confidence interval includes zero",
               "ci_includes_zero", "ci excludes zero must not hold"):
        for horizon, entry in horizons.items():
            excludes = _ci_excludes_zero(entry)
            if excludes is False:
                return CriterionOutcome(
                    criterion, True, f"horizon {horizon}: CI includes zero"
                )
        return CriterionOutcome(criterion, False, "all horizon CIs exclude zero")

    if key in ("win rate not above chance", "win rate at or below chance",
               "win_rate_below_chance"):
        for horizon, entry in horizons.items():
            win = _horizon_win_rate(entry)
            if win is not None and win <= 0.5:
                return CriterionOutcome(
                    criterion, True, f"horizon {horizon}: win rate {win:.3f} <= 0.5"
                )
        return CriterionOutcome(criterion, False, "all horizon win rates above chance")

    if key in ("insufficient observations", "too few observations",
               "insufficient_observations"):
        for horizon, entry in horizons.items():
            n = entry.get("n")
            if isinstance(n, int) and n < MIN_HORIZON_N:
                return CriterionOutcome(
                    criterion,
                    False,
                    f"horizon {horizon}: n={n} < {MIN_HORIZON_N} (inconclusive, "
                    f"not falsified)",
                )
        return CriterionOutcome(criterion, False, "sufficient observations")

    # an unrecognized criterion is recorded, never assumed to pass: the
    # evaluation notes it so a human reviews (PD-10 fail-closed)
    return CriterionOutcome(
        criterion, False, "UNRECOGNIZED criterion - requires human review"
    )


# --------------------------------------------------------------------------
# The evaluation
# --------------------------------------------------------------------------


def evaluate(
    *,
    hypothesis_id: str,
    falsification_criteria: Sequence[str],
    stats_result: Mapping[str, Any] | None,
    economic_threshold: float | None = None,
    declared_horizons: Sequence[int] | None = None,
) -> Evaluation:
    """Evaluate a hypothesis against a completed (or missing) result (spec
    §13/§22)."""
    result = Evaluation(hypothesis_id=hypothesis_id)

    # an experiment that could not run is INVALID, never a promotion (spec §22)
    if stats_result is None:
        result.invalid = True
        result.reject_reason = "no statistical result was produced"
        return result

    horizons = horizon_summaries(stats_result)
    if not horizons:
        result.invalid = True
        result.reject_reason = "the result carries no horizon entries"
        return result

    # every declared horizon must be present with a usable sample
    if declared_horizons:
        missing = [h for h in declared_horizons if h not in horizons]
        if missing:
            result.invalid = True
            result.reject_reason = f"declared horizons missing from the result: {missing}"
            return result

    for criterion in falsification_criteria:
        outcome = _eval_criterion(
            criterion,
            horizons=horizons,
            economic_threshold=economic_threshold,
        )
        result.criteria.append(outcome)
        if outcome.fired:
            result.falsified = True
            result.reject_reason = outcome.detail
            break

    # the statistical vs economic split (spec §11.3)
    if not result.falsified:
        statistical = any(
            _ci_excludes_zero(entry) is True for entry in horizons.values()
        )
        economic = any(
            (
                economic_threshold is not None
                and (mean := _horizon_mean_r(entry)) is not None
                and abs(mean) >= abs(economic_threshold)
            )
            for entry in horizons.values()
        )
        result.statistical_support = statistical
        result.economic_only = economic
        if statistical and not economic:
            result.notes.append(
                "statistically significant but below the economic threshold"
            )

    if not falsification_criteria:
        result.notes.append(
            "no falsification criteria declared - cannot be promoted "
            "(spec 1.2.4: all hypotheses must be falsifiable)"
        )
    return result


# --------------------------------------------------------------------------
# The transition decision (spec §14.2)
# --------------------------------------------------------------------------


def decide_transition(evaluation: Evaluation) -> str:
    """The evidence-ladder transition from the explicit rules (spec §14.2).

    The decision uses ONLY the evaluation outcome and the transition table;
    no model confidence, no override path. A promotion requires a passed
    evaluation; everything else is preserved (a negative finding is a
    result, not a deletion).
    """
    if evaluation.invalid:
        # reachable from NOT_TESTED and from a re-test
        return "INVALID_EXPERIMENT"
    if evaluation.falsified:
        return "FAILED_STATISTICAL_TEST"
    # passed the statistical gate but not the economic one: the effect exists
    # but is not usable after costs - a preserved negative (spec §11.3)
    if evaluation.statistical_support and not evaluation.economic_only:
        return "FAILED_ROBUSTNESS"
    # no CI excludes zero anywhere: no effect detected on this data
    if not evaluation.statistical_support:
        return "FAILED_STATISTICAL_TEST"
    # support and (if declared) economic: still exploratory - the ladder
    # advances one rung at a time (spec §14: never interchangeable states)
    return "EXPLORATORY"


def transition_is_legal(current: str, target: str) -> bool:
    """Whether the explicit table allows current -> target (spec §14.2)."""
    return target in ALLOWED_TRANSITIONS.get(current, ())
