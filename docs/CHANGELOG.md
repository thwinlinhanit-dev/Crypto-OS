# CryptoOS — Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/); version tags start at M0
(the spec defines no release numbering yet).

## [M5b] - 2026-10-10 - Experiments, families, freeze/verify, lineage

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
