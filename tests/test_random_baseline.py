"""M7 (T7.1) — random-entry baseline tests (spec 9.2, 9.3 Level 2).

Seeded determinism, p-calibration within +/-10%, and the percentile
verdicts (PF >= P95 PASS, P80-P95 WARN, <P80 FAIL).
"""

from __future__ import annotations

from engine.strategy.simulator import Bar
from engine.validation.baseline import (
    aggregate,
    calibrate_probability,
    percentile,
    random_entry_signals,
    strategy_percentile,
    verdict,
)


def _bars(count: int) -> list[Bar]:
    return [
        Bar(index=i, time=f"2026-01-{i + 1:02d}T00:00:00Z", open=100.0, high=101.0,
            low=99.0, close=100.0)
        for i in range(count)
    ]


def test_random_entry_is_deterministic_per_seed() -> None:
    """9.2: the engine's RNG is seeded, so one seed always gives one list."""
    bars = _bars(50)
    first = random_entry_signals(bars, seed=7, probability=0.2)
    second = random_entry_signals(bars, seed=7, probability=0.2)
    assert [s.bar_index for s in first] == [s.bar_index for s in second]


def test_random_entry_count_scales_with_probability() -> None:
    bars = _bars(500)
    low = len(random_entry_signals(bars, seed=1, probability=0.05))
    high = len(random_entry_signals(bars, seed=1, probability=0.30))
    assert high > low


def test_excluded_bars_never_enter() -> None:
    bars = [Bar(index=0, time="t0", open=1, high=1, low=1, close=1, excluded=True)]
    assert random_entry_signals(bars, seed=3, probability=1.0) == []


def test_calibration_matches_target_within_tolerance() -> None:
    """9.2: p is calibrated so the mean trade count matches within +/-10%."""
    bar_count = 1000
    target = 100
    p = calibrate_probability(target, bar_count)
    assert abs(p * bar_count - target) <= 0.10 * target


def test_calibration_edge_cases() -> None:
    assert calibrate_probability(0, 100) == 0.0
    assert calibrate_probability(10, 0) == 0.0


def test_percentile_of_sorted_values() -> None:
    values = [float(i) for i in range(1, 101)]  # 1..100
    assert percentile(values, 50) == 50.5
    assert percentile(values, 95) == 95.05
    assert percentile(values, 100) == 100.0
    assert percentile([], 50) is None


def test_strategy_percentile_position() -> None:
    baseline = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert strategy_percentile(baseline, 5.0) == 80.0
    assert strategy_percentile(baseline, 0.5) == 0.0
    assert strategy_percentile(baseline, None) is None


def test_verdicts_pass_warn_fail() -> None:
    """9.3 Level 2 random-baseline verdict thresholds."""
    assert verdict(1.5, 96.0) == "PASS"
    assert verdict(1.2, 85.0) == "WARN"
    assert verdict(0.9, 60.0) == "FAIL"
    assert verdict(None, None) == "INSUFFICIENT"


def test_aggregate_percentiles() -> None:
    from engine.validation.baseline import BaselineRun

    runs = [
        BaselineRun(seed=i, entry_probability=0.1, trade_count=10,
                    profit_factor=float(i), net_return=float(i),
                    expectancy_r=float(i), max_drawdown=float(i))
        for i in range(1, 101)
    ]
    distribution, percentiles = aggregate(runs)
    assert len(distribution["profit_factor"]) == 100
    assert percentiles["profit_factor"]["p50"] == 50.5
    assert percentiles["profit_factor"]["p95"] > 90
