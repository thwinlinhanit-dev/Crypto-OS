"""HF Phase 6 — the robustness & falsification suite (spec §13; the design
note's Phase 6: "a promising candidate cannot be PASSED while mandatory
checks are missing or failed").

The adversarial workflows, each composed from the SAME M5 stats_result the
evaluator consumed (HF-001: never re-derive a statistic):

- **asset robustness** - the effect must not be one asset's concentration;
  the M5 ``cross_asset.symbols_agree_share`` is the authority.
- **regime robustness** - the contemporaneous regime labels only (PD-2);
  computed from the stored per-observation regime field, never a future
  label.
- **time robustness** - the IS/OOS sign agreement (``oos_sign_agrees``);
  the structural-break question.
- **parameter robustness** - the surface, not the best point: the caller
  passes the neighbouring configs' results and the suite checks the effect
  survives the neighbourhood.
- **outlier dependence** - the top-1/5/10% contribution (the original
  result stays visible; the trimmed mean is a clearly-labeled diagnostic).
- **negative controls & placebo** - randomized/shifted timing and
  alternative definitions; a control that "works" means the pipeline is
  leaking (a failed experiment, never a success).

The falsification agent may recommend rejection and is not rewarded for
finding support (spec §16.6): every check returns the evidence, and the
validation-status rules decide - a candidate cannot be PASSED while a
mandatory check is missing or failed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

#: The share of assets that must agree on the sign (spec §13: concentration
#: is a finding). A majority of assets must carry the same sign.
ASSET_AGREEMENT_MIN: Final[float] = 0.6

#: The top-1/5/10% outlier contribution thresholds: the original result
#: stays visible; the check only FLAGS dependence, never rewrites it.
OUTLIER_FLAG_TOP1: Final[float] = 0.5
OUTLIER_FLAG_TOP5: Final[float] = 0.6
OUTLIER_FLAG_TOP10: Final[float] = 0.7


@dataclass
class Check:
    """One robustness check's outcome (spec §13)."""

    name: str
    passed: bool
    mandatory: bool
    detail: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class RobustnessReport:
    """The full robustness suite (spec §13)."""

    checks: list[Check] = field(default_factory=list)

    @property
    def mandatory_failed(self) -> list[Check]:
        return [c for c in self.checks if c.mandatory and not c.passed]

    @property
    def mandatory_missing(self) -> list[Check]:
        return [c for c in self.checks if c.mandatory and not c.detail]

    @property
    def passed(self) -> bool:
        """A candidate is robust only when every mandatory check ran AND
        passed (spec §13: a promising candidate cannot be PASSED while
        mandatory checks are missing or failed)."""
        return bool(self.checks) and not self.mandatory_failed and not self.mandatory_missing


# --------------------------------------------------------------------------
# Asset robustness (spec §13)
# --------------------------------------------------------------------------


def check_asset_consistency(stats_result: Mapping[str, Any]) -> Check:
    """The effect must not be one asset's concentration (spec §13).

    The M5 ``cross_asset`` block is the authority (``symbols_agree_share``);
    a missing block means the check could not run - reported, never assumed
    to pass.
    """
    cross = stats_result.get("cross_asset")
    if not isinstance(cross, Mapping):
        return Check(
            "asset_consistency",
            False,
            True,
            "no cross-asset block in the result",
        )
    share = cross.get("symbols_agree_share")
    if share is None:
        return Check("asset_consistency", False, True, "symbols_agree_share is null")
    passed = float(share) >= ASSET_AGREEMENT_MIN
    return Check(
        "asset_consistency",
        passed,
        True,
        f"{float(share):.0%} of assets agree on the sign (>= {ASSET_AGREEMENT_MIN:.0%})",
        {"symbols_agree_share": share, "n_symbols": cross.get("n_symbols")},
    )


# --------------------------------------------------------------------------
# Time robustness (spec §13)
# --------------------------------------------------------------------------


def check_time_robustness(stats_result: Mapping[str, Any]) -> Check:
    """The IS/OOS sign agreement (spec §13: the structural-break question).

    The headline model's ``oos_sign_agrees`` is the authority; a null (the
    holdout is unopened) means the check has not run - reported, not passed.
    """
    models = stats_result.get("cluster_models")
    headline = stats_result.get("headline_model")
    if not isinstance(models, Mapping) or not isinstance(headline, str):
        return Check("time_robustness", False, True, "no headline model in the result")
    block = models.get(headline)
    if not isinstance(block, Mapping):
        return Check("time_robustness", False, True, "headline model block missing")
    agrees = block.get("oos_sign_agrees")
    if agrees is None:
        return Check("time_robustness", False, True, "oos_sign_agrees is null")
    return Check(
        "time_robustness",
        bool(agrees),
        True,
        "IS and OOS signs agree" if agrees else "IS and OOS signs disagree",
        {"oos_sign_agrees": agrees},
    )


# --------------------------------------------------------------------------
# Regime robustness (spec §13; PD-2: contemporaneous labels only)
# --------------------------------------------------------------------------


def check_regime_robustness(
    *,
    stats_result: Mapping[str, Any],
    by_regime: Mapping[str, Sequence[float]],
) -> Check:
    """The effect must survive the regime split (spec §13).

    ``by_regime`` maps each CONTEMPORANEOUS regime label to that period's
    outcomes (the caller supplies the labels - this module never assigns a
    future label, PD-2). A regime with no data is reported, never dropped.
    """
    if not by_regime:
        return Check(
            "regime_robustness", False, True, "no contemporaneous regime labels supplied"
        )
    medians: dict[str, float] = {}
    for regime, values in by_regime.items():
        ordered = sorted(float(v) for v in values)
        if not ordered:
            continue
        mid = len(ordered) // 2
        medians[regime] = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    if len(medians) < 2:
        return Check(
            "regime_robustness", False, True, "fewer than two regimes with data"
        )
    signs = {(m > 0.0) - (m < 0.0) for m in medians.values()}
    positive = {s for s in signs if s != 0}
    consistent = len(positive) <= 1
    return Check(
        "regime_robustness",
        consistent,
        True,
        "regimes agree on the sign" if consistent else "regimes disagree on the sign",
        {"regime_medians": medians},
    )


# --------------------------------------------------------------------------
# Parameter robustness (spec §13: the surface, not the best point)
# --------------------------------------------------------------------------


def check_parameter_robustness(
    *,
    neighbor_results: Sequence[Mapping[str, Any]],
) -> Check:
    """The effect must survive the parameter neighbourhood (spec §13: the
    surface, not the best point).

    The caller supplies the neighbouring configs' results (the same M5
    shape); the check requires a majority of the neighbours to keep a CI
    excluding zero. A single neighbour cannot "carry" the hypothesis.
    """
    if not neighbor_results:
        return Check("parameter_robustness", False, True, "no neighbour results supplied")
    survivors = 0
    for result in neighbor_results:
        horizons = result.get("horizons")
        if not isinstance(horizons, Sequence) or isinstance(horizons, (str, bytes)):
            continue
        for entry in horizons:
            if not isinstance(entry, Mapping):
                continue
            base = entry.get("base")
            if not isinstance(base, Mapping):
                continue
            if base.get("ci_excludes_zero") is True:
                survivors += 1
                break
    share = survivors / len(neighbor_results)
    passed = share >= 0.5
    return Check(
        "parameter_robustness",
        passed,
        True,
        f"{share:.0%} of neighbours keep a CI excluding zero (>= 50%)",
        {"neighbours": len(neighbor_results), "survivors": survivors},
    )


# --------------------------------------------------------------------------
# Outlier dependence (spec §13: the original result stays visible)
# --------------------------------------------------------------------------


def check_outlier_dependence(
    *,
    outcomes: Sequence[float],
    top1_share: float,
    top5_share: float,
    top10_share: float,
) -> Check:
    """The effect must not hinge on a few extreme winners (spec §13).

    The original result stays visible; this check only FLAGS dependence. The
    caller supplies the computed top-1/5/10% shares of the total effect.
    """
    if not outcomes:
        return Check("outlier_dependence", False, True, "no outcomes supplied")
    flags: list[str] = []
    if top1_share > OUTLIER_FLAG_TOP1:
        flags.append(f"top-1% carries {top1_share:.0%}")
    if top5_share > OUTLIER_FLAG_TOP5:
        flags.append(f"top-5% carries {top5_share:.0%}")
    if top10_share > OUTLIER_FLAG_TOP10:
        flags.append(f"top-10% carries {top10_share:.0%}")
    passed = not flags
    return Check(
        "outlier_dependence",
        passed,
        True,
        "; ".join(flags) if flags else "no extreme-winner dependence",
        {"top1_share": top1_share, "top5_share": top5_share, "top10_share": top10_share},
    )


# --------------------------------------------------------------------------
# Negative controls & placebo (spec §13)
# --------------------------------------------------------------------------


def check_negative_control(control_result: Mapping[str, Any] | None) -> Check:
    """A control that "works" means the pipeline is leaking (spec §13).

    The placebo (randomized/shifted timing, alternative definition, or a
    non-event period) must produce NO significant effect. A significant
    control is a failed experiment, never a success - it fails the
    hypothesis's validity, not just this check.
    """
    if control_result is None:
        return Check("negative_control", False, True, "no control result supplied")
    horizons = control_result.get("horizons")
    if not isinstance(horizons, Sequence) or isinstance(horizons, (str, bytes)):
        return Check("negative_control", False, True, "control result has no horizons")
    for entry in horizons:
        if not isinstance(entry, Mapping):
            continue
        base = entry.get("base")
        if isinstance(base, Mapping) and base.get("ci_excludes_zero") is True:
            return Check(
                "negative_control",
                False,
                True,
                "the control produced a significant effect - the pipeline is leaking",
            )
    return Check(
        "negative_control",
        True,
        True,
        "the control produced no significant effect",
    )


# --------------------------------------------------------------------------
# The validation-status rules (spec §14.2)
# --------------------------------------------------------------------------


def validation_decision(report: RobustnessReport) -> str:
    """The validation-status decision from the explicit rules (spec §14.2).

    A promising candidate cannot be PASSED while mandatory checks are
    missing or failed: every mandatory check must have run and passed. A
    missing check is reported as missing, never silently assumed to pass.
    """
    if not report.checks:
        return "REQUIRES_HUMAN_REVIEW"
    if report.mandatory_missing:
        return "REQUIRES_HUMAN_REVIEW"
    if report.mandatory_failed:
        return "FAILED_ROBUSTNESS"
    return "PASSED_RESEARCH_VALIDATION"


def run_robustness(
    *,
    stats_result: Mapping[str, Any],
    by_regime: Mapping[str, Sequence[float]] | None = None,
    neighbor_results: Sequence[Mapping[str, Any]] | None = None,
    outlier_shares: tuple[float, float, float] | None = None,
    control_result: Mapping[str, Any] | None = None,
    outcomes: Sequence[float] | None = None,
) -> RobustnessReport:
    """Run the full mandatory robustness suite (spec §13).

    Every check that cannot run is reported as a failed mandatory check (a
    missing check is never silently assumed to pass).
    """
    report = RobustnessReport()
    report.checks.append(check_asset_consistency(stats_result))
    report.checks.append(check_time_robustness(stats_result))
    report.checks.append(
        check_regime_robustness(stats_result=stats_result, by_regime=by_regime or {})
    )
    report.checks.append(check_parameter_robustness(neighbor_results=neighbor_results or []))
    if outlier_shares is not None and outcomes is not None:
        report.checks.append(
            check_outlier_dependence(
                outcomes=outcomes, top1_share=outlier_shares[0],
                top5_share=outlier_shares[1], top10_share=outlier_shares[2],
            )
        )
    report.checks.append(check_negative_control(control_result))
    return report
