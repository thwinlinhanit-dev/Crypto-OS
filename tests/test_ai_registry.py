"""M9 — the tool-registry AC (spec M9: "tool-registry test asserts no
order-related tools and that experiment tools respect immutability").

This suite is the AC's explicit gate.
"""

from __future__ import annotations

import pytest

from engine.ai.registry import (
    READ_ONLY_NAMES,
    RESEARCH_NAMES,
    assert_no_order_tools,
    names,
    tool,
    tools,
)
from engine.visualization.mcp import TOOL_NAMES as VISUALIZATION_TOOL_NAMES


def test_the_twenty_spec_tools_exist() -> None:
    """Spec §9 names the tools verbatim; the registry matches it exactly
    (21 names — the spec's list is the authority, not the round number).

    The §26 visualization tools (M16) extend the SAME registry, so this
    counts only the §9 M9 layer; the visualization layer is asserted in
    tests/test_visualization_ai.py."""
    m9_names = [n for n in names() if n not in VISUALIZATION_TOOL_NAMES]
    assert len(m9_names) == 21, sorted(m9_names)
    assert sorted(READ_ONLY_NAMES + RESEARCH_NAMES) == m9_names


def test_read_only_tier_is_the_read_subset() -> None:
    read_tools = {
        t.name for t in tools("READ_ONLY") if t.name not in VISUALIZATION_TOOL_NAMES
    }
    assert read_tools == set(READ_ONLY_NAMES)


def test_research_tier_is_the_write_subset() -> None:
    research_tools = {
        t.name for t in tools("RESEARCH") if t.name not in VISUALIZATION_TOOL_NAMES
    }
    assert research_tools == set(RESEARCH_NAMES)


def test_no_order_related_tools_exist() -> None:
    """The AC (R6/PD-1: research only, no order path)."""
    assert_no_order_tools()


@pytest.mark.parametrize(
    "forbidden",
    ["order", "place_order", "cancel_order", "execute", "kill_switch"],
)
def test_order_shaped_names_are_unknown_tools(forbidden: str) -> None:
    """D-063: the registry is closed - unknown tools raise, never a no-op."""
    from engine.ai.registry import ToolNotFoundError

    with pytest.raises(ToolNotFoundError):
        tool(forbidden)


def test_experiment_tools_respect_immutability() -> None:
    """The AC: experiment tools respect immutability.

    freeze_experiment exists (the freeze itself); create_experiment is
    flagged touches_frozen so the runner routes it through supersedes on a
    frozen family; no UPDATE-shaped experiment tool exists.
    """
    freeze = tool("freeze_experiment")
    assert freeze.tier == "READ_ONLY"

    create = tool("create_experiment")
    assert create.tier == "RESEARCH"
    assert create.touches_frozen

    # there is no update_experiment / edit_experiment / delete_experiment
    from engine.ai.registry import ToolNotFoundError

    for name in ("update_experiment", "edit_experiment", "delete_experiment",
                 "unfreeze_experiment", "modify_experiment"):
        with pytest.raises(ToolNotFoundError):
            tool(name)


def test_run_backtest_is_append_only_not_a_mutation() -> None:
    """D-041: every backtest belongs to an experiment; the tool creates a new
    backtest row, never edits a frozen one."""
    runner = tool("run_backtest")
    assert runner.tier == "READ_ONLY"
    assert runner.touches_frozen is False
    assert runner.requires_holdout is True


def test_holdout_scope_tools_require_the_open_holdout_flow() -> None:
    """Spec §9: "No tool may take a holdout-scope action without the
    open-holdout flow." (D-064)."""
    for name in ("compute_stats", "run_backtest", "run_validation"):
        assert tool(name).requires_holdout, name


def test_read_tools_never_require_holdout() -> None:
    """Read-only READ tools (charts, candles, search) never gate on a
    holdout; the holdout-scope reads do (D-064)."""
    plain_reads = set(READ_ONLY_NAMES) - {
        "run_backtest", "run_validation", "run_event_study",
    }
    for name in plain_reads:
        assert tool(name).requires_holdout is False, name


def test_every_tool_carries_a_schema() -> None:
    """The MCP server exposes the tools "with the same schemas"; a tool
    without a schema would break that contract."""
    for t in tools():
        assert isinstance(t.schema, dict) and t.schema.get("type") == "object", t.name
