"""Feature library (spec section 5). M2 ships the chart indicator registry;
the rest of the library, parity tests and the truncation property suite land
with M3 (PD-9)."""

from __future__ import annotations

from engine.features.indicators import (
    IndicatorSpec,
    compute_indicator,
    known_indicators,
    resolve_indicator,
)

__all__ = ["IndicatorSpec", "compute_indicator", "known_indicators", "resolve_indicator"]
