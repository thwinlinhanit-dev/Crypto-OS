"""M7 (T7.1) — random-entry baseline (spec 9.2, port of the Pine BENCHMARK mode).

Seeds 1..N (default 1000); at each flat bar enter long with probability ``p``
using the SAME exits, sizing, costs, fill model and data_scope. Calibrate
``p`` per universe so the mean trade count matches the strategy within
+/-10% (9.2).

Pine's RNG is not reproducible: **distributions are compared, not bars**
(9.2). The engine's seeded ``random.Random(seed)`` is deterministic; the
strategy-vs-baseline verdict is a percentile comparison (9.3 Level 2):

- strategy PF >= P95 of the baseline -> PASS
- P80 <= PF < P95 -> WARN
- PF < P80 -> FAIL
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field

from engine.strategy.simulator import Bar, Signal

#: Default seed count (9.2; config/validation.yaml baseline_seeds).
DEFAULT_SEEDS = 1000

#: p-calibration tolerance (9.2: mean trade count within +/-10%).
CALIBRATION_TOLERANCE = 0.10
CALIBRATION_MAX_STEPS = 20


@dataclass
class BaselineRun:
    """One baseline seed's outcome (the distributions aggregate these)."""

    seed: int
    entry_probability: float
    trade_count: int
    profit_factor: float | None
    net_return: float | None
    expectancy_r: float | None
    max_drawdown: float | None


@dataclass
class BaselineResult:
    """The aggregated distributions + the strategy's percentile position."""

    p: float
    seeds: int
    mean_trade_count: float
    distribution: dict[str, list[float]] = field(default_factory=dict)
    percentiles: dict[str, dict[str, float]] = field(default_factory=dict)
    calibrated_within_tolerance: bool = False


def random_entry_signals(
    bars: Sequence[Bar],
    *,
    seed: int,
    probability: float,
) -> list[Signal]:
    """A flat-bar random-entry signal list for one seed (deterministic)."""
    rng = random.Random(seed)
    signals: list[Signal] = []
    for bar in bars:
        if bar.excluded:
            continue
        if rng.random() < probability:
            signals.append(
                Signal(bar_index=bar.index, event_type="BENCH", direction="LONG")
            )
    return signals


def calibrate_probability(
    target_trade_count: int,
    bar_count: int,
    *,
    tolerance: float = CALIBRATION_TOLERANCE,
    max_steps: int = CALIBRATION_MAX_STEPS,
) -> float:
    """Binary-search p so the expected entry count matches the strategy's
    trade count within +/-tolerance (9.2)."""
    if bar_count <= 0 or target_trade_count <= 0:
        return 0.0
    low, high = 0.0, 1.0
    best = 0.0
    for _ in range(max_steps):
        mid = (low + high) / 2
        expected = mid * bar_count
        if abs(expected - target_trade_count) <= tolerance * target_trade_count:
            return mid
        best = mid
        if expected < target_trade_count:
            low = mid
        else:
            high = mid
    return best


def percentile(sorted_values: Sequence[float], pct: float) -> float | None:
    """Linear-interpolated percentile of a sorted list."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    fraction = rank - low
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * fraction


def strategy_percentile(
    baseline_values: Sequence[float], strategy_value: float | None
) -> float | None:
    """Where the strategy sits inside the baseline distribution (0-100)."""
    if strategy_value is None or not baseline_values:
        return None
    ordered = sorted(baseline_values)
    below = sum(1 for value in ordered if value < strategy_value)
    return 100.0 * below / len(ordered)


def verdict(strategy_value: float | None, percentile_pct: float | None) -> str:
    """9.3 Level 2 random-baseline verdict."""
    if strategy_value is None or percentile_pct is None:
        return "INSUFFICIENT"
    if percentile_pct >= 95.0:
        return "PASS"
    if percentile_pct >= 80.0:
        return "WARN"
    return "FAIL"


def aggregate(runs: Sequence[BaselineRun]) -> tuple[dict[str, list[float]], dict[str, dict[str, float]]]:
    """Distributions + percentiles (P5/P50/P80/P95) for every metric."""
    keys = ("profit_factor", "net_return", "expectancy_r", "max_drawdown")
    distribution: dict[str, list[float]] = {}
    percentiles: dict[str, dict[str, float]] = {}
    for key in keys:
        values = sorted(
            float(getattr(run, key))
            for run in runs
            if getattr(run, key) is not None
        )
        distribution[key] = values
        percentiles[key] = {
            "p5": percentile(values, 5) or 0.0,
            "p50": percentile(values, 50) or 0.0,
            "p80": percentile(values, 80) or 0.0,
            "p95": percentile(values, 95) or 0.0,
        }
    return distribution, percentiles
