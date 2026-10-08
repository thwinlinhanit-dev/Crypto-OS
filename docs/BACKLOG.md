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
