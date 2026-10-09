"""T5.2b - pure stats computation (spec 8.2-8.3; design note specs/design/m5_stats.md §3).

Covers ``engine/stats/{ci,clusters,grade,compute}.py`` without any I/O:

- hand-computed means/medians/percentiles/histogram/Wilson on a fixed 5-observation fixture;
- Wilson against an independently written closed form (+ degenerate n=0);
- percentile helper (type-7 linear interpolation between closest ranks);
- seeded cluster bootstrap: determinism (same seed twice -> identical CI),
  seed sensitivity (different seed -> different mean CI, documented), input
  validation, CI endpoints ordered;
- cluster partitions for all five models (CUSTOM -> 422) including n_eff;
- evidence-grade ladder A-D (direct scalar tests + end-to-end through
  compute_stats), tier boundaries, sensitivity (lowest wins, flag set,
  request order breaks ties), HOLDOUT grade null;
- compute_stats integration: degenerate histogram, win-rate ties at 0,
  incomplete horizons excluded, cross-asset share, per-horizon shape,
  anchor graded even when outside the requested horizon list, n_raw echo,
  boundaries/multiple-testing/filter echo, JSON-serializability,
  determinism, n_eff <= n_raw enforcement, grade monotonicity.

Conventions pinned here (spec 8.2-8.3 + D-027/D-028/D-031/D-032):

- ``win_rate`` = share of ``fwd_ret_pct > 0`` (ties at 0 are not wins);
- percentiles are type-7; histogram is 20 equal-width bins, degenerate
  ``[v-1, v+1]`` when min == max (both metrics shipped);
- Wilson 95% uses the fixed z from the design note; the cluster bootstrap
  resamples clusters with replacement (5000 resamples at the spec default),
  percentile interval [p2.5, p97.5], ``random.Random(seed)``;
- grade inputs are evaluated at the anchor horizon h=10 on the kept
  in-scope observations whether or not 10 is in the requested horizons;
- headline model = lowest grade, request order breaks ties; every other
  interval is computed under it (D-032).
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from statistics import NormalDist, fmean
from typing import Any

import pytest

from engine.config.validation import load_evidence_grade
from engine.errors import ValidationError
from engine.events.outcomes import HorizonOutcome
from engine.scopes import DEVELOPMENT, HOLDOUT, ScopeBoundaries
from engine.stats import ci as stats_ci
from engine.stats.clusters import assign_clusters
from engine.stats.compute import Observation, compute_stats
from engine.stats.grade import HOLDOUT_GRADE_REASON, evidence_grade

DAY_MS = 86_400_000
S_START = 1_704_067_200_000  # 2024-01-01T00:00:00Z (a Monday)
OOS_START = S_START + 100 * DAY_MS
HOLDOUT_START = S_START + 143 * DAY_MS
S_END = S_START + 180 * DAY_MS

THRESHOLDS: dict[str, Any] = load_evidence_grade()

FILTER: dict[str, Any] = {
    "detector": "RVOL_CLIMAX",
    "event_type": "CAPIT",
    "timeframe": "1D",
}


def _bounds() -> ScopeBoundaries:
    return ScopeBoundaries(
        family="test_family",
        universe=("BINANCE:BTCUSDT", "BINANCE:ETHUSDT"),
        timeframes=("1D",),
        s_start=S_START,
        s_end=S_END,
        oos_start=OOS_START,
        holdout_start=HOLDOUT_START,
        span_days=180,
        frozen_at="2026-10-09T00:00:00Z",
        frozen_by="tests",
    )


def _ho(
    h: int,
    pct: float,
    r: float,
    mfe: float = 1.0,
    mae: float = -0.5,
) -> HorizonOutcome:
    return HorizonOutcome(
        horizon=h,
        complete=True,
        fwd_ret_pct=pct,
        fwd_ret_r=r,
        mfe_r=mfe,
        mae_r=mae,
        bars_to_mfe=1,
        bars_to_mae=1,
        hit_invalidation=False,
    )


def _incomplete(h: int) -> HorizonOutcome:
    return HorizonOutcome(horizon=h, complete=False)


def _obs(
    event_id: str,
    instrument: str,
    origin_ms: int,
    outcomes: dict[int, HorizonOutcome],
    event_type: str = "CAPIT",
) -> Observation:
    return Observation(
        event_id=event_id,
        instrument_id=instrument,
        event_type=event_type,
        origin_ms=origin_ms,
        horizons=dict(outcomes),
    )


def _const_obs(
    event_id: str,
    instrument: str,
    day: int,
    *,
    r: float = 0.5,
    pct: float = 1.0,
    horizons: tuple[int, ...] = (10,),
) -> Observation:
    return _obs(
        event_id, instrument, S_START + day * DAY_MS, {h: _ho(h, pct, r) for h in horizons}
    )


def _run(
    observations: list[Observation],
    *,
    models: list[str],
    horizons: tuple[int, ...] = (10,),
    resamples: int = 5000,
    scope: str = DEVELOPMENT,
    k: int = 3,
    n_raw: int | None = None,
    family_id: str | None = None,
    event_filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return compute_stats(
        observations,
        boundaries=_bounds(),
        data_scope=scope,
        event_filter=dict(event_filter) if event_filter is not None else dict(FILTER),
        cluster_models=list(models),
        seed=1,
        params={
            "horizons": list(horizons),
            "bootstrap_resamples": resamples,
            "market_event_k": k,
        },
        grade_thresholds=dict(THRESHOLDS),
        n_raw=len(observations) if n_raw is None else n_raw,
        bar_ms=DAY_MS,
        family_id=family_id,
    )


def _ms(year: int, month: int, day: int) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return int((datetime(year, month, day, tzinfo=UTC) - epoch).total_seconds() * 1000)


def _iso(ms: int) -> str:
    return (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=ms)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


# --- ci.percentile: type-7 linear interpolation ----------------------------------------


def test_percentile_type7_hand_computed() -> None:
    values = [-2.0, 0.0, 1.0, 3.0, 5.0]
    assert stats_ci.percentile(values, 10) == pytest.approx(-1.2)
    assert stats_ci.percentile(values, 25) == pytest.approx(0.0)
    assert stats_ci.percentile(values, 50) == pytest.approx(1.0)
    assert stats_ci.percentile(values, 75) == pytest.approx(3.0)
    assert stats_ci.percentile(values, 90) == pytest.approx(4.2)


def test_percentile_single_value_and_unsorted_input() -> None:
    assert stats_ci.percentile([4.0], 10) == pytest.approx(4.0)
    assert stats_ci.percentile([4.0], 90) == pytest.approx(4.0)
    assert stats_ci.percentile([3.0, 1.0, 2.0], 50) == pytest.approx(2.0)


def test_percentile_rejects_empty_and_out_of_range() -> None:
    with pytest.raises(ValidationError):
        stats_ci.percentile([], 50)
    with pytest.raises(ValidationError):
        stats_ci.percentile([1.0], -1)
    with pytest.raises(ValidationError):
        stats_ci.percentile([1.0], 101)


# --- ci.wilson_ci: closed form ------------------------------------------------------------


def _wilson_closed_form(wins: int, n: int) -> tuple[float, float]:
    """Independent Wilson score interval (spec 8.2), z from stdlib NormalDist."""
    z = NormalDist().inv_cdf(0.975)
    p = wins / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return (center - half, center + half)


def test_wilson_matches_closed_form() -> None:
    actual = stats_ci.wilson_ci(7, 10)
    assert actual is not None
    expected = _wilson_closed_form(7, 10)
    assert actual[0] == pytest.approx(expected[0])
    assert actual[1] == pytest.approx(expected[1])
    assert 0.0 <= actual[0] <= 0.7 <= actual[1] <= 1.0


def test_wilson_edge_cases() -> None:
    assert stats_ci.wilson_ci(0, 5) is not None
    lo, hi = stats_ci.wilson_ci(0, 5) or (None, None)
    assert lo is not None and hi is not None
    assert 0.0 <= lo <= hi <= 1.0
    assert stats_ci.wilson_ci(5, 5) is not None
    assert stats_ci.wilson_ci(0, 0) is None
    with pytest.raises(ValidationError):
        stats_ci.wilson_ci(-1, 5)
    with pytest.raises(ValidationError):
        stats_ci.wilson_ci(6, 5)


# --- ci.cluster_bootstrap_cis ---------------------------------------------------------------


def test_bootstrap_deterministic_same_seed() -> None:
    values = [0.5, -1.0, 1.5, 0.0, 2.5]
    clusters = ["a", "b", "c", "d", "e"]
    first = stats_ci.cluster_bootstrap_cis(
        {"r": values}, clusters, seed=1, resamples=5000, level=0.95
    )
    second = stats_ci.cluster_bootstrap_cis(
        {"r": values}, clusters, seed=1, resamples=5000, level=0.95
    )
    assert first == second
    assert set(first["r"]) == {"mean", "median"}
    lo, hi = first["r"]["median"]
    assert lo <= hi


def test_bootstrap_different_seed_different_mean_ci() -> None:
    """Documented property: the seed drives the draws, so a new seed moves the
    (continuous) mean CI. Exact endpoints are never pinned - only determinism."""
    values = [0.5, -1.0, 1.5, 0.0, 2.5]
    clusters = ["a", "b", "c", "d", "e"]
    one = stats_ci.cluster_bootstrap_cis(
        {"r": values}, clusters, seed=1, resamples=5000, level=0.95
    )
    two = stats_ci.cluster_bootstrap_cis(
        {"r": values}, clusters, seed=2, resamples=5000, level=0.95
    )
    assert one["r"]["mean"] != two["r"]["mean"]


def test_bootstrap_degenerate_single_cluster() -> None:
    values = [0.5, 0.5, 0.5]
    out = stats_ci.cluster_bootstrap_cis(
        {"r": values}, ["only", "only", "only"], seed=1, resamples=100, level=0.95
    )
    assert out["r"]["median"] == (0.5, 0.5)
    assert out["r"]["mean"] == (0.5, 0.5)


def test_bootstrap_rejects_bad_inputs() -> None:
    with pytest.raises(ValidationError):
        stats_ci.cluster_bootstrap_cis({}, ["a"], seed=1, resamples=100, level=0.95)
    with pytest.raises(ValidationError):
        stats_ci.cluster_bootstrap_cis(
            {"r": []}, [], seed=1, resamples=100, level=0.95
        )
    with pytest.raises(ValidationError):
        stats_ci.cluster_bootstrap_cis(
            {"r": [1.0, 2.0]}, ["a"], seed=1, resamples=100, level=0.95
        )
    with pytest.raises(ValidationError):
        stats_ci.cluster_bootstrap_cis(
            {"r": [1.0]}, ["a"], seed=1, resamples=0, level=0.95
        )
    with pytest.raises(ValidationError):
        stats_ci.cluster_bootstrap_cis(
            {"r": [1.0]}, ["a"], seed=1, resamples=100, level=1.0
        )


# --- clusters.assign_clusters -----------------------------------------------------------------


def _gap_obs() -> list[Observation]:
    return [
        _obs("e1", "BTC", S_START + 0 * DAY_MS, {10: _ho(10, 1.0, 0.5)}),
        _obs("e2", "BTC", S_START + 1 * DAY_MS, {10: _ho(10, 1.0, 0.5)}),
        _obs("e3", "BTC", S_START + 30 * DAY_MS, {10: _ho(10, 1.0, 0.5)}),
    ]


def test_symbol_gap_splits_on_horizon_gap() -> None:
    ids = assign_clusters(_gap_obs(), model="SYMBOL_GAP", horizon_h=10, bar_ms=DAY_MS)
    assert ids[0] == ids[1] != ids[2]


def test_symbol_gap_exact_h_starts_new_cluster() -> None:
    obs = [
        _obs("e1", "BTC", S_START, {10: _ho(10, 1.0, 0.5)}),
        _obs("e2", "BTC", S_START + 10 * DAY_MS, {10: _ho(10, 1.0, 0.5)}),
    ]
    ids = assign_clusters(obs, model="SYMBOL_GAP", horizon_h=10, bar_ms=DAY_MS)
    assert ids[0] != ids[1]


def test_symbol_gap_never_spans_symbols() -> None:
    obs = [
        _obs("e1", "BTC", S_START, {10: _ho(10, 1.0, 0.5)}),
        _obs("e2", "ETH", S_START, {10: _ho(10, 1.0, 0.5)}),
    ]
    ids = assign_clusters(obs, model="SYMBOL_GAP", horizon_h=10, bar_ms=DAY_MS)
    assert ids[0] != ids[1]


def test_calendar_day_groups_across_symbols() -> None:
    obs = [
        _obs("e1", "BTC", S_START + 5 * DAY_MS, {10: _ho(10, 1.0, 0.5)}),
        _obs("e2", "ETH", S_START + 5 * DAY_MS, {10: _ho(10, 1.0, 0.5)}),
        _obs("e3", "BTC", S_START + 6 * DAY_MS, {10: _ho(10, 1.0, 0.5)}),
    ]
    ids = assign_clusters(obs, model="CALENDAR_DAY", horizon_h=10, bar_ms=DAY_MS)
    assert ids[0] == ids[1] != ids[2]


def test_calendar_week_groups_iso_weeks() -> None:
    # 2026-01-05 (Mon) and 2026-01-09 (Fri) share ISO week 2; 2026-01-12 starts week 3.
    obs = [
        _obs("e1", "BTC", _ms(2026, 1, 5), {10: _ho(10, 1.0, 0.5)}),
        _obs("e2", "ETH", _ms(2026, 1, 9), {10: _ho(10, 1.0, 0.5)}),
        _obs("e3", "BTC", _ms(2026, 1, 12), {10: _ho(10, 1.0, 0.5)}),
    ]
    ids = assign_clusters(obs, model="CALENDAR_WEEK", horizon_h=10, bar_ms=DAY_MS)
    assert ids[0] == ids[1] != ids[2]
    assert len(set(ids)) == 2


def _market_event_obs() -> list[Observation]:
    obs: list[Observation] = []
    day20 = S_START + 20 * DAY_MS
    # Flagged bar (3 symbols >= k=3), adjacent flagged bar merges.
    for day, symbols in ((20, ("A", "B", "C")), (21, ("A", "B", "C")), (23, ("A", "B", "C"))):
        for sym in symbols:
            obs.append(
                _obs(
                    f"capit-{day}-{sym}",
                    sym,
                    S_START + day * DAY_MS,
                    {10: _ho(10, 1.0, 0.5)},
                    event_type="CAPIT",
                )
            )
    # Unflagged bar (2 < k): each observation is its own cluster.
    for sym in ("A", "B"):
        obs.append(_obs(f"solo-22-{sym}", sym, S_START + 22 * DAY_MS, {10: _ho(10, 1.0, 0.5)}))
    # Different type on a flagged bar: not part of the CAPIT cluster.
    obs.append(
        _obs("top-20-A", "A", day20, {10: _ho(10, 1.0, 0.5)}, event_type="TOP")
    )
    return obs


def test_market_event_flags_merges_and_singles() -> None:
    obs = _market_event_obs()
    ids = assign_clusters(obs, model="MARKET_EVENT", horizon_h=10, bar_ms=DAY_MS, market_event_k=3)
    merged = ids[0:6]  # day-20 and day-21 CAPIT bars (adjacent -> one cluster)
    assert len(set(merged)) == 1
    day23 = ids[6:9]  # flagged but not adjacent -> its own cluster
    assert len(set(day23)) == 1
    assert day23[0] != merged[0]
    assert ids[9] != ids[10]  # unflagged bar: own clusters
    assert ids[9] != merged[0] and ids[10] != merged[0]
    assert ids[11] != merged[0]  # TOP on a CAPIT bar is not in the CAPIT cluster
    assert len(set(ids)) == 5


def test_market_event_requires_k() -> None:
    with pytest.raises(ValidationError):
        assign_clusters(_gap_obs(), model="MARKET_EVENT", horizon_h=10, bar_ms=DAY_MS)


def test_custom_and_unknown_models_rejected() -> None:
    with pytest.raises(ValidationError, match="CUSTOM cluster model requires an experiment"):
        assign_clusters(_gap_obs(), model="CUSTOM", horizon_h=10, bar_ms=DAY_MS)
    with pytest.raises(ValidationError):
        assign_clusters(_gap_obs(), model="NOPE", horizon_h=10, bar_ms=DAY_MS)


def test_assign_clusters_empty() -> None:
    assert (
        assign_clusters([], model="SYMBOL_GAP", horizon_h=10, bar_ms=DAY_MS) == []
    )


# --- grade.evidence_grade: the A-D ladder ---------------------------------------------------------------


def _grade(**over: Any) -> tuple[str | None, str | None]:
    base: dict[str, Any] = {
        "n_eff": 100,
        "ci_median_r": (0.1, 0.5),
        "pooled_median_r": 0.3,
        "is_median_r": 0.2,
        "oos_median_r": 0.4,
        "symbols_agree_share": 0.8,
        "thresholds": THRESHOLDS,
        "data_scope": DEVELOPMENT,
    }
    base.update(over)
    return evidence_grade(**base)


def test_grade_a_all_conditions() -> None:
    assert _grade()[0] == "A"


def test_grade_a_boundary_values() -> None:
    assert _grade(n_eff=100, symbols_agree_share=0.60)[0] == "A"
    assert _grade(n_eff=99)[0] == "B"
    assert _grade(symbols_agree_share=0.59)[0] == "B"


def test_grade_b_without_share_requirement() -> None:
    assert _grade(n_eff=50, symbols_agree_share=0.0)[0] == "B"
    assert _grade(n_eff=49)[0] == "D"


def test_grade_c_consistent_sign_ci_includes_zero() -> None:
    grade, _ = _grade(
        n_eff=40,
        ci_median_r=(-0.1, 0.5),
        pooled_median_r=0.2,
        is_median_r=0.2,
        oos_median_r=0.2,
    )
    assert grade == "C"
    assert _grade(n_eff=29, ci_median_r=(-0.1, 0.5))[0] == "D"


def test_grade_zero_edge_ci_includes_zero() -> None:
    """A CI touching zero ([0, x]) includes it: excludes-zero is strict."""
    grade, _ = _grade(
        n_eff=100,
        ci_median_r=(0.0, 0.5),
        pooled_median_r=0.2,
        is_median_r=0.2,
        oos_median_r=0.2,
    )
    assert grade == "C"


def test_grade_d_when_oos_disagrees_or_missing() -> None:
    assert _grade(oos_median_r=-0.4)[0] == "D"
    assert _grade(oos_median_r=0.0)[0] == "D"  # a zero median counts as disagreeing
    assert _grade(is_median_r=0.0, oos_median_r=0.0)[0] == "D"
    assert _grade(oos_median_r=None)[0] == "D"
    assert _grade(is_median_r=None)[0] == "D"


def test_grade_holdout_is_null_with_reason() -> None:
    grade, reason = _grade(data_scope=HOLDOUT)
    assert grade is None
    assert reason == HOLDOUT_GRADE_REASON


def test_grade_rejects_incomplete_thresholds() -> None:
    with pytest.raises(ValidationError):
        _grade(thresholds={})


# --- compute_stats: hand-computed fixture ----------------------------------------------------------------------


def _hand_computed_obs() -> list[Observation]:
    pct = [1.0, -2.0, 3.0, 0.0, 5.0]
    r = [0.5, -1.0, 1.5, 0.0, 2.5]
    mfe = [1.0, 0.5, 2.0, 0.0, 3.0]
    mae = [-0.5, -1.5, -0.2, -0.1, -1.0]
    return [
        _obs(f"e{i}", "BTC", S_START + (10 + i) * DAY_MS, {10: _ho(10, p, rv, mf, ma)})
        for i, (p, rv, mf, ma) in enumerate(zip(pct, r, mfe, mae, strict=True))
    ]


def test_hand_computed_pooled_stats() -> None:
    result = _run(_hand_computed_obs(), models=["CALENDAR_WEEK"])
    assert result["n_raw"] == 5
    (entry,) = result["horizons"]
    assert entry["h"] == 10
    assert entry["n"] == 5
    assert entry["per_symbol"] == {"BTC": 5}
    assert entry["mean_pct"] == pytest.approx(1.4)
    assert entry["median_pct"] == pytest.approx(1.0)
    assert entry["mean_r"] == pytest.approx(0.7)
    assert entry["median_r"] == pytest.approx(0.5)
    assert entry["win_rate"] == pytest.approx(0.6)  # the 0.0 tie is not a win
    assert entry["mfe_med"] == pytest.approx(1.0)
    assert entry["mae_med"] == pytest.approx(-0.5)
    assert entry["percentiles"]["pct"] == pytest.approx(
        {"p10": -1.2, "p25": 0.0, "p50": 1.0, "p75": 3.0, "p90": 4.2}
    )
    assert entry["percentiles"]["r"] == pytest.approx(
        {"p10": -0.6, "p25": 0.0, "p50": 0.5, "p75": 1.5, "p90": 2.1}
    )
    # Wilson against the independent closed form.
    expected_win = _wilson_closed_form(3, 5)
    assert entry["win_ci"][0] == pytest.approx(expected_win[0])
    assert entry["win_ci"][1] == pytest.approx(expected_win[1])
    # Histogram: 20 equal-width bins over [-2, 5]; independently expected bins.
    width = (5.0 - -2.0) / 20
    expected_counts = [0] * 20
    for v in (1.0, -2.0, 3.0, 0.0, 5.0):
        expected_counts[min(int((v - -2.0) // width), 19)] += 1
    assert entry["histogram_pct"]["edges"][0] == pytest.approx(-2.0)
    assert entry["histogram_pct"]["edges"][-1] == pytest.approx(5.0)
    assert len(entry["histogram_pct"]["edges"]) == 21
    assert entry["histogram_pct"]["counts"] == expected_counts
    assert sum(entry["histogram_pct"]["counts"]) == 5
    assert entry["histogram_r"]["counts"] is not None
    assert sum(entry["histogram_r"]["counts"]) == 5
    # Bootstrap CIs contain the point estimates (spec stats invariant).
    assert entry["ci_median_r"][0] <= entry["median_r"] <= entry["ci_median_r"][1]
    assert entry["ci_mean_r"][0] <= entry["mean_r"] <= entry["ci_mean_r"][1]
    assert entry["ci_median_pct"][0] <= entry["median_pct"] <= entry["ci_median_pct"][1]
    assert entry["ci_mean_pct"][0] <= entry["mean_pct"] <= entry["ci_mean_pct"][1]
    for key in ("ci_median_r", "ci_mean_r", "ci_median_pct", "ci_mean_pct"):
        assert entry[key][0] <= entry[key][1]
    # All five observations sit in IS: OOS block is null, grade is D (missing OOS).
    assert entry["is"] is not None and entry["is"]["n"] == 5
    assert entry["oos"] is None
    assert result["grade"] == "D"
    assert result["model_sensitive"] is False
    assert result["headline_model"] == "CALENDAR_WEEK"
    assert result["n_eff"] == 2  # days 10-13 share ISO week 2, day 14 is week 3
    assert result["cluster_models"]["CALENDAR_WEEK"]["n_eff"] == 2
    assert result["cross_asset"] == {
        "n_symbols": 1,
        "pooled_median_r": pytest.approx(0.5),
        "pooled_sign": 1,
        "symbols_agree_share": pytest.approx(1.0),
    }


def test_degenerate_histogram_single_value() -> None:
    obs = [_const_obs(f"e{i}", "BTC", 10 + i, r=2.0, pct=5.0) for i in range(4)]
    result = _run(obs, models=["CALENDAR_DAY"])
    (entry,) = result["horizons"]
    assert entry["histogram_pct"]["edges"][0] == pytest.approx(4.0)
    assert entry["histogram_pct"]["edges"][-1] == pytest.approx(6.0)
    assert len(entry["histogram_pct"]["edges"]) == 21
    assert sum(entry["histogram_pct"]["counts"]) == 4
    assert entry["histogram_pct"]["counts"][10] == 4


def test_incomplete_horizons_excluded_from_n() -> None:
    obs = [
        _obs("e1", "BTC", S_START + 10 * DAY_MS, {10: _ho(10, 1.0, 0.5)}),
        _obs("e2", "BTC", S_START + 11 * DAY_MS, {10: _ho(10, 2.0, 1.0)}),
        _obs("e3", "BTC", S_START + 12 * DAY_MS, {10: _incomplete(10)}),
        _obs("e4", "BTC", S_START + 13 * DAY_MS, {10: _ho(10, 3.0, 1.5)}),
        _obs(
            "e5",
            "BTC",
            S_START + 14 * DAY_MS,
            {10: HorizonOutcome(horizon=10, complete=True)},  # null values
        ),
    ]
    result = _run(obs, models=["CALENDAR_DAY"])
    (entry,) = result["horizons"]
    assert entry["n"] == 3
    assert entry["mean_r"] == pytest.approx(fmean([0.5, 1.0, 1.5]))


def test_cross_asset_share_two_symbols() -> None:
    obs = [
        _obs("b1", "BTC", S_START + 10 * DAY_MS, {10: _ho(10, 1.0, 1.0)}),
        _obs("b2", "BTC", S_START + 11 * DAY_MS, {10: _ho(10, 2.0, 2.0)}),
        _obs("b3", "BTC", S_START + 12 * DAY_MS, {10: _ho(10, 3.0, 3.0)}),
        _obs("x1", "ETH", S_START + 13 * DAY_MS, {10: _ho(10, -5.0, -5.0)}),
        _obs("x2", "ETH", S_START + 14 * DAY_MS, {10: _ho(10, -4.0, -4.0)}),
    ]
    result = _run(obs, models=["SYMBOL_GAP"])
    assert result["per_symbol"] == {"BTC": 3, "ETH": 2}
    assert result["cross_asset"]["n_symbols"] == 2
    assert result["cross_asset"]["pooled_median_r"] == pytest.approx(1.0)
    assert result["cross_asset"]["pooled_sign"] == 1
    assert result["cross_asset"]["symbols_agree_share"] == pytest.approx(0.5)
    assert result["cluster_models"]["SYMBOL_GAP"]["n_eff"] == 2
    assert result["cluster_models"]["SYMBOL_GAP"]["symbols_agree_share"] == pytest.approx(0.5)


def test_multiple_horizons_sorted_and_shaped() -> None:
    obs = [
        _obs(
            "e1",
            "BTC",
            S_START + 10 * DAY_MS,
            {1: _ho(1, 0.5, 0.2), 5: _ho(5, 1.0, 0.5), 10: _ho(10, 2.0, 1.0)},
        ),
        _obs(
            "e2",
            "BTC",
            S_START + 11 * DAY_MS,
            {1: _ho(1, -0.5, -0.2), 5: _ho(5, 0.0, 0.1), 10: _ho(10, 1.0, 0.4)},
        ),
    ]
    result = _run(obs, models=["CALENDAR_DAY"], horizons=(10, 1, 5))
    assert [e["h"] for e in result["horizons"]] == [1, 5, 10]
    by_h = {e["h"]: e for e in result["horizons"]}
    assert by_h[1]["n"] == 2
    assert by_h[1]["median_r"] == pytest.approx(0.0)  # type-7 median of [-0.2, 0.2]
    assert by_h[1]["win_rate"] == pytest.approx(0.5)
    assert by_h[5]["median_r"] == pytest.approx(0.3)
    assert set(by_h[10]) >= {
        "h",
        "n",
        "median_r",
        "ci_median_r",
        "win_rate",
        "win_ci",
        "is",
        "oos",
        "cross_asset",
        "histogram_pct",
        "histogram_r",
    }


# --- compute_stats: grade ladder end to end -------------------------------------------------------------------


def _grade_obs(
    is_days: list[int],
    oos_days: list[int],
    instruments: tuple[str, ...],
    r: float | list[float],
) -> list[Observation]:
    """Observations on distinct days; r is a constant or an alternating pattern."""
    obs: list[Observation] = []
    for idx, day in enumerate(is_days + oos_days):
        value = r if isinstance(r, float) else r[idx % len(r)]
        obs.append(_const_obs(f"e{idx}", instruments[idx % len(instruments)], day, r=value))
    return obs


def test_grade_a_end_to_end() -> None:
    obs = _grade_obs(
        list(range(0, 45)) + list(range(45, 70)),
        list(range(100, 115)) + list(range(115, 130)),
        ("BTC", "ETH"),
        0.5,
    )
    result = _run(obs, models=["CALENDAR_DAY"], resamples=500)
    assert result["n_raw"] == 100
    assert result["n_eff"] == 100
    assert result["grade"] == "A"
    assert result["model_sensitive"] is False
    matrix = result["cluster_models"]["CALENDAR_DAY"]
    assert matrix["grade"] == "A"
    assert matrix["ci_median_r"] == [pytest.approx(0.5), pytest.approx(0.5)]
    assert matrix["ci_excludes_zero"] is True
    assert matrix["oos_sign_agrees"] is True
    assert matrix["symbols_agree_share"] == pytest.approx(1.0)


def test_grade_b_end_to_end() -> None:
    obs = _grade_obs(
        list(range(0, 30)) + list(range(30, 40)),
        list(range(100, 110)) + list(range(110, 120)),
        ("BTC", "ETH"),
        0.5,
    )
    result = _run(obs, models=["CALENDAR_DAY"], resamples=500)
    assert result["n_eff"] == 60
    assert result["grade"] == "B"


def test_grade_c_end_to_end() -> None:
    obs = _grade_obs(list(range(0, 20)), list(range(100, 120)), ("BTC",), [1.0, -1.0])
    result = _run(obs, models=["CALENDAR_DAY"], resamples=500)
    assert result["n_eff"] == 40
    matrix = result["cluster_models"]["CALENDAR_DAY"]
    assert matrix["ci_excludes_zero"] is False
    assert result["grade"] == "C"


def test_grade_d_small_n_end_to_end() -> None:
    obs = _grade_obs(list(range(0, 10)), list(range(100, 110)), ("BTC",), 0.5)
    result = _run(obs, models=["CALENDAR_DAY"], resamples=500)
    assert result["n_eff"] == 20
    assert result["grade"] == "D"


def test_grade_monotone_in_sample_size() -> None:
    """Same strong-positive shape, shrinking samples: grades never improve."""
    rank = {"A": 0, "B": 1, "C": 2, "D": 3}
    datasets = [
        _grade_obs(list(range(0, 70)), list(range(100, 130)), ("BTC",), 0.5),  # 100
        _grade_obs(list(range(0, 40)), list(range(100, 120)), ("BTC",), 0.5),  # 60
        _grade_obs(list(range(0, 25)), list(range(100, 115)), ("BTC",), 0.5),  # 40
        _grade_obs(list(range(0, 10)), list(range(100, 110)), ("BTC",), 0.5),  # 20
    ]
    grades = [
        _run(obs, models=["CALENDAR_DAY"], resamples=500)["grade"] for obs in datasets
    ]
    assert grades[0] == "A"
    assert [rank[g] for g in grades] == sorted(rank[g] for g in grades)


# --- compute_stats: sensitivity, headline, holdout ------------------------------------------------------------------


def _sensitivity_obs() -> list[Observation]:
    """60 same-symbol observations on consecutive days: sixty CALENDAR_DAY
    clusters but two SYMBOL_GAP clusters (the 61-bar IS->OOS gap splits)."""
    return _grade_obs(list(range(0, 40)), list(range(100, 120)), ("BTC",), 0.5)


def test_model_sensitivity_lowest_wins() -> None:
    result = _run(_sensitivity_obs(), models=["CALENDAR_DAY", "SYMBOL_GAP"], resamples=500)
    matrix = result["cluster_models"]
    assert matrix["CALENDAR_DAY"]["grade"] == "B"
    assert matrix["CALENDAR_DAY"]["n_eff"] == 60
    assert matrix["SYMBOL_GAP"]["grade"] == "D"
    assert matrix["SYMBOL_GAP"]["n_eff"] == 2
    assert result["grade"] == "D"
    assert result["model_sensitive"] is True
    assert result["headline_model"] == "SYMBOL_GAP"
    assert result["n_eff"] == 2  # the headline model's n_eff rides with the grade


def test_model_tie_headline_is_first_requested() -> None:
    obs = _grade_obs(list(range(0, 10)), list(range(100, 110)), ("BTC",), 0.5)
    result = _run(obs, models=["CALENDAR_DAY", "SYMBOL_GAP"], resamples=500)
    assert result["cluster_models"]["CALENDAR_DAY"]["grade"] == "D"
    assert result["cluster_models"]["SYMBOL_GAP"]["grade"] == "D"
    assert result["grade"] == "D"
    assert result["model_sensitive"] is False
    assert result["headline_model"] == "CALENDAR_DAY"


def test_duplicate_models_deduplicated() -> None:
    result = _run(_hand_computed_obs(), models=["CALENDAR_DAY", "CALENDAR_DAY"])
    assert list(result["cluster_models"]) == ["CALENDAR_DAY"]


def test_holdout_carries_no_grade() -> None:
    obs = [_const_obs(f"h{i}", "BTC", 150 + i) for i in range(10)]
    result = _run(obs, models=["CALENDAR_DAY"], scope=HOLDOUT, resamples=500)
    assert result["data_scope"] == HOLDOUT
    assert result["grade"] is None
    assert result["grade_reason"] == HOLDOUT_GRADE_REASON
    assert result["model_sensitive"] is None
    assert result["n_eff"] == 10
    assert result["cluster_models"]["CALENDAR_DAY"]["grade"] is None
    assert result["cluster_models"]["CALENDAR_DAY"]["n_eff"] == 10
    assert result["cluster_models"]["CALENDAR_DAY"]["ci_median_r"] is not None
    (entry,) = result["horizons"]
    assert entry["n"] == 10
    assert entry["is"] is None
    assert entry["oos"] is None


def test_anchor_graded_outside_requested_horizons() -> None:
    days = list(range(0, 20)) + list(range(100, 120))
    obs = [
        _const_obs(f"e{i}", "BTC", day, r=1.0 if i % 2 == 0 else -1.0, horizons=(10, 50))
        for i, day in enumerate(days)
    ]
    assert len(obs) == 40
    result = _run(obs, models=["CALENDAR_DAY"], horizons=(50,), resamples=500)
    assert [e["h"] for e in result["horizons"]] == [50]
    assert result["horizons"][0]["n"] == 40
    # The grade still ran at the anchor even though 10 was never requested.
    assert result["cluster_models"]["CALENDAR_DAY"]["ci_median_r"] is not None
    assert result["grade"] == "C"


def test_market_event_n_eff_end_to_end() -> None:
    obs: list[Observation] = []
    for day in (20, 120):
        for sym in ("A", "B", "C"):
            obs.append(_const_obs(f"m-{day}-{sym}", sym, day))
    result = _run(obs, models=["MARKET_EVENT"], resamples=500)
    assert result["cluster_models"]["MARKET_EVENT"]["n_eff"] == 2
    assert result["horizons"][0]["n"] == 6


# --- compute_stats: envelope, determinism, validation ---------------------------------------------------------------------


def test_result_envelope_and_json_serializable() -> None:
    result = _run(_hand_computed_obs(), models=["CALENDAR_WEEK"], family_id="fam-1")
    assert result["filter"] == FILTER
    assert result["data_scope"] == DEVELOPMENT
    assert result["boundaries"] == {
        "oos_start": _iso(OOS_START),
        "holdout_start": _iso(HOLDOUT_START),
    }
    assert result["multiple_testing"] == {
        "family_id": "fam-1",
        "variant_count": 1,
        "selection_method": "PRE_SPECIFIED",
    }
    assert result["grade_reason"] is None
    assert result["n_eff"] <= result["n_raw"]
    json.dumps(result)  # every value is a plain JSON scalar/container


def test_compute_stats_deterministic() -> None:
    obs = _hand_computed_obs()
    assert _run(obs, models=["CALENDAR_WEEK"]) == _run(obs, models=["CALENDAR_WEEK"])


def test_empty_observations_shape() -> None:
    result = _run([], models=["CALENDAR_DAY"], n_raw=0)
    assert result["n_raw"] == 0
    assert result["n_eff"] == 0
    assert result["grade"] == "D"
    assert result["per_symbol"] == {}
    (entry,) = result["horizons"]
    assert entry["n"] == 0
    assert entry["median_r"] is None
    assert entry["is"] is None and entry["oos"] is None
    assert result["cross_asset"]["n_symbols"] == 0


def test_compute_stats_rejects_bad_inputs() -> None:
    obs = _hand_computed_obs()
    with pytest.raises(ValidationError):
        _run(obs, models=[])
    with pytest.raises(ValidationError):
        _run(obs, models=["NOPE"])
    with pytest.raises(ValidationError):
        _run(obs, models=["CUSTOM"])
    with pytest.raises(ValidationError):
        compute_stats(
            obs,
            boundaries=_bounds(),
            data_scope="SOMETHING",
            event_filter=dict(FILTER),
            cluster_models=["CALENDAR_DAY"],
            seed=1,
            params={"horizons": [10], "bootstrap_resamples": 100, "market_event_k": 3},
            grade_thresholds=dict(THRESHOLDS),
            n_raw=len(obs),
            bar_ms=DAY_MS,
        )
    with pytest.raises(ValidationError):
        _run(obs, models=["MARKET_EVENT"], k=None)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _run(obs, models=["CALENDAR_DAY"], n_raw=1)  # n_eff (2) > n_raw
