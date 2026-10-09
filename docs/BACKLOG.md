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
- **Experiment families / variants / sweep runner** - the multiple-testing
  banner ships with the M5b shape (`variant_count: 1`, `PRE_SPECIFIED`); real
  `variant_count`, `selection_method` and FDR/White's Reality Check/SPA wait for
  M5b records.
- **Experiment freeze / verify / lineage** - M5b: frozen experiments must lock
  referenced results and verify sha256 hashes (spec 8.5).
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
