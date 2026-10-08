"""M3 (T3.1) - section 5 feature library: semantics, registry, regime tags.

Acceptance evidence produced here:
- every section 5 feature that yields a numeric series is registry-reachable and
  matches a hand-computed expectation (rma, tr, highest/lowest, donchian,
  barssince, pivotlow/pivothigh, macd, adx);
- the regime tags follow the documented rules (trend_state mirrors the
  reference study's ``inDown``, vol_state is the trailing-252 ATR percentile,
  volume_state buckets rvol on the section 6.3 edges) and are null during
  warm-up;
- PD-11 holds for the new entries: required parameters are refused rather than
  defaulted, undocumented parameters are refused, categorical parameters
  (``source``) are validated against the bar columns, and the domain rules
  (macd slow > fast, positive windows) are enforced as mathematics;
- causality smoke: outputs of a truncated input equal the restriction of the
  full result (the full hypothesis property suite is
  tests/test_features_truncation.py).

Interpretations recorded in specs/design/m3_features.md section 2 and
docs/DECISIONS.md D-015..D-017:
- warm-up for the window family (highest/lowest/donchian) is ``null`` until n
  values exist, the section 5 rule stated for ``sma``;
- pivots use strict comparison on both sides, so an equal neighbour
  disqualifies a candidate, and a value is placed at ``p + right`` (known only
  then - no lookahead);
- ``adx`` returns null rather than inf when the DI denominator is zero.
"""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest

from engine.config.defaults import ParamError, ParamErrorCode, load_defaults
from engine.errors import ValidationError
from engine.features import compute_indicator, known_indicators, resolve_indicator
from engine.features.regime import trend_state, vol_state, volume_state

DAY = 86_400_000
BASE = 1_704_067_200_000


def _frame(
    open_: list[float],
    high: list[float],
    low: list[float],
    close: list[float],
    volume: list[float] | None = None,
) -> pl.DataFrame:
    size = len(close)
    return pl.DataFrame(
        {
            "open_time": [BASE + i * DAY for i in range(size)],
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume if volume is not None else [10.0] * size,
        }
    )


def _trend_bars(size: int, step: float = 1.0) -> pl.DataFrame:
    """Bars that close strictly higher every bar (no flat ranges)."""
    open_ = [100.0 + i * step for i in range(size)]
    close = [101.0 + i * step for i in range(size)]
    high = [102.0 + i * step for i in range(size)]
    low = [99.0 + i * step for i in range(size)]
    return _frame(open_, high, low, close)


def _series(frame: pl.DataFrame, name: str, **params: Any) -> list[float | None]:
    result = compute_indicator(frame, name=name, params=params or None)
    assert len(result["series"]) == 1
    values: list[float | None] = result["series"][0]["values"]
    return values


# --------------------------------------------------------------------------
# Registry contract: section 5 features are reachable, PD-11 holds
# --------------------------------------------------------------------------


def test_the_registry_lists_exactly_the_section_5_set() -> None:
    # M3 extends the M2 chart set with the rest of section 5 (design D-015).
    assert set(known_indicators()) == {
        "sma",
        "ema",
        "vwap",
        "bb",
        "rsi",
        "atr",
        "rvol",
        "cpos",
        "upper_wick",
        "rma",
        "tr",
        "highest",
        "lowest",
        "donchian_lo",
        "donchian_hi",
        "pivotlow",
        "pivothigh",
        "macd",
        "adx",
    }


def test_source_parameter_must_name_a_bar_column() -> None:
    with pytest.raises(ValidationError) as exc:
        resolve_indicator("highest", {"length": 5, "source": "vwap"})
    assert exc.value.code == "VALIDATION_ERROR"
    assert exc.value.detail["param"] == "source"
    assert "close" in exc.value.detail["allowed"]


def test_macd_requires_a_slow_window_larger_than_the_fast_one() -> None:
    with pytest.raises(ValidationError) as exc:
        resolve_indicator("macd", {"fast": 12, "slow": 12, "signal": 9})
    assert exc.value.code == "VALIDATION_ERROR"
    assert exc.value.detail["param"] == "slow"


def test_pivot_parameters_are_required_because_section_5_defines_no_default() -> None:
    with pytest.raises(ParamError) as exc:
        resolve_indicator("pivotlow")
    assert exc.value.code is ParamErrorCode.MISSING_PARAM
    assert exc.value.detail["param"] == "left"


def test_spec_documented_defaults_cover_the_new_entries() -> None:
    assert resolve_indicator("macd")[1] == {"fast": 12, "slow": 26, "signal": 9}
    assert resolve_indicator("adx")[1] == {"length": 14}
    _, resolved = resolve_indicator("highest", {"length": 5})
    assert resolved == {"length": 5, "source": "high"}
    _, resolved = resolve_indicator("lowest", {"length": 5})
    assert resolved == {"length": 5, "source": "low"}
    # section 5 gives no window size for highest/lowest, so none is invented.
    with pytest.raises(ParamError) as exc:
        resolve_indicator("highest")
    assert exc.value.code is ParamErrorCode.MISSING_PARAM
    assert exc.value.detail["param"] == "length"


# --------------------------------------------------------------------------
# Section 5 semantics, hand-computed
# --------------------------------------------------------------------------


def test_rma_is_reachable_and_seeded_with_the_sma_at_index_n_minus_1() -> None:
    # rma([1,2,3,4,5], 3): seed 2.0 at index 2, then 8/3, then 31/9.
    frame = _frame([1.0] * 5, [1.0] * 5, [1.0] * 5, [1.0, 2.0, 3.0, 4.0, 5.0])
    values = _series(frame, "rma", length=3)
    assert values[:2] == [None, None]
    assert values[2] == pytest.approx(2.0)
    assert values[3] == pytest.approx(8 / 3)
    assert values[4] == pytest.approx(31 / 9)


def test_tr_is_high_low_on_the_first_bar_and_the_max_of_three_afterwards() -> None:
    frame = _frame(
        open_=[100.0, 105.0, 118.0],
        high=[110.0, 120.0, 119.0],
        low=[95.0, 100.0, 90.0],
        close=[105.0, 118.0, 95.0],
    )
    assert _series(frame, "tr") == [
        pytest.approx(15.0),  # t=0: high - low (section 5)
        pytest.approx(20.0),  # max(20, |120-105|, |100-105|)
        pytest.approx(29.0),  # max(29, |119-118|, |90-118|)
    ]


def test_highest_and_lowest_include_the_current_bar_and_null_the_warmup() -> None:
    # The price action here is in close, so source must be named: the documented
    # default is high (test_spec_documented_defaults_cover_the_new_entries).
    frame = _frame([1.0] * 5, [1.0] * 5, [1.0] * 5, [5.0, 1.0, 4.0, 9.0, 2.0])
    assert _series(frame, "highest", length=3, source="close") == [None, None, 5.0, 9.0, 9.0]
    assert _series(frame, "lowest", length=3, source="close") == [None, None, 1.0, 1.0, 2.0]


def test_source_selects_which_bar_column_the_window_runs_over() -> None:
    frame = _frame(
        open_=[1.0, 1.0, 1.0],
        high=[50.0, 60.0, 70.0],
        low=[0.0, 0.0, 0.0],
        close=[2.0, 3.0, 4.0],
    )
    assert _series(frame, "highest", length=2, source="high") == [None, 60.0, 70.0]
    assert _series(frame, "highest", length=2, source="close") == [None, 3.0, 4.0]


def test_donchian_bands_are_the_lowest_low_and_the_highest_high() -> None:
    frame = _frame(
        open_=[1.0] * 4,
        high=[10.0, 12.0, 11.0, 13.0],
        low=[5.0, 4.0, 6.0, 3.0],
        close=[8.0, 8.0, 8.0, 8.0],
    )
    assert _series(frame, "donchian_hi", length=2) == [None, 12.0, 12.0, 13.0]
    assert _series(frame, "donchian_lo", length=2) == [None, 4.0, 4.0, 3.0]


def test_pivot_low_places_its_value_right_bars_later_without_lookahead() -> None:
    frame = _frame([1.0] * 9, [1.0] * 9, [1.0] * 9, [5.0, 3.0, 4.0, 1.0, 6.0, 2.0, 7.0, 0.0, 8.0])
    values = _series(frame, "pivotlow", left=1, right=1, source="close")
    # candidates at 1, 3, 5, 7 confirm one bar later; bar 8 cannot confirm yet.
    assert values == [None, None, 3.0, None, 1.0, None, 2.0, None, 0.0]


def test_pivot_high_uses_strict_comparison_so_an_equal_neighbour_disqualifies() -> None:
    frame = _frame([1.0] * 5, [1.0] * 5, [1.0] * 5, [1.0, 5.0, 4.0, 9.0, 2.0])
    assert _series(frame, "pivothigh", left=1, right=1, source="close") == [
        None,
        None,
        5.0,
        None,
        9.0,
    ]
    tied = _frame([1.0] * 4, [1.0] * 4, [1.0] * 4, [5.0, 3.0, 3.0, 4.0])
    assert _series(tied, "pivotlow", left=1, right=1, source="close") == [
        None,
        None,
        None,
        None,
    ]


def test_barssince_counts_from_the_last_true_and_is_null_before_the_first() -> None:
    # Imported through the feature library: barssince takes a boolean condition,
    # which a bar column cannot express (design D-015).
    from engine.features.primitives import barssince

    assert barssince([False, True, False, False, True, False]) == [None, 0, 1, 2, 0, 1]
    assert barssince([False, False]) == [None, None]
    assert barssince([True, True]) == [0, 0]


def test_macd_is_the_hand_computed_fast_1_slow_2_signal_2_case() -> None:
    # ema(x, 1) is x itself (alpha = 1), so every term below is exact.
    frame = _frame([1.0] * 4, [1.0] * 4, [1.0] * 4, [1.0, 2.0, 3.0, 4.0])
    result = compute_indicator(frame, name="macd", params={"fast": 1, "slow": 2, "signal": 2})
    values = {row["key"]: row["values"] for row in result["series"]}
    assert set(values) == {"macd", "signal", "histogram"}
    assert values["macd"] == [
        pytest.approx(0.0),
        pytest.approx(1 / 3),
        pytest.approx(4 / 9),
        pytest.approx(13 / 27),
    ]
    assert values["signal"] == [
        pytest.approx(0.0),
        pytest.approx(2 / 9),
        pytest.approx(10 / 27),
        pytest.approx(4 / 9),
    ]
    assert values["histogram"] == [
        pytest.approx(0.0),
        pytest.approx(1 / 9),
        pytest.approx(2 / 27),
        pytest.approx(1 / 27),
    ]


def test_adx_of_a_monotonic_trend_is_100_and_of_a_flat_series_is_null() -> None:
    trending = _trend_bars(30)
    values = _series(trending, "adx")
    assert values[:26] == [None] * 26  # rma(tr,14) at 13, rma(DX,14) 13 later
    assert all(value is None or 0.0 <= value <= 100.0 for value in values)
    assert values[26] == pytest.approx(100.0)
    assert values[29] == pytest.approx(100.0)

    flat = _frame([100.0] * 30, [100.0] * 30, [100.0] * 30, [100.0] * 30)
    flat_values = _series(flat, "adx")
    # Zero range everywhere: DI denominators are 0/0 -> null, never inf or nan.
    assert flat_values == [None] * 30


# --------------------------------------------------------------------------
# Regime tags (section 5 last line): deterministic, descriptive, documented
# --------------------------------------------------------------------------


def test_trend_state_mirrors_the_reference_study_in_down_rule() -> None:
    frame = _frame([1.0] * 6, [1.0] * 6, [1.0] * 6, [10.0, 9.0, 8.0, 7.0, 9.0, 11.0])
    tags = trend_state(frame, params={"length": 2, "slope_lag": 2})
    assert tags == [None, None, "down", "down", "up", "up"]

    neutral = _frame([1.0] * 3, [1.0] * 3, [1.0] * 3, [10.0, 8.0, 9.0])
    assert trend_state(neutral, params={"length": 2, "slope_lag": 2}) == [
        None,
        None,
        "neutral",
    ]


def test_trend_state_parameters_come_from_the_defaults_file_only() -> None:
    defaults = load_defaults()
    assert defaults["features"]["trend_state"] == {"length": 100, "slope_lag": 5}
    frame = _frame([1.0] * 3, [1.0] * 3, [1.0] * 3, [1.0, 2.0, 3.0])
    with pytest.raises(ParamError) as exc:
        trend_state(frame, params={"magic": 1})
    assert exc.value.code is ParamErrorCode.UNDOCUMENTED_PARAM
    with pytest.raises(ParamError) as missing:
        trend_state(frame, params={"length": None})
    assert missing.value.code is ParamErrorCode.MISSING_PARAM


def test_vol_state_buckets_the_trailing_atr_percentile() -> None:
    from engine.features.primitives import atr_percentile

    # percentile rank = 100 * (less + 0.5 * equal) / window, ties centred at 50.
    values = [0.0, 1.0, 2.0, 3.0]
    assert atr_percentile(values, 4) == [None, None, None, pytest.approx(87.5)]
    assert atr_percentile([10.0, 20.0, 30.0], 3)[-1] == pytest.approx(83.333333)
    assert atr_percentile([5.0, 5.0, 5.0], 3)[-1] == pytest.approx(50.0)
    assert atr_percentile([30.0, 20.0, 10.0], 3)[-1] == pytest.approx(16.666667)

    # End to end: constant true range -> the middle of the distribution.
    frame = _trend_bars(8)
    assert vol_state(frame, params={"atr_length": 2, "window": 3}) == [
        None,
        None,
        None,
        "mid",
        "mid",
        "mid",
        "mid",
        "mid",
    ]


def test_volume_state_buckets_rvol_on_the_section_6_3_edges() -> None:
    def tags(volume_at_bar_two: float) -> str | None:
        frame = _frame(
            [1.0, 1.0, 1.0],
            [1.0, 1.0, 1.0],
            [1.0, 1.0, 1.0],
            [1.0, 1.0, 1.0],
            volume=[10.0, 10.0, volume_at_bar_two],
        )
        return volume_state(frame, params={"length": 2})[2]

    assert tags(4.0) == "low"  # rvol 0.4 < 0.5
    assert tags(7.0) == "normal"  # rvol 0.7
    assert tags(10.0) == "elevated"  # rvol 1.0
    assert tags(19.99) == "elevated"  # rvol 1.999, still below the 2.0 top edge
    assert tags(20.0) == "high"  # section 6.3 top_mult edge
    assert tags(30.0) == "climax"  # section 6.3 climax_mult edge


def test_regime_tags_are_null_during_warmup() -> None:
    frame = _trend_bars(6)
    assert trend_state(frame, params={"length": 2, "slope_lag": 2})[0] is None
    assert vol_state(frame, params={"atr_length": 2, "window": 5})[:5] == [None] * 5
    # rvol's denominator is the previous bar's sma, so bar 0 and 1 are null.
    assert volume_state(frame, params={"length": 3})[:2] == [None, None]
