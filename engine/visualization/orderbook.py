"""M14 — the orderbook heatmap + liquidity (spec §19/§15).

§19: "Raw orderbook data remains immutable. Derived heatmap state is
versioned." The §19 operations (add, remove) fold into NEW snapshots
(D-093) - a property test asserts the input snapshots are untouched.

The §15 liquidity concepts (wall, zone, concentration, persistence,
migration, addition/removal, sweep, void, absorption) are pure functions of
the snapshots + declared thresholds. No arbitrary score /100 (§3).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from engine.errors import ValidationError


@dataclass(frozen=True)
class OrderbookSnapshot:
    """One price x displayed-liquidity snapshot (spec §19).

    Immutable (frozen): the §19 ops never mutate a snapshot (D-093).
    """

    ts: str
    levels: tuple[tuple[float, float], ...]  # (price, size), sorted by price
    dataset_version: str = ""
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "levels": [[price, size] for price, size in self.levels],
            "dataset_version": self.dataset_version,
            "source": self.source,
        }


def _sorted_levels(levels: Sequence[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    """Deterministic level order (D-040): by price ascending."""
    return tuple(sorted(levels, key=lambda level: level[0]))


def apply_ops(
    snapshot: OrderbookSnapshot,
    ops: Sequence[Mapping[str, Any]],
) -> OrderbookSnapshot:
    """The §19 operations (add, remove) as an immutable fold (D-093).

    Each op returns a NEW snapshot; ``snapshot`` is never mutated. An op
    removing a level absent from the snapshot is refused (fail-closed).
    """
    current: dict[float, float] = dict(snapshot.levels)
    for op in ops:
        kind = op.get("op")
        price = float(op["price"])
        if kind == "add":
            current[price] = current.get(price, 0.0) + float(op.get("size", 0.0))
        elif kind == "remove":
            if price not in current:
                raise ValidationError(
                    "cannot remove a level absent from the snapshot",
                    {"price": price, "ts": snapshot.ts},
                )
            remaining = current[price] - float(op.get("size", 0.0))
            if remaining > 0:
                current[price] = remaining
            else:
                del current[price]
        else:
            raise ValidationError(
                "unknown orderbook op",
                {"op": kind, "allowed": ["add", "remove"]},
            )
    return OrderbookSnapshot(
        ts=snapshot.ts,
        levels=_sorted_levels(tuple(current.items())),
        dataset_version=snapshot.dataset_version,
        source=snapshot.source,
    )


def liquidity_walls(
    snapshot: OrderbookSnapshot,
    *,
    threshold: float,
) -> tuple[float, ...]:
    """§15 liquidity-wall detection: a price level whose displayed size
    exceeds ``threshold`` times the window's median (a declared threshold, never
    an inferred score)."""
    if threshold <= 0:
        raise ValidationError("threshold must be positive", {"threshold": threshold})
    sizes = [size for _, size in snapshot.levels]
    if not sizes:
        return ()
    ordered = sorted(sizes)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    cutoff = median * threshold
    return tuple(price for price, size in snapshot.levels if size >= cutoff)


def migration(
    before: OrderbookSnapshot,
    after: OrderbookSnapshot,
) -> tuple[tuple[float, float, float], ...]:
    """§19 migration: (price, before_size, after_size) for levels whose size
    changed between the two snapshots (pure; neither input is mutated)."""
    before_map = dict(before.levels)
    after_map = dict(after.levels)
    out: list[tuple[float, float, float]] = []
    for price in sorted(set(before_map) | set(after_map)):
        b = before_map.get(price, 0.0)
        a = after_map.get(price, 0.0)
        if b != a:
            out.append((price, b, a))
    return tuple(out)


def sweep(
    snapshot: OrderbookSnapshot,
    *,
    price: float,
) -> tuple[tuple[float, float], ...]:
    """§19 sweep: the levels consumed when price trades through ``price``
    (the levels at or beyond it, deterministically ordered)."""
    return tuple((p, size) for p, size in snapshot.levels if p >= price)


def void(
    before: OrderbookSnapshot,
    after: OrderbookSnapshot,
) -> tuple[float, ...]:
    """§19 void: the price levels present in ``before`` and absent in
    ``after``."""
    after_prices = {p for p, _ in after.levels}
    return tuple(p for p, _ in before.levels if p not in after_prices)


@dataclass(frozen=True)
class LiquidityZone:
    """The §15 derived-object envelope: timestamp, price range, size,
    persistence, source, dataset_version, provenance."""

    price_low: float
    price_high: float
    size: float
    persistence: float  # the fraction of the window the zone was present
    source: str
    dataset_version: str
    provenance: str = "CALCULATED"

    def as_dict(self) -> dict[str, Any]:
        return {
            "price_low": self.price_low,
            "price_high": self.price_high,
            "size": self.size,
            "persistence": self.persistence,
            "source": self.source,
            "dataset_version": self.dataset_version,
            "provenance": self.provenance,
        }


def detect_zones(
    snapshots: Sequence[OrderbookSnapshot],
    *,
    min_persistence: float = 0.5,
) -> tuple[LiquidityZone, ...]:
    """§15 zone detection: a price level present in at least
    ``min_persistence`` of the window (a declared threshold; no arbitrary
    score /100)."""
    if not snapshots:
        return ()
    if not 0.0 < min_persistence <= 1.0:
        raise ValidationError(
            "min_persistence must lie in (0, 1]", {"min_persistence": min_persistence}
        )
    counts: dict[float, int] = {}
    sizes: dict[float, float] = {}
    for snapshot in snapshots:
        for price, size in snapshot.levels:
            counts[price] = counts.get(price, 0) + 1
            sizes[price] = max(sizes.get(price, 0.0), size)
    n = len(snapshots)
    return tuple(
        LiquidityZone(
            price_low=price,
            price_high=price,
            size=sizes[price],
            persistence=counts[price] / n,
            source=snapshots[0].source,
            dataset_version=snapshots[0].dataset_version,
        )
        for price in sorted(counts)
        if counts[price] / n >= min_persistence
    )


def render_orderbook_heatmap(
    snapshots: Sequence[OrderbookSnapshot],
) -> dict[str, Any]:
    """The §19 price x time x liquidity heatmap (the M13 heatmap contract)."""
    if not snapshots:
        raise ValidationError("an orderbook heatmap needs at least one snapshot", {})
    cells = [
        {"ts": snapshot.ts, "price": price, "value": size}
        for snapshot in snapshots
        for price, size in snapshot.levels
    ]
    return {"kind": "HEATMAP", "mode": "continuous", "cells": cells}
