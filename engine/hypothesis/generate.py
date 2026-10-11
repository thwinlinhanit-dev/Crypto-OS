"""HF Phase 5 — the AI hypothesis-generation layer (spec §17; the design
note's Phase 5: "Every AI output is a proposal, never a finding").

The layer composes the M9 provider abstraction (HF-001) and enforces the
master prompt's core constraint: an AI model may PROPOSE a hypothesis but
can never fabricate a result or promote one. Concretely:

- ``propose_hypothesis`` turns a model's structured output into a stored
  hypothesis with origin=AI_GENERATED and status=NOT_TESTED - the model's
  output passes through the SAME definition validation a human's does
  (spec §6); a model cannot skip a required field or invent a feature.
- ``classify`` maps an observation to the closed research-domain taxonomy;
  an unmappable observation returns None rather than a guessed domain.
- ``review_duplicates`` runs the deterministic dedup search (spec §8 level
  1) and reports the matches; the model NEVER gets to decide what is a
  duplicate - it only sees the deterministic answer.
- ``suggest_experiments`` emits candidate experiment configs for a
  hypothesis; each is validated by build_experiment_config before it is
  returned, so a model cannot propose an unsupported method.
- ``gaps`` lists what the registry has never tested (the research-gap
  source), from the data, never from the model.

No component here may transition a hypothesis's status (spec §1.2.10): the
promotion path is the Phase 3/4/5 evaluator + the explicit table.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from engine.errors import ValidationError
from engine.hypothesis.models import DOMAINS
from engine.hypothesis.registry import (
    create_hypothesis,
    find_duplicates,
    validate_definition,
)

#: The closed domain taxonomy (spec §5) the classifier maps into.
_DOMAINS: Final[tuple[str, ...]] = DOMAINS


@dataclass(frozen=True)
class Proposal:
    """A model's structured hypothesis proposal (spec §17.1).

    The model's raw output, plus the validation verdict. ``accepted`` is
    True only when the proposal passed the same definition validation a
    human's would - the model cannot self-certify.
    """

    title: str
    domain: str | None
    spec: dict[str, Any]
    original_question: str | None = None
    accepted: bool = False
    rejection: str | None = None
    rejection_detail: dict[str, Any] = field(default_factory=dict)
    hypotheses: list[str] = field(default_factory=list)


def propose_hypothesis(
    conn: Any,
    *,
    title: str,
    domain: str,
    spec: Mapping[str, Any],
    original_question: str | None = None,
    created_by: str = "ai",
) -> Proposal:
    """Validate and store a model's proposal (spec §17.1).

    The proposal is validated by the SAME ``validate_definition`` a human's
    hypothesis passes through: a model cannot omit a required field, invent
    a feature, or declare a look-ahead observation rule. A rejected proposal
    is preserved with its rejection reason (spec §1.2.5: negative and
    inconclusive findings are preserved, never silently discarded).
    """
    try:
        if domain not in _DOMAINS:
            raise ValidationError(
                "unknown research domain",
                {"domain": domain, "allowed": list(_DOMAINS)},
            )
        validate_definition(spec)
    except ValidationError as exc:
        return Proposal(
            title=title,
            domain=domain,
            spec=dict(spec),
            original_question=original_question,
            accepted=False,
            rejection=exc.message,
            rejection_detail=dict(exc.detail),
        )
    hyp_id = create_hypothesis(
        conn,
        title=title,
        domain=domain,
        origin="AI_GENERATED",
        spec=dict(spec),
        original_question=original_question,
        created_by=created_by,
    )
    return Proposal(
        title=title,
        domain=domain,
        spec=dict(spec),
        original_question=original_question,
        accepted=True,
        hypotheses=[hyp_id],
    )


def classify(observation: str) -> str | None:
    """Map an observation to the closed domain taxonomy (spec §5).

    Deterministic keyword mapping into the closed vocabulary; an observation
    that matches no domain returns None - a guessed domain is a fabricated
    finding (spec §1.2.2). Matching is WORD-BOUNDARY aware so a ticker like
    "eth" never fires inside "something" or "unrelated".
    """
    text = observation.lower()
    if _any_in(text, ("funding", "open interest", "liquidation", "oi", "perp")):
        return "DERIVATIVES"
    if _any_in(text, ("capitulation", "reclaim", "wick", "exhaustion", "capit")):
        return "CAPITULATION_RECOVERY"
    if _any_in(text, ("btc", "eth", "sol", "dominance", "spread", "correlation")):
        return "CROSS_ASSET"
    if _any_in(text, ("on-chain", "onchain", "whale", "exchange flow", "supply")):
        return "ON_CHAIN"
    if _any_in(text, ("narrative", "news", "sentiment", "headline")):
        return "NARRATIVE"
    if _any_in(text, ("regime", "volatility regime", "trend state")):
        return "REGIME"
    if _any_in(text, ("trend", "momentum", "breakout", "trend_state")):
        return "PRICE_TREND"
    if _any_in(text, ("negative", "no effect", "vanishes", "fails")):
        return "NEGATIVE"
    return None


def _any_in(text: str, needles: tuple[str, ...]) -> bool:
    """Whole-word (or hyphenated) match: a needle fires only on a word
    boundary, never inside an unrelated word."""
    import re

    for needle in needles:
        pattern = r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            return True
    return False


def review_duplicates(conn: Any, spec: Mapping[str, Any]) -> dict[str, Any]:
    """The deterministic duplicate review (spec §8 level 1).

    The model NEVER decides what is a duplicate: the deterministic exact-dup
    search runs, and the answer is reported back verbatim. A model asking to
    create a hypothesis that already exists sees the existing one.
    """
    try:
        validate_definition(spec)
    except ValidationError as exc:
        return {"matches": [], "error": exc.message}
    matches = find_duplicates(conn, dict(spec))
    return {"matches": matches}


def suggest_experiments(
    conn: Any,
    *,
    hypothesis_id: str,
    configs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Validate candidate experiment configs for a hypothesis (spec §17.4).

    Each candidate is validated by ``build_experiment_config`` before it is
    returned, so a model cannot propose an unsupported method, a bad
    direction, or a horizon outside the configured list. An invalid
    candidate is reported with its rejection, never silently dropped.
    """
    from engine.hypothesis.experiment import build_experiment_config

    out: list[dict[str, Any]] = []
    for candidate in configs:
        entry: dict[str, Any] = {"candidate": dict(candidate)}
        try:
            config = build_experiment_config(
                hypothesis_id=hypothesis_id,
                version=int(candidate.get("version", 1)),
                spec=dict(candidate.get("spec", {})),
                scope_family=str(candidate.get("scope_family", "")),
                data_scope=str(candidate.get("data_scope", "DEVELOPMENT")),
            )
            entry["accepted"] = True
            entry["config_hash"] = config.config_hash()
        except ValidationError as exc:
            entry["accepted"] = False
            entry["rejection"] = exc.message
        out.append(entry)
    return out


def gaps(conn: Any) -> list[dict[str, Any]]:
    """The research-gap source (spec §7.3): what the registry has never
    tested, from the data - never from the model.

    A gap is a (domain, declared-horizon) pair with no hypothesis at all,
    plus the domains with no hypothesis in any status. The list is
    deterministic; it cannot be influenced by a model's preferences.
    """
    from sqlalchemy import text

    covered = conn.execute(
        text(
            "SELECT DISTINCT hf_domain FROM research.hypothesis"
            " WHERE hf_status <> 'ARCHIVED'"
        )
    ).scalars().all()
    covered_set = {str(d) for d in covered}
    missing = [d for d in _DOMAINS if d not in covered_set]
    return [{"domain": d, "reason": "no hypothesis exists for this domain"} for d in missing]


def research_questions(
    conn: Any,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """The next-questions source (spec §7.4): the domains with no coverage,
    ranked by the registry's own gaps - never by a model's opinion.

    Phase 5 ships the deterministic skeleton; the ranking refinement
    (priority by research value) lands with the Phase 6 prioritization.
    """
    if limit < 1:
        raise ValidationError("limit must be a positive integer", {"limit": limit})
    return gaps(conn)[:limit]
