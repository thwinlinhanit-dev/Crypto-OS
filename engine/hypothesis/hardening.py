"""HF Phase 8 — the operational-hardening layer (spec §17.2/§18; the design
note's Phase 8: resource controls, error handling, audit logging).

The HF's operational controls, all fail-closed (PD-10):

- **resource controls** (spec §17.2): the generation loop, the experiment
  runs and the MCP calls each carry an explicit budget. A request without a
  resolved budget is refused - never run with an invented one.
- **the audit vocabulary**: every HF mutation writes an ``action`` from a
  closed set, so the audit log is queryable by action (D-062). An unknown
  action is a programming error, not a silent pass.
- **the generation budget**: the deterministic bound on how many hypotheses
  a generation round may propose, so a runaway loop cannot flood the
  registry (spec §17.2: "no runaway processes").

No component here authorizes live trading (spec §1.2.10).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from engine.errors import ValidationError

#: The HF audit-log actions (the closed vocabulary, D-062). Every HF
#: mutation writes one of these; the registry writes hypothesis.create and
#: hypothesis.status, the generator writes hypothesis.propose, the runner
#: writes experiment.run, the evaluator writes hypothesis.evaluate.
AUDIT_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "hypothesis.create",
        "hypothesis.status",
        "hypothesis.version",
        "hypothesis.propose",
        "hypothesis.relationship",
        "hypothesis.testing_family",
        "experiment.run",
        "hypothesis.evaluate",
        "research_question.search",
    }
)

#: The default generation budget (spec §17.2): the deterministic bound on
#: how many hypotheses one generation round may propose.
DEFAULT_GENERATION_BUDGET: Final[int] = 10

#: The default MCP call budget: the maximum HF tool calls per run.
DEFAULT_MCP_CALL_BUDGET: Final[int] = 50

#: The default experiment-run budget: the maximum bars one experiment may
#: load (the M5 served budget; a wider request is refused, not truncated).
DEFAULT_MAX_BARS: Final[int] = 5000


def assert_audit_action(action: str) -> None:
    """The audit action must be in the closed vocabulary (D-062)."""
    if action not in AUDIT_ACTIONS:
        raise ValidationError(
            "unknown audit action",
            {"action": action, "allowed": sorted(AUDIT_ACTIONS)},
        )


def generation_budget(*, requested: int | None = None, config: Mapping[str, Any] | None = None) -> int:
    """Resolve the generation budget (spec §17.2).

    The request may narrow the budget, never widen it past the configured
    maximum; a missing config value fails closed rather than defaulting to
    "unlimited".
    """
    max_budget = DEFAULT_GENERATION_BUDGET
    if config is not None:
        section = config.get("hypothesis_factory")
        if isinstance(section, Mapping):
            value = section.get("generation_budget")
            if value is not None:
                if type(value) is not int or value < 1:
                    raise ValidationError(
                        "generation_budget must be a positive integer",
                        {"generation_budget": value},
                    )
                max_budget = value
    if requested is None:
        return max_budget
    if type(requested) is not int or requested < 1:
        raise ValidationError(
            "requested generation budget must be a positive integer",
            {"requested": requested},
        )
    if requested > max_budget:
        raise ValidationError(
            "requested generation budget exceeds the configured maximum",
            {"requested": requested, "maximum": max_budget},
        )
    return requested


def mcp_call_budget(*, requested: int | None = None, config: Mapping[str, Any] | None = None) -> int:
    """Resolve the MCP call budget (spec §17.2): the request may narrow it,
    never widen it past the configured maximum."""
    max_budget = DEFAULT_MCP_CALL_BUDGET
    if config is not None:
        section = config.get("hypothesis_factory")
        if isinstance(section, Mapping):
            value = section.get("mcp_call_budget")
            if value is not None:
                if type(value) is not int or value < 1:
                    raise ValidationError(
                        "mcp_call_budget must be a positive integer",
                        {"mcp_call_budget": value},
                    )
                max_budget = value
    if requested is None:
        return max_budget
    if type(requested) is not int or requested < 1:
        raise ValidationError(
            "requested mcp_call_budget must be a positive integer",
            {"requested": requested},
        )
    if requested > max_budget:
        raise ValidationError(
            "requested mcp_call_budget exceeds the configured maximum",
            {"requested": requested, "maximum": max_budget},
        )
    return requested


def max_bars(*, requested: int | None = None, config: Mapping[str, Any] | None = None) -> int:
    """Resolve the per-experiment bar budget (spec §17.2 / the M5 served
    budget): a wider request is refused, never silently truncated."""
    budget = DEFAULT_MAX_BARS
    if config is not None:
        candles = config.get("candles")
        if isinstance(candles, Mapping):
            value = candles.get("max_limit")
            if value is not None:
                if type(value) is not int or value < 1:
                    raise ValidationError(
                        "candles.max_limit must be a positive integer",
                        {"max_limit": value},
                    )
                budget = value
    if requested is None:
        return budget
    if type(requested) is not int or requested < 1:
        raise ValidationError(
            "requested max_bars must be a positive integer",
            {"requested": requested},
        )
    if requested > budget:
        raise ValidationError(
            "requested max_bars exceeds the served budget",
            {"requested": requested, "budget": budget},
        )
    return requested
