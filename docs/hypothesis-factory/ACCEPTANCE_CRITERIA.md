# Hypothesis Factory — Acceptance Criteria

Evidence per phase (spec §25: "For every acceptance criterion, provide
evidence of completion. If something cannot be completed, mark it explicitly
as incomplete.").

**Verification status (2026-10-12, HEAD `bddefb1`):**
- **91 of the 152 HF tests pass right now** — every DB-free engine test,
  0 failures, 0 errors (part of the 647-passed + 1-skipped no-database layer).
- **61 DB-backed HF tests are not re-run** — the scratch cluster is down (no
  listener on `:5433` / `:54333`); their last confirmed state is the full-suite
  **970 passed + 2 skipped** at `846206d` (Phase 8). The guarded fixtures skip
  via `connect_timeout=3` (D-076); they do not hang.
- Phase 2 UI: build-verified only (`tsc` clean, `next build` passes with
  `/hypothesis-factory` prerendered, 2026-10-11).

PD-3: nothing below is claimed greener than it is.

## Phase 0 + 1 — Assessment & hypothesis data model ✅ (`2c1b1d3`)

| AC (spec §24 Phase 1) | Evidence |
|---|---|
| Hypotheses can be created, retrieved, versioned and audited | `tests/test_hypothesis_registry.py` — create → version → read back → audit rows |
| Schema validation rejects an underspecified hypothesis | `tests/test_hypothesis_models.py` — missing null/alt/features/horizon → ValidationError |
| Status transitions follow explicit rules | `tests/test_hypothesis_models.py` — the transition table + illegal transitions raise |
| Relationships form an evidence graph | `tests/test_hypothesis_registry.py` — derived-from / contradicts / supersedes edges |
| The research ledger records the testing family | `tests/test_hypothesis_registry.py` — every experiment's family is written before analysis |

(`test_hypothesis_models.py` is DB-free and re-verified 2026-10-12; the three
`registry` ACs are DB-backed — last green at `846206d`, pending the cluster.)

## Phase 2 — Manual research workflow ✅ (`83af84b`)

- AC: the owner can create and manage hypotheses without an AI provider.
- Evidence: `apps/web/app/hypothesis-factory/page.tsx` — research-question
  entry (verbatim preservation) → hypothesis editor → definition validation →
  stored-hypothesis view + the §14.2 evidence ladder; the nine HF client
  functions in `packages/client/src/api.ts`; the nav link. PD-5: every status
  and number comes from a server response — the page never computes or decides.
- Verified 2026-10-11: `tsc --noEmit` clean; `next build` prerenders
  `/hypothesis-factory`; ruff + mypy strict (194 files) clean. Not
  e2e-tested — no browser test covers this page yet.

## Phases 3–8 ✅ (`7376c3a` … `846206d`)

The AC text for these phases lives in the master prompt §24, which is not
committed to this repo (PD-4: no criterion is restated or invented here).
Evidence is mapped to each phase scope in `IMPLEMENTATION_PLAN.md` and that
phase's own tests:

| Phase (scope) | Commit(s) | Evidence (tests collected) | State 2026-10-12 |
|---|---|---|---|
| 3 — experiment integration | `7376c3a`, `0a2693f` | `test_hypothesis_experiment.py` (17) + `test_hypothesis_runner.py` (6) | experiment ✅ passed; runner DB-pending |
| 4 — statistical analysis | `8730a17` | `test_hypothesis_stats.py` (15) | ✅ passed |
| 4/5 — falsification evaluator + transition decision | `48b09a7` | `test_hypothesis_evaluate.py` (21) | ✅ passed |
| 5 — AI hypothesis generation | `462821b` | `test_hypothesis_generate.py` (14) | DB-pending (last green `846206d`) |
| 6 — robustness & falsification suite | `38c205f` | `test_hypothesis_robustness.py` (21) | ✅ passed |
| 7 — MCP tools + decision provider | `50dc568` | `test_hypothesis_mcp.py` (12) | DB-pending (last green `846206d`) |
| 8 — operational hardening | `846206d` | `test_hypothesis_hardening.py` (15) + migration `0012` (live-applied) | DB-pending (last green `846206d`) |

Totals: 152 HF tests = **91 passed today** (DB-free) + **61 DB-pending**.

## Global non-goals (never claimed)

- Live-trading authorization — none exists (PD-1).
- MCP order tools — none exist (D-063).
- The M12–M17 visualization runtime — a separate spec, not built.
- Bybit/OKX adapters, on-chain/social data — no source integrated.

## Incomplete features (spec §25 disclosure)

- **DB-backed verification gap (the only suite gap):** the 61 DB-backed HF
  tests — and the rest of the live-database suite — have not been re-run
  since `846206d`; the scratch PG18 cluster is down and cannot be revived
  from the current shell. Re-run the full suite once it is recovered.
- **Phase 2 e2e:** no browser/e2e test covers `/hypothesis-factory`; only
  build + prerender verification exists.
- The HF UI is one workflow page (the Phase 2 research loop); the spec §20
  dashboard views beyond it (Discover, Experiment Lab, Validation Center,
  Evidence Graph, Reports) are not built.
