# Hypothesis Factory — Acceptance Criteria

Evidence per phase (spec §25: "For every acceptance criterion, provide
evidence of completion. If something cannot be completed, mark it explicitly
as incomplete.").

## Phase 1 — Hypothesis data model ✅ (commit pending)

| AC (spec §24 Phase 1) | Evidence |
|---|---|
| Hypotheses can be created, retrieved, versioned and audited | `tests/test_hypothesis_registry.py` — create → version → read back → audit rows |
| Schema validation rejects an underspecified hypothesis | `tests/test_hypothesis_models.py` — missing null/alt/features/horizon → ValidationError |
| Status transitions follow explicit rules | `tests/test_hypothesis_models.py` — the transition table + illegal transitions raise |
| Relationships form an evidence graph | `tests/test_hypothesis_registry.py` — derived-from / contradicts / supersedes edges |
| The research ledger records the testing family | `tests/test_hypothesis_registry.py` — every experiment's family is written before analysis |

## Phase 2 — Manual research workflow ⬜ (not yet built)

- AC: the owner can create and manage hypotheses without an AI provider.

## Phases 3–8 ⬜ (not yet built)

Each phase's AC is listed in the master prompt §24; none are claimed here.

## Global non-goals (never claimed)

- Live-trading authorization — none exists (PD-1).
- MCP order tools — none exist (D-063).
- The M12–M17 visualization runtime — a separate spec, not built.
- Bybit/OKX adapters, on-chain/social data — no source integrated.

## Incomplete features (spec §25 disclosure)

Phases 2–8 are incomplete by design (this commit lands Phase 0 + Phase 1).
The HF has no UI, no experiment runner, no AI generation, and no
multiple-testing engine yet — only the data model and its API.
