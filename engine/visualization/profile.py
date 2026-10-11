"""M13 — the volume/market profile (spec §13 Profile primitive).

The deterministic binning over a price series. The bin width (or count) is
a DECLARED parameter, never inferred (PD-11); every bin carries its
provenance, and no arbitrary score /100 (§3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from engine.errors import ValidationError
from engine.visualization.models import assert_valid_provenance


@dataclass(frozen=True)
class ProfileBin:
    """One price bin + its accumulated value (§13 Profile)."""

    price_low: float
    price_high: float
    value: float
    provenance: str = "CALCULATED"

    def as_dict(self) -> dict[str, Any]:
        return {
            "price_low": self.price_low,
            "price_high": self.price_high,
            "value": self.value,
            "provenance": self.provenance,
        }


def build_profile(
    prices: Sequence[float],
    values: Sequence[float],
    *,
    bin_width: float | None = None,
    bin_count: int | None = None,
) -> tuple[ProfileBin, ...]:
    """The deterministic profile binning (spec §13).

    Exactly one of ``bin_width``/``bin_count`` must be declared (PD-11: no
    inferred default). Bins are contiguous, half-open ``[low, high)`` with
    the last bin closed; the value series accumulates into the price bins.
    """
    if len(prices) != len(values):
        raise ValidationError(
            "prices and values must align observation-wise",
            {"n_prices": len(prices), "n_values": len(values)},
        )
    if not prices:
        return ()
    if (bin_width is None) == (bin_count is None):
        raise ValidationError(
            "declare exactly one of bin_width or bin_count (PD-11)",
            {"bin_width": bin_width, "bin_count": bin_count},
        )
    if bin_width is not None and (type(bin_width) not in (int, float) or bin_width <= 0):
        raise ValidationError("bin_width must be a positive number", {"bin_width": bin_width})
    if bin_count is not None and (type(bin_count) is not int or bin_count < 1):
        raise ValidationError("bin_count must be a positive integer", {"bin_count": bin_count})

    lo = min(prices)
    hi = max(prices)
    if bin_count is not None:
        width = (hi - lo) / bin_count if hi > lo else 1.0
    else:
        width = float(bin_width)  # type: ignore[arg-type]
    if width <= 0:
        width = 1.0

    n_bins = int((hi - lo) // width) + 1
    totals = [0.0] * n_bins
    for price, value in zip(prices, values, strict=True):
        index = int((price - lo) // width)
        if index >= n_bins:
            index = n_bins - 1  # the max price lands in the last bin
        totals[index] += float(value)

    return tuple(
        ProfileBin(
            price_low=lo + i * width,
            price_high=lo + (i + 1) * width,
            value=totals[i],
        )
        for i in range(n_bins)
        if totals[i] != 0.0
    )


def render_profile(bins: Sequence[ProfileBin]) -> dict[str, Any]:
    """The deterministic §13 Profile render graph (histogram bars)."""
    if not bins:
        raise ValidationError("a profile render needs at least one bin", {})
    for item in bins:
        assert_valid_provenance(item.provenance)
    return {
        "kind": "PROFILE",
        "bars": [b.as_dict() for b in bins],
        "point_of_control": max(bins, key=lambda b: b.value).price_low,
    }
