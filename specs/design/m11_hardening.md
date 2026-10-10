# M11 design note — Hardening

Companion to IMPLEMENTATION_SPEC_v1.1.1 Sections 0 (CI gates), 14
(targets + the e2e core flow) and 15 (milestone M11).

Decisions recorded in `docs/DECISIONS.md` as D-075..D-081; open questions
OQ-27.

> M11 is the final milestone: no new features, only the gates that make
> the build releasable. Every check that is not already green becomes a
> CI gate (spec §0); every performance target gets a measured test; the
> e2e core flow gets a scripted run; the notebook, registry and
> experiments get a backup/export path; the docs get their final pass.

---

## 0. Scope

| Slice | Lands |
| --- | --- |
| A11y (T11.1) | the Master §61-62 states are already wired per page; M11 adds the automated keyboard/ARIA audit + the contrast/reduced-motion checks |
| Performance (T11.2) | the §14 targets measured as tests (5k-candle API <300ms; indicators 5k <150ms; events+stats 30-symbol <5s; single-symbol 10y backtest <5s; scanner 100 <2s; chart interactions <50ms) |
| E2E (T11.3) | the §14 core flow: open BTC 1D → toggle events → click CAPIT → Evidence (grade+n_eff+CI) → add to experiment → run backtest → validation → freeze → verify INTACT |
| Backup/export (T11.4) | export the notebook, the registry (hypotheses/experiments/families) and the experiments' lineage as a portable bundle; import validates + is idempotent |
| Docs (T11.5) | README/AGENTS/CHANGELOG/DECISIONS/BACKLOG final pass; every milestone's AC evidence recorded |

## 1. Performance gates (T11.2)

The §14 targets, each a test with a hard budget (skip-guarded when the
scratch cluster is unreachable; the unit half is deterministic):

| Target | Where |
| --- | --- |
| 5,000-candle API <300ms | `tests/test_perf_api.py` — GET /v1/candles limit=5000 |
| indicators 5,000 bars <150ms | `engine/features` pure-function timing |
| events+stats 30-symbol daily <5s | per-symbol budget x 30 (the seeded universe, OQ-24) |
| single-symbol 10y daily backtest <5s | the M6 simulator over ~2,500 bars |
| scanner 100 symbols 1D <2s | the per-symbol budget (already asserted in M8, skip-guarded) |
| chart interactions <50ms | the crosshair/hover handlers are synchronous; asserted via the pure map lookup |

A perf test that exceeds its budget fails CI (spec §0: no skip/xfail
without a DECISIONS entry — D-076 records the skip-guard policy for an
unreachable cluster).

## 2. E2E core flow (T11.3)

The §14 flow is scripted end-to-end against the real API + seeded data
(one run, one assertion per step, a single evidence artifact):

1. open BTC 1D (GET /v1/candles)
2. toggle events (GET /v1/events)
3. click the CAPIT marker (GET /v1/events/{id} → the Evidence payload:
   grade + n_eff + CI)
4. add to experiment (POST /v1/experiments)
5. run backtest (POST /v1/backtests → job succeeds)
6. open validation (GET /v1/validation/{id})
7. freeze the experiment (POST /v1/experiments/{id}/freeze)
8. verify INTACT (POST /v1/experiments/{id}/verify → status INTACT)

Each step asserts its spec contract; the run is deterministic (fixed
dataset_version, fixed seed).

## 3. Backup/export (T11.4)

`engine/backup.py` + `POST /v1/backups` + `GET /v1/backups/{id}`:

- A backup bundle is a JSON document with a schema version, the created-at
  timestamp, and three sections: `notebook` (notes), `registry`
  (hypotheses + experiment families + variants + experiments), and
  `lineage` (the backtest + validation refs).
- Export is read-only over the live rows; import validates every section
  (the frozen-object contract is preserved: a frozen experiment imports
  frozen, D-021) and is idempotent (re-importing the same bundle inserts
  nothing, ON CONFLICT DO NOTHING).
- No secrets, no data files: the bundle is metadata + lineage (PD-6).

## 4. A11y audit (T11.1)

The pages already ship the Master §61-62 states (loading/empty/error/
stale/partial) and ARIA roles. M11 adds:

- a keyboard walk: every interactive element reachable via Tab, every
  pane toggle/menu operable from the keyboard (the chart's non-canvas
  controls are native buttons/checkboxes — canvas is keyboard-operable
  through the palette);
- contrast: the CSS vars are checked against the WCAG AA ratio
  (the palette tokens already meet it; the test pins it);
- reduced motion: the chart's transitions honor `prefers-reduced-motion`.

## 5. Docs (T11.5)

README (run + gates), AGENTS.md (the conventions), CHANGELOG (M11 entry),
DECISIONS (D-075..D-081), BACKLOG (the remaining OQ items). Every
milestone's AC evidence is recorded in the CHANGELOG.

## 6. Test plan

| Suite | Covers |
| --- | --- |
| `tests/test_perf_api.py` | the §14 targets (skip-guarded on an unreachable cluster) |
| `tests/test_e2e_core_flow.py` | the §14 core flow, one run, one evidence artifact |
| `tests/test_backup.py` | export bundle shape + the three sections; import validation + idempotency + the frozen contract |
| `tests/test_a11y.py` | the contrast ratios + the reduced-motion rule |

## 7. Decisions to record

| ID | Decision |
| --- | --- |
| D-075 | Perf tests fail CI on budget overrun; an unreachable cluster skips (D-076) |
| D-076 | Live-DB suites skip (never hang/fail) when the scratch cluster is unreachable |
| D-077 | The e2e core flow is one deterministic run with a per-step contract assertion |
| D-078 | A backup bundle is JSON with schema_version + notebook/registry/lineage sections |
| D-079 | Import is idempotent (ON CONFLICT DO NOTHING) and preserves the frozen contract |
| D-080 | A11y: the contrast ratio is pinned by a test over the CSS vars |
| D-081 | The chart canvas is keyboard-operable via the command palette |

Open questions for the owner: **OQ-27** — the backup destination (a local
file vs. an export the UI downloads; defaults to the API returning the
bundle JSON, which the UI offers as a download).
