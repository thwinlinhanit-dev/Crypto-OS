"""M13 — the shared viewport, crosshair and clipping (spec §12/§14).

Every layer shares the time axis, price axis (where relevant), viewport,
crosshair, replay clock and instrument/timeframe context (§12). Viewport
clipping and zoom aggregation change only the RENDER graph - never the
stored rows (§14/§31; D-088, asserted by property tests).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from engine.errors import ValidationError


@dataclass(frozen=True)
class Viewport:
    """The shared viewport (§12): the time + price window every layer reads."""

    time_start: str
    time_end: str
    price_low: float | None = None
    price_high: float | None = None
    zoom: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "time_start": self.time_start,
            "time_end": self.time_end,
            "price_low": self.price_low,
            "price_high": self.price_high,
            "zoom": self.zoom,
        }


@dataclass(frozen=True)
class Crosshair:
    """The synchronized crosshair state (§12): a time + price point."""

    time: str
    price: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"time": self.time, "price": self.price}


def clip(
    rows: Sequence[Mapping[str, Any]],
    viewport: Viewport,
    *,
    time_key: str = "ts",
    price_key: str = "close",
) -> tuple[Mapping[str, Any], ...]:
    """§14 viewport clipping: rows outside the viewport are dropped from the
    RENDER only. The stored rows are never mutated (D-088) - the caller
    passes its rows and receives a clipped view.
    """
    if viewport.time_start > viewport.time_end:
        raise ValidationError(
            "viewport time range is inverted",
            {"time_start": viewport.time_start, "time_end": viewport.time_end},
        )
    kept = []
    for row in rows:
        ts = str(row.get(time_key, ""))
        if not (viewport.time_start <= ts <= viewport.time_end):
            continue
        if viewport.price_low is not None and viewport.price_high is not None:
            price = row.get(price_key)
            if price is None or not (viewport.price_low <= float(price) <= viewport.price_high):
                continue
        kept.append(row)
    return tuple(kept)


def layers_at(
    crosshair: Crosshair,
    layers: Sequence[Mapping[str, Any]],
    *,
    time_key: str = "ts",
) -> tuple[str, ...]:
    """§12: the layers under the crosshair, resolved deterministically.

    A layer is "under" the crosshair when it has a row at the crosshair's
    time. The result is sorted by z (the render order) so the UI resolves
    the same set every time.
    """
    under = [
        str(item.get("layer", ""))
        for item in layers
        if item.get(time_key) == crosshair.time
    ]
    zs = {
        str(item.get("layer", "")): int(item.get("z", 0))
        for item in layers
    }
    return tuple(sorted(set(under), key=lambda name: zs.get(name, 0)))


def zoom_aggregate(
    rows: Sequence[Mapping[str, Any]],
    viewport: Viewport,
    *,
    time_key: str = "ts",
    price_key: str = "close",
) -> tuple[Mapping[str, Any], ...]:
    """§14 zoom-dependent aggregation + LOD.

    At zoom <= 1 the rows render as-is; at zoom > 1 the viewport aggregates
    rows into coarser buckets for rendering. The aggregation changes ONLY
    the render graph - the stored rows are never mutated (D-088), and the
    property test asserts the bucketed mean equals the direct computation
    over the same window.
    """
    if viewport.zoom <= 1.0:
        return clip(rows, viewport, time_key=time_key, price_key=price_key)
    window = clip(rows, viewport, time_key=time_key, price_key=price_key)
    bucket_size = int(max(1, round(viewport.zoom)))
    buckets: list[Mapping[str, Any]] = []
    for start in range(0, len(window), bucket_size):
        chunk = window[start : start + bucket_size]
        prices = [float(row.get(price_key, 0.0)) for row in chunk]
        first = chunk[0]
        buckets.append(
            {
                time_key: str(first.get(time_key, "")),
                price_key: sum(prices) / len(prices),
                "n_aggregated": len(chunk),
            }
        )
    return tuple(buckets)


def render_viewport(viewport: Viewport) -> dict[str, Any]:
    """The viewport block every render graph carries (§12: the shared
    context)."""
    return {"viewport": viewport.as_dict()}
