# CryptoOS — Agents Guide

This document defines how coding agents should work on the CryptoOS repository: the operating rules, workflow, collaboration protocols, and project context. It is the companion to [IMPLEMENTATION_SPEC_v1.1.1 (1).md](./specs/IMPLEMENTATION_SPEC_v1.1.1%20(1).md).

---

## 1. Purpose

Agents are autonomous contributors on the CryptoOS team. Every action they take must:

- Respect the specification's constraints (no invented trading rules, no lookahead, no silent fallbacks).
- Keep the repository reproducible, immutable, and auditable.
- Follow the agreed task loop and quality gates.
- Report acceptance-criteria evidence clearly.

This file is the single source of truth for agent behavior. If a spec's rule conflicts with this file, the spec wins.

---

## 2. Agent Operating Rules

These rules (PD-1 through PD-13) are binding on every agent and every PR.

### PD-1 — No Order Placement
No code path, endpoint, MCP tool, or credential for placing orders exists in v1.
- Public read-only market endpoints are the only layer exposed.
- Live trading requires a separate, owner-approved spec.
- **Violation signal:** any agent touches an order-execution path → stop and ask.

### PD-2 — No Lookahead
Every feature, event, signal, and output uses only bars closed at or before its timestamp.
- Truncation-invariance tests must pass.
- Any implementation that references future bars is a blocking bug.

### PD-3 — Honesty Over Green
Never loosen a test, tolerance, threshold, or pre-registered criterion to make a test pass.
- Report failures plainly.
- If evidence is weak, say so. A red test with a clear explanation is more valuable than a green test with a hidden approximation.

### PD-4 — Spec Before Code
If a definition is ambiguous or missing → stop and ask.
- Never invent trading rules, thresholds, or parameter defaults.
- Event rules come **only** from `specs/events/*.md` or the reference Pine file.
- If a detector/event has `status: SPEC_REQUIRED` (e.g. `FAST_RECLAIM`, `TOP_CAPIT`, `TOP_CLIMAX`, `TOPFAIL`, `CAPITFAIL`, `CAPIT_RECLAIM`), do not implement or approximate it.

### PD-5 — Numbers Come from Computation
UI/AI display only values produced by engine modules.
- Each engine module result carries `ref_id` + `dataset_version`.
- The UI never hard-codes numeric results it cannot trace to an engine output.

### PD-6 — Reproducibility
Every result stores: `dataset_version`, `dataset_schema_version`, `engine_version`, `code_version`, `resolved_params`, `seed`.
- Same inputs → identical outputs.
- The reproducibility test (re-run == identical numbers) is a blocking CI gate.
### PD-7 — Immutable Data
Raw/normalized bars are never edited in place.
- Corrections create a new `dataset_version`.
- Any in-place mutation of data files is a critical integrity violation.

### PD-8 — No Secrets in Repo
- `.env` is gitignored.
- `.env.example` holds documented template keys only.
- No real API keys, tokens, or credentials anywhere in the repo (code, docs, tests, configs).

### PD-9 — Scope Discipline
- Build only the current milestone.
- Park future ideas in `docs/BACKLOG.md`.
- Do not create UI pages for unbuilt modules.

### PD-10 — No Silent Data Substitution
If the requested `(venue, market_type, instrument, timeframe, dataset)` is unavailable or incomplete:
- Return `DATA_UNAVAILABLE`, `DATA_INCOMPLETE`, or `NOT_IMPLEMENTED`.
- Never fall back to another venue, spot↔perp, timeframe, or dataset.

### PD-11 — No Silent Parameter Defaults
Parameters resolve only from:
1. The request, or
2. `config/defaults.yaml`.
- Missing or undocumented parameters → HTTP 422.
- Research-relevant Pydantic fields carry no hidden defaults.
- Every result stores `resolved_params`.

### PD-12 — No Silent Universe Changes
Stats, event studies, backtests, and validations require:
- Explicit `instrument_ids`, `data_scope`, and (for backtests) `fill_model`.
- Results store them.
- A changed universe is a new run.

### PD-13 — No Result Overwrite
Result tables are append-only (only `lock`/`freeze` flags may change).
- Recalculation creates a new `ref_id` with `supersedes`.
- Result identity = `(ref_id, dataset_version, engine_version, code_version)`.

---

## 3. Task Loop

Every task follows this loop:

```
read task card
  → design note if >150 LOC (specs/design/)
  → tests first for logic
  → implement
  → make check
  → CHANGELOG entry
  → report with acceptance-criteria evidence
```

**Key rules:**
- Tests before implementation for logic changes.
- `make check` must pass before anything else.
- A CHANGELOG entry is required for every user-visible change.
- The final report must cite acceptance-criteria evidence (test names, outputs, gate results).

