"""M6 T6.1 — sizing tests (spec 7.2 verbatim formulas)."""

from __future__ import annotations

import pytest

from engine.strategy.models import Sizing, Stop
from engine.strategy.sizing import quantity, risk_distance, size_at_signal, stop_price


def _stop(kind: str, **kw: object) -> Stop:
    return Stop.model_validate({"kind": kind, **kw})


def test_atr_risk_distance() -> None:
    """7.2: ATR -> atr_mult*ATR_s."""
    dist = risk_distance(_stop("ATR", atr_mult=3.0), "LONG",
                         close_s=100.0, atr_s=2.0, structure_level=None, tick_size=0.01)
    assert dist == pytest.approx(6.0)


def test_structure_risk_distance_long_and_short() -> None:
    """7.2: STRUCTURE -> long close-(min(low_s,low_{s-1})-buf*ATR);
    short (max(high_s,high_{s-1})+buf*ATR)-close."""
    stop = _stop("STRUCTURE", structure_buffer_atr=0.25)
    long_dist = risk_distance(stop, "LONG", close_s=100.0, atr_s=2.0,
                              structure_level=96.0, tick_size=0.01)
    short_dist = risk_distance(stop, "SHORT", close_s=100.0, atr_s=2.0,
                               structure_level=104.0, tick_size=0.01)
    assert long_dist == pytest.approx(4.0)
    assert short_dist == pytest.approx(4.0)


def test_risk_distance_clamped_to_tick() -> None:
    """7.2: risk_dist = max(risk_dist, tick_size)."""
    dist = risk_distance(_stop("ATR", atr_mult=0.001), "LONG",
                         close_s=100.0, atr_s=1.0, structure_level=None, tick_size=0.05)
    assert dist == pytest.approx(0.05)


def test_none_stop_uses_atr_for_sizing_only() -> None:
    """7.2: NONE -> atr_mult*ATR for sizing only."""
    dist = risk_distance(_stop("NONE", atr_mult=2.0), "LONG",
                         close_s=100.0, atr_s=3.0, structure_level=None, tick_size=0.01)
    assert dist == pytest.approx(6.0)


def test_stop_price_atr_and_none() -> None:
    """7.2: ATR long close-risk_dist / short close+risk_dist; NONE null."""
    atr_stop = _stop("ATR", atr_mult=3.0)
    long_price = stop_price(atr_stop, "LONG", close_s=100.0, risk_dist=6.0,
                            structure_level=None)
    short_price = stop_price(atr_stop, "SHORT", close_s=100.0, risk_dist=6.0,
                             structure_level=None)
    assert long_price == pytest.approx(94.0)
    assert short_price == pytest.approx(106.0)
    assert stop_price(_stop("NONE"), "LONG", close_s=100.0, risk_dist=5.0,
                      structure_level=None) is None


def test_stop_price_structure_is_the_level() -> None:
    price = stop_price(_stop("STRUCTURE"), "LONG", close_s=100.0, risk_dist=4.0,
                       structure_level=96.0)
    assert price == pytest.approx(96.0)


def test_risk_quantity_takes_the_min_leg() -> None:
    """7.2: RISK = min(equity*risk_pct/100/risk_dist, equity*max_alloc/100/entry)."""
    sizing = Sizing.model_validate({"mode": "RISK", "risk_pct": 1.0, "max_alloc_pct": 25.0})
    # equity 10000, risk 1% = 100; risk_dist 5 -> risk leg 20;
    # alloc leg = 2500/100 = 25 -> min = 20.
    qty = quantity(sizing, "LONG", equity=10000.0, risk_dist=5.0,
                   entry_price=100.0, lot_step=0.001)
    assert qty == pytest.approx(20.0)
    # alloc leg binds when risk_pct is large.
    sizing2 = Sizing.model_validate({"mode": "RISK", "risk_pct": 50.0, "max_alloc_pct": 10.0})
    qty2 = quantity(sizing2, "LONG", equity=10000.0, risk_dist=5.0,
                    entry_price=100.0, lot_step=0.001)
    assert qty2 == pytest.approx(10.0)


def test_fixed_quantity() -> None:
    """7.2: fixed = equity*fixed_alloc_pct/100/entry."""
    sizing = Sizing.model_validate({"mode": "FIXED", "fixed_alloc_pct": 10.0})
    qty = quantity(sizing, "LONG", equity=10000.0, risk_dist=5.0,
                   entry_price=100.0, lot_step=0.001)
    assert qty == pytest.approx(10.0)


def test_quantity_rounds_down_to_lot_step() -> None:
    """7.2: round DOWN to lot_step; qty<=0 -> skip."""
    sizing = Sizing.model_validate({"mode": "FIXED", "fixed_alloc_pct": 10.0})
    qty = quantity(sizing, "LONG", equity=10000.0, risk_dist=5.0,
                   entry_price=333.33, lot_step=0.01)
    assert qty < 10000.0 * 0.10 / 333.33
    assert (qty / 0.01) == int(qty / 0.01)
    # allocation too small for one lot -> zero -> caller skips.
    tiny = quantity(sizing, "LONG", equity=10.0, risk_dist=5.0,
                    entry_price=100.0, lot_step=1.0)
    assert tiny == 0.0


def test_size_at_signal_full_flow() -> None:
    stop = _stop("ATR", atr_mult=3.0)
    sizing = Sizing.model_validate({"mode": "RISK", "risk_pct": 1.0, "max_alloc_pct": 25.0})
    sized = size_at_signal(stop, sizing, "LONG", close_s=100.0, atr_s=2.0,
                           structure_level=0.0, tick_size=0.01, lot_step=0.001,
                           equity=10000.0)
    assert sized.risk_dist == pytest.approx(6.0)
    assert sized.stop_price == pytest.approx(94.0)
    assert sized.qty == pytest.approx(16.666)  # 100/6 = 16.6666 -> 16.666 lots of 0.001


def test_size_at_signal_structure_level_missing_raises() -> None:
    """STRUCTURE without a level raises — the caller must supply it (7.2)."""
    with pytest.raises(ValueError, match="STRUCTURE"):
        # structure_level is required for STRUCTURE stops; pass it via the
        # public helper which validates kind-specific inputs.
        from engine.strategy.sizing import risk_distance
        risk_distance(_stop("STRUCTURE"), "LONG", close_s=100.0, atr_s=2.0,
                      structure_level=None, tick_size=0.01)
