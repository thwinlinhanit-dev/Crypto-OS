"""M6 (T6.1) — sizing and initial stop (spec 7.2, verbatim formulas).

Computed at the signal bar s. Pure functions over the bar tuple: the caller
supplies `atr_s`, the structure level (for STRUCTURE stops) and `tick_size`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from engine.strategy.models import Sizing, Stop

Side = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class Sized:
    """The 7.2 outputs a fill needs: risk distance, stop price, quantity."""

    risk_dist: float  # >= tick_size (7.2 clamp)
    stop_price: float | None  # NONE -> None
    qty: float  # rounded DOWN to lot_step; <= 0 -> caller skips
    entry_ref_price: float  # close_s used for sizing


def risk_distance(
    stop: Stop,
    side: Side,
    *,
    close_s: float,
    atr_s: float | None,
    structure_level: float | None,
    tick_size: float,
) -> float:
    """7.2: ATR -> atr_mult*ATR_s; STRUCTURE -> close - structure (long) /
    structure - close (short); NONE -> atr_mult*ATR (sizing only).
    Clamped to at least tick_size.
    """
    if stop.kind.value == "ATR":
        if atr_s is None:
            msg = "ATR stop requires atr_s"
            raise ValueError(msg)
        dist = stop.atr_mult * atr_s
    elif stop.kind.value == "STRUCTURE":
        if structure_level is None:
            msg = "STRUCTURE stop requires the structure level"
            raise ValueError(msg)
        dist = (close_s - structure_level) if side == "LONG" else (structure_level - close_s)
    else:  # NONE
        if atr_s is None:
            msg = "NONE stop sizing requires atr_s"
            raise ValueError(msg)
        dist = stop.atr_mult * atr_s
    return max(dist, tick_size)


def stop_price(
    stop: Stop,
    side: Side,
    *,
    close_s: float,
    risk_dist: float,
    structure_level: float | None,
) -> float | None:
    """7.2: ATR long close_s-risk_dist / short close_s+risk_dist;
    STRUCTURE the structure level; NONE null.
    """
    if stop.kind.value == "ATR":
        return close_s - risk_dist if side == "LONG" else close_s + risk_dist
    if stop.kind.value == "STRUCTURE":
        return structure_level
    return None


def quantity(
    sizing: Sizing,
    side: Side,
    *,
    equity: float,
    risk_dist: float,
    entry_price: float,
    lot_step: float,
) -> float:
    """7.2: RISK = min(equity*risk_pct/100/risk_dist, equity*max_alloc_pct/100/entry);
    FIXED = equity*fixed_alloc_pct/100/entry. Round DOWN to lot_step.
    """
    if sizing.mode == "RISK":
        risk_leg = equity * (sizing.risk_pct / 100.0) / risk_dist
        alloc_leg = equity * (sizing.max_alloc_pct / 100.0) / entry_price
        raw = min(risk_leg, alloc_leg)
    else:
        raw = equity * (sizing.fixed_alloc_pct / 100.0) / entry_price
    if lot_step > 0:
        return int(raw / lot_step) * lot_step
    return raw


def size_at_signal(
    strategy_stop: Stop,
    sizing: Sizing,
    side: Side,
    *,
    close_s: float,
    atr_s: float | None,
    structure_level: float,
    tick_size: float,
    lot_step: float,
    equity: float,
) -> Sized:
    """The full 7.2 computation for one signal bar."""
    dist = risk_distance(
        strategy_stop, side, close_s=close_s, atr_s=atr_s,
        structure_level=structure_level, tick_size=tick_size,
    )
    stop = stop_price(strategy_stop, side, close_s=close_s, risk_dist=dist,
                      structure_level=structure_level)
    qty = quantity(sizing, side, equity=equity, risk_dist=dist,
                   entry_price=close_s, lot_step=lot_step)
    return Sized(risk_dist=dist, stop_price=stop, qty=qty, entry_ref_price=close_s)
