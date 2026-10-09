"""Cluster models for StatsResult (spec 8.2; design note m5_stats §3.3).

Every model partitions the in-scope observations into clusters; ``n_eff`` is
the cluster count and the bootstrap resamples clusters. Cluster ids are
aligned with the input observation order.

- ``SYMBOL_GAP``: within a symbol, consecutive observations share a cluster
  while their origin gap is < H bars (H = max horizon in view); a gap >= H
  starts a new cluster. Clusters never span symbols.
- ``CALENDAR_DAY``: one cluster per UTC calendar day of the origin, across symbols.
- ``CALENDAR_WEEK``: one cluster per ISO week (ISO year + week), across symbols.
- ``MARKET_EVENT``: bars on which >= k symbols emit the same event type form
  one cluster; adjacent flagged bars (same type, consecutive bar origins)
  merge; every observation outside a flagged bar is its own cluster.
- ``CUSTOM``: block definition comes from the experiment config (M5b) - in M5
  this model is a 422 ``VALIDATION_ERROR``, never an invented rule (PD-4/PD-9).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import pairwise
from typing import TYPE_CHECKING

from engine.errors import ValidationError

if TYPE_CHECKING:  # Observation lives in compute.py; reading attributes needs no import.
    from engine.stats.compute import Observation

#: Models compute_stats accepts (CUSTOM is accepted-then-rejected with its M5 message).
CLUSTER_MODELS: tuple[str, ...] = (
    "SYMBOL_GAP",
    "CALENDAR_DAY",
    "CALENDAR_WEEK",
    "MARKET_EVENT",
    "CUSTOM",
)

#: M5 refusal for CUSTOM (needs an experiment config, M5b). Defined once so the
#: endpoint's fail-fast check and assign_clusters raise the identical message.
CUSTOM_M5_MESSAGE = "CUSTOM cluster model requires an experiment (M5b)"

DAY_MS = 86_400_000


def _require_bar_ms(bar_ms: int) -> None:
    if not isinstance(bar_ms, int) or isinstance(bar_ms, bool) or bar_ms <= 0:
        raise ValidationError("bar_ms must be a positive integer", {"bar_ms": bar_ms})


def assign_clusters(
    observations: Sequence[Observation],
    *,
    model: str,
    horizon_h: int,
    bar_ms: int,
    market_event_k: int | None = None,
) -> list[str]:
    """Cluster ids for ``observations`` under ``model``, input order preserved."""
    if model == "CUSTOM":
        raise ValidationError(CUSTOM_M5_MESSAGE, {"cluster_model": model})
    if model not in CLUSTER_MODELS:
        raise ValidationError(
            f"unknown cluster model '{model}'",
            {"cluster_model": model, "allowed": list(CLUSTER_MODELS)},
        )
    if not isinstance(horizon_h, int) or isinstance(horizon_h, bool) or horizon_h < 0:
        raise ValidationError(
            "horizon_h must be a non-negative integer", {"horizon_h": horizon_h}
        )
    _require_bar_ms(bar_ms)
    if not observations:
        return []
    if model == "SYMBOL_GAP":
        return _symbol_gap(observations, horizon_h=horizon_h, bar_ms=bar_ms)
    if model == "CALENDAR_DAY":
        return [f"day:{obs.origin_ms // DAY_MS}" for obs in observations]
    if model == "CALENDAR_WEEK":
        return [_iso_week_id(obs.origin_ms) for obs in observations]
    return _market_event(observations, bar_ms=bar_ms, k=market_event_k)


def _symbol_gap(
    observations: Sequence[Observation], *, horizon_h: int, bar_ms: int
) -> list[str]:
    """Per-symbol runs: a new cluster when the origin gap reaches H bars."""
    by_symbol: dict[str, list[int]] = {}
    for index, obs in enumerate(observations):
        by_symbol.setdefault(obs.instrument_id, []).append(index)
    ids: list[str | None] = [None] * len(observations)
    for symbol in sorted(by_symbol):
        order = sorted(by_symbol[symbol], key=lambda i: observations[i].origin_ms)
        cluster = 0
        ids[order[0]] = f"sym:{symbol}#{cluster}"
        for prev, cur in pairwise(order):
            gap_bars = (observations[cur].origin_ms - observations[prev].origin_ms) / bar_ms
            if gap_bars >= horizon_h:
                cluster += 1
            ids[cur] = f"sym:{symbol}#{cluster}"
    return [cid if cid is not None else "" for cid in ids]


def _iso_week_id(origin_ms: int) -> str:
    year, week, _ = datetime.fromtimestamp(origin_ms / 1000.0, tz=UTC).isocalendar()
    return f"week:{year:04d}-{week:02d}"


def _market_event(
    observations: Sequence[Observation], *, bar_ms: int, k: int | None
) -> list[str]:
    """Flagged (type, bar) cells with >= k distinct symbols; adjacent merges."""
    if k is None:
        raise ValidationError(
            "MARKET_EVENT needs market_event_k (derived: max(3, 10% of universe))",
            {"cluster_model": "MARKET_EVENT"},
        )
    if not isinstance(k, int) or isinstance(k, bool) or k < 1:
        raise ValidationError("market_event_k must be a positive integer", {"k": k})
    symbols_at: dict[tuple[str, int], set[str]] = {}
    for obs in observations:
        symbols_at.setdefault((obs.event_type, obs.origin_ms), set()).add(obs.instrument_id)
    flagged: dict[str, list[int]] = {}
    for (event_type, origin), symbols in symbols_at.items():
        if len(symbols) >= k:
            flagged.setdefault(event_type, []).append(origin)
    # Merge adjacent flagged bars within each event type: every (type, bar)
    # maps to the first origin of its merged group (bar-aligned origins).
    group_first: dict[tuple[str, int], int] = {}
    for event_type, origins in flagged.items():
        ordered = sorted(origins)
        first = ordered[0]
        group_first[(event_type, first)] = first
        for prev, cur in pairwise(ordered):
            if cur - prev != bar_ms:
                first = cur
            group_first[(event_type, cur)] = first
    ids: list[str] = []
    for obs in observations:
        key = (obs.event_type, obs.origin_ms)
        if key in group_first:
            ids.append(f"evt:{obs.event_type}:{group_first[key]}")
        else:
            ids.append(f"obs:{obs.event_id}")
    return ids
