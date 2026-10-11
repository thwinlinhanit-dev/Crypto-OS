"""M9 — the AI API (spec §11: POST /v1/ai/chat, GET /v1/ai/tools,
GET /v1/agents/runs).

The chat endpoint wires the Copilot loop (D-065) to the live database:
it creates an agents.run row, executes the requested tools (each audited,
D-062), and resolves ``{{ref:...}}`` placeholders from the stored results
(D-059). Fail-closed: an unknown mode 422, a tool the mode may not use
422, an unknown tool never executes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import Connection, text

from apps.api.db import connection_scope
from engine.ai.handlers import BOUND_TOOLS as _BOUND_TOOLS
from engine.ai.handlers import _audit
from engine.ai.providers import Message, provider
from engine.ai.registry import ToolNotFoundError, tools
from engine.ai.registry import tool as registry_tool
from engine.ai.runner import MODES, ModeNotAllowedError, allowed_tools, run_turn

router = APIRouter(prefix="/v1/ai", tags=["ai"])

# The spec's §11 puts the run listing at /v1/agents/runs (line 529: "AI/MCP:
# POST /v1/ai/chat (SSE) ; GET /v1/ai/tools ; GET /v1/agents/runs"), so the
# run routes carry the spec's exact paths and the chat/tools routes the
# /v1/ai prefix.
runs_router = APIRouter(prefix="/v1/agents", tags=["ai"])

Mode = str

# The spec's five modes (spec §9: "Modes: Research, Quant, Data, Critic,
# Writer (system prompt + allowed tool subset)").
SPEC_MODES: tuple[str, ...] = ("Research", "Quant", "Data", "Critic", "Writer")


class ChatRequest(BaseModel):
    """POST /v1/ai/chat body."""

    messages: list[dict[str, Any]] = []
    mode: Mode = "Research"


class ToolsResponse(BaseModel):
    """GET /v1/ai/tools body: the registry as JSON (spec §11)."""

    tools: list[dict[str, Any]]


def _iso(value: Any) -> str:
    return value.isoformat().replace("+00:00", "Z") if value is not None else ""


def _parse_messages(raw: list[dict[str, Any]]) -> list[Message]:
    messages: list[Message] = []
    for item in raw:
        role = str(item.get("role", "user"))
        if role not in ("system", "user", "assistant", "tool"):
            raise HTTPException(422, f"unknown message role {role!r}")
        messages.append(
            Message(
                role=role,  # type: ignore[arg-type]
                content=str(item.get("content", "")),
                tool_call_id=item.get("tool_call_id"),
            )
        )
    return messages


def _tool_executor(conn: Connection) -> Any:
    """The tool executor the loop calls: dispatch to the registry, audit the
    call (D-062: agents.tool_call + system.audit_log) and return the stored
    result (D-066: ref_id + dataset_version).

    Handlers are bound for the read tools that have data behind them;
    anything else returns a refusal the model can see (the registry's own
    guards — holdout, mode subset — fire before the executor runs).
    """

    def _execute(name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            registry_tool(name)
        except ToolNotFoundError:
            raise HTTPException(422, f"unknown tool {name!r}") from None
        # D-059/D-066: a tool without a real handler MUST NOT return a
        # success-shaped result. An unbound computation is an explicit
        # failure the model can see (the trust boundary: never fake a
        # research result).
        bound = _BOUND_TOOLS.get(name)
        if bound is None:
            result = {
                "tool": name,
                "ok": False,
                "error": "NOT_IMPLEMENTED",
                "message": (
                    f"tool {name!r} has no data-backed handler; it cannot run a "
                    "real computation"
                ),
            }
            _audit(conn, name, result)
            return result
        return bound(conn, args)

    return _execute


@router.post("/chat", response_class=StreamingResponse)
async def chat(body: ChatRequest) -> Any:
    """SSE stream: one delta event, then a ``done`` event carrying the run
    id, the executed tool calls and the integrity flags (spec §11, PD-5)."""
    if body.mode not in SPEC_MODES:
        raise HTTPException(422, f"unknown mode {body.mode!r}; known: {list(SPEC_MODES)}")

    messages = _parse_messages(body.messages)
    user_prompt = next((m.content for m in reversed(messages) if m.role == "user"), "")

    with connection_scope() as conn:
        row = conn.execute(
            text(
                "INSERT INTO agents.run (mode, user_prompt) VALUES (:mode, :user_prompt) "
                "RETURNING run_id"
            ),
            {"mode": body.mode, "user_prompt": user_prompt},
        ).first()
        assert row is not None
        run_id = str(row.run_id)

        executor = _tool_executor(conn)
        try:
            turn = run_turn(
                provider=provider(),
                mode=body.mode,
                messages=messages,
                executor=executor,
            )
        except ModeNotAllowedError as exc:
            raise HTTPException(422, f"tool not allowed: {exc}") from exc

        for execution in turn.tool_executions:
            conn.execute(
                text(
                    "INSERT INTO agents.tool_call (run_id, tool, args, result_ref) "
                    "VALUES (:run_id, :tool, :args, :result_ref)"
                ),
                {
                    "run_id": run_id,
                    "tool": execution.name,
                    "args": json.dumps(execution.arguments, sort_keys=True),
                    "result_ref": execution.ref_id,
                },
            )

    def _events() -> Iterator[str]:
        yield f"data: {json.dumps({'delta': turn.text})}\n\n"
        done = {
            "type": "done",
            "run_id": run_id,
            "tool_calls": [
                {
                    "name": e.name,
                    "result_ref": e.ref_id,
                    "ok": e.result.get("ok", False),
                    **(
                        {"error": e.result.get("error")}
                        if not e.result.get("ok", True)
                        else {}
                    ),
                }
                for e in turn.tool_executions
            ],
            "unresolved_placeholders": turn.unresolved_placeholders,
        }
        yield f"data: {json.dumps(done)}\n\n"

    return StreamingResponse(_events(), media_type="text/event-stream")


@router.get("/tools", response_model=ToolsResponse)
async def list_tools(tier: str | None = Query(default=None)) -> ToolsResponse:
    """GET /v1/ai/tools: the registry as JSON (spec §11)."""
    if tier is not None and tier not in ("READ_ONLY", "RESEARCH"):
        raise HTTPException(422, f"unknown tier {tier!r}")
    selected_tier = tier if tier in ("READ_ONLY", "RESEARCH") else None
    return ToolsResponse(
        tools=[
            {
                "name": t.name,
                "tier": t.tier,
                "schema": t.schema,
                "requires_holdout": t.requires_holdout,
                "touches_frozen": t.touches_frozen,
            }
            for t in tools(selected_tier)  # type: ignore[arg-type]
        ]
    )


@runs_router.get("/runs")
async def list_runs(
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """GET /v1/agents/runs: the runs and their tool-call counts (spec §11)."""
    with connection_scope() as conn:
        rows = conn.execute(
            text(
                "SELECT r.run_id, r.mode, r.user_prompt, r.created_at, "
                "       (SELECT COUNT(*) FROM agents.tool_call c "
                "         WHERE c.run_id = r.run_id) AS tool_calls "
                "FROM agents.run r ORDER BY r.created_at DESC LIMIT :limit"
            ),
            {"limit": limit},
        ).all()
    return {
        "runs": [
            {
                "run_id": str(row.run_id),
                "mode": row.mode,
                "user_prompt": row.user_prompt,
                "created_at": _iso(row.created_at),
                "tool_calls": int(row.tool_calls),
            }
            for row in rows
        ]
    }


@runs_router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    """GET /v1/agents/runs/{id}: one run plus its tool calls (spec §11)."""
    with connection_scope() as conn:
        run = conn.execute(
            text(
                "SELECT run_id, mode, user_prompt, created_at FROM agents.run "
                "WHERE run_id = :run_id"
            ),
            {"run_id": run_id},
        ).first()
        if run is None:
            raise HTTPException(404, f"run {run_id} not found")
        calls = conn.execute(
            text(
                "SELECT tool, args, result_ref, created_at FROM agents.tool_call "
                "WHERE run_id = :run_id ORDER BY id"
            ),
            {"run_id": run_id},
        ).all()
    return {
        "run_id": str(run.run_id),
        "mode": run.mode,
        "user_prompt": run.user_prompt,
        "created_at": _iso(run.created_at),
        "tool_calls": [
            {
                "tool": call.tool,
                "args": call.args,
                "result_ref": call.result_ref,
                "created_at": _iso(call.created_at),
            }
            for call in calls
        ],
    }


def mode_tool_subset(mode: str) -> list[str]:
    """The mode's tool subset (spec §9), for docs/tests."""
    return sorted(t.name for t in allowed_tools(mode))


def modes() -> tuple[str, ...]:
    """The five spec §9 modes."""
    return MODES
