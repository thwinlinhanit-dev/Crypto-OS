"""M9 — the MCP server (spec §9, design note m9_ai section 5, D-062).

Exposes the READ_ONLY + RESEARCH tools over MCP (stdio + HTTP), same
schemas; **every call is written to agents.tool_call and system.audit_log**
(the M9 AC: "MCP calls audited"). Tiers in v1: READ_ONLY, RESEARCH; order
tools do not exist — a request naming an unknown tool is refused
(fail-closed, PD-4/D-063).

The server is transport-agnostic: ``dispatch()`` handles one JSON-RPC
request and returns one JSON-RPC response, so the stdio and HTTP front
ends share the audit + tier logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import Connection, text

from engine.ai.registry import ToolNotFoundError, tools
from engine.ai.registry import tool as registry_tool

TIERS: Final = ("READ_ONLY", "RESEARCH")

# JSON-RPC error codes used by the fail-closed paths
ERR_PARSE: Final = -32700
ERR_INVALID_REQUEST: Final = -32600
ERR_METHOD_NOT_FOUND: Final = -32601
ERR_INVALID_PARAMS: Final = -32602
ERR_INTERNAL: Final = -32603


class McpError(Exception):
    """A JSON-RPC error the server answers with (fail-closed)."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AuditWriter:
    """Writes one tool call to agents.tool_call + system.audit_log (D-062)."""

    conn: Connection
    run_id: UUID
    actor: str

    def record(self, *, tool_name: str, args: dict[str, Any], result_ref: str) -> None:
        detail = json.dumps({"tool": tool_name, "args": args}, sort_keys=True)
        self.conn.execute(
            text(
                "INSERT INTO agents.tool_call (run_id, tool, args, result_ref) "
                "VALUES (:run_id, :tool, :args, :result_ref)"
            ),
            {
                "run_id": self.run_id,
                "tool": tool_name,
                "args": json.dumps(args, sort_keys=True),
                "result_ref": result_ref,
            },
        )
        self.conn.execute(
            text(
                "INSERT INTO system.audit_log (actor, action, target, detail) "
                "VALUES (:actor, :action, :target, :detail)"
            ),
            {
                "actor": self.actor,
                "action": "mcp.tool_call",
                "target": tool_name,
                "detail": detail,
            },
        )


def start_run(conn: Connection, *, mode: str, user_prompt: str) -> UUID:
    """Create an agents.run row and return its id (spec §10)."""
    row = conn.execute(
        text(
            "INSERT INTO agents.run (mode, user_prompt) VALUES (:mode, :user_prompt) "
            "RETURNING run_id"
        ),
        {"mode": mode, "user_prompt": user_prompt},
    ).first()
    assert row is not None
    return UUID(str(row.run_id))


def tool_schemas(tier: str | None = None) -> list[dict[str, Any]]:
    """The exposed tools as MCP-style schemas ("same schemas", spec §9)."""
    if tier is not None and tier not in TIERS:
        raise McpError(ERR_INVALID_PARAMS, f"unknown tier {tier!r}")
    registry_tier: Any = tier if tier in TIERS else None
    return [
        {"name": t.name, "tier": t.tier, "schema": t.schema} for t in tools(registry_tier)
    ]


def dispatch(
    request: Any,
    *,
    executor: Any,
    audit: AuditWriter | None,
) -> dict[str, Any]:
    """Handle one JSON-RPC request: tools/list and tools/call.

    ``executor(tool_name, args)`` runs the tool and returns its stored
    result (carrying ref_id + dataset_version, D-066); the caller supplies
    it so the server stays free of DB imports.
    """
    if not isinstance(request, dict) or "method" not in request:
        return _error(None, ERR_INVALID_REQUEST, "invalid JSON-RPC request")
    request_id = request.get("id")
    method = str(request.get("method", ""))

    try:
        if method == "tools/list":
            params = request.get("params") or {}
            tier = params.get("tier")
            return _result(request_id, {"tools": tool_schemas(tier)})

        if method == "tools/call":
            params = request.get("params") or {}
            name = str(params.get("name", ""))
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                return _error(request_id, ERR_INVALID_PARAMS, "arguments must be an object")
            try:
                registry_tool(name)  # raises ToolNotFoundError if unknown
            except ToolNotFoundError:
                # D-063: the registry is closed; an unknown tool is refused
                return _error(request_id, ERR_METHOD_NOT_FOUND, f"unknown tool {name!r}")
            result = executor(name, args)
            result_ref = str(result.get("ref_id", "")) or name
            if audit is not None:
                audit.record(tool_name=name, args=args, result_ref=result_ref)
            return _result(
                request_id,
                {"content": result, "result_ref": result_ref},
            )

        return _error(request_id, ERR_METHOD_NOT_FOUND, f"unknown method {method!r}")
    except McpError as exc:
        return _error(request_id, exc.code, exc.message)
    except Exception as exc:
        return _error(request_id, ERR_INTERNAL, str(exc))


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve_stdio(
    *,
    executor: Any,
    audit_factory: Any,
    stream: Any = None,
) -> None:
    """The stdio front end: read JSON-RPC requests line by line, answer
    each, and audit every tools/call (D-062).

    ``audit_factory()`` opens a fresh AuditWriter per request (the caller
    owns the connection lifecycle); ``stream`` defaults to stdin/stdout.
    """
    import sys

    stream = stream or sys.stdin
    for line in stream:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps(_error(None, ERR_PARSE, "parse error")), flush=True)
            continue
        audit = audit_factory() if "tools/call" in line else None
        response = dispatch(request, executor=executor, audit=audit)
        print(json.dumps(response), flush=True)
