"""M4 (T4.5) - h=10 parity AC against the owner's TradingView study table.

Design section 7 item 4: run the detector + outcomes on the engine bars
behind the D-020 parity range (``fixtures/parity/BINANCE_BTCUSDT_1D.csv``);
for each signal type compare ``(n, sum(fwd_ret_pct[10]), win_count)`` against
the reference study table row read from the owner's TV chart via tvcontrol
``data_get_pine_tables`` on the same symbol/tf/range with ``study_horizon=10``
and the reference defaults.

**Limitation (recorded honestly, design section 7 item 4):** the stock study
table publishes aggregates, not the per-signal arrays (they live in Pine
arrays the script does not export), so aggregate equality on exact float sums
plus the independent exact-formula unit tests is the strongest evidence
available without modifying the reference script (which is the golden source
and must not be changed). If the owner later exports per-signal rows, the
test upgrades to per-signal comparison without a rule change.

The CI gate is the golden fixtures (``fixtures/events/rvol_climax_v5/``);
this test skips with a clear reason when no table export is present locally.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from engine.events.outcomes import compute_outcomes
from engine.events.registry import detect_with

DAY = 86_400_000

# Parity CSV columns → engine frame (open_time in ms, OHLCV floats).
_CSV = Path(__file__).resolve().parent.parent / "fixtures" / "parity" / "BINANCE_BTCUSDT_1D.csv"

# Owner's TV study table export (tvcontrol data_get_pine_tables, study
# "RVOL Climax Pro", study_horizon=10, same range). Path to the JSON dump;
# when absent the test skips with a clear reason.
_TABLE_EXPORT = Path(__file__).resolve().parent.parent / "fixtures" / "events" / "rvol_climax_v5" / "study_table_h10.json"

# Reference defaults (config/defaults.yaml detectors.rvol_climax_v5) — the
# AC compares against the study's default parameters, not SMALL.
REF_PARAMS: dict[str, Any] = {
    "vol_len": 20,
    "climax_mult": 2.5,
    "top_mult": 2.0,
    "range_len": 20,
    "trend_len": 5,
    "close_pos_lo": 0.25,
    "close_pos_hi": 0.75,
    "regime_len": 50,
    "bounce_len": 10,
    "bounce_atr": 1.0,
    "wick_min": 0.30,
    "cooldown": 3,
    "atr_len": 14,
    "fail_mode": "failed_bounce",
    "lookback_bars": 100,
}

HORIZON = 10


def _load_frame() -> pl.DataFrame:
    df = pl.read_csv(_CSV)
    return pl.DataFrame(
        {
            "open_time": (df["time"].cast(pl.Int64) * 1000).to_list(),
            "open": df["open"].cast(pl.Float64).to_list(),
            "high": df["high"].cast(pl.Float64).to_list(),
            "low": df["low"].cast(pl.Float64).to_list(),
            "close": df["close"].cast(pl.Float64).to_list(),
            "volume": df["volume"].cast(pl.Float64).to_list(),
        }
    )


def _engine_aggregates(frame: pl.DataFrame) -> dict[str, dict[str, float]]:
    """Per-signal-type (n, sum(fwd_ret_pct[10]), win_count) from the engine."""
    events = detect_with(
        "RVOL_CLIMAX",
        frame,
        params=REF_PARAMS,
        instrument_id="BINANCE:BTCUSDT",
        timeframe="1D",
        dataset_version="parity-h10",
    )
    outcomes = compute_outcomes(events, frame, horizons=(HORIZON,))
    by_type: dict[str, dict[str, float]] = {}
    for event, outcome in zip(events, outcomes, strict=True):
        h10 = outcome.horizons[HORIZON]
        if not h10.complete or h10.fwd_ret_pct is None:
            continue
        slot = by_type.setdefault(event.event_type, {"n": 0, "sum": 0.0, "wins": 0})
        slot["n"] += 1
        slot["sum"] += h10.fwd_ret_pct
        if h10.fwd_ret_pct > 0:
            slot["wins"] += 1
    return by_type


@pytest.mark.skipif(
    not _TABLE_EXPORT.exists(),
    reason=(
        "owner's TV study-table export not present locally "
        f"(expected at {_TABLE_EXPORT.relative_to(_TABLE_EXPORT.parents[3])}); "
        "the CI gate is fixtures/events/rvol_climax_v5/ — read the table via "
        "tvcontrol data_get_pine_tables on the owner's chart to enable this AC"
    ),
)
def test_h10_aggregates_match_study_table() -> None:
    frame = _load_frame()
    engine = _engine_aggregates(frame)
    table = json.loads(_TABLE_EXPORT.read_text())
    rows: dict[str, dict[str, float]] = table["rows"]
    for event_type, expected in rows.items():
        actual = engine.get(event_type, {"n": 0, "sum": 0.0, "wins": 0})
        assert actual["n"] == expected["n"], f"{event_type}: n"
        assert actual["sum"] == pytest.approx(expected["sum"], rel=1e-12), f"{event_type}: sum(fwd_ret_pct[{HORIZON}])"
        assert actual["wins"] == expected["wins"], f"{event_type}: win_count"
