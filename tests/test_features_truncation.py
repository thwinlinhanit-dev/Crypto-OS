"""M3 - truncation-invariance property suite (spec section 14.1, M3 AC).

Two properties, both over every registered feature plus the section 5 library
callables that the registry cannot express:

1. ``result on data cut at t == restriction to <= t of result on full data``
   - the spec's TRUNCATION INVARIANCE rule, asserted with exact equality (no
   tolerance): every implementation walks the series forward element-wise, so
   a prefix computation performs identical operations. A difference here is a
   bug (PD-2), not rounding.
2. editing bars *after* ``t`` never changes any output at or before ``t`` -
   the same causality in the direction that matters for live updates.

The strategy only draws frames that satisfy OHLC sanity
(``low <= min(open, close) <= max(open, close) <= high``), monotonic time and
non-negative volume, and deliberately includes degenerate bars: zero range
(cpos 0.5, upper_wick 0.0), zero volume (rvol null) and repeated closes (flat
trend_state / ADX 0/0 paths).
"""

from __future__ import annotations

from typing import Any

import polars as pl
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.features import compute_indicator, known_indicators
from engine.features.primitives import barssince
from engine.features.regime import trend_state, vol_state, volume_state

DAY = 86_400_000
BASE = 1_704_067_200_000

# Section 5 states no default for most windows: the suite supplies them here
# (PD-11 - nothing may fall back to an invented value inside the test either).
# test_params_cover_every_registered_indicator keeps this table honest.
PARAMS: dict[str, dict[str, Any]] = {
    "sma": {"length": 5},
    "ema": {"length": 4},
    "atr": {"length": 3},
    "rsi": {"length": 3},
    "bb": {},
    "rvol": {"length": 3},
    "cpos": {},
    "upper_wick": {},
    "vwap": {},
    "rma": {"length": 3},
    "tr": {},
    "highest": {"length": 3},
    "lowest": {"length": 3},
    "donchian_lo": {"length": 3},
    "donchian_hi": {"length": 3},
    "pivotlow": {"left": 2, "right": 1},
    "pivothigh": {"left": 2, "right": 1},
    "macd": {"fast": 2, "slow": 4, "signal": 2},
    "adx": {"length": 3},
}

Outputs = dict[str, list[Any]]


@st.composite
def frame_and_cut(draw: st.DrawFn) -> tuple[pl.DataFrame, int]:
    """A valid OHLCV frame plus a cut index in ``1..size-1``."""
    size = draw(st.integers(min_value=5, max_value=60))
    cut = draw(st.integers(min_value=1, max_value=size - 1))
    closes = draw(st.lists(st.integers(1, 5_000), min_size=size, max_size=size))
    half_ranges = draw(st.lists(st.integers(0, 30), min_size=size, max_size=size))
    volumes = draw(st.lists(st.integers(0, 500), min_size=size, max_size=size))

    open_ = [float(closes[0])]
    open_.extend(float(value) for value in closes[:-1])
    high = [max(o, c) + h / 10.0 for o, c, h in zip(open_, closes, half_ranges, strict=True)]
    low = [min(o, c) - h / 10.0 for o, c, h in zip(open_, closes, half_ranges, strict=True)]
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


def _outputs(frame: pl.DataFrame) -> Outputs:
    """Every registered feature plus the library-only section 5 callables."""
    outputs: Outputs = {}
    for name, params in PARAMS.items():
        result = compute_indicator(frame, name=name, params=params)
        for row in result["series"]:
            outputs[f"{name}.{row['key']}"] = row["values"]
    close = [float(value) for value in frame["close"].to_list()]
    first = close[0] if close else 0.0
    outputs["barssince.close_gt_first"] = barssince([value > first for value in close])
    outputs["regime.trend_state"] = trend_state(
        frame, params={"length": 5, "slope_lag": 3}
    )
    outputs["regime.vol_state"] = vol_state(frame, params={"atr_length": 3, "window": 6})
    outputs["regime.volume_state"] = volume_state(frame, params={"length": 4})
    return outputs


def _with_future_rewritten(frame: pl.DataFrame, cut: int) -> pl.DataFrame:
    position = pl.int_range(0, pl.len())
    shift = pl.when(position >= cut)
    prices = [
        shift.then(pl.col(name) + 1_000.0).otherwise(pl.col(name)).alias(name)
        for name in ("open", "high", "low", "close")
    ]
    volume = shift.then(pl.col("volume") * 3.0 + 1.0).otherwise(pl.col("volume"))
    return frame.with_columns(*prices, volume.alias("volume"))


def test_params_cover_every_registered_indicator() -> None:
    assert set(PARAMS) == set(known_indicators())


@settings(max_examples=80, deadline=None)
@given(frame_and_cut())
def test_truncating_the_input_never_changes_the_reachable_output(
    case: tuple[pl.DataFrame, int],
) -> None:
    frame, cut = case
    full = _outputs(frame)
    truncated = _outputs(frame.slice(0, cut))
    for key, values in full.items():
        assert truncated[key] == values[:cut], key


@settings(max_examples=80, deadline=None)
@given(frame_and_cut())
def test_editing_future_bars_never_changes_any_past_output(
    case: tuple[pl.DataFrame, int],
) -> None:
    frame, cut = case
    before = _outputs(frame)
    after = _outputs(_with_future_rewritten(frame, cut))
    # Rows >= cut are the rewritten ones: only the outputs at or before the cut
    # must survive the edit unchanged.
    for key, values in before.items():
        assert after[key][:cut] == values[:cut], key
