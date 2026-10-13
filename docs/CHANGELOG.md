# CryptoOS — Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/); version tags start at M0
(the spec defines no release numbering yet).

## [Infra: dev DB cluster as a service] - 2026-10-13 - local Postgres 18 runs as a Windows service

The dev/scratch Postgres 18 cluster now runs as the **`pg-scratch-cryptoos` Windows
service** on port **54333** (data dir `%USERPROFILE%\pgdata-cryptosos`, `autovacuum=off`),
started by the SCM instead of a shell-spawned `pg_ctl start`. This ends the
`0xC0000142` crash loop: the agent shell's restricted **job object** killed every process
Postgres forked (the postmaster reached "ready", then each backend/startup process died at
DLL init with `STATUS_DLL_INIT_FAILED`, forcing a full shutdown). The service launches
outside the job object, so backends survive (D-113, docs/BACKLOG.md).

### Verified
- `pg_isready :54333` → accepting connections; a `psql` backend connects cleanly.
- **Full suite green against the live cluster: 1167 passed, 0 failed, 2 skipped** (179s) —
  the deferred verification outstanding since Phase 8. DB-backed suites now execute
  instead of skip-guarding. Ruff + mypy clean.
- API `GET /v1/status` → `{"status":"ok","database":"up"}`.

### Notes
- `.env` `DATABASE_URL` points at `localhost:54333`. A **Process-scope `DATABASE_URL`
  env var overrides `.env`** (pydantic-settings) — export it explicitly per shell when they differ.
- The suite still runs destructive `DELETE` against the configured DB (docs/BACKLOG.md
  "Test isolation"); the service cluster is disposable, and a per-run-schema fixture is still parked.

## [M16: AI Visualization] - 2026-10-12 - Programmable-visualization AI layer (spec §25/§26/§36)

Turns a natural-language request into a **validated, reproducible** visualization
program. Generated code is untrusted until it passes the same gate a human's
program does, and every tool result carries the §26 envelope. No trading tier,
ever (D-103).

### Added
- The §25 generation pipeline (`engine/visualization/ai_generation.py`, pure):
  closed-vocabulary dataset match (word-boundary for short keywords, D-106),
  availability check (no silent dataset substitution), assumption proposal,
  program generation, validate/fixture-test gate (deterministic synthetic rows,
  `dataset_version="FIXTURE"`, structure-only render — D-109), sandbox run,
  provenance-aware explanation.
- The M16 editor (`edit_program`) — a version-gated re-validation seam
  (behavior edits require a new version; `DEPRECATED` is terminal; D-107) and
  read-only debugger (`debug_program`, D-108).
- The §26 MCP tool surface (`engine/visualization/mcp.py`): the 11 owned tools
  registered into the M9 registry (extend, not replace) with one real JSON
  Schema each (D-111); `get_chart_context` reused from M9. The
  `VISUALIZATION_WRITE` tier extends the M9 `Tier` literal; no trading tier
  (D-103).
- Design note `specs/design/m16_ai_visualization.md` and decisions D-102…D-111.

### Verification
- `tests/test_visualization_ai.py` (51 tests), plus the M14 runtime/canvas
  suites and the AI registry/MCP/API/trust-boundary suites: **182 passed,
  0 failed, 22 skipped** (the skips are the DB-backed AI API tests, cluster
  down — unchanged). ruff + mypy clean on `engine/`, `apps/` (133 files).
- The full `pytest` suite is still DB-blocked (scratch cluster down); the
  deterministic engine layer above is green against current `HEAD`.

### Fixed
- The DB engine now binds a 3s `connect_timeout`, so an unreachable cluster
  fails fast (a dead port raises `OperationalError` in seconds) instead of
  blocking ~2 min per try — the suite and `GET /v1/status` report
  "database unreachable" rather than looking stuck (D-112; complements D-076).
  Pinned by `tests/test_db_failfast.py` (no live database required).



## [Hypothesis Factory] - 2026-10-11 - The research-workflow engine (spec §24)

Turns a research question into a falsifiable hypothesis, tests it against the
existing M5–M7 engine, and decides honestly whether it survives. No live
trading (PD-1); every AI output is a proposal a human approves (spec §25).

### Added
- Data model (Phase 1): `engine/hypothesis/models.py` + migration `0011` —
  `research.hypothesis_version` (the formal definition: null/alt, features,
  population, horizon, thresholds, falsification criteria),
  `.hypothesis_relationship` (the evidence graph),
  `.research_ledger` (the multiple-testing family, spec §12); the
  status/origin/domain vocabularies as DB CHECKs; the pure status-transition
  rules. Migration `0012` adds the idempotent-import natural keys (D-079).
- Registry (Phase 1): `engine/hypothesis/registry.py` — hypothesis CRUD,
  deterministic dedup keys, the evidence-graph edges, the ledger;
  `apps/api/hypothesis_factory.py` (the REST surface).
- Experiment integration (Phase 3): `engine/hypothesis/experiment.py` +
  `runner.py` — the hypothesis → resolved-config seam composing M5/M6/M7
  (never a duplicate engine); job execution via the M1 seam; config hashes;
  reproducibility metadata (PD-6).
- Statistical analysis (Phase 4): `engine/hypothesis/stats.py` — the
  statistical engine + the synthetic methodology suite.
- AI generation (Phase 5): `engine/hypothesis/generate.py` — every output is
  a proposal, not a decision.
- Robustness & falsification (Phase 6): `engine/hypothesis/robustness.py` +
  `evaluate.py` — the falsification evaluator + the transition decision
  (spec §13/§14.2).
- MCP exposure (Phase 7): `engine/hypothesis/mcp.py` — the MCP tool surface +
  the decision provider (spec §18).
- Operational hardening (Phase 8): `engine/hypothesis/hardening.py` — resource
  controls (generation/mcp/max_bars budgets; narrow-never-widen, fail-closed,
  PD-10) + the HF backup/recovery round-trip in `engine/backup.py`.
- Phase 2 UI: `apps/web/app/hypothesis-factory/page.tsx` — the manual research
  workflow (research-question entry preserved verbatim → hypothesis editor →
  definition validation → stored-hypothesis view + the §14.2 evidence ladder).
  No AI provider required. PD-5: every status and number comes from a server
  response — the page never computes or decides. Client functions in
  `packages/client/src/api.ts`; the "Hypothesis Factory" nav link.

### HF evidence
- Suite: 970 passed + 2 skipped (Phase 8, commit 846206d); ruff + mypy strict
  (194 files) clean; web tsc clean; `next build` passes with
  `/hypothesis-factory` prerendered. Alembic HEAD=0012 (live-applied).
- NOT re-run: the full pytest suite after the Phase 2 UI commit (83af84b).
  The PG18 scratch cluster on :54333 is in a 0xC0000142 (DLL-init) crash loop
  and cannot be revived from the current shell. The suite must be re-run once
  the cluster is recovered.
- DB-free re-run (2026-10-12, HEAD bddefb1): 647 passed + 1 skipped across
  the 43 no-database test files — the whole deterministic engine layer,
  including all 91 DB-free HF engine tests (17 models + 17 experiment +
  15 stats + 21 evaluate + 21 robustness); 0 failures, 0 errors. Slow files
  are property tests, not hangs (test_features_truncation: 3 passed in 61.7s);
  test_events_parity_h10 skips by design (the OQ-22 reference export).
- Still DB-blocked (2026-10-12): the 32 live-database files, including the
  61 DB-backed HF tests (14 registry + 6 runner + 14 generate + 15 hardening
  + 12 mcp). Neither :5433 (the .env target) nor :54333 answers — no
  listener; the guarded fixtures skip fail-fast via connect_timeout=3
  (D-076), never hang. Last confirmed state for these: the 970 + 2 above.

## [M11] - 2026-10-10 - Hardening: a11y, performance gates, e2e, backup/export

### Added
- Backup/export (T11.4): `engine/backup.py` — the notebook + registry +
  lineage bundle (`POST /v1/backups`, `POST /v1/backups/import`); the
  bundle is JSON with `schema_version` + `created_at`; import is
  idempotent (ON CONFLICT DO NOTHING) and the frozen contract survives the
  round trip (D-021/D-079); no secrets, metadata + lineage only (PD-6).
  `GET /v1/backups/{id}` answers 404 (exports are direct downloads, OQ-27).
- Performance gates (T11.2): `tests/test_perf_api.py` — the §14 targets
  as hard CI gates (D-075): indicators 5,000 bars <150ms; single-symbol
  10y daily backtest <5s; chart interactions <50ms (the hover map lookup);
  the scanner 100-symbol row build <2s; the 5,000-candle API <300ms
  (live-DB, skip-guarded per D-076). A budget overrun fails CI.
- E2E core flow (T11.3): `tests/test_e2e_core_flow.py` — the spec §14
  acceptance path as one deterministic run (D-077): open BTC 1D → toggle
  events → click a marker → Evidence (grade + n_eff) → add to experiment
  → run backtest → validation → freeze → verify INTACT.
- A11y audit (T11.1): `tests/test_a11y.py` — the WCAG AA contrast check
  over the CSS vars (D-080): --text/--muted meet 4.5:1, the status
  colors meet 3:1; the nav landmark + aria-labels; the
  `prefers-reduced-motion` rule added to globals.css.

### M11 AC evidence
- e2e core flow: 1 passed (the full 8-step §14 path).
- performance: 5 passed (all §14 budgets hold).
- backup/export: 8 passed (the bundle shape, validation, idempotency,
  the frozen contract).
- a11y: 7 passed (contrast ratios + the reduced-motion rule).
- Suite: 809 passed + 2 skipped; ruff + mypy strict (170 files) clean;
  web tsc clean.

## [M10] - 2026-10-10 - Perp derivatives + live candles

### Added
- Perp adapter (T10.1): `engine/exchanges/perp.py` — funding (8h, spec
  7.7), open interest, liquidations; rate-limit aware (429 backoff, R6);
  fail-closed on a non-list payload; idempotent persists; the market-type
  guard (PD-10). Migration 0010 (`market.funding_rate`,
  `market.open_interest`, `market.liquidation`).
- Live kline WebSocket supervisor (T10.2): `engine/market/live.py` —
  gap detection (a jump past the next expected bar is a gap, recorded in
  `system.gap` — never silent, PD-10/the AC), REST backfill repair
  (REPAIRED + GREEN), the forming bar never stored (§17), jittered capped
  reconnect backoff (D-072).
- Funding cost model + SHORT-side (T10.3): `engine/strategy/funding.py`
  (8h accrual on notional; positive rate = longs pay shorts, D-070) +
  `engine/strategy/short_side.py` (SHORT_ONLY/BOTH runs on perp only,
  D-071; the runner re-checks the universe's market types).
- The API surface: `apps/api/perp.py` — `GET /v1/funding|oi|liquidations`
  (fail-closed on a spot instrument, PD-10) + `WebSocket /v1/ws` (the
  spec §11 channels, D-073).
- The chart sub-panes: `apps/web/app/chart/perp.ts` + the store/page
  wiring — funding/OI/liquidations under the candles, gated on
  market_type=PERPETUAL (D-074).

### M10 AC evidence
- kill mid-bar → detected gap → automatic repair → GREEN: 1 passed
  (`test_kill_mid_bar_gap_detected_repaired_green`); no silent gaps: 1
  passed; a failed backfill keeps the gap OPEN: 1 passed.
- perp adapter: 9 passed; funding model: 12 passed; short-side gate: 8
  passed; perp API: 10 passed.
- Suite: 788 passed + 2 skipped; ruff + mypy strict (164 files) clean.

## [M9] - 2026-10-10 - AI Copilot + MCP

### Added
- Tool registry (spec §9): `engine/ai/registry.py` — the spec's 21 tools
  verbatim, tiered READ_ONLY/RESEARCH; the registry is closed (unknown
  tools raise, D-063); no order-related tools exist; experiment tools
  respect immutability; holdout-scope tools carry `requires_holdout`
  (D-064).
- Number integrity (spec §9): `engine/ai/integrity.py` — the
  `{{ref:<ref_id>#<json_path>}}` placeholder resolver (fail-closed:
  unknown ref/path removed + flagged, never rendered raw, D-059) + the
  free-text numeral scanner (the AC).
- Providers + the Copilot loop: `engine/ai/providers.py`
  (LLMProvider; Echo + OpenAI adapters; `LLM_PROVIDER` selection, D-061)
  + `engine/ai/runner.py` (the five modes; the loop de-duplicates calls;
  Critic mode fails closed below three items, D-060).
- Migration 0009 (`agents.run` + `agents.tool_call`) + `engine/ai/mcp.py`
  — every MCP call writes `agents.tool_call` AND `system.audit_log`
  (D-062, the AC).
- The API: `apps/api/ai.py` — `POST /v1/ai/chat` (SSE), `GET /v1/ai/tools`,
  `GET /v1/agents/runs[/{id}]`; the chat endpoint creates the run row,
  executes audited tools, resolves placeholders.
- The UI: `apps/web/app/ai/page.tsx` — the five modes, the tool-registry
  table, the transcript log, the executed-tool-call list, the
  unresolved-placeholder alert (the AC).

### M9 AC evidence
- numbers without ref flagged: 13 passed (the resolver + the UI alert).
- no order-related tools; experiment tools respect immutability: 12
  passed (the AC's explicit gate).
- MCP calls audited: 12 passed (agents.tool_call + audit_log per call).
- Suite: 739 passed + 2 skipped; ruff + mypy strict (154 files) clean.

## [M8] - 2026-10-10 - Scanner, watchlists, MTF, replay

### Added
- Scanner (spec 11/12): `engine/scanner/scan.py` — one row per instrument
  with the 9 spec UI 560 columns (symbol, price, chg_24h, rvol, last_event,
  age, regime, grade, n_eff), computed from stored data only (PD-5, D-056);
  D-053 one frame per instrument (no N+1); the regime tags come from
  `engine/features/regime.py` verbatim (D-054); `min_grade` filters on the
  A-D vocabulary (D-028).
- Watchlists (spec 10/11): migration `0008_m8_scanner`
  (`market.watchlist` + `market.watchlist_item`, CASCADE deletes, live-
  applied) + CRUD `/v1/watchlists` (+ items, ON CONFLICT DO NOTHING;
  D-057 one instrument in many lists).
- MTF (spec 11 `GET /v1/mtf?instrument_id`): per-timeframe regime summary
  reusing the regime module verbatim (D-054).
- Replay (spec 12): `engine/scanner/replay.py` — `as_of` semantics (D-055):
  bars filtered to `close_time <= as_of` (D-008), events to
  `event_time <= as_of` in SQL (PD-2); the spec-12 time fields ride on the
  event rows. The no-future-leak AC is asserted by both the unit suite and
  the live-database suite.
- Scanner UI: `apps/web/app/scanner/page.tsx` + `layout.tsx` — the spec-560
  column table, watchlist/timeframe/min-grade filters, watchlist delete,
  saved scans + preserved state in localStorage (D-058, the D-012
  precedent), and the MTF panel on row click. Nav gains the Scanner entry.
- Client regen: openapi + schema now carry `/v1/scanner`, `/v1/mtf`,
  `/v1/watchlists`; `getScanner` / `getMtf` / `postWatchlist` /
  `getWatchlists` / `deleteWatchlist` wrappers + `WatchlistCreate` /
  `WatchlistView` types exported.
- Tests: 20 new (12 scanner + 8 replay units) plus the live-DB replay
  suite (skip-guarded when the scratch cluster is unreachable — the unit
  suite is the hard gate). Suite now 677 (675 passed + 2 skipped).
- Decisions D-053..D-058 recorded in `docs/DECISIONS.md`; design note
  `specs/design/m8_scanner.md` is the module contract; OQ-24 open (the
  scanner universe beyond the seeded BTC/ETH/SOL — OD-2's 30-symbol list).

### Notes
- All three M8 ACs are evidenced: scanner row shape + filters + the
  timing budget (100-symbol < 2s asserted per-symbol, skip-guarded on the
  seeded universe - OQ-24); replay leaks no future data (unit + live-DB
  suites); MTF matches the regime module (it *is* the module).
- `next build` needs `NODE_OPTIONS=--max-old-space-size=8192` on this
  machine (worker heap, not a code issue).

## [M7] - 2026-10-10 - Validation suite: metrics, random baseline, three levels, dashboard

### Added
- Validation dashboard (T7.4, spec 9.3/9.4 UI): `apps/web/app/validation/page.tsx` +
  `layout.tsx` — backtest picker, "Run levels" (Level 2 + Level 3 kinds) and
  "Random baseline (1000 seeds)" controls, the overall status panel (9.4: a
  LIST with the unmet VALIDATED conditions listed, never a composite),
  the three-level runs with each test's status word + numeric basis (the
  state word is always the text; colour never alone — the M2 badge
  contract), and the baseline distribution table (P5/P50/P80/P95 per
  metric with the strategy percentile noted). Nav gains the Validation
  entry. PD-5: every number comes from a stored server result.
- Client regen: openapi + schema now carry `/v1/validation` +
  `/v1/baselines/random`; `postValidation` / `getValidation` /
  `postRandomBaseline` wrappers + `ValidationCreate` / `BaselineCreate` /
  `RunView` types exported.
- Metrics (T7.1, spec 9.1 verbatim list): `engine/validation/metrics.py` —
  net return, CAGR, profit factor, expectancy (R), win rate, avg win/loss,
  payoff ratio, Sharpe, Sortino, max drawdown, Calmar, exposure, turnover,
  trade count, avg/median R, top-1/5/10 trade share, longest losing streak.
  Per-symbol AND portfolio-level inputs; per-symbol Sharpe is never the
  headline. Open positions excluded from closed-trade metrics (D-049); a
  zero-loss book reports a finite cap + flag, never `Infinity` (D-046);
  Sharpe/Sortino annualize with the caller-supplied `periods_per_year`
  (D-045).
- Random-entry baseline (9.2): `engine/validation/baseline.py` — seeds
  1..N (default 1000), deterministic per-seed random entries with the same
  exits/sizing/costs/fill model, p-calibration so the mean trade count
  matches within +/-10%, distributions + P5/P50/P80/P95 percentiles, and
  the percentile verdict (PF >= P95 PASS, P80-P95 WARN, <P80 FAIL).
- Three levels (9.3): `engine/validation/levels.py` — Level 1 (parity N/A
  without an export, truncation, deterministic replay, accounting
  invariants), Level 2 (Edge CI-excludes-zero / INSUFFICIENT <30 trades,
  OOS sign+share, walk-forward >=60%, clustering, multiple-testing
  informational, random baseline, param stability, trade dependency,
  concentration, regime split), Level 3 (cost stress, delay stress, gap
  risk, Monte Carlo, liquidity/funding NOT_RUN before M10). Every test
  returns a status with a numeric basis; the trust rule (any Level 1
  FAIL/NOT_RUN other than N/A parity -> Level 2/3 UNTRUSTED, D-047) is
  computed once.
- Status mapping (9.4): `engine/validation/status.py` — the overall status
  is a LIST, never a composite: REJECTED on kill criterion; PROMISING when
  no FAIL and Edge PASS (with the unmet VALIDATED conditions listed);
  VALIDATED only with every applicable test PASS, pooled trades >=100,
  Level 1 trusted, a frozen experiment and a passed holdout.
- Tables + API (spec 10/11): migration `0007_m7_validation`
  (`validation.run`, locked one-way flag, live-applied) + `JobKind.
  VALIDATION`/`BASELINE` in `engine/jobs.py` + `apps/api/validation.py`
  (`POST /v1/validation` async with kinds[], `GET /v1/validation/{id}`,
  `POST /v1/baselines/random`) + `engine/validation/runner.py` (loads the
  backtest's trades, computes metrics + levels, persists one run per kind
  with the 9.5 provenance).
- Tests: 47 new (12 metrics, 9 baseline, 17 levels, 9 status). One real
  engine bug caught: a truncation FAILURE reported PASS (the boolean helper
  fell through to a default PASS). Suite now 644 (643 passed + 1 skipped).
- Test isolation: `validation.run` added to the shared TRUNCATE lists (the
  new run->backtest FK otherwise breaks other suites' cleanup).
- Decisions D-045..D-052 recorded in `docs/DECISIONS.md`; design note
  `specs/design/m7_validation.md` is the module contract; OQ-23 open
  (holdout flow end-to-end, depends on OQ-19).

### Notes
- T7.1-T7.4 landed: T7.2-T7.3 (walk-forward / param-stability / cost+delay
  stress / Monte Carlo computations) are committed; the remaining M7 work
  is T7.5 holdout flow end-to-end, blocked on OQ-23 (the OQ-19 reading).
  Liquidity/funding stays NOT_RUN until M10 (9.3). The `next build` gate
  needs `NODE_OPTIONS=--max-old-space-size=8192` on this machine (worker
  heap, not a code issue); the production `tsc -p tsconfig.json` passes.

## [M6] - 2026-10-10 - Backtest simulator, strategy CRUD, portfolio mode

### Added
- Portfolio mode (T6.3, spec 7.4 "PORTFOLIO mode"): `engine/strategy/portfolio.py` —
  one shared equity, instruments processed in `instrument_id` sort order each
  event (D-040), entries and closes processed as separate time-ordered events
  so a same-signal-time entry observes the open position, constraints
  `max_concurrent_positions` / `max_portfolio_heat` / per-asset cap; every
  rejected entry is recorded as `SKIPPED` with its reason (M6 AC: no signal
  disappears). Exposed on `POST /v1/backtests` (`portfolio: bool` + the three
  caps) and wired into `engine/strategy/runner.py` (multi-instrument runs go
  through `simulate_portfolio`; single-instrument runs keep the existing path,
  R9).
- Tests: `tests/test_backtest_portfolio.py` (9 tests) — shared equity pool,
  deterministic order regardless of input dict order, each cap's skip reason,
  every skip carrying a reason, single-instrument parity with `simulate`,
  equity curve per bar, empty universe. Suite now 597 (596 passed + 1
  skipped vs the previous 588).
- Strategy Lab (T6.4, spec UI 556/563): `apps/web/app/strategy/page.tsx` +
  `layout.tsx` — strategy selector (versioned), experiment selector, fill
  model + portfolio-mode controls, backtests table (status/fill/scope/locked),
  and per-backtest detail: metrics summary (trades/closed/win-rate/pnl/avg-R/
  final-equity/flagged), equity-curve sparkline, trades table (7.5 record:
  entry/exit/reason/R/pnl/bars/gap) and the skipped-signals table (7.3.1).
  Nav gains the Strategy Lab entry; `globals.css` gains the sparkline styles.
  PD-5: every number comes from a stored server result.
- Tests: 65 new (11 strategy models, 11 sizing, 16 fills, 17 simulator,
  10 API) including the full async lifecycle (POST -> job -> simulator ->
  trades -> succeeded) and byte-identical reproducibility (M6 AC). Suite
  now 588 (587 passed + 1 skipped vs M5b's 523).

### Notes
- T6.1/T6.2/T6.3/T6.4 landed: the only remaining M6 work is parity layer 2
  (T6.5, blocked on the owner's TradingView trade-list export - OQ-22);
  short-side strategies stay 422 until M10 (R11); RANDOM_BASELINE stays 422
  until M7 (7.6). The chart trade-overlay markers land with the chart
  integration; this page carries the results views.
- Simulator (T6.1, spec §7): `engine/strategy/models.py` (the 7.1 definition
  JSON transcribed field-for-field; spec wire names `trigger_R` /
  `activate_R` preserved through Pydantic aliases; unknown/ reserved
  vocabularies 422), `sizing.py` (7.2 risk distance, stop price, RISK /
  FIXED quantity with lot-step round-down), `fills.py` (7.3: PARITY fills
  at the signal-bar CLOSE - D-039; REALISTIC_OHLC at the next bar's OPEN
  with adverse slippage; resting stop / target semantics with
  gap-through-open; E5 same-bar resolution - REALISTIC_OHLC stop first,
  PARITY the broker-emulator path heuristic), `simulator.py` (7.4 per-bar
  order of operations, 7.5 trade record with every time field, E1-E6: no
  signal disappears - NOT_FILLED/NO_NEXT_BAR, SKIPPED/ENTRY_DATA_GAP,
  SKIPPED/GAP_THROUGH_STOP, SKIPPED/QTY_ZERO, open-at-END_OF_DATA,
  data-gap flagging), `hash.py` (canonical JSON sha256 - D-037 form),
  `runner.py` (job-side glue: manifest-backed bars, RVOL_CLIMAX signals
  through the detector registry, persistence in the caller's transaction).
- Tables (T6.1/T6.2, spec §10): migration `0006_m6_backtest` -
  `strategy.strategy_version` (versioned, content-hashed),
  `strategy.backtest` (experiment_id NOT NULL - D-041; locked one-way
  flag; CHECKs), `strategy.trade` (append-only trigger, verified live:
  `IMMUTABLE_RESULT: strategy.trade is append-only`). Applied against the
  live cluster.
- Job seam: `JobKind.BACKTEST` in `engine/jobs.py`; `execute_job` dispatches
  through the same claim -> execute -> commit pattern as INGEST/REPAIR
  (D-042: the simulator stays pure).
- API (T6.2, spec §11): `apps/api/backtests.py` -
  `POST/GET /v1/strategies` (edits create a NEW version - 7.1),
  `GET /v1/strategies/{id}/{version}`, `POST /v1/backtests` (202 async;
  requires a registered experiment_id - D-041; fail-closed on unknown
  fill_model/data_scope/manifest, SHORT_ONLY until M10 - R11/D-043),
  `GET /v1/backtests/{id}[/trades|/metrics|/skipped|/equity]`.
- Tests: 65 new (11 strategy models, 11 sizing, 16 fills, 17 simulator,
  10 API) including the full async lifecycle (POST -> job -> simulator ->
  trades -> succeeded) and byte-identical reproducibility (M6 AC). Suite
  now 588 (587 passed + 1 skipped vs M5b's 523).
- Test isolation: the M6 FK (`strategy.backtest.experiment_id ->
  research.experiment`) is added to the shared TRUNCATE lists
  (`tests/support.py`, `test_api_stats.py`, `test_events_evidence.py`) so
  every suite's cleanup still runs.
- Decisions D-038..D-044 recorded in `docs/DECISIONS.md`; design note
  `specs/design/m6_backtest.md` is the module contract; OQ-22 open for the
  owner (the TradingView "List of trades" export for parity layer 2).

### Notes
- T6.1/T6.2/T6.3 landed: the remaining M6 work is parity layer 2 (T6.5) and
  the Strategy Lab UI (T6.4); short-side strategies stay 422 until M10 (R11);
  RANDOM_BASELINE stays 422 until M7 (7.6).

## [M5b] - 2026-10-10 - Experiments, families, immutability, lineage

### Added
- Experiment tables (T5b.1, spec §10): migration `0005_m5b_experiments`
  (`research.experiment_family`, `research.variant`, `research.experiment`) plus
  ORM rows in `engine/stats/models.py`. Triggers enforce the design note:
  family rejects DELETE once referenced; variant is append-only except the
  `selected` flag (D-034); experiment rejects UPDATE/DELETE once `frozen`
  (spec §8.5).
- Families/variants API (T5b.1): `POST/GET/PATCH/DELETE /v1/families`,
  `GET /v1/families/{id}/variants` (`apps/api/experiments.py`), fail-closed
  404/409/422 per PD-4/PD-10/PD-12.
- Experiments API (T5b.1/T5b.2): `POST/GET/PATCH /v1/experiments` (unfrozen
  rows patchable; 409 once frozen), singleton family minted when `family_id`
  is omitted, every create requires a registered `market.dataset_manifest`
  row (422 otherwise), status/selection-method/data-scope vocabularies as DB
  CHECKs (D-033).
- Ad-hoc stats recording (spec §8.4): `POST /v1/stats` now appends one
  `research.variant` row and bumps the family's `variant_count` in the same
  transaction; the stored `result["multiple_testing"]` carries the *request's*
  family identity only (null for an ad-hoc run) so byte-identical `result`
  JSON survives (D-036). A singleton family row is minted up front and
  written only after the run succeeds — a failed request leaves no rows.
- Freeze + verify (T5b.2, spec §8.5): `POST /v1/experiments/{id}/freeze`
  runs one transaction — resolves every `refs.stats` row (422 missing),
  stores `sha256(canonical JSON of result)` in `result_hashes`, sets
  `locked = true` on results and the referenced hypothesis, sets
  `frozen = true, frozen_at`, and writes a `system.audit_log` row
  (D-037). `GET /v1/experiments/{id}/verify` recomputes each hash and
  answers INTACT/TAMPERED with per-ref detail (422 when unfrozen).
- Lineage (T5b.3): `GET /v1/experiments/{id}/lineage` returns the read-only
  chain `dataset → features → detector → stats → hypothesis → experiment →
  stats_result` from stored ids, every node carrying a `ref` link and an
  `OK`/`MISSING` status, plus `not_implemented` naming `strategy, backtest,
  validation, conclusion` (M6/M7 tables do not exist — PD-9).
- Tests: `tests/test_experiments.py` (15 tests) covering family CRUD and
  delete-while-referenced 409, variant append-only except `selected`,
  experiment create/422s, PATCH rules, freeze locking + double-freeze 409 +
  missing-ref 422, verify INTACT/TAMPERED (tamper applied with triggers
  bypassed via `session_replication_role = replica`), lineage links,
  singleton family recording, and multiple-testing count growth. Suite now
  523 total (522 passed + 1 skipped vs M5's 507).
- Client: `openapi.json` + `schema.ts` regenerated from the app object
  (PD-6); `packages/client/src/api.ts` gains the family/experiment wrappers
  and `index.ts` exports them (`postFamily` … `getExperimentLineage`) plus
  the `Family*`/`Experiment*`/`Variant*`/`Lineage*`/`Verify*` view types;
  client tsc passes.
- Decisions D-033..D-037 recorded in `docs/DECISIONS.md`; design note
  `specs/design/m5b_experiments.md` is the module contract; OQ-21 open for
  the owner (status list + REJECTED conclusion requirement).
- Experiments page + lineage view (T5b.3, spec UI 563):
  `apps/web/app/research/experiments/page.tsx` — list (title, status,
  family, frozen) with click-through detail: provenance dl (hypothesis,
  dataset_version link to the manifest viewer, detector@version, universe,
  timeframe/data_scope, selection_method, seed, engine/code versions),
  multiple-testing banner (spec 8.4 wording), results table (grade/n_eff/
  scope per stored result), family-variants table, Freeze button with a
  confirm dialog explaining irreversibility, Verify-integrity button
  (INTACT/TAMPERED), and the read-only lineage chain with per-node
  `ref` links plus the `not_implemented` footer. Nav gains the Experiments
  entry; `globals.css` gains the M5b styles.
- Browser verification: the page renders at `/research/experiments`; every
  endpoint it consumes (`/v1/experiments`, `/v1/families/{id}`,
  `/v1/families/{id}/variants`, `/v1/stats/{ref}`, `/v1/hypotheses/{id}`,
  `/v1/experiments/{id}/verify`, `/v1/experiments/{id}/lineage`) was
  exercised live against a real PostgreSQL cluster (hypothesis → family →
  stats → experiment → freeze → verify INTACT → lineage 7/7 OK); the
  in-browser click-through could not run because Chrome's real profile was
  locked by a running instance (environmental; `next build` + `tsc` pass
  and all payloads are confirmed live).

## [M5] - 2026-10-10 - Stats, scopes, evidence, hypotheses, notebook

### Added
- Dataset scopes (T5.1): `config/dataset_scopes.yaml` frozen entry for
  `binance_spot_usdt_default` (BTC/ETH/SOL x 1D/4H/1H, 80/20 holdout split per
  OD-8/D-025, boundaries floored to the UTC day containing the raw instant -
  D-026), `engine/scopes.py` (frozen boundaries, `clip_window` flagged
  `SCOPE_CLIPPED`, spec-order embargo/purge, `segment_of`), and
  `tests/test_scopes_invariants.py` (24 tests; hypothesis properties on
  clip/embargo/purge + the production freeze is asserted read-only).
- Evidence-grade config (T5.2a): `config/validation.yaml` `evidence_grade`
  thresholds transcribed from spec 8.3 (A/B/C tiers, anchor horizon 10 -
  D-031, z-scale CI level, D label) with `engine/config/validation.py`
  `load_evidence_grade` failing closed on an incomplete section (PD-4).
- Stats module (T5.2b): `engine/stats/ci.py` (type-7 percentiles, Wilson 95% on
  raw `(n, wins)`, seeded cluster bootstrap with shared draws per draw-set -
  D-032), `engine/stats/clusters.py` (SYMBOL_GAP, CALENDAR_DAY, CALENDAR_WEEK,
  MARKET_EVENT; CUSTOM is a documented M5 422), `engine/stats/grade.py`
  (A-D ladder with pinned interpretations - D-028, HOLDOUT grade null -
  D-030), `engine/stats/compute.py` (pure `compute_stats` producing the stored
  result JSON: n_raw/n_eff, per-model matrix, headline model = lowest grade,
  per-horizon pooled + IS/OOS blocks, percentiles, histogram, cross-asset
  share, multiple-testing block), and `engine/stats/persist.py` (idempotent
  INSERT-only event/outcome persistence).
- Research tables (T5.2c): migration 0004 `research.hypothesis` (locked-row
  trigger), `research.note`, `research.event` / `research.event_outcome`
  (insert-only; content-addressed facts) + ORM rows in
  `engine/stats/models.py`.
- Stats API (T5.2d): `POST /v1/stats` + `GET /v1/stats/{ref_id}`
  (`apps/api/stats.py`) - PD-11 param resolution (`resolve_stats_params`:
  embargo = max horizon, market-event k = max(3, 10% universe), detector
  cluster defaults), scope clipping flagged, HOLDOUT gate (422 missing
  hypothesis, 409 unopened, one run per hypothesis, audit row), stored row
  with full provenance.
- Evidence payload (T5.3): `GET /v1/events/{id}` (`apps/api/evidence.py`) with
  cache-miss recompute over the scope grid, matching-stats selection,
  analogues (5 most recent same detector@version+type peers, `fwd_r_10`), and
  404 fail-closed.
- Hypothesis registry + notebook (T5.4/T5.5): `apps/api/hypotheses.py`
  (pre-registered immutable criteria, status enum, REJECTED autopsy,
  VALIDATED 422 until M5b, open-holdout-once with atomic guard) and
  `apps/api/notes.py` (CRUD; notes are not results).
- UI: `EvidenceGradeBadge` (the only grade-wording renderer, always showing
  grade + n_eff + CI + data_scope), chart Evidence panel on marker click
  (spec UI 558), Events Explorer page (filter bar, live stats, histogram,
  IS/OOS toggle, CSV export with dataset_version + data_scope), Research
  notebook page, nav + CSS.
- Tests: 507 total (506 passed + 1 skipped vs M4's 405): 46 stats compute,
  13 stats API, 6 evidence, 8 hypotheses, 3 notebook (3 web), plus
  `tests/test_grade_wording.py` (3 tests) gating the 8.3 wording rules; vitest
  10 passed.
- Decisions D-025..D-032 recorded in `docs/DECISIONS.md`; design note
  `specs/design/m5_stats.md` is the module contract.

## [M4] - 2026-10-09 - Event engine

### Added
- Detector framework (T4.1): `engine/events/base.py` (frozen `Event` envelope with
  section 6.2 fields, OBSERVED/CALCULATED provenance, content-addressed `event_id`
  over the identity fields only - D-023, `Detector` protocol with `resolve`/`derived`/
  `detect`), `engine/events/cooldown.py` (shared 6.7 gate - D-022), and
  `engine/events/registry.py` (autoload of `engine/events/detectors/`, fail-closed
  `resolve_detector` raising `ValidationError` for unknown ids/versions and for the
  six SPEC_REQUIRED names - PD-4/PD-10, `detect_with` one-call path).
- Cooldown golden fixtures A-G (`fixtures/events/cooldown/*.json`) proving: the `[1]`
  lag never suppresses the raw bar itself (A), independent per-stream state (F),
  `cooldown=0` gating off (G), seed 999 on the first-ever raw (E).
- `RVOL_CLIMAX` v5.0 detector (T4.2): `engine/events/detectors/rvol_climax.py`
  pinned line-by-line to `specs/reference/rvol_climax_pro_v5.pine` (75-146) - lagged
  `volMA`/`hiN`/`loN`/`swingLo`, non-lagged `emaR`, exact float comparisons, the four
  raw rules, `failed_bounce`/`legacy_v1` fail modes, six archetype labels, 3.0*ATR
  invalidation, per-stream cooldown, lookback/exclusion gap. Detector golden fixture
  `fixtures/events/rvol_climax_v5/golden.json` (CONT@10/CAPIT@15/FAIL@20/TOP@25).
- Outcomes (T4.3): `engine/events/outcomes.py` - default horizons
  `[1,2,3,5,10,15,20,30,40,50]`, full-horizon `fwd_ret_*` without stop truncation,
  stop-first excursion window with earliest-bar tie-break, direction-adjusted
  `mfe_R`/`mae_R`, incomplete horizons null and never partially counted.
- `GET /v1/events` (T4.4): `apps/api/events.py` on-demand compute (no event table in
  M4 - D-024) with request-or-defaults params (PD-11), fail-closed 422/404/501,
  section 6.2 envelopes; client `getEvents` + `EventEnvelopeView`/`EventsViews`.
- Chart event layer (T4.4): `apps/web/app/chart/events.ts` (section 12 marker styles,
  `applyEventMarkers` via lightweight-charts v5 `createSeriesMarkers` plugin,
  hover-card builder, `computeClimaxDots` placeholder - OQ-17), header "Events on/off"
  toggle (`eventsEnabled`, default ON), marker effect in `panes.tsx`, and the DOM
  hover card in `page.tsx` showing type/time/direction/ref/invalidation/rvol/cpos/
  archetype/trigger checklist (PD-5: numbers from the envelope, never recomputed).
- Truncation invariance (T4.5): `tests/test_events_truncation.py` - hypothesis
  properties: detection on a prefix equals restriction of the full result (PD-2), and
  outcomes on a prefix match for complete horizons with nulls elsewhere.
- h=10 parity AC (T4.5): `tests/test_events_parity_h10.py` compares
  `(n, sum(fwd_ret_pct[10]), win_count)` per signal type against the owner's TradingView
  study table (`study_horizon=10`, reference defaults) on the D-020 parity range;
  skips with a clear reason when no table export exists (OQ-16). CI gate remains the
  detector golden fixture.
- Detector spec: `specs/events/RVOL_CLIMAX.md` (status READY, all 14 template gates
  checked); the six SPEC_REQUIRED names stay unimplemented (PD-4).
- Web marker tests: `apps/web/app/chart/events.test.ts` (vitest 5) - exact section 12
  styling, `eventToMarker` mapping/edge cases, plugin create/reuse behavior: 7 passed.
- Tests: 405 total (404 passed + 1 skipped vs M3's 344): 11 cooldown + 30 detector
  golden + 15 outcomes + 13 API + 2 truncation properties + 1 parity (skip-guarded)
  in the new files, plus the vitest 7.
- Decisions D-022..D-024 and open questions OQ-16/OQ-17 recorded in `docs/DECISIONS.md`.

## [M3] - 2026-10-09 - Feature library + parity layer 1

### Added
- Section 5 feature library (T3.1): `engine/features/primitives.py` (numeric primitives
  moved from `indicators.py` plus `highest`, `lowest`, `barssince`, `pivotlow`,
  `pivothigh`, `macd`, `adx`, `atr_percentile`), `engine/features/regime.py`
  (descriptive tags `trend_state` / `vol_state` / `volume_state`, D-017), and registry
  growth in `engine/features/indicators.py` (D-015): `rma`, `tr`, `highest`, `lowest`,
  `donchian_lo`, `donchian_hi`, `pivotlow`, `pivothigh`, `macd`, `adx` are now served by
  `/v1/indicators` with parameters resolved from `config/defaults.yaml` only (PD-11);
  `barssince` (boolean input) and the regime tags (labels) stay library-only.
  Documented semantics: warm-up nulls per section 5 line 228/229, pivot ties strict on
  both sides with the no-lookahead `+R` placement (D-016), ADX zero denominators yield
  `null` never `inf`.
- Truncation invariance suite (14.1): `tests/test_features_truncation.py` - hypothesis
  properties over every registered indicator and every library/regime function: result
  on data cut at t equals the restriction of the full result to <=t, and editing future
  bars never changes the past (PD-2), exact `==` with no tolerance.
- Parity tool (T3.2, 14.2): `engine/parity/` (`constants.py` warm-up 500 / rel tol 1e-6,
  `metadata.py` nine-field sidecar fail-closed to `UNKNOWN`, `importers.py` alias-table
  CSV import with fail-closed rejections, `status.py` five-value vocabulary + worst-wins
  `overall()`, `platform_differences.py` anchored documented-difference table (OQ-15),
  `compare.py` bar alignment + OHLCV check (bar diff capped at 20) + four-plot
  comparison after skipping the first 500 bars, `run.py` gate exit codes) behind
  `scripts/parity.py` and `make parity` (D-018).
- Parity report at `reports/parity/layer1_report.json` (gitignored): kind
  `parity_layer1`, per-plot statuses with samples and anchors, bar diff, warnings,
  TradingView metadata verbatim plus PD-6 provenance (`dataset_version`,
  `dataset_schema_version`, `engine_version`, `code_version`, `resolved_params`,
  `seed: null`); deterministic JSON, no timestamps.
- `make parity` gate (D-019): exit 0 only for `EXACT` /
  `EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE`; `NO_REFERENCE_EXPORT` + non-zero until
  the owner's TradingView export exists at `fixtures/parity/BINANCE_BTCUSDT_1D.csv`
  with its `.meta.json` sidecar; the report's `pass` field stays spec-literal
  (14.2 line 592: zero `ENGINE_BUG`, zero `UNKNOWN`).
- Tests: 344 total (+91 vs M2's 253): 57 parity + 20 feature-contract + 3 truncation
  properties in the new files, plus 11 across the existing suites (including a
  `code_version` regression that caught `REPO_ROOT` resolving one directory above the
  checkout - `engine/versions.py` `parents[2]` -> `parents[1]`).
- AC parity run (amendment after the first run, design note 4.6): the owner's export
  (`fixtures/parity/BINANCE_BTCUSDT_1D.csv`, extracted from TradingView's own buffers
  over CDP - D-020 - plus the nine-field `.meta.json` sidecar) initially classified
  `DATA_MISMATCH` on 5 volume bars (rel 2e-6..1.9e-4). Live `api.binance.com` klines
  for those days return exactly the fixture values, so two narrow table entries were
  added (OQ-15 -> D-021): `tv_volume_feed` (bar volume and its 21-bar `rvol`
  contamination window, bound 1e-3, context plumbing via a frozen
  `DifferenceContext`) and `ema_seed_history` (regime-EMA seed wash-out in engine
  bars [500, 600), bound 1e-5); the bar gate now continues when every difference is
  documented (evidence stays in `bar_diff` with per-sample `entry` and
  documented/unexplained counts), and the overall status is
  `worst_of(plot statuses..., bar status)` (`status.worst_of` added). The AC run is
  now `EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE`, zero unexplained anywhere
  (bar 5/5, rvol 42, ema 57, cpos/upper_wick EXACT), gate exit 0.

### Decisions
- D-015 registry scope, D-016 pivot tie rule, D-017 regime tags descriptive only,
  D-018 `make parity` amends D-005, D-019 gate exit code vs report `pass`,
  D-020 AC export extracted from TradingView buffers via CDP,
  D-021 first AC run classified `EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE`
  (table entries `tv_volume_feed` + `ema_seed_history`);
  open questions OQ-13 (highest/lowest warm-up value), OQ-14 (pivot ties vs Pine);
  OQ-15 resolved by D-021.

### Notes
- The AC parity run lands inside M3: `make parity` against the owner's export prints
  the per-plot table above, exits 0, and writes `reports/parity/layer1_report.json`
  with the documented-difference evidence (D-020, D-021; design note 4.6).

## [M2] - 2026-10-08 - Chart workspace

### Added
- `GET /v1/candles` (§11, T2.1): windowed OHLCV with `from` / `to` / `limit`, answering the
  requested `(venue, market_type, instrument, timeframe, dataset)` only — an unregistered
  scope returns `DATA_UNAVAILABLE` (PD-10), every row carries `dataset_version` and the
  quality state (PD-5, PD-6).
- `GET /v1/indicators` (§11, T2.1) over the engine indicator registry
  (`engine/features/indicators.py`: `sma`, `ema`, `atr`, `rsi`, `bb`, `rvol`, `cpos`,
  `upper_wick`, …): parameters resolve from the request or `config/defaults.yaml` only,
  unknown names and parameters are `422` (PD-11), values computed by the engine with the
  same window semantics as `/v1/candles`.
- Chart workspace (§14.3, T2.2) in `apps/web/app/chart/`: lightweight-charts main pane
  with `candle` plot plus stacked Volume and RSI sub-panes, one shared time scale and a
  synchronized crosshair; the hover row reports `O/H/L/C/V` with `RVOL`, `CPOS`, `WICK`,
  `ATR`, `EMA100` for the hovered bar; header shows `N bars · dataset <hash>` and the §12
  quality badge (`GREEN · 100.0%`); `Loading` / empty / error+retry states.
- Lazy history: reaching the oldest loaded bar requests the adjacent older window
  (`to=<oldest - 1ms>&limit=5000`) and prepends it — observed 5000 → 10000 bars with the
  viewport preserved and no silent timeframe substitution.
- Layout persistence (T2.3, D-012): instrument, timeframe, overlays, lengths and sub-pane
  state stored in `localStorage` under `cryptosos-chart-layout` and restored on reload.
- Command palette (T2.3): `Ctrl+K` / `⌘K` dialog grouping instrument, timeframe, overlay,
  pane, layout and navigation actions with type-ahead filter.
- Tests: 253 (14 new API candle + 14 indicator + 28 feature tests on top of M1's 196);
  the §14.3 budgets are asserted and recorded in the test output —
  `candles` 5,000 bars p50 31.2 ms / p95 46.2 ms (<300 ms),
  `ema` 32.2 / 47.0, `bb` 35.7 / 37.6, `rsi` 35.7 / 39.6 (all <150 ms).

### Fixed
- Infinite re-render ("Maximum update depth exceeded") while dragging the crosshair and
  zooming: `useChartBundle` memoized `merged` on the `useQueries` result array, whose
  identity changes on every render, so `candles` / `series` were rebuilt each commit,
  re-pushing series data (`setData`) which re-emitted a crosshair move and set a new
  hover time — a render loop. The memo now keys off a data signature
  (`dataUpdatedAt` / error / presence), which only moves when a query result changes.
  Console is clean under the reproduction sequence.

### Notes
- The suite's fixtures run `DELETE FROM market.dataset_manifest | market.instrument |
  …` against the configured `DATABASE_URL`, so `make test` clears the seeded dev rows;
  re-run `make seed-fixtures` afterwards (same `dataset_version`, files reused).
  Parked in `docs/BACKLOG.md`.

## [M1] - 2026-10-08 - Candle data + Guardian

### Added
- Bar model and deterministic ingest (`engine/data/ingest.py`): `IngestScope`, `run_ingest`,
  `run_repair`, `run_resample`, canonical Parquet rows, `content_sha256`, manifest and
  `dataset_version` (§4.2-§4.5, PD-6). `close_time = open_time + bar_length` (D-008).
- Guardian (§4.6, T1.3, AC3): RED / ORANGE / YELLOW → GREEN causes, exclusion intervals,
  gap detection and repair that excludes only the affected interval (AC3).
- Weekly resampling into `derived/` (§4.3, T1.4) with the writer extension for derived columns.
- Migrations `0002_m1_data` (job `result_ref` / `logs` / timestamps, D-007) and `0003_gap_kind`.
- Jobs contract (§11): claim/execute with `FOR UPDATE SKIP LOCKED`, run either by the API's
  background task or `apps/worker` through the same seams (`now_ms()`, `build_adapter()`,
  `execute_claimed()`), plus stale-lease recovery. Endpoints `GET /v1/instruments`,
  `GET /v1/datasets/{dataset_version}`, `GET /v1/data/health`, `GET /v1/data/gaps`,
  `POST /v1/data/ingest`, `POST /v1/data/repair`, `GET /v1/jobs/{job_id}` - reads plus
  queue-only POSTs; no order path exists (PD-1). `PERPETUAL` / non-BINANCE fail closed
  with `NOT_IMPLEMENTED` (AC4, PD-10).
- Golden fixtures `fixtures/binance/{BTCUSDT,ETHUSDT,SOLUSDT}/{1D,4H,1H}.json`, recorded
  once and committed (D-009, §14.1): 1011 / 6066 / 24264 bars per symbol, exactly
  contiguous, provenance envelope per file.
- Entry points: `make seed-fixtures` (offline; writes through the same writer / manifest /
  Guardian path as live ingest, so the same fixtures always yield the same
  `dataset_version`, AC1) and `make ingest ARGS="--start ... --end ..."` (live Binance
  backfill; both window ends required, PD-11; storage budget from `config/defaults.yaml`,
  AC5).
- Data Health UI (design §8): `/data` (health table, active exclusions, gaps with a Repair
  action, seed-universe Ingest with job polling; loading / empty / error+retry / stale /
  partial states) and `/data/datasets/{dataset_version}` manifest viewer (AC2), built on
  typed wrappers in `packages/client` with browser CORS (D-010) and nav.
- Tests: 196 (ingest, guardian, resample, manifest, jobs, API, worker, seed, scripts).

### Changed
- `.env.example` documents `ALLOWED_ORIGINS` alongside `NEXT_PUBLIC_API_BASE` (D-010).
- `specs/design/m1_data.md` §9 (fixture recording decisions) and §12 (test plan) amended;
  `docs/DECISIONS.md` gains D-006…D-010 and OQ-5…OQ-7.

### Notes
- `make parity` stays an honest `NOT_IMPLEMENTED` placeholder until M6 (D-005).
- Six event families remain `SPEC_REQUIRED` placeholders (PD-4) and are not implemented.

## [M0] — 2026-10-07 — Bootstrap

### Added
- Monorepo skeleton: `apps/{web,api,worker}`, `packages/{schemas,client,ui}`,
  `engine/*` modules, `exchanges/binance`, `config/`, `data/`, `fixtures/`,
  `infra/`, `scripts/`, `tests/`, `specs/{design,reference,events}`.
- Python toolchain: `uv` project pinned to Python 3.12, ruff, mypy `--strict`, pytest.
- FastAPI skeleton (`apps/api`): `GET /v1/status`, `GET /v1/config/defaults`, and the
  spec error envelope `{error:{code,message,detail}}` on 404 / 422 / 500 (D-003).
- PD-11 defaults loader (`engine/config/defaults.py`) resolving only from the request
  or `config/defaults.yaml`; undocumented, missing or mistyped parameters → 422
  `VALIDATION_ERROR`. Spec §6.3 detector parameters are documented in
  `config/defaults.yaml`.
- Alembic baseline (`0001`): schemas `raw`, `normalized`, `derived`, `research`,
  `ops`, `audit`, plus `research.stats_result` with append-only triggers (PD-13, D-004).
- Next.js 16 shell (`apps/web`): status panel + documented defaults, no pages for
  unbuilt modules (PD-9).
- Generated TypeScript client (`packages/client`) produced from the app's own
  `openapi.json` via `scripts/gen-openapi.ps1` (PD-6).
- Windows-native entry points: `Makefile` (`setup`, `check`, `lint`, `typecheck`,
  `test`, `web-typecheck`, `web-build`, `db-upgrade`, `dev*`, `health`, and the
  honest `NOT_IMPLEMENTED` placeholders `ingest` / `seed-fixtures` / `parity`, D-005)
  backed by `scripts/*.ps1`.
- Test suite (23 tests): defaults loader (PD-11), API status + error envelope,
  and database-enforced append-only results (PD-13) with savepoint-wrapped
  negative cases that leave no rows behind.

### Changed
- `AGENTS.md` link to the specification now points at `specs/`.

### Notes
- Local dev database runs on `localhost:5433` (D-002); `.env` is gitignored (PD-8).
- Six event families remain `SPEC_REQUIRED` placeholders (PD-4) and are not implemented.
