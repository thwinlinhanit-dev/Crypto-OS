"""M2 (T2.1) - chart indicators: section 5 semantics behind the registry contract.

Acceptance evidence produced here:
- every M2 indicator matches a hand-computed expectation from section 5
  (sma, ema, rma/atr, rsi, bb, rvol, cpos, upper_wick, vwap);
- warm-up values are ``null`` (never filled, PD-2/PD-10) except ``ema``, whose
  first output is ``x[0]`` exactly as section 5 specifies;
- causality: computing on a truncated input equals the restriction of the full
  result (truncation smoke - the full property suite belongs to M3);
- the registry rejects unknown names, undocumented parameters and missing
  documented defaults with the PD-11 422 vocabulary (config ``ParamError``),
  and validates window sizes as a mathematical domain (n >= 1 integer).

INTERPRETATIONS (parked for M3 parity, see docs/DECISIONS.md OQ-8):
- ``rsi`` first emits at index ``n``: the first ``n`` price changes live at
  indices ``1..n`` (there is no change at index 0), so the Wilder seed
  ``sma(change, n)`` lands there; ``avg_loss == 0`` with ``avg_gain > 0`` is
  ``100.0`` (rs -> inf), both zero (flat prices) is ``null`` (Pine ``0/0 -> na``).
- ``vwap`` anchors at each UTC-day boundary (``open_time // DAY``), per section
  5 "UTC-day anchor intraday"; on 1D bars that reduces to the bar's own hlc3.
"""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest

from engine.config.defaults import ParamError, ParamErrorCode, load_defaults
from engine.errors import ValidationError
from engine.features import compute_indicator, known_indicators, resolve_indicator

DAY = 86_400_000


def _bars(
    n: int,
    *,
    close_step: float = 1.0,
    volume: float = 10.0,
) -> pl.DataFrame:
    """n daily bars from a fixed epoch: deterministic, distinct closes."""
    return pl.DataFrame(
        {
            "open_time": [1_704_067_200_000 + i * DAY for i in range(n)],
            "open": [100.0 + i * close_step for i in range(n)],
            "high": [105.0 + i * close_step for i in range(n)],
            "low": [95.0 + i * close_step for i in range(n)],
            "close": [101.0 + i * close_step for i in range(n)],
            "volume": [volume for _ in range(n)],
        }
    )


def _values(frame: pl.DataFrame, name: str, **params: Any) -> list[float | None]:
    result = compute_indicator(frame, name=name, params=params or None)
    assert len(result["series"]) == 1
    values: list[float | None] = result["series"][0]["values"]
    return values


# --------------------------------------------------------------------------
# Registry contract (PD-11: request or config/defaults.yaml, nothing else)
# --------------------------------------------------------------------------


def test_the_registry_lists_exactly_the_m2_chart_set() -> None:
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
    }


def test_unknown_indicator_is_a_validation_error() -> None:
    with pytest.raises(ValidationError) as exc:
        resolve_indicator("macd")
    assert exc.value.code == "VALIDATION_ERROR"
    assert exc.value.detail["name"] == "macd"


def test_undocumented_parameter_is_rejected() -> None:
    with pytest.raises(ParamError) as exc:
        resolve_indicator("sma", {"length": 20, "magic": 1})
    assert exc.value.code is ParamErrorCode.UNDOCUMENTED_PARAM


def test_missing_required_length_is_rejected_rather_than_defaulted() -> None:
    # section 5 defines sma(x, n) with no default n - nothing may be invented.
    with pytest.raises(ParamError) as exc:
        resolve_indicator("sma")
    assert exc.value.code is ParamErrorCode.MISSING_PARAM
    assert exc.value.detail["param"] == "length"


def test_spec_documented_defaults_resolve_without_a_request_value() -> None:
    _, resolved = resolve_indicator("bb")
    assert resolved == {"length": 20, "mult": 2}  # section 5: Bollinger(20, 2)
    _, resolved = resolve_indicator("rvol")
    assert resolved == {"length": 20}  # section 5: rvol(n=20)


def test_window_size_domain_is_validated_as_a_mathematical_requirement() -> None:
    for bad in (0, -3, 2.5, "two", True):
        with pytest.raises(ValidationError) as exc:
            resolve_indicator("sma", {"length": bad})
        assert exc.value.code == "VALIDATION_ERROR"
        assert exc.value.detail["param"] == "length"


def test_resolved_params_are_returned_for_the_response_echo() -> None:
    _, resolved = resolve_indicator("sma", {"length": 7})
    assert resolved == {"length": 7}
    _, resolved = resolve_indicator("bb", {"length": 10})
    assert resolved == {"length": 10, "mult": 2}


def test_indicators_section_is_documented_in_defaults_yaml() -> None:
    defaults = load_defaults()
    assert "indicators" in defaults
    for name in known_indicators():
        assert name in defaults["indicators"], name


# --------------------------------------------------------------------------
# Section 5 semantics, hand-computed
# --------------------------------------------------------------------------


def test_sma_is_the_mean_of_the_last_n_values_with_null_warmup() -> None:
    frame = pl.DataFrame(
        {
            "open_time": [1_704_067_200_000 + i * DAY for i in range(5)],
            "open": [1.0] * 5,
            "high": [1.0] * 5,
            "low": [1.0] * 5,
            "close": [1.0, 2.0, 3.0, 4.0, 5.0],
            "volume": [1.0] * 5,
        }
    )
    assert _values(frame, "sma", length=2) == [
        None,
        pytest.approx(1.5),
        pytest.approx(2.5),
        pytest.approx(3.5),
        pytest.approx(4.5),
    ]


def test_ema_starts_at_x0_with_alpha_2_over_n_plus_1() -> None:
    # e1 = (2/3)*2 + (1/3)*1 = 5/3 ; e2 = 23/9 ; e3 = 95/27 ; e4 = 365/81
    frame = pl.DataFrame(
        {
            "open_time": [1_704_067_200_000 + i * DAY for i in range(5)],
            "open": [1.0] * 5,
            "high": [1.0] * 5,
            "low": [1.0] * 5,
            "close": [1.0, 2.0, 3.0, 4.0, 5.0],
            "volume": [1.0] * 5,
        }
    )
    values = _values(frame, "ema", length=2)
    assert values[0] == pytest.approx(1.0)  # first output = x[0] (section 5)
    assert values[1] == pytest.approx(5 / 3)
    assert values[2] == pytest.approx(23 / 9)
    assert values[3] == pytest.approx(95 / 27)
    assert values[4] == pytest.approx(365 / 81)


def test_rma_is_seeded_with_the_sma_at_index_n_minus_1() -> None:
    frame = pl.DataFrame(
        {
            "open_time": [1_704_067_200_000 + i * DAY for i in range(5)],
            "open": [1.0] * 5,
            "high": [1.0] * 5,
            "low": [1.0] * 5,
            "close": [1.0, 2.0, 3.0, 4.0, 5.0],
            "volume": [1.0] * 5,
        }
    )
    # rma is exposed through atr (rma of true range) below; seed position pinned
    # there and warm-up nulls asserted here via atr.
    assert _values(frame, "atr", length=2)[0] is None


def test_true_range_and_atr_follow_section_5() -> None:
    frame = pl.DataFrame(
        {
            "open_time": [1_704_067_200_000 + i * DAY for i in range(3)],
            "open": [10.0, 11.0, 13.0],
            "high": [12.0, 14.0, 13.5],
            "low": [9.0, 10.0, 12.0],
            "close": [11.0, 13.0, 12.5],
            "volume": [1.0, 1.0, 1.0],
        }
    )
    # tr: t0 = 12-9 = 3; t1 = max(4, |14-11|=3, |10-11|=1) = 4;
    #     t2 = max(1.5, |13.5-13|=0.5, |12-13|=1) = 1.5
    # atr = rma(tr, 2): null, (3+4)/2 = 3.5, 0.5*3.5 + 0.5*1.5 = 2.5
    values = _values(frame, "atr", length=2)
    assert values[0] is None
    assert values[1] == pytest.approx(3.5)
    assert values[2] == pytest.approx(2.5)


def test_rsi_is_wilder_through_rma_with_the_documented_edge_cases() -> None:
    frame = pl.DataFrame(
        {
            "open_time": [1_704_067_200_000 + i * DAY for i in range(5)],
            "open": [1.0, 2.0, 3.0, 2.0, 4.0],
            "high": [1.0, 2.0, 3.0, 2.0, 4.0],
            "low": [1.0, 2.0, 3.0, 2.0, 4.0],
            "close": [1.0, 2.0, 3.0, 2.0, 4.0],
            "volume": [1.0] * 5,
        }
    )
    # changes: +1, +1, -1, +2  -> gains (1,1,0,2), losses (0,0,1,0) from index 1
    # n=2 seed at index 2: avg_gain = 1, avg_loss = 0 -> 100.0 (rs -> inf)
    # index 3: 0.5/0.5 -> rs 1 -> 50.0 ; index 4: 1.25/0.25 -> rs 5 -> 83.33..
    values = _values(frame, "rsi", length=2)
    assert values[0] is None
    assert values[1] is None
    assert values[2] == pytest.approx(100.0)
    assert values[3] == pytest.approx(50.0)
    assert values[4] == pytest.approx(100.0 - 100.0 / 6.0)


def test_rsi_of_a_flat_series_is_null_not_a_number() -> None:
    frame = pl.DataFrame(
        {
            "open_time": [1_704_067_200_000 + i * DAY for i in range(4)],
            "open": [5.0] * 4,
            "high": [5.0] * 4,
            "low": [5.0] * 4,
            "close": [5.0] * 4,
            "volume": [1.0] * 4,
        }
    )
    assert _values(frame, "rsi", length=2) == [None, None, None, None]


def test_bollinger_uses_the_population_standard_deviation() -> None:
    frame = pl.DataFrame(
        {
            "open_time": [1_704_067_200_000 + i * DAY for i in range(4)],
            "open": [1.0] * 4,
            "high": [1.0] * 4,
            "low": [1.0] * 4,
            "close": [1.0, 2.0, 3.0, 4.0],
            "volume": [1.0] * 4,
        }
    )
    result = compute_indicator(frame, name="bb", params={"length": 4})
    series = {entry["key"]: entry["values"] for entry in result["series"]}
    assert set(series) == {"upper", "middle", "lower"}
    sd = (1.25) ** 0.5  # population std of [1,2,3,4]
    assert series["middle"][:3] == [None, None, None]
    assert series["middle"][3] == pytest.approx(2.5)
    assert series["upper"][3] == pytest.approx(2.5 + 2 * sd)
    assert series["lower"][3] == pytest.approx(2.5 - 2 * sd)


def test_rvol_divides_by_the_previous_bar_sma_and_nulls_zero_denominators() -> None:
    frame = pl.DataFrame(
        {
            "open_time": [1_704_067_200_000 + i * DAY for i in range(4)],
            "open": [1.0] * 4,
            "high": [1.0] * 4,
            "low": [1.0] * 4,
            "close": [1.0] * 4,
            "volume": [10.0, 20.0, 30.0, 40.0],
        }
    )
    values = _values(frame, "rvol", length=2)
    assert values[:2] == [None, None]
    assert values[2] == pytest.approx(30.0 / 15.0)
    assert values[3] == pytest.approx(40.0 / 25.0)

    flat = _bars(4, volume=0.0)
    assert all(value is None for value in _values(flat, "rvol", length=2))


def test_cpos_and_upper_wick_handle_a_zero_range_bar() -> None:
    frame = pl.DataFrame(
        {
            "open_time": [1_704_067_200_000 + i * DAY for i in range(2)],
            "open": [10.0, 5.0],
            "high": [12.0, 5.0],
            "low": [8.0, 5.0],
            "close": [11.0, 5.0],
            "volume": [1.0, 1.0],
        }
    )
    # cpos: (11-8)/(12-8) = 0.75 ; flat bar -> 0.5
    assert _values(frame, "cpos") == [pytest.approx(0.75), 0.5]
    # upper_wick: (12 - max(10,11))/4 = 0.25 ; flat bar -> 0.0
    assert _values(frame, "upper_wick") == [pytest.approx(0.25), 0.0]


def test_vwap_resets_at_the_utc_day_boundary() -> None:
    frame = pl.DataFrame(
        {
            "open_time": [
                1_704_067_200_000,  # day 0
                1_704_067_200_000 + 3_600_000,  # day 0
                1_704_067_200_000 + DAY,  # day 1
                1_704_067_200_000 + DAY + 3_600_000,  # day 1
            ],
            "open": [10.0, 20.0, 30.0, 40.0],
            "high": [10.0, 20.0, 30.0, 40.0],
            "low": [10.0, 20.0, 30.0, 40.0],
            "close": [10.0, 20.0, 30.0, 40.0],
            "volume": [10.0, 10.0, 10.0, 30.0],
        }
    )
    values = _values(frame, "vwap")
    # day 0: hlc3 == close; (10*10 + 20*10) / 20 on both bars
    assert values[0] == pytest.approx(10.0)
    assert values[1] == pytest.approx(15.0)
    # day 1 resets: 30, then (30*10 + 40*30) / 40
    assert values[2] == pytest.approx(30.0)
    assert values[3] == pytest.approx(37.5)


def test_an_empty_frame_yields_empty_series() -> None:
    frame = _bars(0)
    result = compute_indicator(frame, name="sma", params={"length": 2})
    assert result["series"][0]["values"] == []
    assert result["t"] == []


# --------------------------------------------------------------------------
# Causality: truncation smoke (PD-2; full property suite in M3)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(known_indicators()))
def test_truncating_the_input_never_changes_the_reachable_output(name: str) -> None:
    frame = _bars(40)
    params: dict[str, Any] = {"length": 5} if name in {"sma", "ema", "atr", "rsi", "rvol", "bb"} else {}
    full = compute_indicator(frame, name=name, params=params or None)
    for cut in (1, 7, 39):
        partial = compute_indicator(frame.head(cut), name=name, params=params or None)
        assert partial["t"] == full["t"][:cut]
        for partial_series, full_series in zip(
            partial["series"], full["series"], strict=True
        ):
            assert partial_series["key"] == full_series["key"]
            assert partial_series["values"] == full_series["values"][:cut]
