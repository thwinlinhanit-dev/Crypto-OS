"""M16 — the visualization MCP tool surface (spec §26).

The §26 tools registered into the EXISTING M9 registry (extend, not
replace - the HF pattern):

    list_visualizations, get_visualization, create_visualization_draft,
    validate_visualization, run_visualization, get_visualization_data,
    get_visualization_provenance, save_visualization, clone_visualization,
    compare_visualizations, get_market_canvas, get_chart_context

The permission tiers (§26): READ_ONLY, RESEARCH, VISUALIZATION_WRITE.
NO TRADING TIER (D-103) - a test asserts the trading tier never exists.

Every tool result carries the §26 envelope (D-104): ref_id, dataset_version,
program_hash, runtime_version, data_quality, result - and every call is
audited (D-062).
"""

from __future__ import annotations

from typing import Any, Final

from engine.ai.handlers import canonical_ref_id
from engine.errors import ValidationError
from engine.visualization.ai_generation import (
    RUNTIME_VERSION,
    GenerationRequest,
    pipeline,
)

#: The §26 tool names the visualization layer OWNS and registers.
#: ``get_chart_context`` is already an M9 §9 tool (it serves the chart/AI
#: surface); the visualization layer REUSES it rather than re-registering,
#: so the M9 registry count is unchanged.
TOOL_NAMES: Final[tuple[str, ...]] = (
    "list_visualizations",
    "get_visualization",
    "create_visualization_draft",
    "validate_visualization",
    "run_visualization",
    "get_visualization_data",
    "get_visualization_provenance",
    "save_visualization",
    "clone_visualization",
    "compare_visualizations",
    "get_market_canvas",
)

#: The §26 tools that already exist in the M9 registry (reused, not
#: re-registered).
M9_REUSED_TOOLS: Final[tuple[str, ...]] = ("get_chart_context",)

#: The §26 tier per tool (D-103). No trading tier exists.
TOOL_TIERS: Final[dict[str, str]] = {
    "list_visualizations": "READ_ONLY",
    "get_visualization": "READ_ONLY",
    "get_visualization_data": "READ_ONLY",
    "get_visualization_provenance": "READ_ONLY",
    "validate_visualization": "RESEARCH",
    "run_visualization": "RESEARCH",
    "compare_visualizations": "RESEARCH",
    "get_market_canvas": "RESEARCH",
    "create_visualization_draft": "VISUALIZATION_WRITE",
    "save_visualization": "VISUALIZATION_WRITE",
    "clone_visualization": "VISUALIZATION_WRITE",
}

#: The forbidden tier (§3/§26): no trading tier ever exists.
FORBIDDEN_TIER: Final[str] = "TRADING"


def schemas() -> dict[str, dict[str, Any]]:
    """The real JSON Schemas for the §26 tools (never stubs).

    One schema per owned ``TOOL_NAMES`` entry — ``set(schemas())`` equals
    ``set(TOOL_NAMES)`` so the registry never falls back to a stub (D-104).
    ``get_chart_context`` is intentionally absent: it is the M9 §9 tool,
    reused, not re-registered, so M9 owns its schema.
    """
    return {
        "list_visualizations": {
            "type": "object",
            "title": "list_visualizations",
            "properties": {
                "status": {"type": "string", "enum": ["DRAFT", "VALIDATED", "DEPRECATED"]},
                "created_by": {"type": "string", "enum": ["human", "ai"]},
                "as_of": {"type": "string"},
            },
            "required": [],
        },
        "get_visualization": {
            "type": "object",
            "title": "get_visualization",
            "properties": {
                "visualization_id": {"type": "string"},
                "version": {"type": "string"},
            },
            "required": ["visualization_id"],
        },
        "create_visualization_draft": {
            "type": "object",
            "title": "create_visualization_draft",
            "properties": {
                "request": {"type": "string"},
                "visualization_id": {"type": "string"},
                "version": {"type": "string"},
                "params": {"type": "object"},
            },
            "required": ["request", "visualization_id"],
        },
        "validate_visualization": {
            "type": "object",
            "title": "validate_visualization",
            "properties": {
                "visualization_id": {"type": "string"},
                "version": {"type": "string"},
                "required_datasets": {"type": "array", "items": {"type": "string"}},
                "performance_budget": {"type": "integer"},
            },
            "required": ["visualization_id", "required_datasets"],
        },
        "run_visualization": {
            "type": "object",
            "title": "run_visualization",
            "properties": {
                "visualization_id": {"type": "string"},
                "version": {"type": "string"},
                "instrument_id": {"type": "string"},
                "timeframe": {"type": "string"},
                "as_of": {"type": "string"},
                "dataset_version": {"type": "string"},
            },
            "required": ["visualization_id"],
        },
        "get_visualization_data": {
            "type": "object",
            "title": "get_visualization_data",
            "properties": {
                "visualization_id": {"type": "string"},
                "version": {"type": "string"},
                "instrument_id": {"type": "string"},
                "timeframe": {"type": "string"},
                "as_of": {"type": "string"},
                "dataset_version": {"type": "string"},
            },
            "required": ["visualization_id"],
        },
        "get_visualization_provenance": {
            "type": "object",
            "title": "get_visualization_provenance",
            "properties": {
                "visualization_id": {"type": "string"},
                "version": {"type": "string"},
            },
            "required": ["visualization_id"],
        },
        "save_visualization": {
            "type": "object",
            "title": "save_visualization",
            "properties": {
                "visualization_id": {"type": "string"},
                "version": {"type": "string"},
                "title": {"type": "string"},
                "source": {"type": "string"},
                "required_datasets": {"type": "array", "items": {"type": "string"}},
                "outputs": {"type": "array", "items": {"type": "string"}},
                "performance_budget": {"type": "integer"},
            },
            "required": ["visualization_id", "source", "required_datasets"],
        },
        "clone_visualization": {
            "type": "object",
            "title": "clone_visualization",
            "properties": {
                "visualization_id": {"type": "string"},
                "version": {"type": "string"},
                "new_visualization_id": {"type": "string"},
                "new_version": {"type": "string"},
            },
            "required": ["visualization_id", "new_visualization_id"],
        },
        "compare_visualizations": {
            "type": "object",
            "title": "compare_visualizations",
            "properties": {
                "visualization_ids": {"type": "array", "items": {"type": "string"}},
                "as_of": {"type": "string"},
            },
            "required": ["visualization_ids"],
        },
        "get_market_canvas": {
            "type": "object",
            "title": "get_market_canvas",
            "properties": {
                "instrument_id": {"type": "string"},
                "timeframe": {"type": "string"},
                "as_of": {"type": "string"},
                "dataset_version": {"type": "string"},
                "viewport": {"type": "object"},
            },
            "required": ["instrument_id"],
        },
    }


def create_visualization_draft(
    conn: Any,
    args: dict[str, Any],
    *,
    available_datasets: tuple[str, ...] = ("candles",),
) -> dict[str, Any]:
    """``create_visualization_draft``: the §25 pipeline from a natural
    language request (the VISUALIZATION_WRITE tier)."""
    request = GenerationRequest(
        request=str(args.get("request", "")),
        visualization_id=str(args.get("visualization_id", "")),
        version=str(args.get("version", "1.0.0")),
        params=dict(args.get("params", {})),
    )
    result = pipeline(request, available_datasets=available_datasets)
    payload = {"tool": "create_visualization_draft", **result.envelope()}
    return {"ok": True, **payload}


def get_chart_context(
    conn: Any,
    args: dict[str, Any],
) -> dict[str, Any]:
    """``get_chart_context``: the canvas context (the §12 shared context:
    instrument/timeframe + as_of). A READ_ONLY tool - it never executes."""
    payload = {
        "tool": "get_chart_context",
        "instrument_id": args.get("instrument_id"),
        "timeframe": args.get("timeframe", "1D"),
        "as_of": args.get("as_of"),
        "runtime_version": RUNTIME_VERSION,
    }
    return {"ok": True, "ref_id": canonical_ref_id(payload), **payload}


def register_all() -> list[str]:
    """Register the §26 tools into the M9 registry (extend, not replace)."""
    from engine.ai.registry import register

    registered: list[str] = []
    for name in TOOL_NAMES:
        handler = (
            create_visualization_draft
            if name == "create_visualization_draft"
            else get_chart_context
            if name == "get_chart_context"
            else _not_implemented(name)
        )
        register(
            name=name,
            tier=TOOL_TIERS[name],  # type: ignore[arg-type]
            schema=schemas().get(
                name,
                {"type": "object", "title": name, "properties": {}, "required": []},
            ),
            handler=handler,
        )
        registered.append(name)
    return registered


def _not_implemented(name: str) -> Any:
    """The M9 trust boundary: an unbound tool returns an explicit
    NOT_IMPLEMENTED, never a fake success."""

    def handler(conn: Any, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "NOT_IMPLEMENTED",
            "tool": name,
            "ref_id": canonical_ref_id({"tool": name, "error": "NOT_IMPLEMENTED"}),
        }

    return handler


def assert_no_trading_tier() -> None:
    """§3/§26 (D-103): no trading tier ever exists. A registry that grows a
    TRADING tier is refused."""
    for name, tier in TOOL_TIERS.items():
        if tier == FORBIDDEN_TIER:
            raise ValidationError(
                "a trading tier must never exist (§3/§26)", {"tool": name}
            )
