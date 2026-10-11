"""M14 — the microstructure core: trades, CVD, delta (spec §17/§18).

The critical rule (§17): "Where actual taker-side data exists, use it rather
than estimating side from candle direction." The seeded parquet bars carry a
``trades`` count but no taker volumes, so this module REFUSES to fabricate
trade side (D-092): the delta-dependent primitives raise ``DATA_INCOMPLETE``
when ``taker_buy_volume``/``taker_sell_volume`` are absent - never
estimated, never silently zeroed (§3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from engine.errors import ValidationError


@dataclass(frozen=True)
class TradeBucket:
    """One bucket's exchange-side inputs (spec §17/§18).

    ``taker_buy_volume``/``taker_sell_volume`` are the ONLY source of trade
    side. A bucket without them cannot be decomposed (D-092).
    """

    ts: str
    price: float
    volume: float
    trades: int
    taker_buy_volume: float | None = None
    taker_sell_volume: float | None = None

    @property
    def has_taker_side(self) -> bool:
        return (
            self.taker_buy_volume is not None
            and self.taker_sell_volume is not None
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "price": self.price,
            "volume": self.volume,
            "trades": self.trades,
            "taker_buy_volume": self.taker_buy_volume,
            "taker_sell_volume": self.taker_sell_volume,
        }


def _require_taker_side(buckets: Sequence[TradeBucket]) -> None:
    """§17/§18: refuse to estimate trade side from candle direction. A
    bucket set without taker volumes is DATA_INCOMPLETE (D-092)."""
    missing = [b.ts for b in buckets if not b.has_taker_side]
    if missing:
        raise ValidationError(
            "taker-side volume is unavailable; trade side is not estimated "
            "from candle direction (spec §17/§18)",
            {"buckets_missing": missing},
        )


def delta(buckets: Sequence[TradeBucket]) -> tuple[tuple[str, float], ...]:
    """The §18 per-bucket delta: ``buy - sell`` (deterministic).

    Refuses without exchange-side taker data (D-092).
    """
    _require_taker_side(buckets)
    return tuple(
        (b.ts, float(b.taker_buy_volume) - float(b.taker_sell_volume))  # type: ignore[arg-type]
        for b in buckets
    )


def cvd(buckets: Sequence[TradeBucket]) -> tuple[tuple[str, float], ...]:
    """The §17 cumulative volume delta: ``sum(buy - sell)`` (deterministic,
    provenance CALCULATED - a transformation of OBSERVED inputs)."""
    deltas = delta(buckets)
    out: list[tuple[str, float]] = []
    running = 0.0
    for ts, value in deltas:
        running += value
        out.append((ts, running))
    return tuple(out)


def taker_decomposition(
    buckets: Sequence[TradeBucket],
) -> tuple[tuple[str, float, float], ...]:
    """The side split: (ts, buy, sell). With real exchange-side data the
    provenance is OBSERVED; without it the call is refused (D-092)."""
    _require_taker_side(buckets)
    return tuple(
        (b.ts, float(b.taker_buy_volume), float(b.taker_sell_volume))  # type: ignore[arg-type]
        for b in buckets
    )


def render_cvd(
    buckets: Sequence[TradeBucket],
    *,
    as_of: str | None = None,
) -> dict[str, Any]:
    """The deterministic CVD render graph (the §13 line primitive).

    ``as_of`` gates the buckets at the replay boundary (§23) before the
    graph is built.
    """
    if as_of is not None:
        buckets = tuple(b for b in buckets if b.ts <= as_of)
    if not buckets:
        raise ValidationError("a CVD render needs at least one bucket", {})
    series = cvd(buckets)
    return {
        "kind": "LINE",
        "label": "CVD",
        "provenance": "CALCULATED",
        "as_of": as_of,
        "points": [[ts, value] for ts, value in series],
    }


def render_delta(
    buckets: Sequence[TradeBucket],
    *,
    as_of: str | None = None,
) -> dict[str, Any]:
    """The deterministic per-bucket delta render graph."""
    if as_of is not None:
        buckets = tuple(b for b in buckets if b.ts <= as_of)
    if not buckets:
        raise ValidationError("a delta render needs at least one bucket", {})
    series = delta(buckets)
    return {
        "kind": "HISTOGRAM",
        "label": "Delta",
        "provenance": "CALCULATED",
        "as_of": as_of,
        "points": [[ts, value] for ts, value in series],
    }
