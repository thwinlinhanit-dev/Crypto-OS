# Hypothesis Factory — Implementation Plan

Phase-0 assessment complete; Phase 1 landed. Adapted to Crypto OS
conventions (ruff + mypy strict + pytest gates; engine modules are pure;
API routers under `apps/api/`; migrations under `infra/migrations/versions/`).

## Phase 0 — Repository assessment ✅

See ARCHITECTURE.md §1. Verified: data, features, events, stats, experiments,
backtests, validation, AI/MCP, jobs, web all exist and are reusable.

## Phase 1 — Hypothesis data model ✅ (this commit)

- migration `0011_hypothesis_factory`: `research.hypothesis_version`
  (the formal spec: null/alt, features, population, horizon, thresholds,
  falsification criteria), `research.hypothesis_relationship` (the evidence
  graph edges: derived-from / contradicts / refines / supersedes / …),
  `research.research_ledger` (the multiple-testing family — spec §12), plus
  the status + origin + domain vocabularies as DB CHECKs.
- `engine/hypothesis/models.py`: the status/relationship/origin vocabularies,
  the pure status-transition rules, the economic-significance split.
- `engine/hypothesis/registry.py`: hypothesis CRUD, dedup keys (deterministic
  first, AI-assisted optional), relationships, the ledger.
- `apps/api/hypothesis_factory.py`: the REST surface (Phase 1 slice).
- `tests/test_hypothesis_*.py`: the Phase 1 AC.

## Phase 2 — Manual research workflow

Research-question entry (verbatim preservation) → hypothesis editor →
definition validation → dataset/feature selection → backlog → detail page.
No AI provider required (spec §24 Phase 2 AC).

## Phase 3 — Experiment integration

The spec builder composes M5 (`compute_stats`), M6 (`simulate`) and M7
(`validation`) — never a duplicate engine. Job execution via the M1 job
seam; config hashes; reproducibility metadata.

## Phase 4 — Statistical analysis

Forward-return analysis + CIs + effect sizes + the multiple-testing controls
(BH first) + baseline comparisons + robustness checks + reports.

## Phase 5 — AI hypothesis generation

The provider abstraction (reuses the M9 `LLMProvider`); structured generation;
classification; duplicate review; gap suggestions; experiment-design
assistance. Every AI output is a proposal, never a finding.

## Phase 6 — Falsification & validation

The adversarial workflows (outlier dependence, parameter/asset/regime/time
robustness, negative controls) + the validation-status rules: a promising
candidate cannot be PASSED while mandatory checks are missing or failed.

## Phase 7 — Decision providers & MCP

Configured decision-model providers (or the deterministic local baseline) +
the MCP tool exposure through the existing M9 server (extend, not replace).

## Phase 8 — Operational hardening

Resource controls (spec §17.2), error handling, security review, audit
logging, performance, docs, backup/recovery, end-to-end testing.

## Sequencing rule

Phase 7 (agent integration) does not start while research correctness is
untested (spec §24: "Do not start Phase 7 while core research correctness is
untested merely to demonstrate agent integration").
