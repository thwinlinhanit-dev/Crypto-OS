"""Feature library (spec section 5).

The numeric registry behind ``GET /v1/indicators`` (``compute_indicator`` and
friends), the regime tags (``trend_state``/``vol_state``/``volume_state``),
and the primitives every detector and the parity tool share. Design note:
specs/design/m3_features.md.
"""

from __future__ import annotations

from engine.features.indicators import (
    IndicatorSpec,
    compute_indicator,
    known_indicators,
    resolve_indicator,
)
from engine.features.regime import trend_state, vol_state, volume_state

__all__ = [
    "IndicatorSpec",
    "compute_indicator",
    "known_indicators",
    "resolve_indicator",
    "trend_state",
    "vol_state",
    "volume_state",
]
