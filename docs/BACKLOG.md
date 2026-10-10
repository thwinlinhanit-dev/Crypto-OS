# CryptoOS — Backlog

Ideas and defects parked out of scope (PD-9). Each entry says why it is not being
built now and what would have to be true to pick it up.

## Test isolation

- **Fixtures clear the configured dev database.** `tests/test_api_*.py`,
  `tests/test_jobs.py`, `tests/test_ingest.py`, `tests/test_manifest.py`,
  `tests/test_seed.py`, `tests/test_instruments.py` and `tests/test_worker.py`
  run `DELETE FROM market.dataset_manifest | market.instrument | system.job |
  system.gap | system.data_quality | system.audit_log` against
  `get_settings().database_url`, i.e. the developer's own database rather than a
  disposable one. After `make test` the seeded fixture rows are gone and the chart
  answers `DATA_UNAVAILABLE` until `make seed-fixtures` re-registers them (the
  Parquet files themselves are untouched and the same `dataset_version` comes back).
  Found while collecting M2 browser evidence.
  To pick it up: give the suite its own database (or truncate only rows it created)
  so `make test` and `make check` can run at any time without a follow-up seed.
- **DB-backed tests need one reachable database.** `get_settings().database_url`
  defaults to `localhost:5432`; when nothing answers there the connection attempts
  hang (no fail-fast), so the suite appears to hang rather than fail. M5 was
  verified against a throwaway cluster on `127.0.0.1:54333` (`initdb --auth=trust`,
  `pg_ctl ... -o "-p 54333 -c listen_addresses=localhost -c autovacuum=off"`,
  `createdb cryptoos`) with `DATABASE_URL` exported per shell; PostgreSQL 18's
  autovacuum child dies under restricted job objects (`0xC0000142`), so it must be
  disabled for the scratch cluster. To pick it up with the entry above: a
  session/database fixture (per-run schema or container) plus a fail-fast timeout
  in `get_engine()` for a clean "database unreachable" message.

## M5 park list (design note `specs/design/m5_stats.md` section 12)

- **Event studies as async jobs** - `POST /v1/event-studies` with job plumbing;
  M5 computes synchronously inside the request. To pick it up: job/worker flow
  from M1 plus M5b's table work.
- **Experiment families / variants / sweep runner** - real `variant_count`,
  `selection_method` and FDR/White's Reality Check/SPA arrived with M5b
  (tables, CRUD API, ad-hoc recording); the async sweep runner itself stays
  parked below.
- **Experiment freeze / verify / lineage** - arrived with M5b: frozen
  experiments lock referenced results, verify recomputes sha256 hashes, and
  `GET /v1/experiments/{id}/lineage` serves the read-only chain (spec 8.5).
- **`CUSTOM` cluster model** - block definition lives in an experiment config
  that does not exist before M5b; M5 returns 422 for it rather than inventing a
  rule (PD-4).
- **Job-based stats for large universes** - the pure bootstrap is fine at
  fixture scale; moving it behind a job (and job-based evidence recompute) is
  parked for universes above a few thousand events.
- **Analogues by outcome** - M5 selects the 5 most recent same
  detector@version+type peers without conditioning on outcomes; similarity
  ranking needs an M9+ scoring rule.
- **Notebook hypotheses/graveyard UI** - the API exists (T5.4); the Research
  section gains the hypothesis and Graveyard pages with the experiment views
  in M5b/P0.
- **Async sweep runner** - `POST /v1/sweeps` with job plumbing (the engine that
  runs a matrix of evaluations belongs with the M6 backtest simulator); the
  M5b tables + `selected` flag are ready for it. To pick it up: M6 job/worker
  flow plus the M5b family/variant rows.

## M5b park list (design note `specs/design/m5b_experiments.md` section 7)

- **T5b.4 Copilot/MCP experiment tools** - the AI service does not exist before
  M9 (PD-9). To pick it up: M9's Copilot/MCP surface plus the M5b endpoints.
- **Experiments page UI** - shipped with M5b (T5b.3):
  `apps/web/app/research/experiments/page.tsx` (list + detail with freeze,
  verify and lineage). The Research section still has no Hypotheses or
  Graveyard page (those APIs exist; the pages are P0 once the section
  layout is set).
- **Strategy/backtest/validation/conclusion lineage nodes** -
  `refs.backtests`, `refs.validations`, `refs.baselines` and the matching
  lineage stages need the M6/M7 tables; M6 lands `strategy.backtest` +
  `strategy.trade`, so the lineage endpoint can now resolve backtest refs
  (M7 adds the rest). `GET /v1/experiments/{id}/lineage` names them under
  `not_implemented` in the meantime.

## M6 park list (design note `specs/design/m6_backtest.md` section 9)

- **Parity layer 2 (T6.5)** - `engine/parity/layer2.py` + the trade-list
  comparison against the owner's TradingView export (OQ-22); until the
  export exists the suite runs skip-guarded (the h=10 precedent). This is
  the only unbuilt M6 task card; T6.1-T6.4 are shipped.
- **Short-side strategies (R11)** - M10 with the perp adapter; parsed but
  422 today (D-043).
- **Random-baseline entry mode (7.6)** - M7 validation surface.
- **`REALISTIC_INTRABAR` (R8)** - reserved, NOT_IMPLEMENTED.
