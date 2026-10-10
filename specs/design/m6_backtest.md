# M6 design note — Backtest simulator, strategy CRUD, portfolio mode, Strategy Lab, parity layer 2

Companion to IMPLEMENTATION_SPEC_v1.1.1 Section 7 (strategy definition &
simulator), Section 11 (API: `/v1/strategies`, `/v1/backtests`), Section 12
(UI: Strategy Lab + trade overlay), Section 14.2 (parity layer 2) and
Section 15 (milestone M6, T6.1–T6.5).

Decisions recorded in `docs/DECISIONS.md` as D-038..D-044; open questions
OQ-22.

> Scope discipline (PD-9): M6 builds T6b.1–T6.5. Short-side strategies
> remain disabled (R11: spot shorts forbidden until M10 — `side` is parsed
> and validated but `SHORT_ONLY`/`BOTH` return 422 until M10). Funding and
> liquidation modeling are M10 (7.7). Random-baseline mode (`entry.mode =
> "RANDOM_BASELINE"`, 7.6) is the M7 validation surface (9.2) and is parked
> with a typed placeholder.

---

## 0. Acceptance-criteria mapping

| AC (M6) | Where it lands |
| --- | --- |
| parity pass (zero ENGINE_BUG/UNKNOWN) on the owner's TradingView export | `engine/parity/layer2.py` + `scripts/parity.py --layer 2`; `tests/test_backtest_parity_l2.py`; gate in `make parity` (D-019 already) |
| reproducibility test passes | `tests/test_backtest_repro.py`: same inputs ⇒ byte-identical trade JSON (canonical JSON hash, same form as D-037) |
| no signal disappears (trade, SKIPPED or NOT_FILLED with reason) | `engine/strategy/simulator.py` outcome vocabulary; `tests/test_backtest_edge_cases.py` E1-E6 |
| every backtest belongs to an experiment | `POST /v1/backtests` requires a registered `experiment_id` (422 otherwise), fail-closed per PD-12; `tests/test_backtests_api.py` |

---

## 1. Engine layout (T6.1, spec section 7)

```
engine/strategy/
  models.py     StrategyDefinition + nested models (7.1, verbatim transcription)
  hash.py       strategy version hash (canonical JSON, D-037 form)
  sizing.py     7.2 risk distance + quantity
  fills.py      7.3 fill models + time fields (PARITY / REALISTIC_OHLC)
  simulator.py  7.4 per-bar order of operations + 7.5 trade record
  jobs.py       async run behind /v1/backtests (M1 job/worker seam)
  baseline.py   entry.mode=RANDOM_BASELINE stub -> NOT_IMPLEMENTED (7.6, M7)
```

- **StrategyDefinition** is the 7.1 JSON transcribed field-for-field into
  Pydantic models with the spec's own vocabulary enums (`stop.kind`,
  `trail.kind`, `side`, fill/fee/slippage model kinds). Unknown kinds 422
  (PD-4/PD-11); reserved fee/slippage kinds (SPREAD_BASED,
  VOLATILITY_BASED, DEPTH_BASED, CUSTOM) raise NOT_IMPLEMENTED per 7.1.
- **Edits create a new version** (7.1): `POST /v1/strategies/{id}/versions`
  appends; `strategy_id@version` is content-addressed (`hash.py`), so a
  stored backtest can always name the exact definition it used (PD-6).
- The simulator is **pure** (`simulate(strategy, bars, events, ...)`): it
  never touches the database or the network. Persistence and job plumbing
  live in `jobs.py` + the M1 seam, so the same function runs sync in tests
  and async in the worker (D-024 precedent).

## 2. Fill models and time fields (7.3)

Every trade carries: `signal_bar_index`, `signal_time` (close_time of the
signal bar), `decision_time`, `fill_bar_index`, `fill_time`,
`exit_signal_time`, `exit_decision_time`, `exit_fill_time` (D-038: all
timestamps UTC ISO-8601, `close_time` exclusive upper bound per D-008).

- **PARITY** (verification only, D-039): entries and discretionary exits
  fill at the CLOSE of the signal/decision bar (`entry_price = close_s`,
  `fill_time = signal_time`; TradingView `process_orders_on_close=true`).
  Same-bar stop+target replicates the broker-emulator path heuristic
  (`|high-open| < |low-open|` ⇒ O→H→L→C else O→L→H→C). Runs must use the
  Pine settings (fee 0.10% per side, slippage 0, `delay_bars = 0`; a
  non-PARITY-legal config is 422).
- **REALISTIC_OHLC** (default for research claims, R8): fill bar =
  `signal bar + 1 + delay_bars`, fill at that bar's OPEN with adverse
  slippage (long: `open + slippage`, short: `open - slippage`); qty
  recomputed at the actual fill price; the stop is fixed at signal time
  (`risk_dist_actual = fill - stop`). Same-bar stop+target: **stop first**
  (7.3). Gaps: stop fills at OPEN when the bar opens through it.
- `REALISTIC_INTRABAR` is reserved and raises NOT_IMPLEMENTED (R8) — no
  approximation invented (PD-4).

## 3. Execution edge cases (7.3.1 — no signal disappears)

| Case | Recorded outcome |
| --- | --- |
| E1 no next bar / end of data | `NOT_FILLED/NO_NEXT_BAR`; open positions marked to market, reported OPEN, excluded from closed-trade stats (`close_open_at_end` false default), listed separately |
| E2 fill bar missing/excluded | `SKIPPED/ENTRY_DATA_GAP` — never filled later |
| E3 fill at/beyond stop | `SKIPPED/GAP_THROUGH_STOP` (equality included); `SKIPPED/STOP_TOUCHED_BEFORE_FILL` when `delay_bars > 0` touched the stop |
| E4 gap while position open | first valid bar tests stop vs OPEN first (gap rule), then OHLC; `data_gap_during_hold = true`; gap > `max_hold_gap_bars` closes at that open with `DATA_GAP`; exclusion applies only to the interval (4.6) |
| E5 same-bar stop+target | per fill model (§2) |
| E6 report surface | every result shows fill/fee/slippage models, `delay_bars`, `close_open_at_end`, flagged-trade count, skipped counts by reason |

## 4. Per-bar order (7.4, transcribed)

1. features for bar t; 2. exit fills for resting orders; 3. management
   (breakeven, trailing stop per `trail.kind`, ratchet only tightens,
   `stop_keep`); 4. discretionary exits at close t (time stop counts from
   the **signal** bar — §17 trap); 5. entries (flat after 2–4; same-bar
   re-entry allowed — a stop hit at t leaves the account flat at close of
   t); 6. mark-to-market equity.

Trailing-stop kinds (ATR_CHANDELIER, PERCENT, DONCHIAN, PIVOT_SWING,
PROFIT_GIVEBACK, EMA_CLOSE) follow 7.4 verbatim, including the
`clamp = close - tick` guard and the PIVOT_SWING "last confirmed pivot low
with pivot bar > signal bar" rule (D-016 strict-tie semantics apply).

## 5. Portfolio mode (T6.3)

One shared equity; instruments processed in a fixed deterministic order
each bar (`instrument_id` sort, D-040); constraints
`max_concurrent_positions`, `max_portfolio_heat`, per-asset cap; sizing
uses shared equity. Single-symbol results are still produced but flagged
`low-N` (R9).

## 6. API (spec 11)

```
POST   /v1/strategies                        (create; returns strategy_id + version)
GET    /v1/strategies                        (list)
GET    /v1/strategies/{id}                   (latest version)
POST   /v1/strategies/{id}/versions          (new version from an edited body)
POST   /v1/backtests                         (async job; requires experiment_id,
                                              instrument_ids, data_scope, fill_model)
GET    /v1/backtests/{id}                    (one + status)
GET    /v1/backtests/{id}/trades             (7.5 trade records)
GET    /v1/backtests/{id}/equity             (per-bar equity curve)
GET    /v1/backtests/{id}/metrics            (M7 metrics; summary block only for now)
GET    /v1/backtests/{id}/skipped            (SKIPPED/NOT_FILLED with reasons)
```

Fail-closed: unknown experiment 404 (D-041: every backtest belongs to an
experiment — PD-12), unknown/removed dataset_version 422 (a manifest must
resolve), unknown fill model 422, `SHORT_ONLY`/`BOTH` 422 until M10 (R11),
`entry.mode = RANDOM_BASELINE` 422 until M7 (7.6, parked). Async runs go
through the M1 job/worker seam; `GET /v1/backtests/{id}` reports
`queued/running/succeeded/failed` with the job contract.

## 7. Parity layer 2 (T6.5, spec 14.2)

`POST /v1/parity/runs` (upload TradingView "List of trades" CSV + run
metadata) and `engine/parity/layer2.py` compare per trade: entry/exit times
(exact), prices (rel 1e-6), tags/exit reasons, qty (rel 1e-3 after lot
rounding), PnL (rel 0.5%), on the same data/settings/script version. The
report reuses the layer-1 five-value vocabulary + `worst_of` (D-018/D-019)
and the `make parity` gate: exit 0 only for EXACT /
EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE; zero ENGINE_BUG, zero UNKNOWN.

## 8. Strategy Lab UI (T6.4, spec UI 563/556)

Strategy Lab page: strategy selector (versioned), backtest run (fill model,
scope), results table (trades), equity curve, metrics summary; the chart's
**trade overlay** (spec UI 556): entry x-cross (aqua long / red short),
active stop as orange step line, target line, exit marker with reason + R,
skipped signals hollow with reason on hover. Reuses the chart workspace and
the dataset manifest link (`/data/datasets/{version}`) so "what exactly did
this backtest use?" is answerable (PD-6).

## 9. Out of scope (parked)

- Short-side strategies (R11) — M10 with the perp adapter.
- Funding/liquidation modeling (7.7) — M10.
- `REALISTIC_INTRABAR` (R8) — reserved, NOT_IMPLEMENTED.
- Random-baseline entry mode (7.6) — M7 validation surface.
- Validation levels, Monte Carlo, walk-forward (M7); scanner/watchlists/
  replay (M8); Copilot/MCP (M9).

## 10. Test plan (tests first)

| Suite | Covers |
| --- | --- |
| `tests/test_strategy_models.py` | 7.1 JSON transcription, versioning + hash stability, 422 vocabularies |
| `tests/test_backtest_sim.py` | sizing (7.2), fills (PARITY + REALISTIC_OHLC), per-bar order (7.4), trailing kinds, time fields |
| `tests/test_backtest_edge_cases.py` | E1-E6: every outcome recorded, no signal disappears |
| `tests/test_backtest_portfolio.py` | shared equity, deterministic order, caps |
| `tests/test_backtest_repro.py` | same inputs ⇒ byte-identical trades (canonical JSON hash) |
| `tests/test_backtests_api.py` | strategies CRUD + versions; backtests async + trade/equity/metrics/skipped views; experiment-boundary 422s |
| `tests/test_backtest_parity_l2.py` | trade-list comparison vs the owner export (skip-guarded like the h=10 AC) |

## 11. Decisions to record

| ID | Decision |
| --- | --- |
| D-038 | Trade time fields are UTC ISO-8601 with `close_time` exclusive upper bound (D-008) |
| D-039 | PARITY fill = signal-bar CLOSE; management starts the next bar; Pine settings enforced (fee 0.10%, slippage 0, delay 0) |
| D-040 | Portfolio mode processes instruments in `instrument_id` sort order each bar |
| D-041 | Every backtest requires a registered experiment_id (fail-closed 422 otherwise) |
| D-042 | Simulator is a pure function; jobs/persistence sit behind the M1 seam |
| D-043 | Short-side strategies rejected with 422 until M10 (R11), parsed but not runnable |
| D-044 | Parity layer 2 reuses the layer-1 vocabulary and gate semantics |

Open questions for the owner: **OQ-22** (owner's current TradingView
"List of trades" export for layer 2 — the AC's reference data).
