"""M4 (T4.3) - outcomes (spec section 6.4, design section 5).

Pure function over (events, closed bars). Horizons default to the spec
list [1, 2, 3, 5, 10, 15, 20, 30, 40, 50] (PD-11: no other defaults).
``fwd_ret_*`` always uses the full horizon - no stop truncation - which is
what the Pine study computes and what the h=10 AC compares against.
``hit_invalidation`` and the excursion metrics apply the spec's same-bar
ambiguity rule: stop first (design section 5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from engine.events.base import CALCULATED, Event

#: Spec 6.4 horizon list (frozen; no other defaults).
DEFAULT_HORIZONS: tuple[int, ...] = (1, 2, 3, 5, 10, 15, 20, 30, 40, 50)

_OUTCOME_PROVENANCE: dict[str, str] = {
    "fwd_ret_pct": CALCULATED,
    "fwd_ret_r": CALCULATED,
    "mfe_r": CALCULATED,
    "mae_r": CALCULATED,
    "hit_invalidation": CALCULATED,
    "complete": CALCULATED,
}


@dataclass(frozen=True)
class HorizonOutcome:
    """One horizon's outcome for one event (spec 6.4)."""

    horizon: int
    complete: bool
    fwd_ret_pct: float | None = None
    fwd_ret_r: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    bars_to_mfe: int | None = None
    bars_to_mae: int | None = None
    hit_invalidation: bool | None = None


@dataclass(frozen=True)
class Outcome:
    """All requested horizons for one event (spec 6.4)."""

    event_id: str
    event_index: int
    direction: str
    ref_price: float
    invalidation_price: float
    r_basis: str
    r_value: float
    horizons: Mapping[int, HorizonOutcome]
    provenance: Mapping[str, str] = field(default_factory=lambda: dict(_OUTCOME_PROVENANCE))


def _column(frame: Mapping[str, Sequence[float]] | Any, name: str) -> list[float]:
    """Extract a float column from a dict-of-lists or a polars DataFrame."""
    if isinstance(frame, Mapping):
        return [float(v) for v in frame[name]]
    return [float(v) for v in frame[name].to_list()]


def compute_outcomes(
    events: Sequence[Event],
    frame: Mapping[str, Sequence[float]] | Any,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> list[Outcome]:
    """Compute section 6.4 outcomes for each event.

    Incomplete horizons (``t + h > last closed bar``) carry ``complete=false``
    and null values - never partially counted. ``ref_price == 0`` leaves
    ``fwd_ret_pct`` null (division undefined) without fabricating inf/nan.
    """
    if not events:
        return []
    closes = _column(frame, "close")
    highs = _column(frame, "high")
    lows = _column(frame, "low")
    last = len(closes) - 1

    results: list[Outcome] = []
    for event in sorted(events, key=lambda e: e.event_index):
        if event.event_index < 0:
            continue
        t = event.event_index
        ref = event.ref_price
        inv = event.invalidation_price
        r_value = abs(ref - inv)
        is_long = event.direction == "LONG"

        horizon_map: dict[int, HorizonOutcome] = {}
        for h in sorted(set(horizons)):
            if t + h > last:
                horizon_map[h] = HorizonOutcome(horizon=h, complete=False)
                continue

            # Full-horizon direction-adjusted return (no stop truncation).
            close_h = closes[t + h]
            move = (close_h - ref) if is_long else (ref - close_h)
            fwd_pct: float | None = (move / ref * 100.0) if ref != 0.0 else None
            fwd_r: float | None = (move / r_value) if r_value != 0.0 else None

            # Stop first: first bar in (t, t+h] touching the invalidation.
            stop_bar: int | None = None
            for k in range(1, h + 1):
                idx = t + k
                touched = lows[idx] <= inv if is_long else highs[idx] >= inv
                if touched:
                    stop_bar = idx
                    break

            # Excursion window: [t+1, min(t+h, stop_bar)] inclusive.
            end = stop_bar if stop_bar is not None else t + h
            window = range(t + 1, end + 1)
            window_highs = [highs[i] for i in window]
            window_lows = [lows[i] for i in window]

            if is_long:
                max_h = max(window_highs)
                min_l = min(window_lows)
                mfe = (max_h - ref) / r_value if r_value != 0.0 else None
                mae = (min_l - ref) / r_value if r_value != 0.0 else None
                # 1-based offset of the first bar printing the extreme.
                bars_mfe = window_highs.index(max_h) + 1
                bars_mae = window_lows.index(min_l) + 1
            else:
                min_l = min(window_lows)
                max_h = max(window_highs)
                mfe = (ref - min_l) / r_value if r_value != 0.0 else None
                mae = (ref - max_h) / r_value if r_value != 0.0 else None
                bars_mfe = window_lows.index(min_l) + 1
                bars_mae = window_highs.index(max_h) + 1

            horizon_map[h] = HorizonOutcome(
                horizon=h,
                complete=True,
                fwd_ret_pct=fwd_pct,
                fwd_ret_r=fwd_r,
                mfe_r=mfe,
                mae_r=mae,
                bars_to_mfe=bars_mfe,
                bars_to_mae=bars_mae,
                hit_invalidation=stop_bar is not None,
            )

        results.append(
            Outcome(
                event_id=event.event_id,
                event_index=t,
                direction=event.direction,
                ref_price=ref,
                invalidation_price=inv,
                r_basis="atr",
                r_value=r_value,
                horizons=horizon_map,
            )
        )
    return results
