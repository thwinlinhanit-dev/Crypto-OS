"""M12 — the visualization program vocabularies (spec §6/§9/§13/§22).

Pure closed vocabularies + the lifecycle transition table. The lifecycle
mirrors the HF status pattern (spec §6: behavior cannot silently change
under an existing version), and the provenance classes extend the event
engine's OBSERVED/CALCULATED/INFERRED vocabulary with AI (§22).
"""

from __future__ import annotations

from typing import Final

from engine.errors import ValidationError

#: The §6 program lifecycle: DRAFT → VALIDATED → PUBLISHED → DEPRECATED.
LIFECYCLE: Final[tuple[str, ...]] = (
    "DRAFT",
    "VALIDATED",
    "PUBLISHED",
    "DEPRECATED",
)

#: The §22 provenance classes. The first three extend the event engine's
#: vocabulary (engine/events/base.py); AI is the fourth (an AI value is
#: never presented as observed or calculated).
PROVENANCE_CLASSES: Final[tuple[str, ...]] = ("OBSERVED", "CALCULATED", "INFERRED", "AI")

#: The §13 render primitives M12 implements (the candle overlay and the
#: heatmap/profile/footprint primitives land with M13/M14 - spec §38.10).
RENDER_KINDS: Final[tuple[str, ...]] = ("LINE", "HUD", "TABLE")

#: The §9 initial dataset APIs (the declared-required_datasets vocabulary).
DATASETS: Final[tuple[str, ...]] = (
    "candles",
    "trades",
    "orderbook",
    "open_interest",
    "funding",
    "liquidations",
)

#: The §9 dataset quality states. The first three reuse the market
#: engine's vocabulary; the last two are the explicit failure states
#: (missing data fails explicitly, never substitutes - §3).
QUALITY_STATES: Final[tuple[str, ...]] = (
    "GREEN",
    "OPEN",
    "REPAIRED",
    "DATA_UNAVAILABLE",
    "DATA_INCOMPLETE",
)

#: The runtime identifier (§6: runtime is metadata; the worker/WASM
#: backends land later - spec §8).
RUNTIME: Final[str] = "cryptoos-ts"

#: The §3 non-negotiable permissions: a visualization program cannot
#: place/cancel/modify orders, touch credentials, the database, arbitrary
#: network or the filesystem.
PERMISSION_KEYS: Final[tuple[str, ...]] = ("network", "filesystem", "trading")

#: The §6 lifecycle transition table (explicit; no silent state change).
ALLOWED_LIFECYCLE_TRANSITIONS: Final[dict[str, tuple[str, ...]]] = {
    "DRAFT": ("VALIDATED", "DEPRECATED"),
    "VALIDATED": ("PUBLISHED", "DRAFT", "DEPRECATED"),
    "PUBLISHED": ("DEPRECATED",),
    "DEPRECATED": (),
}


def assert_valid_lifecycle(status: str) -> None:
    if status not in LIFECYCLE:
        raise ValidationError(
            "unknown program lifecycle status",
            {"status": status, "allowed": list(LIFECYCLE)},
        )


def assert_valid_lifecycle_transition(current: str, target: str) -> None:
    """A lifecycle transition must be in the explicit table (spec §6:
    behavior cannot silently change under an existing version)."""
    assert_valid_lifecycle(current)
    assert_valid_lifecycle(target)
    if target not in ALLOWED_LIFECYCLE_TRANSITIONS.get(current, ()):
        raise ValidationError(
            "illegal program lifecycle transition",
            {
                "from": current,
                "to": target,
                "allowed_from_here": list(ALLOWED_LIFECYCLE_TRANSITIONS.get(current, ())),
            },
        )


def assert_valid_provenance(provenance: str) -> None:
    if provenance not in PROVENANCE_CLASSES:
        raise ValidationError(
            "unknown provenance class",
            {"provenance": provenance, "allowed": list(PROVENANCE_CLASSES)},
        )


def assert_valid_render_kind(kind: str) -> None:
    if kind not in RENDER_KINDS:
        raise ValidationError(
            "unknown render kind",
            {"kind": kind, "allowed": list(RENDER_KINDS)},
        )


def assert_valid_dataset(dataset: str) -> None:
    if dataset not in DATASETS:
        raise ValidationError(
            "unknown dataset",
            {"dataset": dataset, "allowed": list(DATASETS)},
        )


def assert_valid_quality_state(state: str) -> None:
    if state not in QUALITY_STATES:
        raise ValidationError(
            "unknown dataset quality state",
            {"state": state, "allowed": list(QUALITY_STATES)},
        )


def is_terminal_lifecycle(status: str) -> bool:
    """DEPRECATED is the only terminal lifecycle state (spec §6)."""
    return status == "DEPRECATED"
