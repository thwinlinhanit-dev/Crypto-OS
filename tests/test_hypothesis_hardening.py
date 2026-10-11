"""HF Phase 8 — the operational-hardening tests (spec §17.2/§18).

Resource controls (fail-closed), the audit vocabulary, and the backup/
recovery round-trip for the HF tables (D-079 idempotent).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Connection, create_engine, text

from apps.api.settings import get_settings
from engine.backup import export_backup, import_backup, validate_bundle
from engine.errors import ValidationError
from engine.hypothesis import hardening as hf_hardening
from engine.hypothesis import registry as hf_registry

_RESET_SQL = (
    "TRUNCATE research.stats_result, research.research_ledger,"
    " research.hypothesis_relationship, research.hypothesis_version,"
    " research.hypothesis, research.note, research.experiment_family,"
    " research.experiment CASCADE"
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


@pytest.fixture(autouse=True)
def clean(conn: Connection) -> Iterator[None]:
    def _reset() -> None:
        conn.rollback()
        conn.execute(text("DELETE FROM system.audit_log"))
        conn.execute(text(_RESET_SQL))
        conn.commit()

    _reset()
    try:
        yield
    finally:
        _reset()


def _spec(**over: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "null_hypothesis": "no relationship",
        "alternative_hypothesis": "a relationship",
        "independent_vars": ["a"],
        "dependent_vars": ["b"],
        "features": ["rvol"],
        "population": "p",
        "timeframe": "1D",
        "observation_ts_rule": "bar close_time",
        "outcome_horizon": "5 bars",
        "expected_direction": "HIGHER",
        "data_requirements": ["d"],
        "proposed_statistical_method": "block-bootstrap mean difference",
        "falsification_criteria": ["effect vanishes after fees"],
    }
    spec.update(over)
    return spec


# --------------------------------------------------------------------------
# The resource controls (spec §17.2): a request may narrow, never widen
# --------------------------------------------------------------------------


def test_the_generation_budget_defaults_to_the_configured_maximum() -> None:
    assert hf_hardening.generation_budget() == hf_hardening.DEFAULT_GENERATION_BUDGET


def test_a_narrower_generation_budget_is_allowed() -> None:
    assert hf_hardening.generation_budget(requested=3) == 3


def test_a_wider_generation_budget_is_refused() -> None:
    """Spec §17.2: no runaway processes - a request cannot widen the budget
    past the configured maximum."""
    with pytest.raises(ValidationError):
        hf_hardening.generation_budget(requested=999_999)


def test_a_bad_generation_budget_is_refused() -> None:
    with pytest.raises(ValidationError):
        hf_hardening.generation_budget(requested=0)


def test_a_configured_generation_budget_is_respected() -> None:
    config = {"hypothesis_factory": {"generation_budget": 3}}
    assert hf_hardening.generation_budget(config=config) == 3
    with pytest.raises(ValidationError):
        hf_hardening.generation_budget(requested=4, config=config)


def test_the_mcp_call_budget_may_narrow_never_widen() -> None:
    assert hf_hardening.mcp_call_budget(requested=5) == 5
    with pytest.raises(ValidationError):
        hf_hardening.mcp_call_budget(requested=10_000)


def test_the_max_bars_budget_refuses_a_wider_request() -> None:
    """A wider request is refused, never silently truncated."""
    assert hf_hardening.max_bars() == hf_hardening.DEFAULT_MAX_BARS
    with pytest.raises(ValidationError):
        hf_hardening.max_bars(requested=999_999)


# --------------------------------------------------------------------------
# The audit vocabulary (D-062)
# --------------------------------------------------------------------------


def test_the_audit_actions_are_a_closed_vocabulary() -> None:
    hf_hardening.assert_audit_action("hypothesis.create")
    hf_hardening.assert_audit_action("experiment.run")
    with pytest.raises(ValidationError):
        hf_hardening.assert_audit_action("whatever")


# --------------------------------------------------------------------------
# The backup/recovery round-trip (D-079)
# --------------------------------------------------------------------------


def _seed(conn: Connection) -> str:
    hyp_id = hf_registry.create_hypothesis(
        conn,
        title="OI contraction improves reclaim expectancy",
        domain="CAPITULATION_RECOVERY",
        origin="HUMAN",
        spec=_spec(),
    )
    conn.commit()
    return hyp_id


def test_the_backup_carries_the_hf_section(conn: Connection) -> None:
    _seed(conn)
    bundle = export_backup(conn)
    hf = bundle["hypothesis_factory"]
    assert len(hf["registry"]) == 1
    assert hf["registry"][0]["domain"] == "CAPITULATION_RECOVERY"
    assert len(hf["versions"]) == 1
    assert hf["versions"][0]["null_hypothesis"] == "no relationship"


def test_the_round_trip_restores_the_hf_tables(conn: Connection) -> None:
    hyp_id = _seed(conn)
    bundle = export_backup(conn)
    # wipe and re-import: the idempotent import (D-079) restores the record
    conn.execute(text(_RESET_SQL))
    conn.commit()
    inserted = import_backup(conn, bundle)
    conn.commit()
    assert inserted["hypotheses"] == 1
    assert inserted["hf_versions"] == 1
    view = hf_registry.get_hypothesis(conn, hyp_id)
    assert view["domain"] == "CAPITULATION_RECOVERY"
    assert view["spec"]["null_hypothesis"] == "no relationship"


def test_the_reimport_is_idempotent(conn: Connection) -> None:
    """Re-importing the same bundle inserts nothing (D-079).

    The first import restores the wiped rows; the second inserts nothing
    (every insert is ON CONFLICT DO NOTHING)."""
    hyp_id = _seed(conn)
    bundle = export_backup(conn)
    conn.execute(text(_RESET_SQL))
    conn.commit()
    first = import_backup(conn, bundle)
    conn.commit()
    assert first["hf_versions"] == 1
    assert hf_registry.get_hypothesis(conn, hyp_id)["version"] == 1
    second = import_backup(conn, bundle)
    conn.commit()
    assert second["hf_versions"] == 0
    assert second["hypotheses"] == 0


def test_the_evidence_graph_round_trips(conn: Connection) -> None:
    a = _seed(conn)
    b = hf_registry.create_hypothesis(
        conn, title="B", domain="DERIVATIVES", origin="AI_GENERATED",
        spec=_spec(outcome_horizon="10 bars"),
    )
    conn.commit()
    hf_registry.add_relationship(
        conn, from_id=a, to_id=b, relationship="CONTRADICTS", rationale="opposite"
    )
    conn.commit()
    bundle = export_backup(conn)
    assert len(bundle["hypothesis_factory"]["relationships"]) == 1
    conn.execute(text(_RESET_SQL))
    conn.commit()
    inserted = import_backup(conn, bundle)
    conn.commit()
    assert inserted["hf_relationships"] == 1
    # the re-import is idempotent: the unique constraint catches the second
    second = import_backup(conn, bundle)
    conn.commit()
    assert second["hf_relationships"] == 0


def test_the_ledger_round_trips(conn: Connection) -> None:
    hyp_id = _seed(conn)
    hf_registry.declare_testing_family(
        conn, family_id="fam-1", hypothesis_id=hyp_id, correction_plan="BONFERRONI"
    )
    conn.commit()
    bundle = export_backup(conn)
    assert bundle["hypothesis_factory"]["ledger"][0]["correction_plan"] == "BONFERRONI"
    conn.execute(text(_RESET_SQL))
    conn.commit()
    inserted = import_backup(conn, bundle)
    conn.commit()
    assert inserted["hf_ledger"] == 1


def test_a_pre_hf_bundle_validates_without_the_section() -> None:
    """A pre-0011 bundle imports without the HF section (backward
    compatible)."""
    bundle = {
        "schema_version": "1",
        "notebook": [],
        "registry": {"hypotheses": [], "experiment_families": [], "experiments": []},
        "lineage": {},
    }
    assert validate_bundle(bundle)["schema_version"] == "1"


def test_a_malformed_hf_section_is_rejected() -> None:
    bundle = {
        "schema_version": "1",
        "notebook": [],
        "registry": {"hypotheses": [], "experiment_families": [], "experiments": []},
        "lineage": {},
        "hypothesis_factory": {"registry": "not a list"},
    }
    with pytest.raises(Exception):  # noqa: B017 - fail-closed
        validate_bundle(bundle)
