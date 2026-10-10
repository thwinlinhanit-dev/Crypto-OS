"""M9 — AI package: the tool registry + number integrity (spec §9)."""

from engine.ai.integrity import (
    resolve_placeholders,
    scan_free_text,
    validate_report,
)
from engine.ai.registry import (
    READ_ONLY_NAMES,
    RESEARCH_NAMES,
    ForbiddenToolError,
    Tool,
    ToolNotFoundError,
    assert_no_order_tools,
    register,
    tool,
    tools,
)

__all__ = [
    "READ_ONLY_NAMES",
    "RESEARCH_NAMES",
    "ForbiddenToolError",
    "Tool",
    "ToolNotFoundError",
    "assert_no_order_tools",
    "register",
    "resolve_placeholders",
    "scan_free_text",
    "tool",
    "tools",
    "validate_report",
]
