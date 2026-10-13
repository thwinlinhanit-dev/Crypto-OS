"""M9 — the Copilot tool registry (spec §9, design note m9_ai section 2).

The registry is **closed**: the 20 tools of spec §9 exist and nothing else.
There are no order-related tools (R6/PD-1: research only, no order path)
and every tool is tiered (READ_ONLY / RESEARCH). ``tool()`` returns the
handler by name; an unknown name raises ``ToolNotFound`` (fail-closed, PD-4).

Each tool result carries ``ref_id`` + ``dataset_version`` by construction
(D-066 / PD-5 / PD-6), so the number-integrity resolver can substitute
placeholders from a stored result.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Literal

Tier = Literal["READ_ONLY", "RESEARCH", "VISUALIZATION_WRITE"]


class ToolNotFoundError(KeyError):
    """An unknown tool name was requested (fail-closed, PD-4)."""


class ForbiddenToolError(ValueError):
    """The requested tool does not exist in this deployment."""


@dataclass(frozen=True)
class Tool:
    """One registered tool: name, tier, JSON schema, handler."""

    name: str
    tier: Tier
    schema: dict[str, Any]
    handler: Callable[..., dict[str, Any]]
    requires_holdout: bool = False
    touches_frozen: bool = False


_REGISTRY: dict[str, Tool] = {}


def register(
    *,
    name: str,
    tier: Tier,
    schema: dict[str, Any],
    handler: Callable[..., dict[str, Any]],
    requires_holdout: bool = False,
    touches_frozen: bool = False,
) -> None:
    _REGISTRY[name] = Tool(
        name=name,
        tier=tier,
        schema=schema,
        handler=handler,
        requires_holdout=requires_holdout,
        touches_frozen=touches_frozen,
    )


def tool(name: str) -> Tool:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ToolNotFoundError(name) from None


def tools(tier: Tier | None = None) -> list[Tool]:
    """The registered tools, optionally filtered by tier."""
    return [
        t for t in _REGISTRY.values() if tier is None or t.tier == tier
    ]


def names(tier: Tier | None = None) -> list[str]:
    return sorted(t.name for t in tools(tier))


READ_ONLY_NAMES: Final = [
    "search_instruments",
    "get_candles",
    "get_data_health",
    "search_events",
    "get_event",
    "run_event_study",
    "list_hypotheses",
    "get_experiment",
    "freeze_experiment",
    "get_lineage",
    "run_backtest",
    "get_backtest",
    "run_validation",
    "run_random_baseline",
    "get_chart_context",
]

RESEARCH_NAMES: Final = [
    "compute_stats",
    "compare_event_sets",
    "create_hypothesis",
    "create_experiment",
    "create_note",
    "create_report",
]


def _register_defaults() -> None:
    """Register the spec §9 tools with their JSON schemas and the immutability
    flags. Handlers are injected by the runner (the registry holds schemas +
    guards; the runtime binds handlers)."""

    def _read(tool_name: str) -> Callable[..., dict[str, Any]]:
        def _handler(**_kwargs: Any) -> dict[str, Any]:
            return {"tool": tool_name, "ok": True}

        return _handler


    #: The real JSON Schemas (review Phase 1: "a model or MCP client cannot
    #: reliably discover the actual calling contract from the advertised
    #: schema"). Every tool describes its arguments; the executor validates
    #: against these before running.
    _tool_schemas: Final[dict[str, dict[str, Any]]] = {
        "search_instruments": {
            "type": "object",
            "title": "search_instruments",
            "properties": {
                "query": {"type": "string", "description": "symbol fragment (e.g. BTC)"},
            },
            "required": ["query"],
        },
        "get_candles": {
            "type": "object",
            "title": "get_candles",
            "properties": {
                "instrument_id": {"type": "string"},
                "tf": {"type": "string", "enum": ["1D", "4H", "1H"]},
                "from": {"type": "string"},
                "to": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5000},
            },
            "required": ["instrument_id", "tf"],
        },
        "get_data_health": {
            "type": "object",
            "title": "get_data_health",
            "properties": {
                "instrument_id": {"type": "string"},
                "tf": {"type": "string", "enum": ["1D", "4H", "1H"]},
            },
            "required": ["instrument_id"],
        },
        "search_events": {
            "type": "object",
            "title": "search_events",
            "properties": {
                "instrument_id": {"type": "string"},
                "detector": {"type": "string"},
                "tf": {"type": "string", "enum": ["1D", "4H", "1H"]},
                "event_type": {"type": "string", "enum": ["CONT", "CAPIT", "FAIL", "TOP"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5000},
            },
            "required": ["instrument_id", "tf"],
        },
        "get_event": {
            "type": "object",
            "title": "get_event",
            "properties": {"event_id": {"type": "string"}},
            "required": ["event_id"],
        },
        "run_event_study": {
            "type": "object",
            "title": "run_event_study",
            "properties": {
                "detector": {"type": "string"},
                "timeframe": {"type": "string", "enum": ["1D", "4H", "1H"]},
                "instrument_ids": {"type": "array", "items": {"type": "string"}},
                "data_scope": {"type": "string", "enum": ["DEVELOPMENT", "HOLDOUT"]},
            },
            "required": ["detector", "timeframe"],
        },
        "list_hypotheses": {
            "type": "object",
            "title": "list_hypotheses",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
        },
        "get_experiment": {
            "type": "object",
            "title": "get_experiment",
            "properties": {"experiment_id": {"type": "string"}},
            "required": ["experiment_id"],
        },
        "freeze_experiment": {
            "type": "object",
            "title": "freeze_experiment",
            "properties": {"experiment_id": {"type": "string"}},
            "required": ["experiment_id"],
        },
        "get_lineage": {
            "type": "object",
            "title": "get_lineage",
            "properties": {"experiment_id": {"type": "string"}},
            "required": ["experiment_id"],
        },
        "run_backtest": {
            "type": "object",
            "title": "run_backtest",
            "properties": {
                "experiment_id": {"type": "string"},
                "strategy_id": {"type": "string"},
                "version": {"type": "string"},
                "universe": {"type": "array", "items": {"type": "string"}},
                "timeframe": {"type": "string", "enum": ["1D", "4H", "1H"]},
                "fill_model": {"type": "string", "enum": ["PARITY", "REALISTIC_OHLC"]},
                "data_scope": {"type": "string", "enum": ["DEVELOPMENT", "HOLDOUT"]},
            },
            "required": ["experiment_id", "strategy_id", "universe", "timeframe"],
        },
        "get_backtest": {
            "type": "object",
            "title": "get_backtest",
            "properties": {"backtest_id": {"type": "string"}},
            "required": ["backtest_id"],
        },
        "run_validation": {
            "type": "object",
            "title": "run_validation",
            "properties": {
                "backtest_id": {"type": "string"},
                "kinds": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["backtest_id"],
        },
        "run_random_baseline": {
            "type": "object",
            "title": "run_random_baseline",
            "properties": {
                "backtest_id": {"type": "string"},
                "seeds": {"type": "integer", "minimum": 1, "maximum": 10000},
            },
            "required": ["backtest_id"],
        },
        "get_chart_context": {
            "type": "object",
            "title": "get_chart_context",
            "properties": {
                "instrument_id": {"type": "string"},
                "tf": {"type": "string", "enum": ["1D", "4H", "1H"]},
            },
            "required": ["instrument_id"],
        },
        "compute_stats": {
            "type": "object",
            "title": "compute_stats",
            "properties": {
                "detector": {"type": "string"},
                "timeframe": {"type": "string", "enum": ["1D", "4H", "1H"]},
                "instrument_ids": {"type": "array", "items": {"type": "string"}},
                "data_scope": {"type": "string", "enum": ["DEVELOPMENT", "HOLDOUT"]},
            },
            "required": ["detector", "timeframe"],
        },
        "compare_event_sets": {
            "type": "object",
            "title": "compare_event_sets",
            "properties": {
                "detector": {"type": "string"},
                "timeframe": {"type": "string", "enum": ["1D", "4H", "1H"]},
                "baseline_detector": {"type": "string"},
            },
            "required": ["detector", "timeframe"],
        },
        "create_hypothesis": {
            "type": "object",
            "title": "create_hypothesis",
            "properties": {
                "title": {"type": "string"},
                "statement": {"type": "string"},
                "criteria": {
                    "type": "object",
                    "properties": {
                        "pass": {"type": "array", "items": {"type": "string"}},
                        "kill": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "required": ["title", "statement"],
        },
        "create_experiment": {
            "type": "object",
            "title": "create_experiment",
            "properties": {
                "hypothesis_id": {"type": "string"},
                "title": {"type": "string"},
                "universe": {"type": "array", "items": {"type": "string"}},
                "timeframe": {"type": "string", "enum": ["1D", "4H", "1H"]},
                "data_scope": {"type": "string", "enum": ["DEVELOPMENT", "HOLDOUT"]},
                "dataset_version": {"type": "string"},
                "detector_refs": {"type": "object"},
            },
            "required": ["hypothesis_id", "title", "universe", "timeframe"],
        },
        "create_note": {
            "type": "object",
            "title": "create_note",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "experiment_id": {"type": "string"},
            },
            "required": ["title", "body"],
        },
        "create_report": {
            "type": "object",
            "title": "create_report",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "experiment_id": {"type": "string"},
            },
            "required": ["title", "body"],
        },
    }

    for name in READ_ONLY_NAMES:
        register(
            name=name,
            tier="READ_ONLY",
            schema=_tool_schemas[name],
            handler=_read(name),
            # the holdout-scope reads (spec: "No tool may take a
            # holdout-scope action without the open-holdout flow"): the M5
            # gate (D-030) answers 409 without an opened holdout
            requires_holdout=name
            in {"run_backtest", "run_validation", "run_event_study"},
        )
    for name in RESEARCH_NAMES:
        register(
            name=name,
            tier="RESEARCH",
            schema=_tool_schemas[name],
            handler=_read(name),
            # compute_stats on the HOLDOUT scope is holdout-gated too
            # (D-064); create_experiment writes only via supersedes on
            # frozen families (D-041); no UPDATE-shaped call exists (D-063)
            requires_holdout=name in {"compute_stats"},
            touches_frozen=name in {"create_experiment"},
        )


_register_defaults()


def assert_no_order_tools() -> None:
    """The AC gate: no order-related tool exists in the registry."""
    forbidden = ("order", "place_order", "cancel_order", "trade", "execute")
    for name in _REGISTRY:
        lowered = name.lower()
        assert not any(
            forbidden_word in lowered for forbidden_word in forbidden
        ), f"order-related tool exists: {name}"
