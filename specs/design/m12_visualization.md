# Visualization Runtime — M12 Design Note (spec §38.7: build M12 only)

**Source of truth:** `specs/CRYPTOOS_PROGRAMMABLE_VISUALIZATION_RUNTIME_SPEC_v1.0.md` (§3 non-negotiables, §6 program schema, §8 runtime pipeline, §9 data API, §10 sandbox, §22 provenance, §23 replay, §28 DDL, §30 testing, §36 M12).

**What M12 is:** the foundation — visualization schema + registry + sandbox interface + deterministic data API abstraction + provenance contract + simple line/HUD renderer + replay/as_of enforcement.

**What M12 is NOT:** the Market Canvas (M13), microstructure (M14), derivatives (M15), AI generation (M16), research bridge (M17). No orderbook, liquidation, options, on-chain, or AI code generation — §38.10 forbids them until their dependencies exist.

## Reuse, not rebuild (HF-001 pattern)

The visualization runtime is "a controlled projection layer" (§1) over the existing engines. M12 composes what exists:

| Need | Existing asset | Reuse |
|---|---|---|
| as_of / no-future-leak | `engine/scanner/replay.py` (D-055) | the replay invariant (§23) is the same PD-2 guarantee |
| provenance classes | `engine/events/base.py` `OBSERVED/CALCULATED/INFERRED` | §22's four classes extend the event vocabulary with `AI` |
| program hash determinism | HF `ExperimentConfig.config_hash()` (sha256 canonical JSON) | the same canonical-JSON sha256, process-stable |
| sandbox trust boundary | M9 `engine/ai/registry.py` closed registry + `-32601` unknown tool | an undeclared dataset is refused, never fetched |
| dataset quality states | `engine/market/live.py` (GREEN/OPEN/REPAIRED) + `engine/data/guardian.py` | the quality block on every dataset response (§9) |
| immutable versions | HF migration 0011 `hf_version_unique` + `research.experiment.frozen` | behavior cannot silently change under a version (§6) |
| fail-closed validation | `engine/errors.py` `ValidationError` (422) | every M12 module raises it, never returns a default |

## The new modules (engine/visualization/)

### `models.py` — the closed vocabularies (pure)

- `LIFECYCLE` = `DRAFT → VALIDATED → PUBLISHED → DEPRECATED` (§6; an explicit transition table, like HF status)
- `PROVENANCE_CLASSES` = `OBSERVED, CALCULATED, INFERRED, AI` (§22)
- `RENDER_KINDS` = the §13 primitives M12 implements: `LINE, HUD, TABLE` (candle overlay lands with M13; heatmap/profile/footprint later)
- `DATASETS` = the §9 initial set: `candles, trades, orderbook, open_interest, funding, liquidations` — the declared `required_datasets` vocabulary
- `QUALITY_STATES` = `GREEN, OPEN, REPAIRED, DATA_UNAVAILABLE, DATA_INCOMPLETE` (§9)
- `assert_valid_transition(current, target)` — the lifecycle table (§6: no silent behavior change)

### `program.py` — the versioned program + its hash (pure)

- `VisualizationProgram` — the §6 required metadata (id, version, title/description, source, runtime, program_hash, datasets, features/events, parameters, permissions, outputs, performance budget, created_by)
- `program_hash(source, id, version, runtime)` — **sha256 of the canonical JSON**, deterministic across processes (the HF `config_hash` lesson: never Python's `hash()`)
- `validate_program()` — fail-closed (§3): a program that declares a dataset outside the vocabulary, a forbidden permission (network/filesystem/trading), or an unknown render kind is **refused before it is stored**
- `permissions` defaults to `network: false, filesystem: false, trading: false` — a program cannot request `trading: true` (§3: a visualization program cannot place/cancel/modify orders)

### `data_api.py` — the deterministic dataset API (pure)

- `DatasetResponse` — the §9 envelope (`dataset_version, schema_version, source, venue, instrument_id, timeframe, as_of, quality`) + rows
- `assert_datasets_declared(declared, requested)` — the permission-safety property test (§30): **an undeclared dataset is refused, never fetched** (the M9 closed-registry pattern)
- `truncate_as_of(rows, as_of)` — the §23 replay invariant: rows with `ts > as_of` are removed; `program(full, as_of=t) == program(truncate_at_t)` is asserted in tests
- `quality_of(rows, expected)` — `DATA_UNAVAILABLE` (no rows at all) vs `DATA_INCOMPLETE` (partial coverage); **missing data fails explicitly, never substitutes** (§3)

### `provenance.py` — the provenance contract (pure)

- `ProvenanceValue` — a value tagged with its class; `classify()` maps computed/observed/inferred/AI
- `assert_distinguishable(values)` — §3: observed/calculated/inferred/AI must remain distinguishable; a mixed list without tags is refused
- No `signal strength /100` (§3): there is no scoring API in M12

### `render.py` — the line/HUD/table renderer (pure)

- `LineSeries`, `HudElement`, `TableRow` — the §13 primitives
- `render_line(series) -> RenderGraph` — the golden fixture (§30.1): deterministic output for identical input
- `render_hud(elements)` — the HUD primitive (§30.3)
- The render graph is declarative JSON (the web app renders it; the engine never computes client-side stats, PD-5)

### `sandbox.py` — the sandbox interface (pure, the boundary)

- `SandboxRequest` — the program + declared capabilities + as_of
- `assert_no_forbidden_capabilities(program)` — §10: network/filesystem/trading denied; a program requesting them is refused before execution
- `run_program(program, datasets, params, as_of)` — the deterministic pipeline (§8): source → validate → data API (as_of-gated) → compute → render graph. **The M12 compute is the pure functions above**; the §8 worker/WASM backends are explicitly deferred (backend parity is a gate for later, §30)

## What M12 defers (and why)

- **Worker/WASM backends** (§8.291) — "MVP may use an isolated worker runtime"; backend parity is a §30 property test for when WASM exists. M12's pure functions are backend-agnostic by construction.
- **Candle/heatmap/profile/footprint/orderbook renderers** (§13–19) — M13/M14; §38.10 forbids them now.
- **Stateful visualizations** (§11), **options/GEX** (§21), **liquidations model** (§16) — M13+.
- **AI program generation** (§25) — M16; an AI-generated program is untrusted code validated before execution (§3).
- **The DB tables** (§28) — M12 ships the engine + tests; the `visualization.program/run/snapshot` migration lands with the API slice (the same sequencing as HF: migration + engine + tests per phase).

## Testing (spec §30)

`tests/test_visualization_*.py`:
- **parser/program validation** — the fail-closed paths (unknown dataset, forbidden permission, unknown render kind, bad lifecycle transition)
- **determinism** — same inputs → identical outputs, incl. `program_hash` across two calls
- **truncation invariance** — `as_of=t` equals history truncated at `t` (the §23 invariant, asserted directly)
- **permission safety** — an undeclared dataset is refused
- **provenance** — the four classes stay distinguishable; a mixed untagged list is refused
- **golden fixtures** — line, HUD, replay, missing data, unavailable data, permission violation, deterministic rerun, version change (§30's M12-applicable set)

Live-DB suites stay skip-guarded (D-076); M12's engine tests are pure (no DB), matching the HF models/stats/robustness pattern.

## Decisions to record (docs/DECISIONS.md)

- D-077: the visualization runtime reuses the event provenance vocabulary (OBSERVED/CALCULATED/INFERRED) and adds AI as the fourth class (§22).
- D-078: `program_hash` is sha256 of canonical JSON — never Python's `hash()` (process-randomized; the HF ref_id lesson).
- D-079: the M12 sandbox refuses undeclared datasets before execution — the M9 closed-registry pattern applied to datasets.
- D-080: M12 ships pure engine functions; the §8 worker/WASM backends and the §28 DB tables are deferred to the API/canvas slices.
