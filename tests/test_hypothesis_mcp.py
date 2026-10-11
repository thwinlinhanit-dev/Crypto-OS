"""HF Phase 7 — the MCP tool exposure + the decision provider tests
(spec §18).

The three HF tools register into the EXISTING M9 registry (extend, not
replace): real JSON schemas, the ``(conn, args) -> dict`` handler shape,
the ``canonical_ref_id`` provenance, and the unchanged trust boundary. The
deterministic local baseline is the only decision provider; any other is
refused explicitly rather than silently substituted.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Connection, create_engine, text

from apps.api.settings import get_settings
from engine.errors import ValidationError
from engine.hypothesis import mcp as hf_mcp
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


def _result(**over: Any) -> dict[str, Any]:
    base = {"n": 100, "mean_r": 0.4, "ci_excludes_zero": True}
    result: dict[str, Any] = {
        "headline_model": "CALENDAR_DAY",
        "cluster_models": {
            "CALENDAR_DAY": {"n_eff": 100, "grade": "B", "oos_sign_agrees": True}
        },
        "cross_asset": {
            "n_symbols": 4, "pooled_median_r": 0.4, "pooled_sign": 1,
            "symbols_agree_share": 0.8,
        },
        "horizons": [{"h": 5, "base": base}],
    }
    result.update(over)
    return result


# --------------------------------------------------------------------------
# The schemas (real JSON Schemas, spec §9)
# --------------------------------------------------------------------------


def test_the_schemas_are_real_not_stubs() -> None:
    schemas = hf_mcp.schemas()
    assert set(schemas) == {
        hf_mcp.RESEARCH_QUESTION_SEARCH, hf_mcp.HYPOTHESIS_EVALUATE, hf_mcp.HYPOTHESIS_PROPOSE
    }
    for name, schema in schemas.items():
        assert schema["type"] == "object"
        assert schema["title"] == name
        assert "properties" in schema


# --------------------------------------------------------------------------
# The registration (extend the M9 registry)
# --------------------------------------------------------------------------


def test_the_tools_register_into_the_existing_registry() -> None:
    from engine.ai.registry import names, tools

    registered = hf_mcp.register_all()
    assert registered == [
        hf_mcp.RESEARCH_QUESTION_SEARCH, hf_mcp.HYPOTHESIS_EVALUATE, hf_mcp.HYPOTHESIS_PROPOSE
    ]
    # extend, not replace: the M9 tools are still present
    assert "search_instruments" in names()
    all_tools = tools()
    for name in registered:
        assert any(t.name == name for t in all_tools)


def test_the_hf_tools_are_research_tier() -> None:
    hf_mcp.register_all()
    from engine.ai.registry import tools

    for tool in tools():
        if tool.name in (
            hf_mcp.RESEARCH_QUESTION_SEARCH, hf_mcp.HYPOTHESIS_EVALUATE, hf_mcp.HYPOTHESIS_PROPOSE
        ):
            assert tool.tier == "RESEARCH"


# --------------------------------------------------------------------------
# research_question.search (deterministic, spec §7.4)
# --------------------------------------------------------------------------


def test_research_question_search_returns_the_deterministic_gaps(
    conn: Connection,
) -> None:
    result = hf_mcp.research_question_search(conn, {"limit": 3})
    assert result["ok"] is True
    assert len(result["questions"]) <= 3
    # the ref_id is the canonical sha256 (D-066), never Python's hash()
    assert len(result["ref_id"]) == 24
    assert all("domain" in q for q in result["questions"])


def test_research_question_search_rejects_a_bad_limit(conn: Connection) -> None:
    with pytest.raises(ValidationError):
        hf_mcp.research_question_search(conn, {"limit": 0})


# --------------------------------------------------------------------------
# hypothesis.evaluate (the model reads the verdict, it cannot author it)
# --------------------------------------------------------------------------


def test_hypothesis_evaluate_returns_the_decision(conn: Connection) -> None:
    result = hf_mcp.hypothesis_evaluate(
        conn,
        {
            "hypothesis_id": "h",
            "stats_result": _result(),
            "by_regime": {"a": [0.4, 0.5], "b": [0.3, 0.4]},
            "neighbor_results": [_result(), _result()],
            "outlier_shares": [0.05, 0.1, 0.2],
            "control_result": _result(ci_excludes_zero=False),
        },
    )
    assert result["ok"] is True
    assert result["decision"] in (
        "PASSED_RESEARCH_VALIDATION", "FAILED_ROBUSTNESS", "REQUIRES_HUMAN_REVIEW"
    )
    assert result["checks"]
    # outlier_shares without outcomes: the outlier check is skipped, the
    # others still run
    names = {c["name"] for c in result["checks"]}
    assert "asset_consistency" in names


def test_hypothesis_evaluate_requires_the_stats_result(conn: Connection) -> None:
    with pytest.raises(ValidationError):
        hf_mcp.hypothesis_evaluate(conn, {"hypothesis_id": "h"})


# --------------------------------------------------------------------------
# hypothesis.propose (every AI output is a proposal, never a finding)
# --------------------------------------------------------------------------


def test_hypothesis_propose_stores_a_valid_proposal(conn: Connection) -> None:
    result = hf_mcp.hypothesis_propose(
        conn,
        {
            "title": "OI contraction improves reclaim expectancy",
            "domain": "CAPITULATION_RECOVERY",
            "spec": _spec(),
            "original_question": "Does OI contraction improve reclaim?",
        },
    )
    assert result["ok"] is True
    assert result["accepted"] is True
    assert result["hypothesis_ids"]
    view = hf_registry.get_hypothesis(conn, result["hypothesis_ids"][0])
    assert view["origin"] == "AI_GENERATED"
    assert view["status"] == "NOT_TESTED"


def test_hypothesis_propose_preserves_a_rejection(conn: Connection) -> None:
    spec = _spec()
    del spec["falsification_criteria"]
    result = hf_mcp.hypothesis_propose(
        conn, {"title": "x", "domain": "DERIVATIVES", "spec": spec}
    )
    assert result["ok"] is True
    assert result["accepted"] is False
    assert result["rejection"] is not None


def test_hypothesis_propose_requires_the_fields(conn: Connection) -> None:
    with pytest.raises(ValidationError):
        hf_mcp.hypothesis_propose(conn, {"title": "x"})


# --------------------------------------------------------------------------
# The decision provider (spec §18.1)
# --------------------------------------------------------------------------


def test_the_local_baseline_is_the_only_provider(conn: Connection) -> None:
    result = hf_mcp.recommendation(conn, provider="LOCAL_BASELINE", limit=2)
    assert result["ok"] is True
    assert result["provider"] == "LOCAL_BASELINE"
    assert result["authoritative"] is False  # a model is never authoritative


def test_an_unconfigured_provider_is_refused_not_substituted(
    conn: Connection,
) -> None:
    """PD-10: an unconfigured provider is refused explicitly, never silently
    swapped for the baseline."""
    with pytest.raises(ValidationError):
        hf_mcp.recommendation(conn, provider="OPENAI")
