"""M15 — the options interface (spec §21, the future layer).

§21 is explicitly the future layer:

    live options chain -> contract normalization -> pricing/Greeks ->
    spot-grid evaluation -> dealer exposure model -> GEX / gamma flip /
    max pain / skew / vega / delta

"Model assumptions must be visible and versioned."

Per §38.10, M15 ships the INTERFACE CONTRACT only: the versioned model
shape and the pricing hook points. The chain feed + the real pricing are
M16+. The absence of the feed is an explicit NOT_IMPLEMENTED, never a
fabricated GEX (D-100).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from engine.errors import ValidationError


@dataclass(frozen=True)
class OptionsModelSpec:
    """The versioned options model interface (spec §21).

    "Model assumptions must be visible and versioned." An empty spec is
    refused; the pricing model is named but not implemented in M15.
    """

    version: str
    pricing_model: str
    assumptions: Mapping[str, Any]

    def validate(self) -> None:
        if not self.version or not self.version.strip():
            raise ValidationError("the options model must declare a version", {})
        if not self.pricing_model or not self.pricing_model.strip():
            raise ValidationError("the options model must name its pricing model", {})
        if not self.assumptions:
            raise ValidationError(
                "the options model must declare its assumptions (§21)", {}
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "pricing_model": self.pricing_model,
            "assumptions": dict(self.assumptions),
        }


def normalize_chain(rows: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    """The contract-normalization hook (spec §21).

    The chain feed does not exist in M15: a non-empty input is refused as
    NOT_IMPLEMENTED (D-100 - never a fabricated GEX), and an empty input
    returns empty (nothing to normalize).
    """
    if rows:
        raise ValidationError(
            "the live options chain feed is not implemented in M15 (§21/§38.10)",
            {"n_rows": len(rows)},
        )
    return ()


def render_gamma_curve(spec: OptionsModelSpec, *, spot: float) -> dict[str, Any]:
    """The deterministic gamma-curve render graph skeleton (§21).

    The curve is EMPTY in M15 (the feed is absent); the graph carries the
    model spec and the named-but-not-computed outputs (gamma flip, max pain,
    skew, vega, delta) so the shape is explicit.
    """
    spec.validate()
    if spot <= 0:
        raise ValidationError("spot must be positive", {"spot": spot})
    return {
        "kind": "GAMMA_CURVE",
        "spot": spot,
        "model": spec.as_dict(),
        "points": [],
        "outputs": ["gamma_flip", "max_pain", "skew", "vega", "delta"],
        "computed": [],
    }
