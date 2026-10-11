# Hypothesis Factory — Data & Feature Contracts

## 1. Principles

The HF reuses Crypto OS's existing data contracts (M1/M3/M5) rather than
defining parallel ones. Every hypothesis references:

- a **dataset_version** (the M1 manifest SHA-256 — "what exactly did this
  use?");
- **feature versions** (the M3 `engine/features` definitions + the resolved
  params);
- a **code version** (the engine version + the commit).

## 2. Dataset registry (spec §7)

Read from the existing `market.dataset_manifest` (M1): dataset_id (the
manifest's dataset_version), provider (venue), data type, assets, timeframes,
start/end, granularity, schema version, update frequency, missing-data
characteristics, known limitations, last ingestion, data-quality status
(the Guardian's assessment), revision behavior.

The HF adds no ingest path; it *reads* the registry and refuses a hypothesis
whose required data is unavailable (fail-closed, PD-10).

## 3. Feature registry (spec §7.1)

A hypothesis's `features` array references registry entries; each entry:
name, definition, formula, input datasets, required columns, units, lookback,
calculation timestamp, earliest availability, missing-value behavior,
normalization, version, test coverage.

Agents cannot silently invent or redefine feature names: `validate_definition`
rejects an unknown feature (the same fail-closed rule as the AI tool
executor's Integrity Gate).

## 4. Data-quality gate (spec §7.3)

Before an experiment runs, the HF checks (via the M1 Guardian + the manifest):
duplicate timestamps, missing bars, invalid prices/volumes, ordering,
symbol consistency, timezone consistency, unexplained gaps, stale values,
survivorship, symbol changes, leakage, feature-availability violations.

The report is stored with the experiment. Material unresolved issues mark the
experiment INVALID_EXPERIMENT — never silently successful (spec §7.3).

## 5. The formal hypothesis record (spec §6)

Every hypothesis carries: id, title, version, description, domain, origin,
original question/prompt (verbatim for human input), mechanism, null
hypothesis, alternative, independent variables, dependent variables, feature
definitions, population/universe, timeframe, event definition, observation
timestamp, outcome horizon, expected direction, baseline, inclusion/exclusion
criteria, confounders, data requirements, proposed statistical method,
proposed robustness tests, falsification criteria, economic threshold, known
limitations, status, parent/related ids, created_at, creator/agent identity,
provenance refs.

## 6. Immutability (spec §18.1)

Once an experiment starts, its effective configuration is not silently
edited: a change produces a new version/run. Stored: config hash, dataset
version/fingerprint, code commit, feature versions, seeds, environment,
start/completion timestamps, artifact refs. (The M5b freeze/verify pattern,
D-021.)

## 7. Storage

Structured records in PostgreSQL (`research.hypothesis*` — see migration
0011); large artifacts follow the M1 parquet convention with metadata +
provenance in the DB (spec §18).
