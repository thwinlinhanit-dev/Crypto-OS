"""Confidence intervals for StatsResult (spec 8.2; design note m5_stats §3.2).

Two intervals, both 95%:

- **Wilson score** for the win rate, computed on the raw ``(n, wins)`` of the
  segment with the fixed ``z = 1.959963984540054`` the design note pins. The
  spec names Wilson, which is a binomial interval; cluster adjustment is what
  the bootstrap provides for the mean/median.
- **Seeded cluster bootstrap** for the mean and median of ``fwd_ret_r`` (the
  statistic the grade consumes) and of ``fwd_ret_pct``: ``random.Random(seed)``
  draws, resampling **clusters** with replacement (all observations of the
  drawn clusters ride along), percentile interval ``[p2.5, p97.5]``.

Statistics helpers use type-7 linear interpolation between closest ranks for
every quantile (percentile table and median alike), so ``median == p50`` by
construction. Pure stdlib, no I/O, deterministic for a fixed seed (PD-6).
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from statistics import fmean

from engine.errors import ValidationError

#: Spec 8.2 Wilson 95% z, pinned by the design note (fixed, not derived).
WILSON_Z_95 = 1.959963984540054


def percentile(values: Sequence[float], p: float) -> float:
    """Type-7 quantile of ``values`` at percent ``p`` in [0, 100].

    ``h = (n - 1) * p / 100``; linear interpolation between the closest ranks.
    The spec's percentile table (p10/25/50/75/90) and every CI endpoint use
    this one definition.
    """
    if not values:
        raise ValidationError("percentile of an empty sample is undefined", {"p": p})
    if not isinstance(p, (int, float)) or isinstance(p, bool) or not 0.0 <= p <= 100.0:
        raise ValidationError("percentile p must be within [0, 100]", {"p": p})
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * (float(p) / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(ordered[low])
    return float(ordered[low] + (rank - low) * (ordered[high] - ordered[low]))


def median(values: Sequence[float]) -> float:
    """Median = p50 under the same type-7 rule as every other quantile."""
    return percentile(values, 50.0)


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean (statistics.fmean); empty input fails closed."""
    if not values:
        raise ValidationError("mean of an empty sample is undefined", {})
    return float(fmean(values))


def wilson_ci(wins: int, n: int, *, z: float = WILSON_Z_95) -> tuple[float, float] | None:
    """Wilson score interval for a ``wins``-of-``n`` rate (spec 8.2, win rate).

    Returns ``None`` when ``n == 0`` (no observations, no interval) rather
    than a fabricated [0, 0]. Out-of-range counts fail closed (PD-10).
    """
    if (
        not isinstance(wins, int)
        or not isinstance(n, int)
        or isinstance(wins, bool)
        or isinstance(n, bool)
        or wins < 0
        or n < 0
        or wins > n
    ):
        raise ValidationError(
            "wilson_ci needs 0 <= wins <= n", {"wins": wins, "n": n}
        )
    if n == 0:
        return None
    p = wins / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return (center - half, center + half)


def cluster_bootstrap_cis(
    series: Mapping[str, Sequence[float]],
    cluster_ids: Sequence[str],
    *,
    seed: int,
    resamples: int,
    level: float,
) -> dict[str, dict[str, tuple[float, float]]]:
    """Seeded cluster-bootstrap CIs for the mean and median of each series.

    One draw resamples ``len(clusters)`` cluster ids with replacement and
    carries every member observation along; the mean and median of **every**
    series are computed on that same draw, so all statistics in one draw-set
    share their draws (D-032). Endpoints are the percentile interval
    ``[100*alpha/2, 100*(1-alpha/2)]`` with ``alpha = 1 - level``.

    Deterministic for a fixed ``(series, cluster_ids, seed, resamples, level)``
    tuple: the RNG sequence depends only on the cluster count, never on the
    series contents, so adding a series cannot shift another series' draws.
    """
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValidationError("bootstrap seed must be an integer", {"seed": seed})
    if not isinstance(resamples, int) or isinstance(resamples, bool) or resamples < 1:
        raise ValidationError(
            "bootstrap resamples must be a positive integer", {"resamples": resamples}
        )
    if not isinstance(level, (int, float)) or not 0.0 < level < 1.0:
        raise ValidationError("ci level must lie strictly in (0, 1)", {"level": level})
    if not series:
        raise ValidationError("bootstrap needs at least one value series", {})
    if not cluster_ids:
        raise ValidationError("bootstrap needs at least one cluster", {})
    for name, values in series.items():
        if len(values) != len(cluster_ids):
            raise ValidationError(
                "series and cluster ids must align observation-wise",
                {"series": name, "n_values": len(values), "n_ids": len(cluster_ids)},
            )
        if not values:
            raise ValidationError("bootstrap series must not be empty", {"series": name})

    members: dict[str, list[int]] = {}
    for index, cid in enumerate(cluster_ids):
        members.setdefault(cid, []).append(index)
    ordered = sorted(members)
    n_clusters = len(ordered)

    rng = random.Random(seed)
    draws: dict[str, list[float]] = {}
    for name in series:
        draws[f"{name}:mean"] = []
        draws[f"{name}:median"] = []
    for _ in range(resamples):
        picked = [ordered[rng.randrange(n_clusters)] for _ in range(n_clusters)]
        idxs = [i for cid in picked for i in members[cid]]
        for name, values in series.items():
            sample = [values[i] for i in idxs]
            draws[f"{name}:mean"].append(float(fmean(sample)))
            draws[f"{name}:median"].append(percentile(sample, 50.0))

    alpha = 1.0 - float(level)
    lo_p = 100.0 * alpha / 2.0
    hi_p = 100.0 * (1.0 - alpha / 2.0)
    result: dict[str, dict[str, tuple[float, float]]] = {}
    for name in series:
        result[name] = {
            "mean": (percentile(draws[f"{name}:mean"], lo_p), percentile(draws[f"{name}:mean"], hi_p)),
            "median": (
                percentile(draws[f"{name}:median"], lo_p),
                percentile(draws[f"{name}:median"], hi_p),
            ),
        }
    return result
