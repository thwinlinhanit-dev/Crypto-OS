"""M4 (T4.5) - truncation-invariance properties for detect + outcomes.

Design section 7 item 5: the same two properties the M3 feature suite
asserts, now over the event pipeline.

1. ``detect(prefix) == restrict(detect(full), prefix)``: an event fires on
   a bar using only data at or before that bar (PD-2), so cutting the input
   at t can never create, destroy or move an event at or before t.
2. ``compute_outcomes`` on a prefix equals the restriction of the full-data
   outcomes for complete horizons; horizons that would need bars past the
   cut are incomplete on the prefix and absent from the full restriction.

The generator mirrors ``tests/test_features_truncation.py``: valid OHLCV
frames with degenerate bars (zero range, zero volume, repeated closes).
Lookback_bars is pinned to 5 so hypothesis frames that are too short
contribute no events but still exercise the outcomes path.
"""

from __future__ import annotations

from typing import Any

import polars as pl
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.events.outcomes import compute_outcomes
from engine.events.registry import detect_with

DAY = 86_400_000
BASE = 1_704_067_200_000

# Every value documented in config/defaults.yaml (PD-11: the test overrides,
# it never invents a threshold).
SMALL: dict[str, Any] = {
    "vol_len": 3,
    "climax_mult": 3.0,
    "top_mult": 2.0,
    "range_len": 3,
    "trend_len": 2,
    "close_pos_lo": 0.30,
    "close_pos_hi": 0.70,
    "regime_len": 4,
    "bounce_len": 3,
    "bounce_atr": 1.5,
    "wick_min": 0.40,
    "cooldown": 0,
    "atr_len": 3,
    "fail_mode": "failed_bounce",
    "lookback_bars": 5,
}


@st.composite
def frame_and_cut(draw: st.DrawFn) -> tuple[pl.DataFrame, int]:
    """A valid OHLCV frame plus a cut index in ``1..size-1``."""
    size = draw(st.integers(min_value=8, max_value=60))
    cut = draw(st.integers(min_value=1, max_value=size - 1))
    closes = draw(st.lists(st.integers(1, 5_000), min_size=size, max_size=size))
    half_ranges = draw(st.lists(st.integers(0, 30), min_size=size, max_size=size))
    volumes = draw(st.lists(st.integers(0, 500), min_size=size, max_size=size))

    open_ = [float(closes[0])]
    open_.extend(float(value) for value in closes[:-1])
    high = [
        max(o, c) + h / 10.0
        for o, c, h in zip(open_, closes, half_ranges, strict=True)
    ]
    low = [
        min(o, c) - h / 10.0
        for o, c, h in zip(open_, closes, half_ranges, strict=True)
    ]
    frame = pl.DataFrame(
        {
            "open_time": [BASE + i * DAY for i in range(size)],
            "open": open_,
            "high": high,
            "low": low,
            "close": [float(value) for value in closes],
            "volume": [float(value) for value in volumes],
        }
    )
    return frame, cut


def _detect(frame: pl.DataFrame) -> list[Any]:
    return detect_with("RVOL_CLIMAX", frame, params=SMALL)


def _event_key(event: Any) -> tuple[str, str, str]:
    """Identity fields that must survive truncation unchanged."""
    return (event.event_type, event.event_time.isoformat(), event.params_hash)


@settings(max_examples=60, deadline=None)
@given(frame_and_cut())
def test_detect_on_prefix_equals_restriction_of_full(case: tuple[pl.DataFrame, int]) -> None:
    frame, cut = case
    full = _detect(frame)
    prefix = _detect(frame.slice(0, cut))
    # Events at bars < cut must be identical on prefix and full.
    full_restricted = [_event_key(e) for e in full if e.event_index < cut]
    prefix_keys = [_event_key(e) for e in prefix]
    assert prefix_keys == full_restricted


@settings(max_examples=60, deadline=None)
@given(frame_and_cut())
def test_outcomes_on_prefix_matches_restriction(case: tuple[pl.DataFrame, int]) -> None:
    frame, cut = case
    full = _detect(frame)
    if not full:
        return
    full_outcomes = compute_outcomes(full, frame)
    prefix = _detect(frame.slice(0, cut))
    prefix_outcomes = compute_outcomes(prefix, frame.slice(0, cut)) if prefix else []

    for outcome in full_outcomes:
        if outcome.event_index >= cut:
            continue
        matching = [o for o in prefix_outcomes if o.event_id == outcome.event_id]
        if not matching:
            # The event fired on full data at index < cut but not on prefix:
            # that would be a PD-2 violation.
            raise AssertionError(
                f"event {outcome.event_id} at index {outcome.event_index} missing from prefix"
            )
        prefix_outcome = matching[0]
        for h, full_horizon in outcome.horizons.items():
            prefix_horizon = prefix_outcome.horizons[h]
            # On a prefix of size `cut`, last index is cut-1; a horizon h is
            # complete when t + h <= cut - 1, i.e. h <= cut - 1 - t.
            if h <= cut - 1 - outcome.event_index:
                # Horizon fully within the prefix: must match exactly.
                assert prefix_horizon.complete == full_horizon.complete
                if full_horizon.complete:
                    assert prefix_horizon.fwd_ret_pct == full_horizon.fwd_ret_pct
                    assert prefix_horizon.fwd_ret_r == full_horizon.fwd_ret_r
                    assert prefix_horizon.mfe_r == full_horizon.mfe_r
                    assert prefix_horizon.mae_r == full_horizon.mae_r
                    assert prefix_horizon.hit_invalidation == full_horizon.hit_invalidation
            else:
                # Horizon extends past the prefix: must be incomplete with nulls.
                assert not prefix_horizon.complete
                assert prefix_horizon.fwd_ret_pct is None
                assert prefix_horizon.fwd_ret_r is None
