"""HF Phase 1 — the hypothesis model (spec §6/§14.2; design note
docs/hypothesis-factory/ARCHITECTURE.md).

Pure vocabularies + status-transition rules. The status vocabulary is the
spec §14.2 list (HF-003); transitions derive from explicit rules, never from
an AI model's confidence (spec §14.2).
"""

from __future__ import annotations

from typing import Final

from engine.errors import ValidationError

#: The spec §14.2 validation status vocabulary (HF-003).
STATUSES: Final[tuple[str, ...]] = (
    "NOT_TESTED",
    "EXPLORATORY",
    "NEEDS_MORE_DATA",
    "INVALID_EXPERIMENT",
    "FAILED_STATISTICAL_TEST",
    "FAILED_ROBUSTNESS",
    "FAILED_OUT_OF_SAMPLE",
    "PASSED_RESEARCH_VALIDATION",
    "PAPER_TRADING",
    "PAPER_TRADING_PASSED",
    "REQUIRES_HUMAN_REVIEW",
    "ARCHIVED",
)

#: The spec §5 taxonomy (the initial set; extensible by migration).
DOMAINS: Final[tuple[str, ...]] = (
    "PRICE_TREND",
    "DERIVATIVES",
    "CAPITULATION_RECOVERY",
    "CROSS_ASSET",
    "ON_CHAIN",
    "FUNDAMENTAL",
    "NARRATIVE",
    "REGIME",
    "NEGATIVE",
)

#: The spec §4 origin vocabulary (AI is a proposal, never a finding).
ORIGINS: Final[tuple[str, ...]] = ("AI_GENERATED", "PATTERN_DISCOVERY", "RESEARCH_GAP", "HUMAN")

#: The spec §18.2 relationship vocabulary.
RELATIONSHIPS: Final[tuple[str, ...]] = (
    "DERIVED_FROM",
    "REPLICATION_OF",
    "CONTRADICTS",
    "REFINES",
    "TESTS_INTERACTION_WITH",
    "SHARES_FEATURE_WITH",
    "SUPERSEDES",
    "INVALIDATES",
)

#: The spec §11.3 economic split: a threshold is predeclared, never retrofitted.
DIRECTIONS: Final[tuple[str, ...]] = ("HIGHER", "LOWER", "DIFFERENT")

#: The explicit transition table (spec §14.2: "Statuses must be derived from
#: explicit rules and actual results"). A status not listed here is reachable
#: only via the human-review workflow (the owner's override).
ALLOWED_TRANSITIONS: Final[dict[str, tuple[str, ...]]] = {
    "NOT_TESTED": ("EXPLORATORY", "NEEDS_MORE_DATA", "INVALID_EXPERIMENT", "ARCHIVED"),
    "EXPLORATORY": (
        "FAILED_STATISTICAL_TEST",
        "FAILED_ROBUSTNESS",
        "NEEDS_MORE_DATA",
        "REQUIRES_HUMAN_REVIEW",
        "INVALID_EXPERIMENT",
        "ARCHIVED",
    ),
    "NEEDS_MORE_DATA": ("EXPLORATORY", "ARCHIVED"),
    "FAILED_STATISTICAL_TEST": ("ARCHIVED", "REQUIRES_HUMAN_REVIEW"),
    "FAILED_ROBUSTNESS": ("ARCHIVED", "REQUIRES_HUMAN_REVIEW"),
    "FAILED_OUT_OF_SAMPLE": ("ARCHIVED", "REQUIRES_HUMAN_REVIEW"),
    "REQUIRES_HUMAN_REVIEW": (
        "PASSED_RESEARCH_VALIDATION",
        "ARCHIVED",
        "FAILED_OUT_OF_SAMPLE",
    ),
    "PASSED_RESEARCH_VALIDATION": (
        "PAPER_TRADING",
        "FAILED_OUT_OF_SAMPLE",
        "REQUIRES_HUMAN_REVIEW",
    ),
    "PAPER_TRADING": ("PAPER_TRADING_PASSED", "REQUIRES_HUMAN_REVIEW", "ARCHIVED"),
    "PAPER_TRADING_PASSED": ("REQUIRES_HUMAN_REVIEW", "ARCHIVED"),
    "INVALID_EXPERIMENT": ("ARCHIVED", "NOT_TESTED"),
    "ARCHIVED": (),
}


def assert_valid_status(status: str) -> None:
    if status not in STATUSES:
        raise ValidationError(
            "unknown hypothesis status",
            {"status": status, "allowed": list(STATUSES)},
        )


def assert_valid_domain(domain: str) -> None:
    if domain not in DOMAINS:
        raise ValidationError(
            "unknown research domain",
            {"domain": domain, "allowed": list(DOMAINS)},
        )


def assert_valid_origin(origin: str) -> None:
    if origin not in ORIGINS:
        raise ValidationError(
            "unknown hypothesis origin",
            {"origin": origin, "allowed": list(ORIGINS)},
        )


def assert_valid_relationship(relationship: str) -> None:
    if relationship not in RELATIONSHIPS:
        raise ValidationError(
            "unknown hypothesis relationship",
            {"relationship": relationship, "allowed": list(RELATIONSHIPS)},
        )


def assert_valid_transition(current: str, target: str) -> None:
    """A status transition must be in the explicit table (spec §14.2)."""
    assert_valid_status(current)
    assert_valid_status(target)
    if target not in ALLOWED_TRANSITIONS.get(current, ()):
        raise ValidationError(
            "illegal hypothesis status transition",
            {"from": current, "to": target, "allowed_from_here": list(ALLOWED_TRANSITIONS.get(current, ()))},
        )


def is_terminal(status: str) -> bool:
    """ARCHIVED is the only terminal status (a frozen evidence record)."""
    return status == "ARCHIVED"
