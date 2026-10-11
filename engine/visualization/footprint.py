"""M14 — the footprint (spec §18).

Footprint cells expose price, buy_volume, sell_volume, delta, total_volume,
trade_count and imbalance. The §18 modes are bid/ask, delta, volume and
imbalance.

§18: "Do not estimate trade side when reliable exchange-side data exists."
The seed carries no taker volumes, so this module REFUSES to fabricate side
(D-092) - the same rule as the microstructure core.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from engine.errors import ValidationError
from engine.visualization.microstructure import TradeBucket, _require_taker_side

#: The §18 footprint modes.
FOOTPRINT_MODES: tuple[str, ...] = ("bid_ask", "delta", "volume", "imbalance")


@dataclass(frozen=True)
class FootprintCell:
    """One footprint cell (spec §18): the exposed fields + provenance."""

    price: float
    buy_volume: float
    sell_volume: float
    trade_count: int

    @property
    def delta(self) -> float:
        return self.buy_volume - self.sell_volume

    @property
    def total_volume(self) -> float:
        return self.buy_volume + self.sell_volume

    @property
    def imbalance(self) -> float | None:
        """``(buy - sell) / (buy + sell)``; a zero denominator is None,
        never 0.0 (no fabricated value)."""
        total = self.total_volume
        if total == 0:
            return None
        return self.delta / total

    def as_dict(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "buy_volume": self.buy_volume,
            "sell_volume": self.sell_volume,
            "delta": self.delta,
            "total_volume": self.total_volume,
            "trade_count": self.trade_count,
            "imbalance": self.imbalance,
            "provenance": "OBSERVED",
        }


def build_footprint(buckets: Sequence[TradeBucket]) -> tuple[FootprintCell, ...]:
    """The per-price-cell aggregation from taker-side rows (spec §18).

    Refuses without exchange-side taker data (D-092): side is never
    estimated from candle direction.
    """
    _require_taker_side(buckets)
    by_price: dict[float, FootprintCell] = {}
    for bucket in buckets:
        existing = by_price.get(bucket.price)
        buy = float(bucket.taker_buy_volume)  # type: ignore[arg-type]
        sell = float(bucket.taker_sell_volume)  # type: ignore[arg-type]
        if existing is None:
            by_price[bucket.price] = FootprintCell(
                price=bucket.price,
                buy_volume=buy,
                sell_volume=sell,
                trade_count=bucket.trades,
            )
        else:
            by_price[bucket.price] = FootprintCell(
                price=bucket.price,
                buy_volume=existing.buy_volume + buy,
                sell_volume=existing.sell_volume + sell,
                trade_count=existing.trade_count + bucket.trades,
            )
    return tuple(by_price[p] for p in sorted(by_price))


def render_footprint(
    cells: Sequence[FootprintCell],
    *,
    mode: str = "bid_ask",
) -> dict[str, Any]:
    """The §18 mode-specific render graph (deterministic)."""
    if mode not in FOOTPRINT_MODES:
        raise ValidationError(
            "unknown footprint mode",
            {"mode": mode, "allowed": list(FOOTPRINT_MODES)},
        )
    if not cells:
        raise ValidationError("a footprint render needs at least one cell", {})
    rendered = []
    for cell in cells:
        entry = cell.as_dict()
        if mode == "delta":
            entry["value"] = entry["delta"]
        elif mode == "volume":
            entry["value"] = entry["total_volume"]
        elif mode == "imbalance":
            entry["value"] = entry["imbalance"]
        else:  # bid_ask
            entry["value"] = {"buy": entry["buy_volume"], "sell": entry["sell_volume"]}
        rendered.append(entry)
    return {"kind": "FOOTPRINT", "mode": mode, "cells": rendered}
