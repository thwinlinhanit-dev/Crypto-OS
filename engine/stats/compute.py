"""StatsResult computation (spec 8.2-8.3; design note m5_stats §3).

Pure function ``compute_stats`` producing the stored ``result`` JSON: no I/O,
no clock, no RNG except ``random.Random(seed)`` inside the bootstrap (PD-6).
Inputs are ``(observations, boundaries, filter, cluster_models, seed, params,
grade_thresholds, n_raw, bar_ms)`` so the computation is unit-testable and
deterministic: the same inputs always yield the identical dict.

Pipeline:

1. validate every input fail-closed (PD-4/PD-10/PD-11: nothing invented);
2. scope-filter observations by origin via ``engine.scopes.segment_of``
   (DEVELOPMENT keeps IS+OOS, HOLDOUT keeps HOLDOUT) - embargo/purge itself
   ran upstream in ``engine.scopes.embargo_purge`` and is not re-done here;
3. partition the kept observations once per requested cluster model;
4. per-model matrix at the anchor horizon (pooled): ``{n_eff, grade,
   ci_median_r, ci_excludes_zero, oos_sign_agrees, symbols_agree_share,
   consistent_sign}`` - graded even when the anchor is outside the requested
   horizon list;
5. headline model = lowest grade (request order breaks ties; first requested
   model under HOLDOUT where no grades exist);
6. per-horizon entries under the headline model only: pooled stats promoted
   to the entry (spec line 539 shape) plus ``is``/``oos`` side-by-side blocks
   (``None`` when a segment has no observations) and cross-asset consistency.

An observation contributes at horizon h only when its outcome is complete
and both ``fwd_ret_pct`` and ``fwd_ret_r`` are defined (one ``n`` per
segment; incomplete horizons are never partially counted).
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from engine.config.validation import REQUIRED_GRADE_KEYS
from engine.errors import ValidationError
from engine.events.outcomes import HorizonOutcome
from engine.scopes import (
    DEVELOPMENT,
    HOLDOUT,
    SCOPES,
    ScopeBoundaries,
    boundaries_payload,
    segment_of,
)
from engine.stats import ci as stats_ci
from engine.stats.clusters import assign_clusters
from engine.stats.grade import evidence_grade, grade_rank

#: Multiple-testing block until families arrive in M5b (shape ships now, spec 8.4).
MULTIPLE_TESTING_SELECTION = "PRE_SPECIFIED"

_HISTOGRAM_BINS = 20
_PERCENTILES = (10, 25, 50, 75, 90)


@dataclass(frozen=True)
class Observation:
    """One in-scope candidate: an event's identity for clustering plus its outcomes.

    ``origin_ms`` is the bar open_time of the event (spec 8.1.3); the segment
    is always re-derived from it via ``segment_of`` so scopes.py stays the
    single segment authority. ``horizons`` maps horizon -> outcome; horizons
    absent from the map count as incomplete at that h.
    """

    event_id: str
    instrument_id: str
    event_type: str
    origin_ms: int
    horizons: Mapping[int, HorizonOutcome]


def compute_stats(
    observations: Sequence[Observation],
    *,
    boundaries: ScopeBoundaries,
    data_scope: str,
    event_filter: Mapping[str, Any],
    cluster_models: Sequence[str],
    seed: int,
    params: Mapping[str, Any],
    grade_thresholds: Mapping[str, Any],
    n_raw: int,
    bar_ms: int,
    family_id: str | None = None,
    variant_count: int = 1,
    selection_method: str = MULTIPLE_TESTING_SELECTION,
) -> dict[str, Any]:
    """Compute the stored StatsResult ``result`` JSON (pure, deterministic)."""
    horizons = _require_horizons(params)
    resamples = _require_resamples(params)
    level, anchor = _require_grade_inputs(grade_thresholds)
    _require_scope(data_scope)
    _require_cluster_models(cluster_models)
    _require_int("seed", seed, minimum=None)
    _require_int("n_raw", n_raw, minimum=0)
    _require_int("bar_ms", bar_ms, minimum=1)
    if not isinstance(event_filter, Mapping):
        raise ValidationError("event_filter must be a mapping", {"filter": event_filter})
    if family_id is not None and not isinstance(family_id, str):
        raise ValidationError("family_id must be a string or null", {"family_id": family_id})
    _require_int("variant_count", variant_count, minimum=1)
    if not isinstance(selection_method, str) or not selection_method:
        raise ValidationError(
            "selection_method must be a non-empty string", {"selection_method": selection_method}
        )

    ordered = sorted(observations, key=lambda o: (o.origin_ms, o.instrument_id, o.event_id))
    wanted = {"IS", "OOS"} if data_scope == DEVELOPMENT else {"HOLDOUT"}
    kept = [o for o in ordered if segment_of(o.origin_ms, boundaries) in wanted]
    segments = [segment_of(o.origin_ms, boundaries) for o in kept]

    horizon_h = max(horizons)
    model_order = list(dict.fromkeys(cluster_models))
    partitions = {
        model: assign_clusters(
            kept,
            model=model,
            horizon_h=horizon_h,
            bar_ms=bar_ms,
            market_event_k=params.get("market_event_k"),
        )
        for model in model_order
    }

    anchor_idx = [i for i, o in enumerate(kept) if _outcome_at(o, anchor) is not None]
    matrix: dict[str, dict[str, Any]] = {}
    for model in model_order:
        matrix[model] = _grade_model(
            kept,
            anchor_idx,
            segments,
            anchor,
            cluster_ids=[partitions[model][i] for i in anchor_idx],
            seed=seed,
            resamples=resamples,
            level=level,
            thresholds=grade_thresholds,
            data_scope=data_scope,
        )

    if max((entry["n_eff"] for entry in matrix.values()), default=0) > n_raw:
        raise ValidationError(
            "n_eff cannot exceed n_raw (observations before embargo/purge)",
            {"n_eff": max(e["n_eff"] for e in matrix.values()), "n_raw": n_raw},
        )

    headline = _headline_model(model_order, matrix, data_scope)
    head_partition = partitions[headline]
    entries = [
        _horizon_entry(
            kept,
            segments,
            h,
            cluster_ids=head_partition,
            seed=seed,
            resamples=resamples,
            level=level,
        )
        for h in sorted(horizons)
    ]

    per_symbol: dict[str, int] = {}
    for obs in kept:
        per_symbol[obs.instrument_id] = per_symbol.get(obs.instrument_id, 0) + 1
    top = matrix[headline]
    grades = {entry["grade"] for entry in matrix.values()}
    if data_scope == HOLDOUT:
        final_grade: str | None = None
        grade_reason: str | None = top["grade_reason"]
        sensitive: bool | None = None
    else:
        final_grade = top["grade"]
        grade_reason = None
        sensitive = len(grades) > 1

    bounds = boundaries_payload(boundaries, data_scope)
    return {
        "filter": dict(event_filter),
        "data_scope": data_scope,
        "boundaries": {"oos_start": bounds["oos_start"], "holdout_start": bounds["holdout_start"]},
        "n_raw": n_raw,
        "n_eff": top["n_eff"],
        "grade": final_grade,
        "grade_reason": grade_reason,
        "model_sensitive": sensitive,
        "headline_model": headline,
        "cluster_models": matrix,
        "per_symbol": dict(sorted(per_symbol.items())),
        "cross_asset": _cross_asset(
            _by_symbol_r(kept, anchor_idx, anchor),
            _pooled_median(kept, anchor_idx, anchor),
        ),
        "horizons": entries,
        "multiple_testing": {
            "family_id": family_id,
            "variant_count": variant_count,
            "selection_method": selection_method,
        },
    }


# --- validation (fail-closed; no hidden defaults) ----------------------------------------------


def _require_int(name: str, value: Any, *, minimum: int | None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{name} must be an integer", {name: value})
    if minimum is not None and value < minimum:
        raise ValidationError(f"{name} must be >= {minimum}", {name: value})
    return value


def _require_horizons(params: Mapping[str, Any]) -> list[int]:
    horizons = params.get("horizons") if isinstance(params, Mapping) else None
    if not isinstance(horizons, Sequence) or isinstance(horizons, (str, bytes)) or not horizons:
        raise ValidationError("params.horizons must be a non-empty list", {"horizons": horizons})
    clean = [_require_int("horizons[]", h, minimum=1) for h in horizons]
    return sorted(set(clean))


def _require_resamples(params: Mapping[str, Any]) -> int:
    resamples = params.get("bootstrap_resamples") if isinstance(params, Mapping) else None
    return _require_int("params.bootstrap_resamples", resamples, minimum=1)


def _require_grade_inputs(thresholds: Mapping[str, Any]) -> tuple[float, int]:
    if not isinstance(thresholds, Mapping):
        raise ValidationError("grade thresholds must be a mapping", {})
    missing = [key for key in REQUIRED_GRADE_KEYS if key not in thresholds]
    if missing:
        raise ValidationError("evidence-grade thresholds are incomplete", {"missing": missing})
    level = thresholds["ci_level"]
    if not isinstance(level, (int, float)) or isinstance(level, bool) or not 0.0 < level < 1.0:
        raise ValidationError("ci_level must lie strictly in (0, 1)", {"ci_level": level})
    anchor = _require_int("anchor_horizon", thresholds["anchor_horizon"], minimum=1)
    return (float(level), anchor)


def _require_scope(data_scope: str) -> None:
    if data_scope not in SCOPES:
        raise ValidationError(
            f"data_scope must be one of {list(SCOPES)}",
            {"data_scope": data_scope},
        )


def _require_cluster_models(cluster_models: Sequence[str]) -> None:
    if (
        not isinstance(cluster_models, Sequence)
        or isinstance(cluster_models, (str, bytes))
        or not cluster_models
        or any(not isinstance(m, str) for m in cluster_models)
    ):
        raise ValidationError(
            "cluster_models must be a non-empty list of model names",
            {"cluster_models": cluster_models},
        )


# --- samples --------------------------------------------------------------------------------------


def _outcome_at(obs: Observation, h: int) -> HorizonOutcome | None:
    """The usable outcome at h, else None (incomplete or null-valued: skipped)."""
    outcome = obs.horizons.get(h)
    if outcome is None or not outcome.complete:
        return None
    if outcome.fwd_ret_pct is None or outcome.fwd_ret_r is None:
        return None
    return outcome


def _values(
    kept: Sequence[Observation], idxs: Sequence[int], h: int
) -> tuple[list[float], list[float], list[float | None], list[float | None]]:
    pct: list[float] = []
    r: list[float] = []
    mfe: list[float | None] = []
    mae: list[float | None] = []
    for i in idxs:
        outcome = _outcome_at(kept[i], h)
        if outcome is None:  # pragma: no cover - callers pre-filter; kept total
            continue
        assert outcome.fwd_ret_pct is not None and outcome.fwd_ret_r is not None
        pct.append(outcome.fwd_ret_pct)
        r.append(outcome.fwd_ret_r)
        mfe.append(outcome.mfe_r)
        mae.append(outcome.mae_r)
    return (pct, r, mfe, mae)


def _by_symbol_r(
    kept: Sequence[Observation], idxs: Sequence[int], h: int
) -> dict[str, list[float]]:
    by_symbol: dict[str, list[float]] = {}
    for i in idxs:
        outcome = _outcome_at(kept[i], h)
        if outcome is None or outcome.fwd_ret_r is None:  # pragma: no cover - pre-filtered
            continue
        by_symbol.setdefault(kept[i].instrument_id, []).append(outcome.fwd_ret_r)
    return by_symbol


def _pooled_median(
    kept: Sequence[Observation], idxs: Sequence[int], h: int
) -> float | None:
    values = [
        outcome.fwd_ret_r
        for i in idxs
        if (outcome := _outcome_at(kept[i], h)) is not None
        and outcome.fwd_ret_r is not None
    ]
    return stats_ci.median(values) if values else None


# --- blocks -----------------------------------------------------------------------------------------


def _histogram(values: Sequence[float], *, bins: int = _HISTOGRAM_BINS) -> dict[str, list[Any]] | None:
    if not values:
        return None
    low, high = min(values), max(values)
    if low == high:  # degenerate: the [v-1, v+1] range still takes 20 bins
        low, high = low - 1.0, high + 1.0
    width = (high - low) / bins
    edges = [low + i * width for i in range(bins)] + [high]
    counts = [0] * bins
    for value in values:
        slot = bisect.bisect_right(edges, value) - 1
        counts[min(max(slot, 0), bins - 1)] += 1
    return {"edges": edges, "counts": counts}


def _segment_block(
    kept: Sequence[Observation],
    idxs: Sequence[int],
    h: int,
    *,
    cluster_ids: Sequence[str],
    seed: int,
    resamples: int,
    level: float,
) -> dict[str, Any] | None:
    if not idxs:
        return None
    pct, r, mfe, mae = _values(kept, idxs, h)
    if not r:
        return None
    wins = sum(1 for v in pct if v > 0.0)
    wilson = stats_ci.wilson_ci(wins, len(r))
    boot = stats_ci.cluster_bootstrap_cis(
        {"r": r, "pct": pct},
        [cluster_ids[i] for i in idxs],
        seed=seed,
        resamples=resamples,
        level=level,
    )
    defined_mfe = [v for v in mfe if v is not None]
    defined_mae = [v for v in mae if v is not None]
    return {
        "n": len(r),
        "mean_pct": stats_ci.mean(pct),
        "median_pct": stats_ci.median(pct),
        "mean_r": stats_ci.mean(r),
        "median_r": stats_ci.median(r),
        "win_rate": wins / len(r),
        "win_ci": [wilson[0], wilson[1]] if wilson is not None else None,
        "mfe_med": stats_ci.median(defined_mfe) if defined_mfe else None,
        "mae_med": stats_ci.median(defined_mae) if defined_mae else None,
        "percentiles": {
            "pct": {f"p{p}": stats_ci.percentile(pct, p) for p in _PERCENTILES},
            "r": {f"p{p}": stats_ci.percentile(r, p) for p in _PERCENTILES},
        },
        "histogram_pct": _histogram(pct),
        "histogram_r": _histogram(r),
        "ci_median_r": [boot["r"]["median"][0], boot["r"]["median"][1]],
        "ci_mean_r": [boot["r"]["mean"][0], boot["r"]["mean"][1]],
        "ci_median_pct": [boot["pct"]["median"][0], boot["pct"]["median"][1]],
        "ci_mean_pct": [boot["pct"]["mean"][0], boot["pct"]["mean"][1]],
    }


def _cross_asset(
    by_symbol: Mapping[str, Sequence[float]], pooled_median: float | None
) -> dict[str, Any]:
    empty = {
        "n_symbols": 0,
        "pooled_median_r": None,
        "pooled_sign": None,
        "symbols_agree_share": None,
    }
    medians = {s: stats_ci.median(v) for s, v in by_symbol.items() if v}
    if not medians or pooled_median is None:
        return empty
    pooled_sign = (pooled_median > 0.0) - (pooled_median < 0.0)
    agree = sum(1 for m in medians.values() if ((m > 0.0) - (m < 0.0)) == pooled_sign)
    return {
        "n_symbols": len(medians),
        "pooled_median_r": pooled_median,
        "pooled_sign": pooled_sign,
        "symbols_agree_share": agree / len(medians),
    }


def _horizon_entry(
    kept: Sequence[Observation],
    segments: Sequence[str],
    h: int,
    *,
    cluster_ids: Sequence[str],
    seed: int,
    resamples: int,
    level: float,
) -> dict[str, Any]:
    idx_all = [i for i, o in enumerate(kept) if _outcome_at(o, h) is not None]
    idx_is = [i for i in idx_all if segments[i] == "IS"]
    idx_oos = [i for i in idx_all if segments[i] == "OOS"]
    pooled = _segment_block(
        kept, idx_all, h, cluster_ids=cluster_ids, seed=seed, resamples=resamples, level=level
    )
    counts: dict[str, int] = {}
    for i in idx_all:
        counts[kept[i].instrument_id] = counts.get(kept[i].instrument_id, 0) + 1
    base: dict[str, Any] = (
        dict(pooled)
        if pooled is not None
        else {
            "n": 0,
            "mean_pct": None,
            "median_pct": None,
            "mean_r": None,
            "median_r": None,
            "win_rate": None,
            "win_ci": None,
            "mfe_med": None,
            "mae_med": None,
            "percentiles": None,
            "histogram_pct": None,
            "histogram_r": None,
            "ci_median_r": None,
            "ci_mean_r": None,
            "ci_median_pct": None,
            "ci_mean_pct": None,
        }
    )
    return {
        "h": h,
        **base,
        "per_symbol": dict(sorted(counts.items())),
        "is": _segment_block(
            kept, idx_is, h, cluster_ids=cluster_ids, seed=seed, resamples=resamples, level=level
        ),
        "oos": _segment_block(
            kept, idx_oos, h, cluster_ids=cluster_ids, seed=seed, resamples=resamples, level=level
        ),
        "cross_asset": _cross_asset(
            _by_symbol_r(kept, idx_all, h), _pooled_median(kept, idx_all, h)
        ),
    }


# --- grade matrix + headline ---------------------------------------------------------------------------


def _median_or_none(values: Sequence[float]) -> float | None:
    return stats_ci.median(values) if values else None


def _grade_model(
    kept: Sequence[Observation],
    anchor_idx: Sequence[int],
    segments: Sequence[str],
    anchor: int,
    *,
    cluster_ids: Sequence[str],
    seed: int,
    resamples: int,
    level: float,
    thresholds: Mapping[str, Any],
    data_scope: str,
) -> dict[str, Any]:
    n_eff = len(set(cluster_ids))
    pooled_r = [
        outcome.fwd_ret_r
        for i in anchor_idx
        if (outcome := _outcome_at(kept[i], anchor)) is not None
        and outcome.fwd_ret_r is not None
    ]
    ci: tuple[float, float] | None = None
    if pooled_r:
        boot = stats_ci.cluster_bootstrap_cis(
            {"r": pooled_r}, cluster_ids, seed=seed, resamples=resamples, level=level
        )
        ci = boot["r"]["median"]
    pooled_med = _median_or_none(pooled_r)
    is_med = _median_or_none(
        [
            outcome.fwd_ret_r
            for i in anchor_idx
            if segments[i] == "IS"
            and (outcome := _outcome_at(kept[i], anchor)) is not None
            and outcome.fwd_ret_r is not None
        ]
    )
    oos_med = _median_or_none(
        [
            outcome.fwd_ret_r
            for i in anchor_idx
            if segments[i] == "OOS"
            and (outcome := _outcome_at(kept[i], anchor)) is not None
            and outcome.fwd_ret_r is not None
        ]
    )
    cross = _cross_asset(_by_symbol_r(kept, anchor_idx, anchor), pooled_med)
    grade, reason = evidence_grade(
        n_eff=n_eff,
        ci_median_r=ci,
        pooled_median_r=pooled_med,
        is_median_r=is_med,
        oos_median_r=oos_med,
        symbols_agree_share=cross["symbols_agree_share"],
        thresholds=thresholds,
        data_scope=data_scope,
    )
    pooled_sign = cross["pooled_sign"]
    if is_med is None or oos_med is None or pooled_sign is None:
        oos_agrees: bool | None = None
        consistent: bool | None = None
    else:
        signs = [((m > 0.0) - (m < 0.0)) for m in (is_med, oos_med)]
        oos_agrees = signs[0] != 0 and signs[1] == signs[0]
        consistent = all(s == pooled_sign for s in signs if s != 0)
    return {
        "n_eff": n_eff,
        "grade": grade,
        "grade_reason": reason,
        "ci_median_r": [ci[0], ci[1]] if ci is not None else None,
        "ci_excludes_zero": (ci is not None and (ci[0] > 0.0 or ci[1] < 0.0)),
        "oos_sign_agrees": oos_agrees,
        "symbols_agree_share": cross["symbols_agree_share"],
        "consistent_sign": consistent,
    }


def _headline_model(
    model_order: Sequence[str], matrix: Mapping[str, Mapping[str, Any]], data_scope: str
) -> str:
    """Lowest grade wins; request order breaks ties (first requested under HOLDOUT)."""
    if data_scope == HOLDOUT:
        return model_order[0]
    headline = model_order[0]
    for model in model_order[1:]:
        if grade_rank(str(matrix[model]["grade"])) > grade_rank(str(matrix[headline]["grade"])):
            headline = model
    return headline
