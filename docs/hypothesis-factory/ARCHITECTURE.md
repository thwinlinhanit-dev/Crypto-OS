# Hypothesis Factory — Architecture

**Module:** Hypothesis Factory (the master prompt's "Hypothesis Factory").
**Status:** Phase 0 (repository assessment) + Phase 1 (data model) landed.

## 1. Repository assessment

Verified capabilities (from the working tree + the running scratch cluster,
HEAD = migration 0010):

| Area | Exists | Reused for |
|---|---|---|
| Market data (M1) | `engine/data/ingest.py`, parquet store, `market.instrument` + `market.dataset_manifest`, the Guardian | the dataset registry (HF-7) reads the manifests; no second ingest |
| Features (M3) | `engine/features/` (indicators, primitives, regime) + parity layer 1 | the feature registry (HF-7.1) describes these; no rewrite |
| Events (M4) | `engine/events/` (RVOL_CLIMAX registry, outcomes, cooldown) | the event definition in a hypothesis |
| Stats (M5) | `engine/stats/compute.py` (pure, deterministic, bootstrap CIs, grading) + `research.stats_result` | the statistical engine (HF-11) |
| Experiments (M5b) | `research.experiment` / `experiment_family` / `variant`, freeze + verify + lineage | the experiment record (HF-10) |
| Backtests (M6) | `engine/strategy/` (simulator, fills, sizing, portfolio, funding) + `strategy.backtest` | the strategy-experiment runner |
| Validation (M7) | `engine/validation/` (levels, baseline, metrics) + `validation.run` | the validation center (HF-14) |
| AI/MCP (M9) | `engine/ai/` (registry, integrity, providers, runner, mcp) + `agents.run`/`agents.tool_call` | the decision-provider interface (HF-15) |
| Jobs (M1) | `engine/jobs.py` (enqueue/run seam, status tracking) | the orchestrator (HF-17) |
| Web (M2-M10) | Next.js pages, generated client, design tokens | the dashboard (HF-16) |

**Assumptions, not verified:** the M12–M17 visualization runtime (a separate
spec, not built); Bybit/OKX adapters (v1.1); on-chain/social data (no
source integrated).

**Integration risks:** the HF must not duplicate the stats/backtest/validation
engines — it *composes* them; the AI layer's executor already refuses unbound
tools (the Integrity Gate, commit 8ebecd7), so HF tools that need computation
must bind real handlers or return NOT_IMPLEMENTED.

## 2. Module boundaries (the 18 logical subsystems → cohesive modules)

```
engine/hypothesis/
  models.py       the formal hypothesis + version + status vocabulary
  registry.py     hypothesis CRUD, dedup keys, relationships, status rules
  discovery.py    the pattern-discovery engine (deterministic sources B/C)
  generator.py    the AI + human generation sources (A/D)
  priority.py     the research-priority engine (deterministic scoring)
  experiment.py   the experiment-spec builder (composes M5/M6/M7)
  stats.py        the forward-return + multiple-testing analysis (extends M5)
  robustness.py   the falsification suite (parameter/asset/regime/time/outlier)
  lineage.py      the evidence graph
  api.py          the FastAPI routers
  tests/          (tests/test_hypothesis_*.py)
```

## 3. The workflow (spec §17)

DISCOVER → NORMALIZE → DEDUPLICATE → CHECK DATA → FORMALIZE → PRIORITIZE →
HUMAN/POLICY APPROVAL → CREATE EXPERIMENT → RUN TESTS → MULTIPLE-TESTING
CONTROLS → FALSIFICATION → VALIDATE → REVIEW → ARCHIVE/ADVANCE.

Each transition is a status rule in `engine/hypothesis/models.py` (the same
one-way `locked` pattern as M5b/M7: a transition is a DB CHECK + a trigger
where the record must be immutable).

## 4. Decisions (HF-001..HF-006, recorded in docs/DECISIONS.md)

- HF-001: the HF composes M5/M6/M7 engines; it never re-implements a test.
- HF-002: a hypothesis is a versioned, append-only record (D-021 pattern).
- HF-003: the status vocabulary is the spec §14.2 list, a DB CHECK.
- HF-004: discovery methods are deterministic code; AI only proposes.
- HF-005: the multiple-testing family + correction is declared BEFORE the
  inferential analysis (spec §12).
- HF-006: the HF reuses `research.*` tables + adds `research.hypothesis_*`
  where the existing hypothesis table is insufficient (it becomes the
  hypothesis's backing table via a new status column set + the version table).

## 5. Phase plan (spec §24, adapted to this repo)

| Phase | Scope | Status |
|---|---|---|
| 0 | Repository assessment + plan | **done** (this document) |
| 1 | Hypothesis data model (records, versioning, status, provenance, API) | **done** (migration 0011 + engine + tests) |
| 2 | Manual research workflow (question entry, editor, backlog, detail) | next |
| 3 | Experiment integration (spec builder → M5/M6/M7) | planned |
| 4 | Statistical analysis + multiple-testing controls | planned |
| 5 | AI hypothesis generation | planned |
| 6 | Falsification + validation | planned |
| 7 | Decision providers + MCP | planned |
| 8 | Operational hardening | planned |
