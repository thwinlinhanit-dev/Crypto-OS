"""M7 (T7.1) — overall status mapping (spec 9.4).

The overall status is a **list, never a composite score** (9.4): a rejection
that cites its pre-registered kill criterion, a PROMISING verdict with its
conditions, or the explicit VALIDATED requirements. Serialized as a list
object (D-052).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from engine.validation.levels import TestResult

OverallStatus = Literal["REJECTED", "PROMISING", "VALIDATED", "INCONCLUSIVE"]


def overall_status(
    results: Sequence[TestResult],
    *,
    kill_criterion_met: bool,
    trusted: bool,
    frozen: bool,
    holdout_opened_and_passed: bool,
    pooled_trades: int,
    min_pooled_trades: int = 100,
) -> dict[str, Any]:
    """9.4: the overall status list with every condition that fed it.

    - kill criterion met -> REJECTED;
    - no FAIL and Edge PASS -> PROMISING;
    - VALIDATED requires ALL applicable tests PASS (no FAIL / INSUFFICIENT /
      NOT_RUN other than N/A parity and liquidity/funding), pooled trades
      >= 100, Level 1 trusted, a frozen experiment, and the holdout opened
      once and passed.
    """
    statuses = {result.name: result.status for result in results}
    has_fail = any(result.status == "FAIL" for result in results)
    edge = statuses.get("edge")

    # Applicable = every test except the two documented N/A-able ones.
    inapplicable = {
        name
        for name, status in statuses.items()
        if status == "NOT_RUN"
        and name in {"tradingview_parity", "liquidity_funding"}
    }
    blocking = {
        name: status
        for name, status in statuses.items()
        if status in {"FAIL", "INSUFFICIENT", "NOT_RUN"} and name not in inapplicable
    }

    if kill_criterion_met:
        return {
            "status": "REJECTED",
            "reason": "pre-registered kill criterion met",
            "blocking_tests": blocking,
        }
    if (
        not blocking
        and edge == "PASS"
        and pooled_trades >= min_pooled_trades
        and trusted
        and frozen
        and holdout_opened_and_passed
    ):
        return {
            "status": "VALIDATED",
            "conditions": {
                "all_tests_pass": True,
                "pooled_trades": pooled_trades,
                "min_pooled_trades": min_pooled_trades,
                "level1_trusted": trusted,
                "frozen_experiment": frozen,
                "holdout_passed": holdout_opened_and_passed,
            },
        }
    if not has_fail and edge == "PASS":
        return {
            "status": "PROMISING",
            "conditions": {
                "no_fail": True,
                "edge_pass": True,
                "unmet": _unmet(
                    blocking=dict(blocking),
                    pooled_trades=pooled_trades,
                    min_pooled_trades=min_pooled_trades,
                    trusted=trusted,
                    frozen=frozen,
                    holdout=holdout_opened_and_passed,
                ),
            },
        }
    return {
        "status": "INCONCLUSIVE",
        "reason": "edge not PASS or a FAIL is present",
        "blocking_tests": dict(blocking),
    }


def _unmet(
    *,
    blocking: dict[str, str],
    pooled_trades: int,
    min_pooled_trades: int,
    trusted: bool,
    frozen: bool,
    holdout: bool,
) -> list[str]:
    unmet: list[str] = []
    if blocking:
        unmet.append("applicable tests outstanding: " + ", ".join(sorted(blocking)))
    if pooled_trades < min_pooled_trades:
        unmet.append(f"pooled trades {pooled_trades} < {min_pooled_trades}")
    if not trusted:
        unmet.append("level 1 not trusted")
    if not frozen:
        unmet.append("experiment not frozen")
    if not holdout:
        unmet.append("holdout not opened and passed")
    return unmet
