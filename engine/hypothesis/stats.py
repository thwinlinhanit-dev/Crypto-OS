"""HF Phase 4 — the statistical engine (spec §11/§12; design note
docs/hypothesis-factory/RESEARCH_METHODOLOGY.md).

The HF composes the M5 stats engine (HF-001: never re-implement a test).
This module adds the HF-specific layers the master prompt requires:

- forward-return analysis with declared horizons + the economic-significance
  split (spec §11.2/§11.3 — statistical ≠ economic);
- multiple-testing control with the family declared BEFORE the inferential
  analysis (spec §12, HF-005): Benjamini-Hochberg FDR first.

Every result carries the tested claim, the method, the raw + adjusted p,
the baseline, the effect size and the outlier sensitivity (spec §11.1). A
p-value is never reported without the claim and the method.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

#: The master prompt's forward-return horizons (spec §11.2: configurable;
#: these are the initial set).
DEFAULT_HORIZONS: Final[tuple[int, ...]] = (1, 3, 5, 10, 20, 40)


@dataclass(frozen=True)
class HorizonResult:
    """One horizon's analysis (spec §11.2)."""

    horizon: int
    n: int
    mean: float
    median: float
    win_rate: float
    baseline_mean: float
    difference: float
    effect_size: float
    raw_p: float
    adjusted_p: float | None
    economically_significant: bool
    outlier_sensitivity: float | None = None


@dataclass
class HypothesisTestResult:
    """The complete result of testing one hypothesis (spec §11.1)."""

    hypothesis_id: str
    claim: str
    method: str
    family_id: str
    correction_plan: str
    horizons: list[HorizonResult] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    conclusion: str = "INCONCLUSIVE"


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """The Benjamini-Hochberg FDR adjustment (spec §12's first method).

    Returns the adjusted p-values in the input order. Monotone enforcement
    (the standard step-up variant) keeps the sequence non-decreasing.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running_min = 1.0
    for rank in range(m, 0, -1):
        index = order[rank - 1]
        value = p_values[index] * m / rank
        running_min = min(running_min, value)
        adjusted[index] = min(1.0, running_min)
    return adjusted


def bonferroni(p_values: Sequence[float]) -> list[float]:
    """The Bonferroni family-wise-error adjustment (spec §12's conservative
    option)."""
    m = len(p_values)
    if m == 0:
        return []
    return [min(1.0, p * m) for p in p_values]


def apply_correction(
    correction_plan: str,
    p_values: Sequence[float],
) -> list[float]:
    """Apply the declared correction plan (spec §12: the plan is declared
    BEFORE the analysis, never chosen after seeing the results)."""
    if correction_plan == "BENJAMINI_HOCHBERG":
        return benjamini_hochberg(p_values)
    if correction_plan == "BONFERRONI":
        return bonferroni(p_values)
    if correction_plan in ("WHITE_REALITY_CHECK", "DEFLATED_SHARPE"):
        # the strategy-selection corrections need a returns matrix (Phase 6);
        # until then they are declared but not silently substituted
        raise NotImplementedError(
            f"{correction_plan} requires the strategy-return matrix (Phase 6)"
        )
    raise ValueError(f"unknown correction plan {correction_plan!r}")


def cohens_d(treatment: Sequence[float], baseline: Sequence[float]) -> float:
    """Cohen's d effect size (spec §11.1: an effect size is always reported)."""
    if not treatment or not baseline:
        return 0.0
    n1, n0 = len(treatment), len(baseline)
    mean1: float = sum(treatment) / n1
    mean0: float = sum(baseline) / n0
    var1: float = sum((x - mean1) ** 2 for x in treatment) / max(1, n1 - 1)
    var0: float = sum((x - mean0) ** 2 for x in baseline) / max(1, n0 - 1)
    pooled: float = float(((n1 - 1) * var1 + (n0 - 1) * var0) / max(1, n1 + n0 - 2))
    if pooled <= 0:
        return 0.0
    difference: float = mean1 - mean0
    std: float = pooled**0.5
    return difference / std


def outlier_sensitivity(values: Sequence[float]) -> float:
    """The full-sample mean minus the mean excluding the top 10% (spec
    §13.5: "the effect of the largest winning observations")."""
    if len(values) < 10:
        return 0.0
    ordered = sorted(values)
    trimmed = ordered[: max(1, int(len(ordered) * 0.9))]
    full_mean = sum(ordered) / len(ordered)
    trimmed_mean = sum(trimmed) / len(trimmed)
    return full_mean - trimmed_mean


def median(values: Sequence[float]) -> float:
    """The median (spec §11.1)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def win_rate(values: Sequence[float]) -> float:
    """The fraction of positive outcomes (spec §11.2)."""
    if not values:
        return 0.0
    return sum(1 for v in values if v > 0) / len(values)


def test_hypothesis(
    *,
    hypothesis_id: str,
    claim: str,
    family_id: str,
    correction_plan: str,
    forward_returns: Mapping[int, Sequence[float]],
    baseline_returns: Mapping[int, Sequence[float]],
    raw_p_values: Mapping[int, float],
    economic_threshold: float | None,
) -> HypothesisTestResult:
    """Test one hypothesis across horizons (spec §11).

    The raw p-values come from the M5 stats engine (or the declared method);
    the HF applies the declared multiple-testing correction across the
    horizon family and reports the economic split (§11.3).
    """
    horizons = sorted(forward_returns)
    adjusted = apply_correction(
        correction_plan,
        [raw_p_values.get(h, 1.0) for h in horizons],
    )
    result = HypothesisTestResult(
        hypothesis_id=hypothesis_id,
        claim=claim,
        method=correction_plan,
        family_id=family_id,
        correction_plan=correction_plan,
    )
    for index, horizon in enumerate(horizons):
        treatment = forward_returns[horizon]
        baseline = baseline_returns.get(horizon, [])
        if not treatment:
            continue
        mean = sum(treatment) / len(treatment)
        baseline_mean = sum(baseline) / len(baseline) if baseline else 0.0
        difference = mean - baseline_mean
        _statistically_significant = adjusted[index] < 0.05
        economic = (
            economic_threshold is not None
            and abs(difference) >= abs(economic_threshold)
        )
        result.horizons.append(
            HorizonResult(
                horizon=horizon,
                n=len(treatment),
                mean=mean,
                median=median(treatment),
                win_rate=win_rate(treatment),
                baseline_mean=baseline_mean,
                difference=difference,
                effect_size=cohens_d(treatment, baseline) if baseline else 0.0,
                raw_p=raw_p_values.get(horizon, 1.0),
                adjusted_p=adjusted[index],
                economically_significant=bool(economic),
                outlier_sensitivity=outlier_sensitivity(treatment),
            )
        )
    result.conclusion = _conclusion(result)
    return result


def _conclusion(result: HypothesisTestResult) -> str:
    """The conclusion from the explicit rules (spec §11.3: statistical and
    economic significance are separate)."""
    if not result.horizons:
        return "INVALID_EXPERIMENT"
    statistical = any(h.adjusted_p is not None and h.adjusted_p < 0.05 for h in result.horizons)
    economic = any(h.economically_significant for h in result.horizons)
    if statistical and economic:
        return "EXPLORATORY_SUPPORT"
    if statistical and not economic:
        return "STATISTICAL_ONLY_NOT_ECONOMIC"
    return "NO_EFFECT_DETECTED"


def synthetic_check(
    *,
    treatment: Sequence[float],
    baseline: Sequence[float],
    correction_plan: str = "BENJAMINI_HOCHBERG",
    raw_p: float = 0.01,
    economic_threshold: float | None = None,
) -> HypothesisTestResult:
    """The single-horizon test used by the synthetic methodology tests
    (spec §23.3)."""
    horizon = 5
    return test_hypothesis(
        hypothesis_id="synthetic",
        claim="synthetic methodology check",
        family_id="synthetic-family",
        correction_plan=correction_plan,
        forward_returns={horizon: treatment},
        baseline_returns={horizon: baseline},
        raw_p_values={horizon: raw_p},
        economic_threshold=economic_threshold,
    )
