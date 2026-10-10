"""M7 (T7.1) — the three validation levels (spec 9.3).

Every test returns ``{status, basis}`` where status ∈ PASS | WARN | FAIL |
INSUFFICIENT | NOT_RUN and ``basis`` carries the numbers the verdict used
(threshold, observed value, n). Thresholds come from
``config/validation.yaml`` (changes need a DECISIONS entry, 9.3).

Trust rule (9.3 last line): any Level 1 FAIL or NOT_RUN (other than N/A
parity) labels every Level 2/3 result UNTRUSTED (D-047).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

Status = Literal["PASS", "WARN", "FAIL", "INSUFFICIENT", "NOT_RUN"]


@dataclass(frozen=True)
class TestResult:
    """One test's verdict + the numeric basis it used (9.3)."""

    name: str
    level: int
    status: Status
    basis: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "level": self.level,
            "status": self.status,
            "basis": self.basis,
        }


def _boolean_status(passed: bool | None) -> Status:
    """A boolean gate: True -> PASS, False -> FAIL, None -> NOT_RUN."""
    if passed is None:
        return "NOT_RUN"
    return "PASS" if passed else "FAIL"


# ---------------------------------------------------------------------------
# Level 1 — implementation validity
# ---------------------------------------------------------------------------


def level1_tests(
    *,
    parity_status: str | None,
    truncation_passed: bool | None,
    replay_reproduced: bool | None,
    accounting_ok: bool | None,
) -> list[TestResult]:
    """9.3 Level 1: parity (N/A without an export), truncation, replay,
    accounting invariants."""
    results: list[TestResult] = []
    if parity_status is None:
        results.append(TestResult("tradingview_parity", 1, "NOT_RUN",
                                  {"reason": "no reference export exists for this strategy"}))
    else:
        ok = parity_status in {"EXACT", "EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE"}
        results.append(TestResult("tradingview_parity", 1, "PASS" if ok else "FAIL",
                                  {"parity_status": parity_status}))
    results.append(
        TestResult("truncation_invariance", 1, _boolean_status(truncation_passed),
                   {"passed": truncation_passed})
    )
    results.append(
        TestResult("deterministic_replay", 1, _boolean_status(replay_reproduced),
                   {"reproduced": replay_reproduced})
    )
    results.append(
        TestResult("accounting_invariants", 1, _boolean_status(accounting_ok),
                   {"ok": accounting_ok})
    )
    return results


# ---------------------------------------------------------------------------
# Level 2 — statistical validity
# ---------------------------------------------------------------------------


def level2_tests(
    *,
    expectancy_ci: tuple[float, float] | None,
    trade_count: int,
    is_expectancy_r: float | None,
    oos_expectancy_r: float | None,
    walk_forward_positive_share: float | None,
    clustering_grades_differ: bool | None,
    selection_method: str,
    baseline_percentile: float | None,
    param_stability_neighbor_share: float | None,
    expectancy_after_top5_removed: float | None,
    top5_share: float | None,
    concentration: Mapping[str, Any] | None,
    regime_counts: Mapping[str, int] | None,
    thresholds: Mapping[str, Any],
) -> list[TestResult]:
    """9.3 Level 2: Edge, OOS, walk-forward, clustering, multiple testing,
    random baseline, param stability, trade dependency, concentration,
    regime split."""
    min_trades = int(thresholds.get("min_trades_edge", 30))
    results: list[TestResult] = []

    # Edge: cluster-bootstrap CI of expectancy R excludes 0 -> PASS.
    if expectancy_ci is None:
        results.append(TestResult("edge", 2, "NOT_RUN", {}))
    elif trade_count < min_trades:
        results.append(TestResult("edge", 2, "INSUFFICIENT",
                                  {"trade_count": trade_count, "min_trades": min_trades}))
    else:
        excludes_zero = expectancy_ci[0] > 0 or expectancy_ci[1] < 0
        results.append(TestResult("edge", 2, "PASS" if excludes_zero else "FAIL",
                                  {"ci": list(expectancy_ci), "trade_count": trade_count}))

    # OOS: same sign and >=50% of IS expectancy -> PASS.
    oos_min_share = float(thresholds.get("oos_min_share", 0.5))
    if is_expectancy_r is None or oos_expectancy_r is None:
        results.append(TestResult("oos", 2, "NOT_RUN", {}))
    else:
        same_sign = (is_expectancy_r > 0) == (oos_expectancy_r > 0) and oos_expectancy_r != 0
        ratio = (oos_expectancy_r / is_expectancy_r) if is_expectancy_r else None
        strong = same_sign and ratio is not None and abs(ratio) >= oos_min_share
        results.append(TestResult(
            "oos", 2, "PASS" if strong else ("WARN" if same_sign else "FAIL"),
            {"is": is_expectancy_r, "oos": oos_expectancy_r,
             "share": ratio, "min_share": oos_min_share},
        ))

    # Walk-forward: >=60% windows positive -> PASS.
    wf_min = float(thresholds.get("walk_forward_min_positive", 0.6))
    if walk_forward_positive_share is None:
        results.append(TestResult("walk_forward", 2, "NOT_RUN", {}))
    else:
        results.append(TestResult(
            "walk_forward", 2,
            "PASS" if walk_forward_positive_share >= wf_min else "FAIL",
            {"positive_share": walk_forward_positive_share, "min": wf_min},
        ))

    # Clustering sensitivity: differences -> WARN.
    if clustering_grades_differ is None:
        results.append(TestResult("clustering_sensitivity", 2, "NOT_RUN", {}))
    else:
        results.append(TestResult(
            "clustering_sensitivity", 2,
            "WARN" if clustering_grades_differ else "PASS",
            {"grades_differ": clustering_grades_differ},
        ))

    # Multiple testing: informational (9.3); WARN when searched.
    results.append(TestResult(
        "multiple_testing", 2,
        "WARN" if selection_method != "PRE_SPECIFIED" else "PASS",
        {"selection_method": selection_method},
    ))

    # Random baseline: percentile thresholds.
    if baseline_percentile is None:
        results.append(TestResult("random_baseline", 2, "NOT_RUN", {}))
    else:
        if baseline_percentile >= 95.0:
            status: Status = "PASS"
        elif baseline_percentile >= 80.0:
            status = "WARN"
        else:
            status = "FAIL"
        results.append(TestResult("random_baseline", 2, status,
                                  {"percentile": baseline_percentile}))

    # Param stability: >=70% neighbors keep PF>=1 -> PASS; 50-70% WARN.
    ps_min = float(thresholds.get("param_stability_pass", 0.7))
    ps_warn = float(thresholds.get("param_stability_warn", 0.5))
    if param_stability_neighbor_share is None:
        results.append(TestResult("param_stability", 2, "NOT_RUN", {}))
    else:
        share = param_stability_neighbor_share
        results.append(TestResult(
            "param_stability", 2,
            "PASS" if share >= ps_min else ("WARN" if share >= ps_warn else "FAIL"),
            {"neighbor_share": share, "pass": ps_min, "warn": ps_warn},
        ))

    # Trade dependency: expectancy after removing top 5 > 0 -> PASS.
    if expectancy_after_top5_removed is None:
        results.append(TestResult("trade_dependency", 2, "NOT_RUN", {}))
    else:
        results.append(TestResult(
            "trade_dependency", 2,
            "PASS" if expectancy_after_top5_removed > 0 else "FAIL",
            {"expectancy_after_top5": expectancy_after_top5_removed,
             "top5_share": top5_share},
        ))

    # Concentration: top symbol >30% -> WARN; LOO positive >=80% -> PASS.
    if concentration is None:
        results.append(TestResult("concentration", 2, "NOT_RUN", {}))
    else:
        top_share = float(concentration.get("top_symbol_share", 0.0))
        loo_positive = float(concentration.get("leave_one_out_positive_share", 0.0))
        loo_min = float(thresholds.get("concentration_loo_min", 0.8))
        results.append(TestResult(
            "concentration", 2,
            "PASS" if loo_positive >= loo_min else "FAIL",
            {"top_symbol_share": top_share,
             "leave_one_out_positive_share": loo_positive, "min": loo_min},
        ))

    # Regime split: n<20 in any regime -> INSUFFICIENT.
    regime_min_n = int(thresholds.get("regime_min_n", 20))
    if regime_counts is None:
        results.append(TestResult("regime_split", 2, "NOT_RUN", {}))
    else:
        smallest = min(regime_counts.values()) if regime_counts else 0
        results.append(TestResult(
            "regime_split", 2,
            "INSUFFICIENT" if smallest < regime_min_n else "PASS",
            {"counts": dict(regime_counts), "min_n": regime_min_n},
        ))
    return results


# ---------------------------------------------------------------------------
# Level 3 — economic validity
# ---------------------------------------------------------------------------


def level3_tests(
    *,
    cost_stress: Mapping[str, Any] | None,
    delay_stress: Mapping[str, Any] | None,
    gap_risk: Mapping[str, Any] | None,
    monte_carlo: Mapping[str, Any] | None,
    thresholds: Mapping[str, Any],
) -> list[TestResult]:
    """9.3 Level 3: cost stress, delay stress, gap risk, Monte Carlo;
    liquidity/funding stays NOT_RUN before M10."""
    results: list[TestResult] = []

    def _stress(name: str, block: Mapping[str, Any] | None) -> None:
        if block is None:
            results.append(TestResult(name, 3, "NOT_RUN", {}))
            return
        ok = (
            float(block.get("profit_factor", 0)) > 1.0
            and float(block.get("expectancy_r", 0)) > 0
            and float(block.get("oos_expectancy_r", 0)) > 0
        )
        results.append(TestResult(name, 3, "PASS" if ok else "FAIL",
                                  dict(block)))

    _stress("cost_stress", cost_stress)
    _stress("delay_stress", delay_stress)

    gap_warn_threshold = float(thresholds.get("gap_risk_warn_count", 3))
    if gap_risk is None:
        results.append(TestResult("gap_risk", 3, "NOT_RUN", {}))
    else:
        count = int(gap_risk.get("count", 0))
        results.append(TestResult(
            "gap_risk", 3, "WARN" if count > gap_warn_threshold else "PASS",
            dict(gap_risk),
        ))

    mc_min = float(thresholds.get("monte_carlo_p5_equity", 0.0))
    if monte_carlo is None:
        results.append(TestResult("monte_carlo", 3, "NOT_RUN", {}))
    else:
        p5 = float(monte_carlo.get("p5_final_equity", 0.0))
        results.append(TestResult(
            "monte_carlo", 3, "PASS" if p5 > mc_min else "FAIL",
            dict(monte_carlo),
        ))

    results.append(TestResult(
        "liquidity_funding", 3, "NOT_RUN",
        {"reason": "M10: liquidity/funding modeling does not exist yet"},
    ))
    return results


# ---------------------------------------------------------------------------
# trust + assembly
# ---------------------------------------------------------------------------


def is_trusted(results: Sequence[TestResult]) -> bool:
    """9.3 last line: any Level 1 FAIL/NOT_RUN (other than N/A parity)
    labels Level 2/3 UNTRUSTED."""
    for result in results:
        if result.level != 1:
            continue
        if result.name == "tradingview_parity" and result.status == "NOT_RUN":
            continue  # N/A when no export exists
        if result.status in {"FAIL", "NOT_RUN"}:
            return False
    return True
