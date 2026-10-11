"""HF Phase 1 — the hypothesis registry tests (spec §24 Phase 1 AC:
"hypotheses can be created, retrieved, versioned, and audited").

Live-DB suite: skip-guarded when the scratch cluster is unreachable
(D-076).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text

from apps.api.main import create_app
from apps.api.settings import get_settings
from engine.errors import ValidationError
from engine.hypothesis import registry as hf_registry


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


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def clean(conn: Connection) -> Iterator[None]:
    conn.execute(text("SET session_replication_role = replica"))
    for table in (
        "research.research_ledger", "research.hypothesis_relationship",
        "research.hypothesis_version", "research.hypothesis",
        "system.audit_log",
    ):
        conn.execute(text(f"DELETE FROM {table}"))
    conn.execute(text("SET session_replication_role = DEFAULT"))
    conn.commit()
    try:
        yield
    finally:
        conn.execute(text("SET session_replication_role = replica"))
        for table in (
            "research.research_ledger", "research.hypothesis_relationship",
            "research.hypothesis_version", "research.hypothesis",
            "system.audit_log",
        ):
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("SET session_replication_role = DEFAULT"))
        conn.commit()


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
# The Phase 1 AC: create, retrieve, version, audit
# --------------------------------------------------------------------------


def test_create_retrieve_and_audit(conn: Connection) -> None:
    """The AC: hypotheses can be created, retrieved, versioned and audited."""
    hyp_id = hf_registry.create_hypothesis(
        conn,
        title="OI contraction improves capitulation-reclaim expectancy",
        domain="CAPITULATION_RECOVERY",
        origin="HUMAN",
        spec=_spec(),
        original_question="Does extreme OI contraction improve reclaim signals?",
    )
    conn.commit()
    view = hf_registry.get_hypothesis(conn, hyp_id)
    assert view["hypothesis_id"] == hyp_id
    assert view["status"] == "NOT_TESTED"
    assert view["version"] == 1
    assert view["original_question"] == "Does extreme OI contraction improve reclaim signals?"
    assert view["spec"]["null_hypothesis"].startswith("OI contraction")
    audit = conn.execute(
        text(
            "SELECT action, target FROM system.audit_log"
            " WHERE action = 'hypothesis.create' AND target = :id"
        ),
        {"id": hyp_id},
    ).first()
    assert audit is not None, "the creation was audited"


def test_a_new_version_is_appended(conn: Connection) -> None:
    hyp_id = hf_registry.create_hypothesis(
        conn, title="v1", domain="DERIVATIVES", origin="AI_GENERATED", spec=_spec()
    )
    conn.commit()
    version = hf_registry.add_version(
        conn, hypothesis_id=hyp_id, spec=_spec(outcome_horizon="10 bars")
    )
    conn.commit()
    assert version == 2
    view = hf_registry.get_hypothesis(conn, hyp_id)
    assert view["version"] == 2
    assert view["spec"]["outcome_horizon"] == "10 bars"


def test_an_unknown_hypothesis_is_not_found(conn: Connection) -> None:
    from engine.errors import NotFoundError

    with pytest.raises(NotFoundError):
        hf_registry.get_hypothesis(conn, str(uuid4()))


# --------------------------------------------------------------------------
# The evidence graph (spec §18.2)
# --------------------------------------------------------------------------


def test_relationships_form_an_evidence_graph(conn: Connection) -> None:
    a = hf_registry.create_hypothesis(
        conn, title="A", domain="DERIVATIVES", origin="AI_GENERATED", spec=_spec()
    )
    b = hf_registry.create_hypothesis(
        conn,
        title="B",
        domain="DERIVATIVES",
        origin="PATTERN_DISCOVERY",
        spec=_spec(outcome_horizon="10 bars"),
    )
    conn.commit()
    hf_registry.add_relationship(
        conn, from_id=a, to_id=b, relationship="CONTRADICTS", rationale="opposite direction"
    )
    conn.commit()
    rows = conn.execute(
        text(
            "SELECT relationship, rationale FROM research.hypothesis_relationship"
            " WHERE from_hypothesis_id = :a AND to_hypothesis_id = :b"
        ),
        {"a": a, "b": b},
    ).all()
    assert len(rows) == 1
    assert rows[0].relationship == "CONTRADICTS"


def test_a_self_relationship_is_rejected(conn: Connection) -> None:
    a = hf_registry.create_hypothesis(
        conn, title="A", domain="REGIME", origin="HUMAN", spec=_spec()
    )
    conn.commit()
    with pytest.raises(ValidationError):
        hf_registry.add_relationship(conn, from_id=a, to_id=a, relationship="REFINES")


# --------------------------------------------------------------------------
# The research ledger (spec §12, HF-005)
# --------------------------------------------------------------------------


def test_the_testing_family_is_declared_before_analysis(conn: Connection) -> None:
    hyp_id = hf_registry.create_hypothesis(
        conn, title="A", domain="REGIME", origin="HUMAN", spec=_spec()
    )
    conn.commit()
    hf_registry.declare_testing_family(
        conn,
        family_id="oi-reclaim-family-001",
        hypothesis_id=hyp_id,
        correction_plan="BENJAMINI_HOCHBERG",
    )
    conn.commit()
    row = conn.execute(
        text(
            "SELECT family_id, correction_plan FROM research.research_ledger"
            " WHERE hypothesis_id = :id"
        ),
        {"id": hyp_id},
    ).first()
    assert row is not None
    assert row.family_id == "oi-reclaim-family-001"
    assert row.correction_plan == "BENJAMINI_HOCHBERG"


def test_an_unknown_correction_plan_is_rejected(conn: Connection) -> None:
    hyp_id = hf_registry.create_hypothesis(
        conn, title="A", domain="REGIME", origin="HUMAN", spec=_spec()
    )
    conn.commit()
    with pytest.raises(ValidationError):
        hf_registry.declare_testing_family(
            conn,
            family_id="f",
            hypothesis_id=hyp_id,
            correction_plan="CHERRY_PICKED",
        )


# --------------------------------------------------------------------------
# Dedup (spec §8)
# --------------------------------------------------------------------------


def test_an_exact_duplicate_is_found(conn: Connection) -> None:
    hf_registry.create_hypothesis(
        conn, title="A", domain="DERIVATIVES", origin="AI_GENERATED", spec=_spec()
    )
    conn.commit()
    matches = hf_registry.find_duplicates(conn, _spec())
    assert len(matches) == 1
    assert matches[0]["match"] == "EXACT"


def test_a_different_horizon_is_not_a_duplicate(conn: Connection) -> None:
    hf_registry.create_hypothesis(
        conn, title="A", domain="DERIVATIVES", origin="AI_GENERATED", spec=_spec()
    )
    conn.commit()
    assert hf_registry.find_duplicates(conn, _spec(outcome_horizon="40 bars")) == []


# --------------------------------------------------------------------------
# The status transitions through the registry
# --------------------------------------------------------------------------


def test_a_status_transition_is_audited(conn: Connection) -> None:
    hyp_id = hf_registry.create_hypothesis(
        conn, title="A", domain="REGIME", origin="HUMAN", spec=_spec()
    )
    conn.commit()
    hf_registry.set_status(conn, hyp_id, "EXPLORATORY")
    conn.commit()
    assert hf_registry.get_hypothesis(conn, hyp_id)["status"] == "EXPLORATORY"
    audit = conn.execute(
        text(
            "SELECT detail FROM system.audit_log"
            " WHERE action = 'hypothesis.status' AND target = :id"
        ),
        {"id": hyp_id},
    ).first()
    assert audit is not None
    detail = audit.detail  # JSONB: a dict
    assert detail["from"] == "NOT_TESTED"
    assert detail["to"] == "EXPLORATORY"


def test_an_illegal_transition_is_rejected_by_the_registry(conn: Connection) -> None:
    hyp_id = hf_registry.create_hypothesis(
        conn, title="A", domain="REGIME", origin="HUMAN", spec=_spec()
    )
    conn.commit()
    with pytest.raises(ValidationError):
        hf_registry.set_status(conn, hyp_id, "PASSED_RESEARCH_VALIDATION")


# --------------------------------------------------------------------------
# The API surface
# --------------------------------------------------------------------------


def test_the_api_creates_and_retrieves(client: TestClient) -> None:
    response = client.post(
        "/v1/hypothesis-factory/hypotheses",
        json={
            "title": "OI contraction improves capitulation-reclaim expectancy",
            "domain": "CAPITULATION_RECOVERY",
            "origin": "HUMAN",
            "spec": _spec(),
            "original_question": "Does extreme OI contraction improve reclaim signals?",
        },
    )
    assert response.status_code == 201, response.text
    hyp_id = response.json()["hypothesis_id"]
    view = client.get(f"/v1/hypothesis-factory/hypotheses/{hyp_id}")
    assert view.status_code == 200
    assert view.json()["version"] == 1


def test_the_api_rejects_an_underspecified_definition(client: TestClient) -> None:
    response = client.post(
        "/v1/hypothesis-factory/validate-definition",
        json={"title": "too vague"},
    )
    assert response.status_code == 422


def test_the_api_exposes_the_vocabularies(client: TestClient) -> None:
    response = client.get("/v1/hypothesis-factory/vocabularies")
    assert response.status_code == 200
    statuses = response.json()["statuses"]
    assert "NOT_TESTED" in statuses
    assert "PASSED_RESEARCH_VALIDATION" in statuses
