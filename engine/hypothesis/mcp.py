"""HF Phase 7 — the MCP tool exposure + the decision provider (spec §18;
the design note's Phase 7: "extend, not replace").

The HF tools are registered into the EXISTING M9 registry (HF-001): same
``register()`` seam, same ``(conn, args) -> dict`` handler shape, same
``canonical_ref_id`` provenance, same audit. The trust boundary is
unchanged: a tool with no real implementation returns an explicit
NOT_IMPLEMENTED, never a fake success.

The three tools this adds (spec §9's HF surface):

- ``research_question.search`` - the next-questions source (deterministic:
  the registry's own gaps, never a model's opinion).
- ``hypothesis.evaluate`` - run the robustness suite + the validation
  decision for a hypothesis (the rules; the model reads the verdict, it
  cannot author it).
- ``hypothesis.propose`` - a model's structured proposal, stored through the
  SAME validation a human's passes (every AI output is a proposal, never a
  finding).

The decision provider (spec §18.1): a configured model provider OR the
deterministic local baseline. The baseline ranks research questions by the
registry's coverage - reproducible, no key, no network. A model provider is
never authoritative: it produces a recommendation the owner may accept,
and every recommendation is audited.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from engine.ai.handlers import canonical_ref_id
from engine.errors import ValidationError
from engine.hypothesis import generate as hf_generate
from engine.hypothesis import robustness as hf_robustness

#: The HF MCP tool names (spec §9; extend the M9 registry, never fork it).
RESEARCH_QUESTION_SEARCH: Final[str] = "research_question.search"
HYPOTHESIS_EVALUATE: Final[str] = "hypothesis.evaluate"
HYPOTHESIS_PROPOSE: Final[str] = "hypothesis.propose"

#: The tier: the HF research tools are RESEARCH-tier (they read and write
#: research state, never orders - D-063).
_TIER: Final[str] = "RESEARCH"


# --------------------------------------------------------------------------
# The tool schemas (real JSON Schemas, spec §9; never a stub)
# --------------------------------------------------------------------------


def schemas() -> dict[str, dict[str, Any]]:
    """The real JSON Schemas for the three HF tools."""
    return {
        RESEARCH_QUESTION_SEARCH: {
            "type": "object",
            "title": RESEARCH_QUESTION_SEARCH,
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": [],
        },
        HYPOTHESIS_EVALUATE: {
            "type": "object",
            "title": HYPOTHESIS_EVALUATE,
            "properties": {
                "hypothesis_id": {"type": "string"},
                "stats_result": {"type": "object"},
                "by_regime": {"type": "object"},
                "neighbor_results": {"type": "array", "items": {"type": "object"}},
                "outlier_shares": {"type": "array", "items": {"type": "number"}},
                "control_result": {"type": "object"},
            },
            "required": ["hypothesis_id", "stats_result"],
        },
        HYPOTHESIS_PROPOSE: {
            "type": "object",
            "title": HYPOTHESIS_PROPOSE,
            "properties": {
                "title": {"type": "string"},
                "domain": {"type": "string"},
                "spec": {"type": "object"},
                "original_question": {"type": "string"},
            },
            "required": ["title", "domain", "spec"],
        },
    }


# --------------------------------------------------------------------------
# The handlers ((conn, args) -> dict; the M9 shape)
# --------------------------------------------------------------------------


def research_question_search(conn: Any, args: dict[str, Any]) -> dict[str, Any]:
    """``research_question.search``: the deterministic next-questions
    source (spec §7.4) - the registry's own gaps, never a model's opinion."""
    limit = args.get("limit", 5)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValidationError("limit must be a positive integer", {"limit": limit})
    questions = hf_generate.research_questions(conn, limit=limit)
    payload = {"tool": RESEARCH_QUESTION_SEARCH, "questions": questions}
    return {"ok": True, "ref_id": canonical_ref_id(payload), **payload}


def hypothesis_evaluate(conn: Any, args: dict[str, Any]) -> dict[str, Any]:
    """``hypothesis.evaluate``: run the robustness suite and return the
    validation decision (spec §13/§14.2). The model reads the verdict; it
    cannot author it."""
    hypothesis_id = args.get("hypothesis_id")
    stats_result = args.get("stats_result")
    if not isinstance(hypothesis_id, str) or not hypothesis_id:
        raise ValidationError("hypothesis_id is required", {"hypothesis_id": hypothesis_id})
    if not isinstance(stats_result, Mapping):
        raise ValidationError("stats_result is required", {})

    by_regime = args.get("by_regime")
    neighbors = args.get("neighbor_results")
    shares = args.get("outlier_shares")
    control = args.get("control_result")
    report = hf_robustness.run_robustness(
        stats_result=dict(stats_result),
        by_regime=dict(by_regime) if isinstance(by_regime, Mapping) else None,
        neighbor_results=list(neighbors) if isinstance(neighbors, Sequence) else None,
        outlier_shares=(
            (float(shares[0]), float(shares[1]), float(shares[2]))
            if isinstance(shares, Sequence) and len(shares) == 3
            else None
        ),
        control_result=dict(control) if isinstance(control, Mapping) else None,
        outcomes=None,
    )
    decision = hf_robustness.validation_decision(report)
    payload = {
        "tool": HYPOTHESIS_EVALUATE,
        "hypothesis_id": hypothesis_id,
        "decision": decision,
        "checks": [
            {
                "name": c.name,
                "passed": c.passed,
                "mandatory": c.mandatory,
                "detail": c.detail,
            }
            for c in report.checks
        ],
    }
    return {"ok": True, "ref_id": canonical_ref_id(payload), **payload}


def hypothesis_propose(conn: Any, args: dict[str, Any]) -> dict[str, Any]:
    """``hypothesis.propose``: a model's structured proposal (spec §17.1).

    Every AI output is a proposal, never a finding: the proposal is stored
    through the SAME validation a human's passes, as AI_GENERATED +
    NOT_TESTED. A rejected proposal is preserved with its detail.
    """
    title = args.get("title")
    domain = args.get("domain")
    spec = args.get("spec")
    if not isinstance(title, str) or not isinstance(domain, str) or not isinstance(spec, Mapping):
        raise ValidationError(
            "title, domain and spec are required",
            {"title": title, "domain": domain, "spec": type(spec).__name__},
        )
    proposal = hf_generate.propose_hypothesis(
        conn,
        title=title,
        domain=domain,
        spec=dict(spec),
        original_question=args.get("original_question"),
    )
    payload = {
        "tool": HYPOTHESIS_PROPOSE,
        "accepted": proposal.accepted,
        "rejection": proposal.rejection,
        "rejection_detail": proposal.rejection_detail,
        "hypothesis_ids": list(proposal.hypotheses),
    }
    return {"ok": True, "ref_id": canonical_ref_id(payload), **payload}


# --------------------------------------------------------------------------
# The registration (extend the M9 registry)
# --------------------------------------------------------------------------


def register_all() -> list[str]:
    """Register the three HF tools into the existing M9 registry (spec §18:
    extend, not replace). Returns the registered names."""
    from engine.ai.registry import register

    registered: list[str] = []
    for name, handler in (
        (RESEARCH_QUESTION_SEARCH, research_question_search),
        (HYPOTHESIS_EVALUATE, hypothesis_evaluate),
        (HYPOTHESIS_PROPOSE, hypothesis_propose),
    ):
        register(
            name=name,
            tier=_TIER,  # type: ignore[arg-type]
            schema=schemas()[name],
            handler=handler,
        )
        registered.append(name)
    return registered


# --------------------------------------------------------------------------
# The decision provider (spec §18.1)
# --------------------------------------------------------------------------


def local_baseline_ranking(conn: Any, *, limit: int = 5) -> list[dict[str, Any]]:
    """The deterministic local baseline (spec §18.1): reproducible, no key,
    no network. Ranks the registry's coverage gaps.

    A model provider, when configured, is never authoritative - it produces
    a recommendation the owner may accept, and every recommendation is
    audited. The baseline needs no such trust: it reads the data.
    """
    return hf_generate.research_questions(conn, limit=limit)


def recommendation(
    conn: Any,
    *,
    provider: str = "LOCAL_BASELINE",
    limit: int = 5,
) -> dict[str, Any]:
    """A decision recommendation (spec §18.1). Only the LOCAL_BASELINE
    provider is implemented; any other provider is refused explicitly rather
    than silently substituted (PD-10)."""
    if provider != "LOCAL_BASELINE":
        raise ValidationError(
            "decision provider is not configured",
            {"provider": provider, "implemented": ["LOCAL_BASELINE"]},
        )
    ranking = local_baseline_ranking(conn, limit=limit)
    payload = {
        "provider": provider,
        "authoritative": False,
        "ranking": ranking,
    }
    return {"ok": True, "ref_id": canonical_ref_id(payload), **payload}
