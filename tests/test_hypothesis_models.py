"""HF Phase 1 — the hypothesis model tests (spec §14.2, §6).

The status-transition table, the vocabulary checks and the validation
rules (an underspecified hypothesis is rejected, not silently accepted).
"""

from __future__ import annotations

from typing import Any

import pytest

from engine.errors import ValidationError
from engine.hypothesis.models import (
    ALLOWED_TRANSITIONS,
    assert_valid_domain,
    assert_valid_origin,
    assert_valid_relationship,
    assert_valid_status,
    assert_valid_transition,
    is_terminal,
)
from engine.hypothesis.registry import dedup_key, validate_definition


def _spec(**over: Any) -> dict[str, Any]:
    """A complete formal hypothesis (spec §6's required fields)."""
    spec: dict[str, Any] = {
        "null_hypothesis": "OI contraction does not improve reclaim expectancy.",
        "alternative_hypothesis": "OI contraction improves reclaim expectancy.",
        "independent_vars": ["capitulation_indicator", "oi_change_pct"],
        "dependent_vars": ["forward_return_5"],
        "features": ["rvol", "oi_change_pct"],
        "population": "BTC, ETH daily 2024-2026",
        "timeframe": "1D",
        "observation_ts_rule": "bar close_time of the reclaim bar",
        "outcome_horizon": "5 bars",
        "expected_direction": "HIGHER",
        "data_requirements": ["market.funding_rate", "market.open_interest"],
        "proposed_statistical_method": "block-bootstrap mean difference",
        "falsification_criteria": ["effect vanishes after fees"],
    }
    spec.update(over)
    return spec


# --------------------------------------------------------------------------
# The vocabularies
# --------------------------------------------------------------------------


def test_the_status_vocabulary_is_the_spec_list() -> None:
    assert "NOT_TESTED" in ALLOWED_TRANSITIONS
    assert "PASSED_RESEARCH_VALIDATION" in ALLOWED_TRANSITIONS
    assert "ARCHIVED" in ALLOWED_TRANSITIONS
    assert len(ALLOWED_TRANSITIONS) == 12


def test_an_unknown_status_is_rejected() -> None:
    with pytest.raises(ValidationError):
        assert_valid_status("PROFITABLE")


def test_an_unknown_domain_is_rejected() -> None:
    with pytest.raises(ValidationError):
        assert_valid_domain("ASTROLOGY")


def test_an_unknown_origin_is_rejected() -> None:
    with pytest.raises(ValidationError):
        assert_valid_origin("VIBES")


def test_an_unknown_relationship_is_rejected() -> None:
    with pytest.raises(ValidationError):
        assert_valid_relationship("LOVES")


# --------------------------------------------------------------------------
# The status transitions (spec §14.2)
# --------------------------------------------------------------------------


def test_a_legal_transition_passes() -> None:
    assert_valid_transition("NOT_TESTED", "EXPLORATORY")


def test_an_illegal_transition_is_rejected() -> None:
    """An AI model cannot promote a hypothesis by expressing confidence: a
    NOT_TESTED hypothesis cannot jump to PASSED_RESEARCH_VALIDATION."""
    with pytest.raises(ValidationError):
        assert_valid_transition("NOT_TESTED", "PASSED_RESEARCH_VALIDATION")


def test_the_holdout_failure_route_is_explicit() -> None:
    assert_valid_transition("PASSED_RESEARCH_VALIDATION", "FAILED_OUT_OF_SAMPLE")


def test_archived_is_terminal() -> None:
    assert is_terminal("ARCHIVED")
    assert not is_terminal("EXPLORATORY")
    with pytest.raises(ValidationError):
        assert_valid_transition("ARCHIVED", "EXPLORATORY")


# --------------------------------------------------------------------------
# The definition validation (spec §6/§10)
# --------------------------------------------------------------------------


def test_a_complete_spec_passes() -> None:
    validate_definition(_spec())


def test_a_missing_required_field_is_rejected() -> None:
    spec = _spec()
    del spec["falsification_criteria"]
    with pytest.raises(ValidationError) as excinfo:
        validate_definition(spec)
    assert "falsification_criteria" in str(excinfo.value.detail["missing_fields"])


def test_an_unknown_feature_is_rejected() -> None:
    """Agents cannot invent feature names (spec §7.1)."""
    spec = _spec(features=["my_secret_edge"])
    with pytest.raises(ValidationError) as excinfo:
        validate_definition(spec)
    assert "my_secret_edge" in excinfo.value.detail["unknown_features"]


def test_a_bad_direction_is_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_definition(_spec(expected_direction="UP"))


def test_a_missing_time_availability_rule_is_rejected() -> None:
    """PD-2: a hypothesis without a closed-bar observation rule is not
    causally evaluable."""
    with pytest.raises(ValidationError):
        validate_definition(_spec(observation_ts_rule="whenever"))


# --------------------------------------------------------------------------
# The dedup key (spec §8)
# --------------------------------------------------------------------------


def test_the_dedup_key_is_stable_for_the_same_claim() -> None:
    assert dedup_key(_spec()) == dedup_key(_spec())


def test_the_dedup_key_differs_for_a_different_horizon() -> None:
    assert dedup_key(_spec()) != dedup_key(_spec(outcome_horizon="10 bars"))


def test_the_dedup_key_is_feature_order_independent() -> None:
    a = _spec(features=["rvol", "oi_change_pct"])
    b = _spec(features=["oi_change_pct", "rvol"])
    assert dedup_key(a) == dedup_key(b)
