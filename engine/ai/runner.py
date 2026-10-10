"""M9 — the Copilot loop (spec §9, design note m9_ai section 3, D-065).

The loop is a pure function over ``(messages, mode, tools)`` with the
provider injected (D-065): it sends the system prompt for the mode plus
the allowed tool subset, executes the requested tools against the
registry, feeds the results back, and applies the number-integrity rules
(D-059) to the final text. The model never sees raw numbers: it emits
placeholders and the server resolves them from the stored tool results.

Modes (spec §9): Research, Quant, Data, Critic, Writer — each is a system
prompt plus an allowed tool subset. Critic Mode must list at least three
ways the conclusion could be wrong (D-060: the runner fails closed below
three).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from engine.ai.integrity import resolve_placeholders
from engine.ai.providers import Message, ToolCall, _parse_tool_calls
from engine.ai.registry import Tool
from engine.ai.registry import tool as registry_tool

Mode = Literal["Research", "Quant", "Data", "Critic", "Writer"]

MODES: Final[tuple[Mode, ...]] = ("Research", "Quant", "Data", "Critic", "Writer")

CRITIC_MIN_ITEMS: Final = 3

_SYSTEM_PROMPTS: Final[dict[str, str]] = {
    "Research": (
        "You are the Research mode. Use the tools to answer research "
        "questions from stored results. Emit numbers only as "
        "{{ref:<ref_id>#<json_path>}} placeholders; never write a raw "
        "number. Grade wording per spec 8.3."
    ),
    "Quant": (
        "You are the Quant mode. Answer with the stored backtest and "
        "validation results; emit numbers only as "
        "{{ref:<ref_id>#<json_path>}} placeholders. Include data_scope "
        "and n_eff where the results carry them."
    ),
    "Data": (
        "You are the Data mode. Answer data-health and ingest questions "
        "from the stored results; emit numbers only as "
        "{{ref:<ref_id>#<json_path>}} placeholders."
    ),
    "Critic": (
        "You are the Critic mode. You must list at least three ways the "
        "conclusion could be wrong. Emit numbers only as "
        "{{ref:<ref_id>#<json_path>}} placeholders."
    ),
    "Writer": (
        "You are the Writer mode. Compose the report from the stored "
        "results: n_eff, intervals, data_scope, OOS, the multiple-testing "
        "block, limitations. Emit numbers only as "
        "{{ref:<ref_id>#<json_path>}} placeholders. Grade wording per 8.3."
    ),
}

# The tool subset each mode may use (spec §9: "system prompt + allowed
# tool subset"). All modes may read; only Research/Quant/Writer may
# create; Critic reads only (it critiques, never writes).
_MODE_TOOLS: Final[dict[str, frozenset[str]]] = {
    "Research": frozenset(
        {
            "search_instruments", "get_candles", "get_data_health",
            "search_events", "get_event", "run_event_study", "list_hypotheses",
            "get_experiment", "freeze_experiment", "get_lineage",
            "run_backtest", "get_backtest", "run_validation",
            "run_random_baseline", "get_chart_context",
            "compute_stats", "compare_event_sets", "create_hypothesis",
            "create_experiment", "create_note", "create_report",
        }
    ),
    "Quant": frozenset(
        {
            "search_instruments", "get_candles", "search_events", "get_event",
            "run_event_study", "list_hypotheses", "get_experiment",
            "get_lineage", "run_backtest", "get_backtest", "run_validation",
            "run_random_baseline", "get_chart_context",
            "compute_stats", "compare_event_sets", "create_note",
        }
    ),
    "Data": frozenset(
        {
            "search_instruments", "get_candles", "get_data_health",
            "search_events", "get_event", "get_chart_context",
        }
    ),
    "Critic": frozenset(
        {
            "search_instruments", "get_candles", "get_data_health",
            "search_events", "get_event", "run_event_study",
            "list_hypotheses", "get_experiment", "get_lineage",
            "get_backtest", "get_chart_context",
        }
    ),
    "Writer": frozenset(
        {
            "search_instruments", "get_candles", "get_data_health",
            "search_events", "get_event", "run_event_study",
            "list_hypotheses", "get_experiment", "get_lineage",
            "run_backtest", "get_backtest", "run_validation",
            "run_random_baseline", "get_chart_context",
            "compute_stats", "compare_event_sets", "create_note",
            "create_report",
        }
    ),
}


class ModeNotAllowedError(ValueError):
    """An unknown mode was requested (fail-closed, PD-4)."""


class CriticTooThinError(RuntimeError):
    """Critic mode produced fewer than three critique items (D-060)."""


@dataclass
class ToolExecution:
    """One executed tool call: the name, args, result and its ref_id."""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    ref_id: str = ""


@dataclass
class Turn:
    """One assistant turn: the resolved text, its tool calls and flags."""

    text: str
    tool_executions: list[ToolExecution] = field(default_factory=list)
    unresolved_placeholders: list[str] = field(default_factory=list)
    free_text_numerals: list[str] = field(default_factory=list)


def mode_prompt(mode: str) -> str:
    try:
        return _SYSTEM_PROMPTS[mode]
    except KeyError:
        raise ModeNotAllowedError(mode) from None


def allowed_tools(mode: str) -> list[Tool]:
    """The mode's tool subset (spec §9), from the registry."""
    try:
        names = _MODE_TOOLS[mode]
    except KeyError:
        raise ModeNotAllowedError(mode) from None
    return [registry_tool(name) for name in sorted(names)]


def _critic_item_count(text: str) -> int:
    """Count the numbered critique items (1. / 2. / 3. …)."""
    return len(re.findall(r"^\s*\d+[.)]\s+\S", text, flags=re.MULTILINE))


def run_turn(
    *,
    provider: Any,
    mode: str,
    messages: list[Message],
    executor: Callable[[str, dict[str, Any]], dict[str, Any]],
    max_rounds: int = 4,
) -> Turn:
    """The Copilot loop (D-065): pure over (messages, mode, tools) with the
    provider injected.

    ``executor(name, args)`` runs a tool and returns its stored result
    (carrying ``ref_id`` + ``dataset_version``, D-066); the caller supplies
    it so the loop stays free of DB imports.
    """
    if mode not in MODES:
        raise ModeNotAllowedError(mode)

    system = Message(role="system", content=mode_prompt(mode))
    conversation: list[Message] = [system, *messages]
    mode_tools = allowed_tools(mode)
    allowed = {t.name for t in mode_tools}
    tool_results: dict[str, dict[str, Any]] = {}
    executions: list[ToolExecution] = []

    for _ in range(max_rounds):
        deltas = list(provider.stream_chat(conversation, [t.schema for t in mode_tools]))
        text = "".join(deltas)
        calls = _parse_tool_calls_from_deltas(text)
        if not calls:
            break
        conversation.append(Message(role="assistant", content=text))
        for call in calls:
            if call.name not in allowed:
                raise ModeNotAllowedError(
                    f"tool {call.name!r} is not allowed in {mode} mode"
                )
            registry_tool(call.name)  # raises ToolNotFoundError if unknown
            result = executor(call.name, call.arguments)
            ref_id = str(result.get("ref_id", "")) or call.name
            tool_results[ref_id] = result
            executions.append(
                ToolExecution(
                    name=call.name,
                    arguments=call.arguments,
                    result=result,
                    ref_id=ref_id,
                )
            )
            conversation.append(
                Message(
                    role="tool",
                    content=json.dumps(result),
                    tool_call_id=call.name,
                )
            )
    else:
        # max_rounds exhausted: answer with what we have (fail-closed on
        # further tool use, never loop forever)
        pass

    resolved, flagged = resolve_placeholders(text, tool_results)
    final = Turn(
        text=resolved,
        tool_executions=executions,
        unresolved_placeholders=flagged,
        free_text_numerals=[],
    )
    if mode == "Critic":
        items = _critic_item_count(resolved)
        if items < CRITIC_MIN_ITEMS:
            raise CriticTooThinError(
                f"Critic mode produced {items} critique items; {CRITIC_MIN_ITEMS} required"
            )
    return final


def _parse_tool_calls_from_deltas(text: str) -> list[ToolCall]:
    """Pull the tagged ``__tool_calls__`` chunk (if any) out of the joined
    provider output and parse it."""
    calls: list[ToolCall] = []
    for chunk in text.split("\n"):
        chunk = chunk.strip()
        if not chunk.startswith("{"):
            continue
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        raw = parsed.get("__tool_calls__")
        if isinstance(raw, list):
            calls.extend(_parse_tool_calls(raw))
    return calls
