# CryptoOS — Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/); version tags start at M0
(the spec defines no release numbering yet).

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
