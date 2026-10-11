"""M13 — the Market Canvas layer manager (spec §12).

The §12 layer vocabulary is closed (D-087): PRICE, MICROSTRUCTURE,
LIQUIDITY, DERIVATIVES, OPTIONS, ON_CHAIN, EVENTS, AI. A layer outside it
is refused before the stack is built. Every layer carries its provenance
(§22) and its data binding; an undeclared binding is a fail-closed refusal.

Deterministic ordering (D-040's lesson): layers render in z-order, ties
broken by insertion order — the stack is a pure function of its inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from engine.errors import ValidationError
from engine.visualization.models import (
    assert_valid_provenance,
    assert_valid_render_kind,
)

#: The §12 layer vocabulary (closed; D-087).
LAYERS: Final[tuple[str, ...]] = (
    "PRICE",
    "MICROSTRUCTURE",
    "LIQUIDITY",
    "DERIVATIVES",
    "OPTIONS",
    "ON_CHAIN",
    "EVENTS",
    "AI",
)

#: The §12 progressive-disclosure default: the PRICE layer only. "Do not
#: render every dataset at once."
DEFAULT_VISIBLE: Final[tuple[str, ...]] = ("PRICE",)


def assert_valid_layer(name: str) -> None:
    if name not in LAYERS:
        raise ValidationError(
            "unknown canvas layer",
            {"layer": name, "allowed": list(LAYERS)},
        )


@dataclass(frozen=True)
class Layer:
    """One canvas layer (§12/§13): a primitive bound to a dataset."""

    layer: str
    kind: str
    z: int
    dataset: str
    provenance: str
    visible: bool = False
    label: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "kind": self.kind,
            "z": self.z,
            "dataset": self.dataset,
            "provenance": self.provenance,
            "visible": self.visible,
            "label": self.label,
        }


@dataclass(frozen=True)
class LayerStack:
    """The ordered layer set (§12). Deterministic: z-order, then insertion
    order (D-040)."""

    layers: tuple[Layer, ...] = ()

    def validate(self) -> None:
        """Fail-closed (§12/§22): unknown layer, unknown primitive, unknown
        provenance, an undeclared dataset, or a duplicate (layer, dataset)
        binding are all refused."""
        if not self.layers:
            raise ValidationError("a canvas needs at least one layer", {})
        seen: set[tuple[str, str]] = set()
        for item in self.layers:
            assert_valid_layer(item.layer)
            assert_valid_render_kind(item.kind)
            assert_valid_provenance(item.provenance)
            if not item.dataset:
                raise ValidationError(
                    "a layer must declare its dataset binding (§12)",
                    {"layer": item.layer},
                )
            key = (item.layer, item.dataset)
            if key in seen:
                raise ValidationError(
                    "duplicate layer/dataset binding",
                    {"layer": item.layer, "dataset": item.dataset},
                )
            seen.add(key)

    def ordered(self) -> tuple[Layer, ...]:
        """The render order: z ascending, ties by insertion order."""
        return tuple(
            sorted(self.layers, key=lambda item: item.z)
        )

    def visible_layers(self) -> tuple[Layer, ...]:
        """§12 progressive disclosure: only the visible layers render."""
        return tuple(item for item in self.ordered() if item.visible)

    def as_dict(self) -> dict[str, Any]:
        return {"layers": [item.as_dict() for item in self.ordered()]}


def default_stack() -> LayerStack:
    """The §12 progressive-disclosure default: the PRICE layer only."""
    return LayerStack(
        layers=(
            Layer(
                layer="PRICE",
                kind="LINE",
                z=0,
                dataset="candles",
                provenance="OBSERVED",
                visible=True,
                label="Price",
            ),
        )
    )


def add_layer(stack: LayerStack, layer: Layer) -> LayerStack:
    """Append a layer (a new stack; the existing one is immutable)."""
    candidate = LayerStack(layers=(*stack.layers, layer))
    candidate.validate()
    return candidate


def remove_layer(stack: LayerStack, *, layer: str, dataset: str) -> LayerStack:
    """Remove a layer by its (layer, dataset) binding."""
    remaining = tuple(
        item
        for item in stack.layers
        if not (item.layer == layer and item.dataset == dataset)
    )
    if len(remaining) == len(stack.layers):
        raise ValidationError(
            "layer not found",
            {"layer": layer, "dataset": dataset},
        )
    candidate = LayerStack(layers=remaining)
    candidate.validate()
    return candidate


def set_visibility(stack: LayerStack, *, layer: str, dataset: str, visible: bool) -> LayerStack:
    """Toggle a layer's visibility (§12 progressive disclosure)."""
    updated: list[Layer] = []
    found = False
    for item in stack.layers:
        if item.layer == layer and item.dataset == dataset:
            found = True
            updated.append(
                Layer(
                    layer=item.layer,
                    kind=item.kind,
                    z=item.z,
                    dataset=item.dataset,
                    provenance=item.provenance,
                    visible=visible,
                    label=item.label,
                )
            )
        else:
            updated.append(item)
    if not found:
        raise ValidationError(
            "layer not found",
            {"layer": layer, "dataset": dataset},
        )
    return LayerStack(layers=tuple(updated))
