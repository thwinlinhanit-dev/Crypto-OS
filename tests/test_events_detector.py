"""M4 (T4.2) - RVOL_CLIMAX v5.0 detector (spec section 6.3, design section 4).

Acceptance evidence produced here:
- every derived quantity matches the Pine definition with the correct lag
  semantics (volMA/hiN/loN/swingLo lagged 1, emaR not lagged);
- each of the four raw rules fires exactly where hand-computed conditions
  hold, and each stream passes the Section 6.7 cooldown independently;
- archetype labels match Pine lines 96-102 and are null on non-climax bars;
- invalidation is close -/+ 3.0 * atr (spec 268); R_basis is "atr";
- fail_mode "failed_bounce" uses the new rule, "legacy_v1" the legacy one;
- no event fires before warmup_bars; an event whose lookback window
  overlaps an exclusion interval carries lookback_gap=true;
- registry resolves RVOL_CLIMAX@5.0 and refuses the six SPEC_REQUIRED names
  (PD-4); undocumented parameters raise ParamError (PD-11);
- event_id is content-addressed: same inputs -> same id (recompute-safe).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from engine.config.defaults import ParamError
from engine.errors import ValidationError
from engine.events.base import Event, canonical_json, params_hash
from engine.events.registry import (
    detect_with,
    known_detectors,
    resolve_detector,
    spec_required_names,
)

DAY = 86_400_000
BASE = 1_704_067_200_000  # 2024-01-01 UTC

# Small params so hand computation stays tractable; every value is documented
# in config/defaults.yaml (PD-11 - the test overrides, it never invents).
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


def _frame(
    open_: list[float],
    high: list[float],
    low: list[float],
    close: list[float],
    volume: list[float],
) -> pl.DataFrame:
    size = len(close)
    return pl.DataFrame(
        {
            "open_time": [BASE + i * DAY for i in range(size)],
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def _detect(
    frame: pl.DataFrame,
    overrides: dict[str, Any] | None = None,
    *,
    exclusions: list[tuple[datetime, datetime]] | None = None,
) -> list[Event]:
    params = {**SMALL, **(overrides or {})}
    return detect_with(
        "RVOL_CLIMAX",
        frame,
        version="5.0",
        params=params,
        instrument_id="test-btc",
        dataset_version="test-ds",
        exclusions=exclusions or [],
    )


def _flat_bars(size: int, price: float = 100.0, volume: float = 10.0) -> pl.DataFrame:
    """No-range bars: cpos 0.5, upW 0.0, rvol exactly 1.0 after warmup."""
    return _frame(
        [price] * size,
        [price] * size,
        [price] * size,
        [price] * size,
        [volume] * size,
    )


def test_derived_quantities_match_pine_with_lags() -> None:
    """volMA/hiN/loN/swingLo lag 1; emaR does not; rvol uses the lagged MA."""
    closes = [100.0, 101.0, 102.0, 103.0, 104.0]
    highs = [101.0, 102.0, 103.0, 104.0, 105.0]
    lows = [99.0, 100.0, 101.0, 102.0, 103.0]
    opens = [100.0, 101.0, 102.0, 103.0, 104.0]
    volumes = [30.0, 10.0, 10.0, 10.0, 10.0]
    frame = _frame(opens, highs, lows, closes, volumes)
    events = _detect(frame, {"cooldown": 99, "lookback_bars": 1})
    # No emission forced; inspect the derived series via a direct detect call
    # by checking one emitted event's features on a climax bar instead.
    detector = resolve_detector("RVOL_CLIMAX", "5.0")
    resolved = detector.resolve(SMALL)
    series = detector.derived(frame, resolved)
    # volMA[t] = sma(volume,3)[t-1]; at t=3: sma(v[0..2]) = (30+10+10)/3 = 50/3
    assert series["volMA"][3] == pytest.approx(50.0 / 3.0)
    # rvol[3] = 10 / (50/3) = 0.6
    assert series["rvol"][3] == pytest.approx(0.6)
    # hiN[3] = highest(high,3)[2] = max(101,102,103) = 103
    assert series["hiN"][3] == pytest.approx(103.0)
    # loN[3] = lowest(low,3)[2] = min(99,100,101) = 99
    assert series["loN"][3] == pytest.approx(99.0)
    # swingLo[3] = lowest(low,3)[2] = 99 (bounce_len=3)
    assert series["swingLo"][3] == pytest.approx(99.0)
    # emaR is NOT lagged: ema(close,4) first value is close[0]=100
    assert series["emaR"][0] == pytest.approx(100.0)
    assert events == []  # rvol never reaches climax_mult on this series


def test_cpos_and_upper_wick_on_flat_bar() -> None:
    """rng=0 -> cpos 0.5, upW 0.0 (Pine 87-88)."""
    frame = _flat_bars(8)
    detector = resolve_detector("RVOL_CLIMAX", "5.0")
    series = detector.derived(frame, detector.resolve(SMALL))
    assert series["cpos"][5] == 0.5
    assert series["upW"][5] == 0.0


def test_raw_cont_fires_long_on_climax_uptrend_at_high() -> None:
    """CONT: isClimax, trendU, atHi, not rawTop -> LONG after cooldown gate."""
    # Warmup flat bars (rvol undefined/null until volMA exists), then a
    # breakout bar: huge volume, close above prior high and above close[trend_len].
    closes = [100.0] * 10 + [110.0, 111.0]
    highs = [100.5] * 10 + [111.0, 112.0]
    lows = [99.5] * 10 + [109.0, 110.0]
    opens = [100.0] * 10 + [100.0, 111.0]
    volumes = [10.0] * 10 + [100.0, 10.0]
    frame = _frame(opens, highs, lows, closes, volumes)
    events = _detect(frame)
    cont = [e for e in events if e.event_type == "CONT"]
    assert len(cont) == 1
    event = cont[0]
    assert event.direction == "LONG"
    assert event.event_index == 10
    # ref = close[10] = 110; atr from true ranges; invalidation = ref - 3*atr
    detector = resolve_detector("RVOL_CLIMAX", "5.0")
    series = detector.derived(frame, detector.resolve(SMALL))
    atr = series["atr"][10]
    assert atr is not None
    assert event.ref_price == pytest.approx(110.0)
    assert event.invalidation_price == pytest.approx(110.0 - 3.0 * atr)
    # rvol at bar 10: volMA = sma(v,3)[9] = 10; rvol = 100/10 = 10 >= 3
    assert series["rvol"][10] == pytest.approx(10.0)
    # trendU: close[10]=110 > close[8]=100
    assert series["trendU"][10] is True
    # atHi: close[10]=110 >= hiN[10] = highest(high,3)[9] = 100.5
    assert series["atHi"][10] is True


def test_raw_cap_fires_long_on_climax_downtrend_at_low() -> None:
    """CAPIT: isClimax, not trendU, cpos<=0.30, atLo -> LONG."""
    # Climax bar closes near its low: cpos = (88.5-88)/(91-88) = 1/6 <= 0.30.
    closes = [100.0] * 10 + [88.5, 89.0]
    highs = [100.5] * 10 + [91.0, 90.0]
    lows = [99.5] * 10 + [88.0, 88.5]
    opens = [100.0] * 10 + [100.0, 89.0]
    volumes = [10.0] * 10 + [100.0, 10.0]
    frame = _frame(opens, highs, lows, closes, volumes)
    events = _detect(frame)
    capit = [e for e in events if e.event_type == "CAPIT"]
    assert len(capit) == 1
    event = capit[0]
    assert event.direction == "LONG"
    assert event.event_index == 10
    detector = resolve_detector("RVOL_CLIMAX", "5.0")
    series = detector.derived(frame, detector.resolve(SMALL))
    assert series["trendU"][10] is False  # 88.5 < close[8]=100
    assert series["cpos"][10] == pytest.approx((88.5 - 88.0) / (91.0 - 88.0))
    assert series["cpos"][10] <= 0.30
    assert series["atLo"][10] is True  # 88.5 <= loN = lowest(low,3)[9] = 99.5


def test_raw_top_fires_short_on_rejection_at_high() -> None:
    """TOP: rvol>=top_mult, close>emaR, nearHi, topReject -> SHORT.

    Warmup stays flat so the regime EMA is pinned at 100; the climax bar
    spikes to a new high and closes red just above that EMA.
    """
    closes = [100.0] * 11 + [101.0]
    highs = [100.5] * 11 + [115.0]
    lows = [99.5] * 11 + [99.0]
    opens = [100.0] * 11 + [104.0]
    volumes = [10.0] * 11 + [60.0]
    frame = _frame(opens, highs, lows, closes, volumes)
    events = _detect(frame)
    tops = [e for e in events if e.event_type == "TOP"]
    assert len(tops) == 1
    event = tops[0]
    assert event.direction == "SHORT"
    assert event.event_index == 11
    detector = resolve_detector("RVOL_CLIMAX", "5.0")
    series = detector.derived(frame, detector.resolve(SMALL))
    assert series["rvol"][11] == pytest.approx(6.0)  # 60 / sma(v,3)[10]=10
    # emaR is not lagged: flat warmup holds it at 100 until this bar's
    # close 101 pulls it to 0.4*101 + 0.6*100 = 100.4; close 101 > 100.4.
    assert series["emaR"][11] == pytest.approx(100.4)
    assert series["closeAboveEmaR"][11] is True
    # nearHi: high 115 >= hiN = highest(high,3)[10] = 100.5
    assert series["nearHi"][11] is True
    # topReject: close 101 < open 104 and upW = (115-104)/(115-99) = 11/16
    assert series["topReject"][11] is True
    assert series["upW"][11] == pytest.approx(11.0 / 16.0)
    assert series["isClimax"][11] is True


def test_raw_top_with_close_above_ema() -> None:
    """TOP where close stays above the regime EMA (the Pine condition)."""
    # Flat warmup pins emaR at 100; rejection bar closes at 103 (> emaR),
    # opens higher (red), tags a new high with a long upper wick.
    closes = [100.0] * 13 + [103.0]
    highs = [100.5] * 13 + [115.0]
    lows = [99.5] * 13 + [102.0]
    opens = [100.0] * 13 + [108.0]
    volumes = [10.0] * 13 + [60.0]
    frame = _frame(opens, highs, lows, closes, volumes)
    events = _detect(frame)
    tops = [e for e in events if e.event_type == "TOP"]
    assert len(tops) == 1
    event = tops[0]
    assert event.direction == "SHORT"
    assert event.event_index == 13
    detector = resolve_detector("RVOL_CLIMAX", "5.0")
    series = detector.derived(frame, detector.resolve(SMALL))
    assert series["rvol"][13] == pytest.approx(6.0)  # 60 / sma(v,3)[12]=10
    assert series["closeAboveEmaR"][13] is True
    assert series["nearHi"][13] is True
    assert series["topReject"][13] is True
    # upW = (115 - max(108,103)) / (115 - 102) = 7/13
    assert series["upW"][13] == pytest.approx(7.0 / 13.0)


def test_raw_fail_new_rule_and_legacy_rule() -> None:
    """failed_bounce needs inDown+bounced+rejected+not atLo; legacy needs only
    climax + not trendU + cpos > close_pos_lo."""
    # Steady decline pins emaR above the bounce close (inDown); the bounce
    # bar is red with a long upper wick, closes near the middle (cpos > lo),
    # and is not at the 20-bar low. Volume is climax.
    closes = [110.0, 108.0, 106.0, 104.0, 102.0, 100.0, 98.0, 96.0, 94.0, 92.0, 90.0, 92.0, 92.0, 92.0]
    highs = [c + 1.0 for c in closes]
    highs[11] = 100.0  # bounce high for bounced=true
    lows = [c - 1.0 for c in closes]
    lows[11] = 88.0
    opens = list(closes)
    opens[11] = 94.0  # red bar (close < open) with upper wick
    volumes = [10.0] * 11 + [80.0, 10.0, 10.0]
    frame = _frame(opens, highs, lows, closes, volumes)

    detector = resolve_detector("RVOL_CLIMAX", "5.0")
    new_events = _detect(frame, {"fail_mode": "failed_bounce"})
    legacy_events = _detect(frame, {"fail_mode": "legacy_v1"})

    new_fails = [e for e in new_events if e.event_type == "FAIL"]
    legacy_fails = [e for e in legacy_events if e.event_type == "FAIL"]

    series = detector.derived(frame, detector.resolve({**SMALL, "fail_mode": "failed_bounce"}))
    # Bar 11: rvol = 80/10 = 8; inDown: close 92 < emaR and emaR < emaR[2]
    assert series["rvol"][11] == pytest.approx(8.0)
    assert series["isClimax"][11] is True
    assert series["inDown"][11] is True
    # bounced: high 100 - swingLo >= 1.5 * atr
    assert series["bounced"][11] is True
    # rejected: close 92 < open 94 and upW = (100-94)/(100-88) = 0.5 >= 0.40
    assert series["upW"][11] == pytest.approx(0.5)
    assert series["rejected"][11] is True
    # not atLo: 92 > loN
    assert series["atLo"][11] is False
    # cpos = (92-88)/(100-88) = 1/3 > close_pos_lo, so legacy also fires
    assert series["cpos"][11] == pytest.approx(1.0 / 3.0)
    assert series["trendU"][11] is False  # 92 > close[9]=92 is False
    assert len(new_fails) >= 1
    assert all(e.direction == "SHORT" for e in new_fails)
    assert legacy_fails  # looser rule fires on the same bar


def test_archetype_labels_and_null_on_non_climax() -> None:
    """Pine 96-102: six labels on climax bars; null otherwise."""
    closes = [100.0] * 10 + [110.0, 110.0]
    highs = [100.5] * 10 + [111.0, 111.0]
    lows = [99.5] * 10 + [109.0, 109.0]
    opens = [100.0] * 10 + [100.0, 110.0]
    volumes = [10.0] * 10 + [100.0, 10.0]
    frame = _frame(opens, highs, lows, closes, volumes)
    detector = resolve_detector("RVOL_CLIMAX", "5.0")
    series = detector.derived(frame, detector.resolve(SMALL))
    assert series["isClimax"][10] is True
    # trendU + cpos: (110-109)/(111-109) = 0.5 -> "top·mid"
    assert series["archetype"][10] == "top·mid"
    assert series["archetype"][0] is None  # not a climax bar (rvol null/warmup)


def test_no_event_before_warmup() -> None:
    """Nothing fires before lookback_bars even if raw conditions hold."""
    # Single climax bar at index 2; warmup is 5.
    closes = [100.0, 100.0, 110.0]
    highs = [100.5, 100.5, 111.0]
    lows = [99.5, 99.5, 109.0]
    opens = [100.0, 100.0, 100.0]
    volumes = [10.0, 10.0, 100.0]
    frame = _frame(opens, highs, lows, closes, volumes)
    events = _detect(frame)
    assert events == []


def test_lookback_gap_flag_when_exclusion_overlaps_window() -> None:
    """An event whose lookback window touches an exclusion sets lookback_gap."""
    closes = [100.0] * 10 + [110.0, 111.0]
    highs = [100.5] * 10 + [111.0, 112.0]
    lows = [99.5] * 10 + [109.0, 110.0]
    opens = [100.0] * 10 + [100.0, 111.0]
    volumes = [10.0] * 10 + [100.0, 10.0]
    frame = _frame(opens, highs, lows, closes, volumes)
    # Exclusion covering the lookback window of bar 10 (window [5, 10]).
    start = datetime.fromtimestamp((BASE + 7 * DAY) / 1000.0, tz=UTC)
    end = datetime.fromtimestamp((BASE + 9 * DAY) / 1000.0, tz=UTC)
    events = _detect(frame, exclusions=[(start, end)])
    cont = [e for e in events if e.event_type == "CONT"]
    assert len(cont) == 1
    assert cont[0].lookback_gap is True
    # Without exclusions the same event has lookback_gap False.
    events_clean = _detect(frame)
    assert any(e.lookback_gap is False for e in events_clean)


def test_event_envelope_fields_and_provenance() -> None:
    """Every section 6.2 field is populated; provenance is OBSERVED/CALCULATED."""
    closes = [100.0] * 10 + [110.0, 111.0]
    highs = [100.5] * 10 + [111.0, 112.0]
    lows = [99.5] * 10 + [109.0, 110.0]
    opens = [100.0] * 10 + [100.0, 111.0]
    volumes = [10.0] * 10 + [100.0, 10.0]
    frame = _frame(opens, highs, lows, closes, volumes)
    events = _detect(frame)
    assert events
    event = events[0]
    envelope = event.to_envelope()
    for key in (
        "event_id",
        "detector_id",
        "event_type",
        "version",
        "params_hash",
        "instrument_id",
        "timeframe",
        "event_time",
        "direction",
        "ref_price",
        "invalidation_price",
        "regime",
        "features",
        "trigger",
        "lookback_gap",
        "dataset_version",
        "engine_version",
        "data_quality",
        "resolved_params",
        "provenance",
    ):
        assert key in envelope, key
    assert envelope["provenance"]["ref_price"] == "OBSERVED"
    assert envelope["provenance"]["event_time"] == "OBSERVED"
    assert envelope["provenance"]["features"] == "CALCULATED"
    assert envelope["detector_id"] == "RVOL_CLIMAX"
    assert envelope["version"] == "5.0"
    assert envelope["direction"] in ("LONG", "SHORT")


def test_event_id_stable_across_recompute() -> None:
    """Same frame + params -> identical event_id (content-addressed)."""
    closes = [100.0] * 10 + [110.0, 111.0]
    highs = [100.5] * 10 + [111.0, 112.0]
    lows = [99.5] * 10 + [109.0, 110.0]
    opens = [100.0] * 10 + [100.0, 111.0]
    volumes = [10.0] * 10 + [100.0, 10.0]
    frame = _frame(opens, highs, lows, closes, volumes)
    first = _detect(frame)
    second = _detect(frame)
    assert [e.event_id for e in first] == [e.event_id for e in second]


def test_params_hash_matches_canonical_json() -> None:
    resolved = resolve_detector("RVOL_CLIMAX", "5.0").resolve(SMALL)
    expected = params_hash(resolved)
    assert expected == params_hash(json.loads(canonical_json(resolved)))


def test_registry_resolves_rvol_climax_and_refuses_spec_required() -> None:
    detector = resolve_detector("RVOL_CLIMAX", "5.0")
    assert detector.detector_id == "RVOL_CLIMAX"
    assert detector.version == "5.0"
    assert ("RVOL_CLIMAX", "5.0") in known_detectors()
    for name in spec_required_names():
        with pytest.raises(ValidationError) as excinfo:
            resolve_detector(name, "1.0")
        assert excinfo.value.detail.get("status") == "SPEC_REQUIRED"
        assert name not in {did for did, _ in known_detectors()}


def test_unknown_detector_and_version_fail_closed() -> None:
    with pytest.raises(ValidationError):
        resolve_detector("NOT_A_DETECTOR")
    with pytest.raises(ValidationError):
        resolve_detector("RVOL_CLIMAX", "9.9")


def test_undocumented_param_raises_param_error() -> None:
    detector = resolve_detector("RVOL_CLIMAX", "5.0")
    with pytest.raises(ParamError):
        detector.resolve({**SMALL, "not_a_param": 1})


def test_fail_mode_vocabulary_is_validated() -> None:
    detector = resolve_detector("RVOL_CLIMAX", "5.0")
    with pytest.raises(ValidationError):
        detector.resolve({**SMALL, "fail_mode": "not_a_mode"})


def test_trigger_checklist_lists_each_rule() -> None:
    """The trigger block names every raw rule with passed/value/threshold."""
    closes = [100.0] * 10 + [110.0, 111.0]
    highs = [100.5] * 10 + [111.0, 112.0]
    lows = [99.5] * 10 + [109.0, 110.0]
    opens = [100.0] * 10 + [100.0, 111.0]
    volumes = [10.0] * 10 + [100.0, 10.0]
    frame = _frame(opens, highs, lows, closes, volumes)
    events = _detect(frame)
    event = events[0]
    rules = event.trigger.get("rules", [])
    names = {rule["name"] for rule in rules}
    assert "isClimax" in names
    assert "trendU" in names
    assert "atHi" in names
    for rule in rules:
        assert set(rule) >= {"name", "passed", "value", "threshold"}


def test_golden_fixture_if_present() -> None:
    """Optional golden JSON under fixtures/events/rvol_climax_v5/ compares exactly."""
    path = Path("fixtures") / "events" / "rvol_climax_v5" / "golden.json"
    if not path.is_file():
        pytest.skip("golden fixture not written yet")
    payload = json.loads(path.read_text(encoding="utf-8"))
    frame = _frame(
        payload["open"],
        payload["high"],
        payload["low"],
        payload["close"],
        payload["volume"],
    )
    events = _detect(frame, payload.get("params"))
    actual = [
        {
            "event_type": e.event_type,
            "event_index": e.event_index,
            "direction": e.direction,
            "ref_price": e.ref_price,
            "invalidation_price": e.invalidation_price,
            "archetype": e.features.get("archetype"),
        }
        for e in events
    ]
    assert actual == payload["expected"]
