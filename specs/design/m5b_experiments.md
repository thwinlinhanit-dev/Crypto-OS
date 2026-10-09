# M5b design note — Experiments, families, immutability, lineage

Companion to IMPLEMENTATION_SPEC_v1.1.1 Section 8.4 (research objects and
multiple testing), 8.5 (immutability of experiments and results), 10 (data
model), 11 (API), 12 (UI: Experiments page) and Section 15 (milestone M5b,
T5b.1–T5b.4).

Decisions recorded in `docs/DECISIONS.md` as D-033..D-037; questions OQ-21.

> Scope discipline (PD-9): M5b builds T5b.1–T5b.3. T5b.4 (Copilot/MCP experiment
> tools) belongs to the AI service that does not exist before M9 and is parked
> in `docs/BACKLOG.md`. Sweeps stay a runner that records variants; the async
> job/worker plumbing for `POST /v1/sweeps` is part of M6 (backtests) and is
> likewise parked.

---

## 0. Acceptance-criteria mapping

| AC (M5b) | Where it lands |
| --- | --- |
| frozen experiment and all referenced results reject UPDATE/DELETE | migration 0005 `experiment_frozen` trigger + 0001 `stats_result` trigger; `tests/test_experiments.py::test_frozen_experiment_rejects_mutation`, `test_freeze_locks_referenced_results` |
| verify detects tampering | `GET /v1/experiments/{id}/verify`; `test_verify_intact` / `test_verify_detects_tampering` (tamper applied with triggers bypassed, so the sha256 layer is what catches it) |
| every stored result resolves to its dataset manifest | creation requires a registered `market.dataset_manifest` row (422 otherwise); `test_create_experiment_requires_registered_dataset`, lineage dataset node links to `/v1/datasets/{dataset_version}` |
| lineage nodes all link | `GET /v1/experiments/{id}/lineage`; `test_lineage_links` asserts every node is `OK` and the stored nodes carry links |
| singleton family auto-created for ad-hoc runs | `POST /v1/stats` records a family + variant for every run; `test_ad_hoc_run_creates_singleton_family`, `test_family_variant_count_grows_across_runs` |

---

## 1. Tables (T5b.1, spec section 10)

Migration `0005` transcribes the DDL verbatim and adds:

- `ck_experiment_data_scope` / `ck_experiment_selection_method` — the spec's own
  inline CHECKs.
- `ck_experiment_status` (ADDITION, D-033): the 8.4/11 status vocabulary
  (`IDEA, RUNNING, PROMISING, SUPPORTED, WEAK, REJECTED, VALIDATED`) so a typo
  cannot become a silent state (same precedent as 0002/0004 CHECKs).
- `variant` is append-only with one exception (D-034): the trigger rejects
  DELETE outright and UPDATE of any column except `selected`. The DDL's own
  comment says "append-only", and `selected boolean DEFAULT false` exists
  precisely so a sweep can record which cell it picked; the exception mirrors
  PD-13's "only lock/freeze flags may change" and 0001's `stats_result`
  trigger.
- `experiment` rejects UPDATE and DELETE outright once `frozen` (spec 8.5).
  Unfrozen rows stay patchable through `PATCH /v1/experiments/{id}`.
- `experiment_family` rejects DELETE once variants or experiments reference it
  (a family is the record of a search); DELETE of an unreferenced family is
  allowed so a typo can be cleaned up before anything is recorded.

`hypothesis.trials_count` is not a column in the section 10 DDL, so the
8.4 sentence "hypothesis.trials_count = sum of variant_count over its families"
is computed on read (families whose `hypothesis_ids` contain the hypothesis);
killed variants are counted because variant rows are never deleted (D-035).

---

## 2. Ad-hoc runs never bypass recording (spec 8.4)

`POST /v1/stats` (M5) is the ad-hoc evaluation endpoint. M5b makes it record:

1. `family_id` in the request names an existing family (404 otherwise) and the
   run's `variant_count` is that family's count + 1.
2. Without `family_id`, a **singleton family id is minted up front** and the
   family row is written only after the run has succeeded, so a failed request
   (422 detector/params, empty window) leaves no rows behind.
3. Either way one `research.variant` row is appended (`experiment_id` stays
   NULL until an experiment adopts the variant) pointing at the new stats
   `ref_id`, and the family's `variant_count` is bumped in the same
   transaction.

The stored `result["multiple_testing"]` block carries the **request's** family
identity — `family_id` (null for an ad-hoc run), `variant_count`,
`selection_method` — and never the minted singleton id (D-036). That keeps the
M5 AC "same request ⇒ byte-identical `result` JSON" true: the singleton family
is an audit row that *points at* the result, not a value stored *inside* it.
For a named family the count legitimately grows run by run, because the 8.4
display rule ("when variant_count>1 … show the warning") has to reflect the
family's recorded search; a family whose contents changed is not the same
request.

The UI banner (spec 8.4 display rule) fires when
`variant_count > 1 || selection_method != "PRE_SPECIFIED"` with the exact
wording: "Multiple-testing warning: {variant_count} variants evaluated; this
result was selected after parameter search." plus the family's `search_space`
summary.

---

## 3. Freeze and verify (T5b.2, spec 8.5)

`POST /v1/experiments/{id}/freeze` runs in one transaction:

1. loads the experiment (404 unknown, 409 already frozen);
2. for every ref in `refs.stats`: resolves the row (422 when missing),
   records `sha256(canonical JSON of result)` into `result_hashes`, and sets
   `locked = true` (the 0001 trigger allows exactly that column);
3. sets `locked = true` on the referenced hypothesis (8.5 (a) "hypothesis
   criteria" — our criteria are immutable from creation, so locking the row is
   the freeze point);
4. sets `frozen = true, frozen_at = now()`;
5. writes a `system.audit_log` row (`experiment.freeze`) with the hashes.

Hashing uses the canonical JSON form already used for `params_hash`
(sorted keys, tight separators), so it is byte-stable across Python
processes (D-037).

`GET /v1/experiments/{id}/verify` recomputes each stored hash against the
current row and returns INTACT or TAMPERED with per-ref detail
(`expected`/`actual`). Verifying an unfrozen experiment is 422: there is no
snapshot to check against.

The tamper test bypasses the DB triggers (`session_replication_role = replica`)
on purpose: the trigger layer stops the ordinary path, and verify is the
backstop for a mutation that got past it. Defence in depth is the point of
8.5(b).

---

## 4. Endpoints (spec 11)

```
POST   /v1/families                 {description, hypothesis_ids[], search_space?}
GET    /v1/families                 list (newest first)
GET    /v1/families/{id}            one (404 unknown)
PATCH  /v1/families/{id}            description / search_space / hypothesis_ids
DELETE /v1/families/{id}            204 (409 once referenced)
GET    /v1/families/{id}/variants   insertion order
POST   /v1/experiments              family_id omitted -> singleton created
GET    /v1/experiments              list
GET    /v1/experiments/{id}         one (404 unknown)
PATCH  /v1/experiments/{id}         status / conclusion / assessment / refs / title
POST   /v1/experiments/{id}/freeze  200 (409 already frozen, 422 missing ref)
GET    /v1/experiments/{id}/verify  {status: INTACT|TAMPERED, results}
GET    /v1/experiments/{id}/lineage {experiment_id, nodes[], not_implemented[]}
```

Fail-closed resolution (PD-4/PD-10/PD-12): unknown hypothesis/family 404,
dataset_version that is not a registered manifest 422 (this is what makes
"every stored result resolves to its dataset manifest" true), unknown
status/selection-method/data_scope 422, empty universe 422, PATCH on a frozen
experiment 409 (a trigger rejects it at the DB level too).

---

## 5. Lineage (T5b.3, spec 8.5)

Read-only, from stored ids only: `dataset -> features -> detector -> stats ->
hypothesis -> experiment -> stats_result`, plus `not_implemented` naming
`strategy, backtest, validation, conclusion` (M6/M7 tables do not exist yet —
PD-9, no fabricated nodes). Every node carries a `ref` link to the stored
object (`/v1/datasets/{version}`, `/v1/hypotheses/{id}`, `/v1/experiments/{id}`,
`/v1/stats/{ref_id}`) and a status (`OK` or `MISSING`).

---

## 6. Test plan (tests first)

| Suite | Covers |
| --- | --- |
| `tests/test_experiments.py` | family CRUD incl. delete-while-referenced 409; variant append-only except `selected`; experiment create/422s; PATCH rules; freeze locking + 409 double-freeze + 422 missing ref; verify INTACT/TAMPERED; lineage links; singleton family recording; multiple-testing count growth |

---

## 7. Out of scope (parked)

- `POST /v1/sweeps` async runner + job plumbing — M6 (the engine that runs a
  matrix of evaluations belongs with the backtest simulator).
- T5b.4 Copilot/MCP experiment tools — M9 (the AI service does not exist yet).
- Strategy/backtest/validation/conclusion lineage nodes, `refs.backtests`,
  `refs.validations`, `refs.baselines` — M6/M7 tables.
- Experiments page UI — T5b.3 (shipped with the client types regenerated).

---

## 8. Decisions to record

| ID | Decision |
| --- | --- |
| D-033 | Experiment status enum as a DB CHECK (`IDEA..VALIDATED`) |
| D-034 | Variant append-only except the `selected` flag |
| D-035 | `hypothesis.trials_count` computed on read (no column in the DDL) |
| D-036 | Stored result embeds the request's family identity only (reproducibility) |
| D-037 | Canonical JSON sha256 for `result_hashes` (same form as `params_hash`) |

Open questions for the owner: **OQ-21** (confirm the `IDEA..VALIDATED`
experiment status list and that `REJECTED` requires the conclusion text).
