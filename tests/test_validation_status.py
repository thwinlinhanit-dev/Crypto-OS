"""M7 (T7.1) — overall status tests (spec 9.4).

The overall status is a LIST, never a composite score: REJECTED when the
pre-registered kill criterion is met; PROMISING when no FAIL and Edge
PASS; VALIDATED only with every applicable test PASS, pooled trades >=100,
Level 1 trusted, a frozen experiment and a passed holdout.
"""

from __future__ import annotations

from engine.validation.levels import Status, TestResult
from engine.validation.status import overall_status


def _results(*statuses: tuple[str, Status]) -> list[TestResult]:
    return [TestResult(name=name, level=2, status=status) for name, status in statuses]


def _clean_two_level() -> list[TestResult]:
    """Every applicable test PASS; parity and liquidity/funding N/A."""
    return [
        TestResult("tradingview_parity", 1, "NOT_RUN"),
        TestResult("truncation_invariance", 1, "PASS"),
        TestResult("deterministic_replay", 1, "PASS"),
        TestResult("accounting_invariants", 1, "PASS"),
        TestResult("edge", 2, "PASS"),
        TestResult("oos", 2, "PASS"),
        TestResult("liquidity_funding", 3, "NOT_RUN"),
    ]


def test_kill_criterion_met_is_rejected() -> None:
    result = overall_status(
        _clean_two_level(), kill_criterion_met=True, trusted=True, frozen=True,
        holdout_opened_and_passed=True, pooled_trades=500,
    )
    assert result["status"] == "REJECTED"
    assert "kill criterion" in result["reason"]


def test_validated_requires_every_condition() -> None:
    result = overall_status(
        _clean_two_level(), kill_criterion_met=False, trusted=True, frozen=True,
        holdout_opened_and_passed=True, pooled_trades=150,
    )
    assert result["status"] == "VALIDATED"
    assert result["conditions"]["frozen_experiment"] is True


def test_not_validated_below_pooled_trades() -> None:
    result = overall_status(
        _clean_two_level(), kill_criterion_met=False, trusted=True, frozen=True,
        holdout_opened_and_passed=True, pooled_trades=50,
    )
    assert result["status"] == "PROMISING"
    assert any("pooled trades" in item for item in result["conditions"]["unmet"])


def test_not_validated_when_not_frozen() -> None:
    result = overall_status(
        _clean_two_level(), kill_criterion_met=False, trusted=True, frozen=False,
        holdout_opened_and_passed=True, pooled_trades=150,
    )
    assert result["status"] == "PROMISING"
    assert any("frozen" in item for item in result["conditions"]["unmet"])


def test_not_validated_when_not_trusted() -> None:
    result = overall_status(
        _clean_two_level(), kill_criterion_met=False, trusted=False, frozen=True,
        holdout_opened_and_passed=True, pooled_trades=150,
    )
    assert result["status"] == "PROMISING"
    assert any("trusted" in item for item in result["conditions"]["unmet"])


def test_not_validated_when_holdout_not_passed() -> None:
    result = overall_status(
        _clean_two_level(), kill_criterion_met=False, trusted=True, frozen=True,
        holdout_opened_and_passed=False, pooled_trades=150,
    )
    assert result["status"] == "PROMISING"
    assert any("holdout" in item for item in result["conditions"]["unmet"])


def test_edge_fail_is_inconclusive_not_promising() -> None:
    results = _results(("edge", "FAIL"), ("oos", "PASS"))
    result = overall_status(
        results, kill_criterion_met=False, trusted=True, frozen=True,
        holdout_opened_and_passed=True, pooled_trades=500,
    )
    assert result["status"] == "INCONCLUSIVE"
    assert result["blocking_tests"]["edge"] == "FAIL"


def test_applicable_insufficient_blocks_validated() -> None:
    results = _clean_two_level()
    results.append(TestResult("walk_forward", 2, "INSUFFICIENT"))
    result = overall_status(
        results, kill_criterion_met=False, trusted=True, frozen=True,
        holdout_opened_and_passed=True, pooled_trades=500,
    )
    assert result["status"] == "PROMISING"
    assert any("walk_forward" in item for item in result["conditions"]["unmet"])


def test_na_parity_and_liquidity_do_not_block() -> None:
    """NOT_RUN is acceptable only for parity-without-export and
    liquidity/funding (9.3/9.4)."""
    result = overall_status(
        _clean_two_level(), kill_criterion_met=False, trusted=True, frozen=True,
        holdout_opened_and_passed=True, pooled_trades=500,
    )
    assert result["status"] == "VALIDATED"
