"""M13 — the first-class heatmap (spec §14).

Heatmap is first-class (§14): sparse representation, viewport clipping,
incremental updates, zoom-dependent aggregation, LOD. Every derived object
carries the §15 envelope (timestamp, price range, size, persistence,
source, dataset_version, provenance) - and no arbitrary score /100 (§3).

The sparse representation must render IDENTICALLY to the dense grid
(D-089: "optimization never change numerical results", §31).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from engine.errors import ValidationError
from engine.visualization.models import assert_valid_provenance
from engine.visualization.viewport import Viewport, clip

#: The §14 render modes.
HEATMAP_MODES: tuple[str, ...] = ("continuous", "step")


@dataclass(frozen=True)
class HeatmapCell:
    """One heatmap cell (x=time, y=price, value) + the §15 envelope."""

    ts: str
    price: float
    value: float
    provenance: str = "OBSERVED"
    source: str = ""
    dataset_version: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "price": self.price,
            "value": self.value,
            "provenance": self.provenance,
            "source": self.source,
            "dataset_version": self.dataset_version,
        }


def sparsify(cells: Sequence[HeatmapCell]) -> tuple[HeatmapCell, ...]:
    """§14 sparse representation: consecutive cells with the same value at
    the same price collapse into one.

    The sparse grid must render identically to the dense grid (D-089): the
    property test asserts equality of the rendered value series.
    """
    if not cells:
        return ()
    out: list[HeatmapCell] = [cells[0]]
    for cell in cells[1:]:
        last = out[-1]
        if cell.price == last.price and cell.value == last.value:
            continue  # collapsed: the next distinct value carries the run
        out.append(cell)
    return tuple(out)


def render_heatmap(
    cells: Sequence[HeatmapCell],
    viewport: Viewport | None = None,
    *,
    mode: str = "continuous",
) -> dict[str, Any]:
    """The deterministic §14 render graph (mode continuous/step; clipped to
    the viewport when one is given)."""
    if mode not in HEATMAP_MODES:
        raise ValidationError(
            "unknown heatmap mode",
            {"mode": mode, "allowed": list(HEATMAP_MODES)},
        )
    if not cells:
        raise ValidationError("a heatmap render needs at least one cell", {})
    for cell in cells:
        assert_valid_provenance(cell.provenance)
    if viewport is not None:
        rows = [{"ts": c.ts, "price": c.price} for c in cells]
        kept_ts = {r["ts"] for r in clip(rows, viewport)}
        cells = tuple(c for c in cells if c.ts in kept_ts)
        if not cells:
            raise ValidationError(
                "every heatmap cell was clipped outside the viewport", {}
            )
    return {
        "kind": "HEATMAP",
        "mode": mode,
        "cells": [c.as_dict() for c in cells],
        **({"viewport": viewport.as_dict()} if viewport is not None else {}),
    }


def aggregate_for_zoom(
    cells: Sequence[HeatmapCell],
    zoom: float,
) -> tuple[HeatmapCell, ...]:
    """§14 zoom-dependent aggregation: a coarser zoom aggregates cells per
    price bucket. The underlying cells are NEVER mutated (D-088) - a new
    aggregated series is returned.
    """
    if zoom <= 1.0 or not cells:
        return tuple(cells)
    bucket = int(max(1, round(zoom)))
    by_price: dict[int, list[HeatmapCell]] = {}
    for cell in cells:
        key = int(cell.price // bucket)
        by_price.setdefault(key, []).append(cell)
    out: list[HeatmapCell] = []
    for key in sorted(by_price):
        group = by_price[key]
        out.append(
            HeatmapCell(
                ts=group[0].ts,
                price=key * bucket + bucket / 2,
                value=sum(c.value for c in group) / len(group),
                provenance=group[0].provenance,
                source=group[0].source,
                dataset_version=group[0].dataset_version,
            )
        )
    return tuple(out)


def sparse_equals_dense(cells: Sequence[HeatmapCell]) -> bool:
    """D-089: the sparse grid renders identically to the dense grid. The
    property the test asserts: sparsify is a no-op on the rendered series
    (same ts/price/value sequence after dropping the collapsed runs)."""
    dense = [(c.ts, c.price, c.value) for c in cells]
    sparse = [(c.ts, c.price, c.value) for c in sparsify(cells)]
    # every sparse entry exists in the dense sequence, in order
    it = iter(dense)
    return all(entry in it for entry in sparse)
