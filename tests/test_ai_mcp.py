"""M9 — the MCP server tests (spec §9, design note m9_ai section 5,
D-062): every call is written to agents.tool_call **and** system.audit_log
(the M9 AC: "MCP calls audited"); the registry is closed (unknown tools
refused); the tiers are exposed with the same schemas.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

from engine.ai.mcp import (
    ERR_INVALID_PARAMS,
    ERR_METHOD_NOT_FOUND,
    AuditWriter,
    dispatch,
    start_run,
    tool_schemas,
)

RUN_ID = UUID("11111111-2222-3333-4444-555555555555")

TOOL_RESULTS: dict[str, Any] = {
    "s1": {"ref_id": "s1", "dataset_version": "1", "grade": "A"},
}


def _executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
    result = dict(TOOL_RESULTS["s1"])
    result["tool"] = name
    return result


def _audit() -> AuditWriter:
    conn = MagicMock()
    conn.execute.return_value.first.return_value = None
    return AuditWriter(conn=conn, run_id=RUN_ID, actor="mcp-test")


def _recorded_calls(audit: AuditWriter) -> list[Any]:
    return list(audit.conn.execute.call_args_list)  # type: ignore[attr-defined]


def test_tools_list_returns_the_registry() -> None:
    response = dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        executor=_executor,
        audit=None,
    )
    names = [t["name"] for t in response["result"]["tools"]]
    assert "get_experiment" in names
    assert "create_experiment" in names


def test_tools_list_filters_by_tier() -> None:
    response = dispatch(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"tier": "READ_ONLY"}},
        executor=_executor,
        audit=None,
    )
    tiers = {t["tier"] for t in response["result"]["tools"]}
    assert tiers == {"READ_ONLY"}


def test_tools_list_rejects_an_unknown_tier() -> None:
    response = dispatch(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"tier": "ORDER"}},
        executor=_executor,
        audit=None,
    )
    # an unknown tier is a bad parameter, not an unknown method
    assert response["error"]["code"] == ERR_INVALID_PARAMS


def test_tools_list_accepts_the_visualization_write_tier() -> None:
    """D-103: the §26 VISUALIZATION_WRITE tier reaches the MCP surface."""
    response = dispatch(
        {
            "jsonrpc": "2.0",
            "id": 33,
            "method": "tools/list",
            "params": {"tier": "VISUALIZATION_WRITE"},
        },
        executor=_executor,
        audit=None,
    )
    assert "error" not in response
    tools_ = response["result"]["tools"]
    assert {t["tier"] for t in tools_} <= {"VISUALIZATION_WRITE"}


def test_tools_call_executes_and_returns_the_ref() -> None:
    response = dispatch(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "get_experiment", "arguments": {"id": "x"}},
        },
        executor=_executor,
        audit=None,
    )
    assert response["result"]["result_ref"] == "s1"
    assert response["result"]["content"]["tool"] == "get_experiment"


def test_a_tool_call_is_audited_to_both_tables() -> None:
    """The M9 AC: every call writes agents.tool_call **and** system.audit_log
    (D-062) — two INSERTs per call."""
    audit = _audit()
    dispatch(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "search_instruments", "arguments": {"q": "BTC"}},
        },
        executor=_executor,
        audit=audit,
    )
    calls = _recorded_calls(audit)
    assert len(calls) == 2
    sql_texts = [str(call[0][0]) for call in calls]
    assert any("agents.tool_call" in sql for sql in sql_texts)
    assert any("system.audit_log" in sql for sql in sql_texts)


def test_an_unknown_tool_is_refused_fail_closed() -> None:
    """D-063: the registry is closed — an unknown tool never reaches an
    executor and is never audited as a success."""
    audit = _audit()
    response = dispatch(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "place_order", "arguments": {}},
        },
        executor=_executor,
        audit=audit,
    )
    assert response["error"]["code"] == ERR_METHOD_NOT_FOUND
    assert _recorded_calls(audit) == []


def test_a_bad_arguments_shape_is_refused() -> None:
    response = dispatch(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "get_experiment", "arguments": "not-an-object"},
        },
        executor=_executor,
        audit=None,
    )
    assert response["error"]["code"] == ERR_INVALID_PARAMS


def test_an_unknown_method_is_refused() -> None:
    response = dispatch(
        {"jsonrpc": "2.0", "id": 8, "method": "tools/order"},
        executor=_executor,
        audit=None,
    )
    assert response["error"]["code"] == ERR_METHOD_NOT_FOUND


def test_an_invalid_request_is_refused() -> None:
    response = dispatch(
        {"jsonrpc": "2.0"},
        executor=_executor,
        audit=None,
    )
    assert response["error"]["code"] == -32600


def test_a_crashed_executor_becomes_a_jsonrpc_error() -> None:
    """Fail-closed: a tool that raises answers an error, never a crash."""
    def _boom(name: str, args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("tool exploded")

    response = dispatch(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "get_experiment", "arguments": {}},
        },
        executor=_boom,
        audit=None,
    )
    assert response["error"]["code"] == -32603
    assert "tool exploded" in response["error"]["message"]


def test_start_run_inserts_and_returns_the_run_id() -> None:
    conn = MagicMock()
    conn.execute.return_value.first.return_value = MagicMock(run_id=str(RUN_ID))
    run_id = start_run(conn, mode="Research", user_prompt="hello")
    assert run_id == RUN_ID
    sql = str(conn.execute.call_args[0][0])
    assert "agents.run" in sql


def test_tool_schemas_match_the_registry() -> None:
    schemas = tool_schemas()
    assert len(schemas) == 21
    assert all(isinstance(s["schema"], dict) for s in schemas)
