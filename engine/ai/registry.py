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

Tier = Literal["READ_ONLY", "RESEARCH"]


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

    for name in READ_ONLY_NAMES:
        register(
            name=name,
            tier="READ_ONLY",
            schema={"type": "object", "title": name},
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
            schema={"type": "object", "title": name},
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
