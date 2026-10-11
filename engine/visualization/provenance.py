"""M12 — the provenance contract (spec §22).

Every output is traceable (program → computation node → dataset →
dataset_version → venue/source), and the four provenance classes -
OBSERVED, CALCULATED, INFERRED, AI - must remain distinguishable (§3).

There is deliberately no ``signal strength /100`` scoring API (§3): a
visual heuristic is not automatically a trading event.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from engine.errors import ValidationError
from engine.visualization.models import (
    PROVENANCE_CLASSES,
    assert_valid_provenance,
)


@dataclass(frozen=True)
class ProvenanceValue:
    """A value tagged with its provenance class (§22)."""

    value: Any
    provenance: str
    source: str
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "provenance": self.provenance,
            "source": self.source,
            "detail": self.detail,
        }


def classify(
    value: Any,
    *,
    provenance: str,
    source: str,
    detail: str | None = None,
) -> ProvenanceValue:
    """Tag a computed value with its provenance class.

    ``OBSERVED`` is reserved for data read directly from a dataset (the
    engine never invents one); ``CALCULATED`` for a deterministic
    transformation; ``INFERRED`` for a model-based estimate; ``AI`` for a
    model-generated value.
    """
    assert_valid_provenance(provenance)
    return ProvenanceValue(
        value=value, provenance=provenance, source=source, detail=detail
    )


def assert_distinguishable(values: Sequence[ProvenanceValue]) -> None:
    """§3: observed/calculated/inferred/AI values must remain
    distinguishable - a mixed list without provenance tags is refused, so a
    model value can never masquerade as an observation."""
    if not values:
        return
    for item in values:
        if not isinstance(item, ProvenanceValue):
            raise ValidationError(
                "every value must carry a provenance tag (§3)",
                {"value": repr(item)},
            )
        if item.provenance not in PROVENANCE_CLASSES:
            raise ValidationError(
                "unknown provenance class",
                {"provenance": item.provenance, "allowed": list(PROVENANCE_CLASSES)},
            )
