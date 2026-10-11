"""HF Phase 5 — the AI hypothesis-generation layer tests (spec §17).

The non-negotiable contract: every AI output is a proposal, never a
finding. A model cannot skip a required field, invent a feature, decide
what is a duplicate, or promote a hypothesis. These tests assert each of
those, plus the deterministic classification and the data-derived gaps.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Connection, create_engine, text

from apps.api.settings import get_settings
from engine.hypothesis import generate as hf_generate
from engine.hypothesis import registry as hf_registry

_RESET_SQL = (
    "TRUNCATE research.stats_result, research.research_ledger,"
    " research.hypothesis_relationship, research.hypothesis_version,"
    " research.hypothesis CASCADE"
)


@pytest.fixture()
def conn() -> Iterator[Connection]:
    engine = create_engine(
        get_settings().database_url, future=True, connect_args={"connect_timeout": 3}
    )
    try:
        connection = engine.connect()
    except Exception as exc:
        engine.dispose()
        pytest.skip(f"database unreachable: {exc}")
    try:
        yield connection
    finally:
        connection.close()
        engine.dispose()


def _reset(conn: Connection) -> None:
    # recover an aborted transaction first: a failed validation can leave the
    # session dirty, and the next fixture must not inherit it
    conn.rollback()
    conn.execute(text("DELETE FROM system.audit_log"))
    conn.execute(text(_RESET_SQL))
    conn.commit()


@pytest.fixture(autouse=True)
def clean(conn: Connection) -> Iterator[None]:
    _reset(conn)
    try:
        yield
    finally:
        _reset(conn)


def _spec(**over: Any) -> dict[str, Any]:
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
        "data_requirements": ["market.open_interest"],
        "proposed_statistical_method": "block-bootstrap mean difference",
        "falsification_criteria": ["effect vanishes after fees"],
    }
    spec.update(over)
    return spec


# --------------------------------------------------------------------------
# The proposal: AI output is a proposal, never a finding (spec §17.1)
# --------------------------------------------------------------------------


def test_a_valid_proposal_is_stored_as_ai_generated_and_not_tested(
    conn: Connection,
) -> None:
    proposal = hf_generate.propose_hypothesis(
        conn,
        title="OI contraction improves reclaim expectancy",
        domain="CAPITULATION_RECOVERY",
        spec=_spec(),
        original_question="Does extreme OI contraction improve reclaim signals?",
    )
    conn.commit()
    assert proposal.accepted is True
    assert proposal.hypotheses
    view = hf_registry.get_hypothesis(conn, proposal.hypotheses[0])
    # the model's proposal is AI_GENERATED and starts at NOT_TESTED - the
    # model can never author a status above it (spec §1.2.10)
    assert view["origin"] == "AI_GENERATED"
    assert view["status"] == "NOT_TESTED"


def test_a_proposal_missing_a_field_is_rejected_with_the_reason(
    conn: Connection,
) -> None:
    """A model cannot skip a required field: the SAME validate_definition a
    human's hypothesis passes through applies (spec §6)."""
    spec = _spec()
    del spec["falsification_criteria"]
    proposal = hf_generate.propose_hypothesis(
        conn, title="incomplete", domain="DERIVATIVES", spec=spec
    )
    conn.commit()
    assert proposal.accepted is False
    assert proposal.rejection is not None
    assert "missing_fields" in str(proposal.rejection_detail)
    # the rejection is preserved, never silently dropped (spec §1.2.5)
    assert proposal.hypotheses == []


def test_a_proposal_with_an_invented_feature_is_rejected(conn: Connection) -> None:
    """A model cannot invent a feature name (spec §7.1)."""
    proposal = hf_generate.propose_hypothesis(
        conn,
        title="magic",
        domain="DERIVATIVES",
        spec=_spec(features=["my_secret_edge"]),
    )
    conn.commit()
    assert proposal.accepted is False
    assert "my_secret_edge" in str(proposal.rejection_detail)


def test_a_proposal_with_a_look_ahead_rule_is_rejected(conn: Connection) -> None:
    """A model cannot smuggle look-ahead past the PD-2 guard."""
    proposal = hf_generate.propose_hypothesis(
        conn,
        title="leaky",
        domain="DERIVATIVES",
        spec=_spec(observation_ts_rule="the next bar's close_time"),
    )
    conn.commit()
    assert proposal.accepted is False
    assert "future information" in str(proposal.rejection)


def test_an_unknown_domain_is_rejected(conn: Connection) -> None:
    proposal = hf_generate.propose_hypothesis(
        conn, title="x", domain="ASTROLOGY", spec=_spec()
    )
    conn.commit()
    assert proposal.accepted is False
    assert "unknown research domain" in str(proposal.rejection)


# --------------------------------------------------------------------------
# The classification (spec §5): a guess is a fabrication
# --------------------------------------------------------------------------


def test_an_observation_maps_to_its_domain() -> None:
    assert hf_generate.classify("extreme open interest contraction") == "DERIVATIVES"
    assert hf_generate.classify("capitulation wick on the daily") == "CAPITULATION_RECOVERY"
    assert hf_generate.classify("ETH/BTC dominance spread") == "CROSS_ASSET"


def test_an_unmappable_observation_returns_none_not_a_guess() -> None:
    """Spec §1.2.2: a model must not fabricate - an unmappable observation
    returns None rather than a guessed domain."""
    assert hf_generate.classify("something entirely unrelated") is None


# --------------------------------------------------------------------------
# The duplicate review (spec §8): the model never decides
# --------------------------------------------------------------------------


def test_a_duplicate_proposal_sees_the_existing_hypothesis(conn: Connection) -> None:
    hf_registry.create_hypothesis(
        conn, title="A", domain="DERIVATIVES", origin="AI_GENERATED", spec=_spec()
    )
    conn.commit()
    review = hf_generate.review_duplicates(conn, _spec())
    assert len(review["matches"]) == 1
    assert review["matches"][0]["match"] == "EXACT"


def test_a_different_horizon_is_not_a_duplicate(conn: Connection) -> None:
    hf_registry.create_hypothesis(
        conn, title="A", domain="DERIVATIVES", origin="AI_GENERATED", spec=_spec()
    )
    conn.commit()
    review = hf_generate.review_duplicates(conn, _spec(outcome_horizon="40 bars"))
    assert review["matches"] == []


def test_an_invalid_duplicate_review_reports_the_error(conn: Connection) -> None:
    review = hf_generate.review_duplicates(conn, {"title": "vague"})
    assert review["matches"] == []
    assert review["error"] is not None


# --------------------------------------------------------------------------
# The experiment suggestions (spec §17.4): validated before returned
# --------------------------------------------------------------------------


def test_a_valid_candidate_config_is_accepted_with_a_hash(conn: Connection) -> None:
    hyp_id = hf_registry.create_hypothesis(
        conn, title="A", domain="DERIVATIVES", origin="HUMAN", spec=_spec()
    )
    conn.commit()
    suggestions = hf_generate.suggest_experiments(
        conn,
        hypothesis_id=hyp_id,
        configs=[
            {
                "version": 1,
                "spec": _spec(),
                "scope_family": "binance_spot_usdt_default",
                "data_scope": "DEVELOPMENT",
            }
        ],
    )
    assert len(suggestions) == 1
    assert suggestions[0]["accepted"] is True
    assert suggestions[0]["config_hash"]


def test_an_invalid_candidate_is_reported_not_dropped(conn: Connection) -> None:
    """A model cannot propose an unsupported method: the candidate is
    reported with its rejection, never silently dropped."""
    hyp_id = hf_registry.create_hypothesis(
        conn, title="A", domain="DERIVATIVES", origin="HUMAN", spec=_spec()
    )
    conn.commit()
    suggestions = hf_generate.suggest_experiments(
        conn,
        hypothesis_id=hyp_id,
        configs=[
            {
                "version": 1,
                "spec": _spec(proposed_statistical_method="vibes"),
                "scope_family": "binance_spot_usdt_default",
            }
        ],
    )
    assert suggestions[0]["accepted"] is False
    assert suggestions[0]["rejection"] is not None


# --------------------------------------------------------------------------
# The gaps (spec §7.3): from the data, never from the model
# --------------------------------------------------------------------------


def test_the_gaps_are_domains_with_no_hypothesis(conn: Connection) -> None:
    hf_registry.create_hypothesis(
        conn, title="A", domain="DERIVATIVES", origin="HUMAN", spec=_spec()
    )
    conn.commit()
    gaps = hf_generate.gaps(conn)
    domains = {g["domain"] for g in gaps}
    assert "DERIVATIVES" not in domains  # it has a hypothesis
    assert "NARRATIVE" in domains  # it does not


def test_the_research_questions_are_bounded(conn: Connection) -> None:
    questions = hf_generate.research_questions(conn, limit=2)
    assert len(questions) <= 2
    with pytest.raises(Exception):  # noqa: B017 - a bad limit fails closed
        hf_generate.research_questions(conn, limit=0)
