"""M10 — SHORT-side enablement on perp only (spec T10.3, design note
m10_perp_live section 4, D-071).

Before M10 the short strategy side was disabled outright (R11, D-043).
At M10 a SHORT_ONLY or BOTH strategy becomes runnable — but **only on
perp instruments** (R2/7.7): the market type of every universe instrument
must be PERPETUAL, else the strategy is rejected (fail-closed, PD-10).

A pure function of the instrument market types + the strategy side, so
the API and the job runner share one gate.
"""

from __future__ import annotations

from collections.abc import Sequence

from engine.data.instruments import MarketType
from engine.errors import ValidationError

#: The sides that need the perp-only gate (LONG_ONLY stays spot-capable).
SHORT_SIDES: frozenset[str] = frozenset({"SHORT_ONLY", "BOTH"})


def is_short_side(side: str) -> bool:
    """True when the strategy's entry side needs perp-only treatment."""
    return side.strip().upper() in SHORT_SIDES


def assert_short_side_markets(
    *,
    side: str,
    market_types: Sequence[MarketType],
    symbols: Sequence[str] | None = None,
) -> None:
    """Raise when a SHORT_ONLY/BOTH strategy targets a spot instrument
    (D-071: SHORT on perp only). A LONG_ONLY strategy passes regardless."""
    if not is_short_side(side):
        return
    spot = [
        symbols[index] if symbols is not None else market_types[index].value
        for index, market_type in enumerate(market_types)
        if market_type is not MarketType.PERPETUAL
    ]
    if spot:
        raise ValidationError(
            "short-side strategies run on perp only (R11/7.7)",
            {"side": side, "spot_instruments": spot},
        )
