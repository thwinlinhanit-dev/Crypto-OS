# M7 design note — Validation suite (metrics, random baseline, three levels)

Companion to IMPLEMENTATION_SPEC_v1.1.1 Section 9 (validation suite),
Section 11 (API: `/v1/validation`, `/v1/baselines/random`), Section 12 (UI:
Validation dashboard) and Section 15 (milestone M7, T7.1–T7.5).

Decisions recorded in `docs/DECISIONS.md` as D-045..D-052; open questions
OQ-23.

> Scope discipline (PD-9): M7 builds T7.1–T7.5. Liquidity/funding tests are
> M10 (NOT_RUN before — 9.3); randomized signal timing and simple benchmark
> strategies are v1.1 (9.6). The random-entry baseline is the v1 baseline
> (9.2, the Pine BENCHMARK mode port).

---

## 0. Acceptance-criteria mapping

| AC (M7) | Where it lands |
| --- | --- |
| all tests return a status with numeric basis | `engine/validation/levels.py` returns `{status, basis}` for every test (9.3); `tests/test_validation_levels.py` |
| baseline distribution renders | `engine/validation/baseline.py` (9.2) + the dashboard's percentile view; `tests/test_random_baseline.py` |
| Level 1 failure marks Level 2/3 UNTRUSTED | `engine/validation/levels.py::trust` (9.3 last line); `test_level1_failure_untrusts` |
| every backtest/validation run stores provenance (9.5) | migration 0007 `validation.run` + `engine/validation/persist.py`; `test_validation_provenance` |

---

## 1. Engine layout

```
engine/validation/
  metrics.py   9.1: net return, CAGR, PF, expectancy, win rate, payoff,
                Sharpe, Sortino, maxDD, Calmar, exposure, turnover,
                avg/median R, top-1/5/10 share, longest losing streak
  baseline.py  9.2: random-entry seeds 1..N (default 1000), same exits/
                sizing/costs/fill model; p calibrated so mean trade count
                matches the strategy within +/-10%
  levels.py    9.3: Level 1 (implementation), Level 2 (statistical),
                Level 3 (economic) tests with PASS|WARN|FAIL|INSUFFICIENT|
                NOT_RUN + numeric basis
  status.py    9.4: overall status is a LIST, never a composite
  persist.py   migration 0007 writes; re-run reproduces identical numbers
```

## 2. Metrics (9.1, verbatim list)

`compute_metrics(trades, equity_curve, *, annualization)` returns every
metric the spec names: net return, CAGR, profit factor, expectancy (R), win
rate, avg win/loss, payoff ratio, Sharpe, Sortino, max drawdown, Calmar,
exposure, turnover, trade count, avg/median R, top-1/5/10 trade share,
longest losing streak. **Per-symbol AND portfolio-level** (shared equity);
per-symbol Sharpe is never the headline (9.1).

- Sharpe/Sortino annualize with the data's bar frequency (D-045: the
  caller passes `periods_per_year` from the timeframe; the engine never
  guesses).
- Profit factor = gross wins / gross losses; `inf` when no losses is
  reported as a large finite value plus a `gross_loss == 0` flag (D-046:
  never `Infinity` in stored JSON — it breaks the 9.5 byte-identical rule).
- Top-N share and longest losing streak come from the closed-trade list
  only (open positions excluded, E1).

## 3. Random-entry baseline (9.2)

Port of the Pine BENCHMARK mode: seeds 1..N (default 1000 from
`config/validation.yaml`); at each flat bar enter long with probability `p`
using the SAME exits, sizing, costs, fill model and data_scope. Calibrate
`p` per universe so the mean trade count matches the strategy within
±10% (binary search over `p`, capped at N iterations).

- Pine's RNG is not reproducible: **distributions are compared, not bars**
  (9.2). The engine's own seeded `random.Random(seed)` is deterministic for
  the engine side; the strategy-vs-baseline verdict is a percentile
  comparison (PF >= P95 PASS, P80–P95 WARN, <P80 FAIL).
- `p` calibration and the seed list are stored with the run (9.5).

## 4. Three levels (9.3)

Every test returns `{status, basis}` where status ∈ PASS | WARN | FAIL |
INSUFFICIENT | NOT_RUN and `basis` carries the numbers the verdict used
(threshold, observed value, n). Thresholds live in
`config/validation.yaml` (changes need a DECISIONS entry, 9.3).

- **Level 1 — implementation validity**: TradingView parity (reuses the
  layer-1/layer-2 status vocabulary, D-044; N/A only when no reference
  export exists), truncation invariance (the CI gate result),
  deterministic replay (re-run identical), accounting invariants (equity
  closes, stops only tighten, no unprotected position when
  `stop.kind != NONE`).
- **Level 2 — statistical validity**: Edge (cluster-bootstrap CI of
  expectancy R excludes 0; <30 trades INSUFFICIENT), OOS (same sign and
  >=50% of IS), Walk-forward (rolling 3y/1y windows, >=60% positive),
  clustering sensitivity, multiple testing (informational banner),
  random baseline, param stability, trade dependency, concentration,
  regime split.
- **Level 3 — economic validity**: cost stress (2x fee+slippage), delay
  stress (+1 bar), gap risk, Monte Carlo (seeded 5000), liquidity/funding
  (NOT_RUN before M10).
- **Trust rule (9.3 last line)**: any Level 1 FAIL or NOT_RUN (other than
  N/A parity) labels every Level 2/3 result UNTRUSTED (D-047: the
  UNTRUSTED label rides on the run's `trusted` flag, computed once).

## 5. Status mapping (9.4)

The overall status is a **list, never a composite score**: kill criterion
met -> REJECTED; no FAIL and Edge PASS -> PROMISING; VALIDATED requires all
applicable tests PASS (no FAIL/INSUFFICIENT/NOT_RUN), pooled trades >=100,
Level 1 trusted, a frozen experiment, and the holdout opened once and
passed. Validation language follows the 8.3 wording rules (the M5
`EvidenceGradeBadge` contract).

## 6. API (spec 11)

```
POST   /v1/validation        (async; requires backtest_id, kinds[])
GET    /v1/validation/{id}   (one + status)
POST   /v1/baselines/random  (async; requires backtest_id, seeds)
GET    /v1/baselines/random/{id}
```

Fail-closed: unknown backtest 404, unknown kind 422, `RANDOM_BASELINE`
entry mode still 422 (the baseline is a validation run, not a strategy
entry — 7.6 vs 9.2 are different things), non-holdout scope required for
Level 2/3 (9.3: all Level 2/3 tests run in DEVELOPMENT).

## 7. Validation dashboard (T7.4, spec UI)

`/validation`: run picker, the three levels as expandable panels with the
status word + numeric basis (the state word is always the text; colour
never alone — the M2 badge contract), baseline distribution render (PF /
return / expectancy / maxDD percentiles with the strategy percentile
marked), and the overall status list (9.4).

## 8. Out of scope (parked)

- Liquidity/funding tests — M10 (NOT_RUN before).
- Randomized signal timing and simple benchmark strategies — v1.1 (9.6).
- Holdout flow end-to-end (T7.5) ships when the holdout AC has an owner
  ruling (OQ-19 is still open; the API gate exists since M5).

## 9. Test plan (tests first)

| Suite | Covers |
| --- | --- |
| `tests/test_validation_metrics.py` | every 9.1 metric on hand-computed fixtures; per-symbol AND portfolio; gross-loss-zero handling |
| `tests/test_random_baseline.py` | seeded determinism; p-calibration within ±10%; percentile verdicts |
| `tests/test_validation_levels.py` | each level's PASS/WARN/FAIL/INSUFFICIENT/NOT_RUN + basis; Level 1 failure -> UNTRUSTED |
| `tests/test_validation_status.py` | 9.4 list mapping |
| `tests/test_validation_api.py` | runs + provenance (9.5), re-run reproduces identical numbers |

## 10. Decisions to record

| ID | Decision |
| --- | --- |
| D-045 | Sharpe/Sortino annualization uses the timeframe's periods_per_year, supplied by the caller |
| D-046 | Profit factor with zero losses reports a finite cap + a flag, never `Infinity` |
| D-047 | The UNTRUSTED label rides on the run's `trusted` flag (computed once from Level 1) |
| D-048 | Baseline seeds and calibrated p are stored with the run (9.5) |
| D-049 | Open positions are excluded from closed-trade metrics (E1) |
| D-050 | Validation runs are append-only with the 0001-style locked flag |
| D-051 | Multiple-testing remains informational (WARN when selection_method != PRE_SPECIFIED) |
| D-052 | The overall status is serialized as a list object, never a single string |

Open questions for the owner: **OQ-23** (holdout flow end-to-end, T7.5 —
depends on the OQ-19 reading of "once per hypothesis").
