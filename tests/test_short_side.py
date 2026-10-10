"""M10 — the SHORT-side gate tests (spec T10.3, design note m10_perp_live
section 4, D-071).

SHORT_ONLY/BOTH becomes runnable at M10, but only on perp instruments;
LONG_ONLY stays spot-capable; the gate fails closed (PD-10).
"""

from __future__ import annotations

import pytest

from engine.data.instruments import MarketType
from engine.errors import ValidationError
from engine.strategy.short_side import (
    assert_short_side_markets,
    is_short_side,
)


def test_short_sides_are_recognised() -> None:
    assert is_short_side("SHORT_ONLY")
    assert is_short_side("BOTH")
    assert is_short_side("both")


def test_long_only_is_not_a_short_side() -> None:
    assert not is_short_side("LONG_ONLY")


def test_a_short_strategy_passes_on_perp() -> None:
    assert_short_side_markets(
        side="SHORT_ONLY",
        market_types=[MarketType.PERPETUAL],
    )


def test_a_short_strategy_fails_on_spot() -> None:
    """D-071: SHORT on perp only; a spot instrument is rejected."""
    with pytest.raises(ValidationError):
        assert_short_side_markets(
            side="SHORT_ONLY",
            market_types=[MarketType.SPOT],
            symbols=["BTCUSDT"],
        )


def test_a_both_side_strategy_fails_on_spot() -> None:
    with pytest.raises(ValidationError):
        assert_short_side_markets(
            side="BOTH",
            market_types=[MarketType.SPOT],
            symbols=["ETHUSDT"],
        )


def test_a_mixed_universe_rejects_the_spot_instruments_by_name() -> None:
    """The error names the offending spot instruments (fail-closed, PD-10)."""
    with pytest.raises(ValidationError) as excinfo:
        assert_short_side_markets(
            side="SHORT_ONLY",
            market_types=[MarketType.PERPETUAL, MarketType.SPOT],
            symbols=["BTCUSDT-PERP", "SOLUSDT"],
        )
    detail = excinfo.value.detail
    assert detail["spot_instruments"] == ["SOLUSDT"]
    assert detail["side"] == "SHORT_ONLY"


def test_a_long_only_strategy_passes_on_spot() -> None:
    """LONG_ONLY stays spot-capable (the pre-M10 behavior for longs)."""
    assert_short_side_markets(
        side="LONG_ONLY",
        market_types=[MarketType.SPOT],
    )


def test_an_empty_universe_passes() -> None:
    """No instruments, no market-type violation (the empty universe is the
    caller's PD-12 error, not this gate's)."""
    assert_short_side_markets(
        side="SHORT_ONLY",
        market_types=[],
    )
