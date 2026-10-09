# M5 design note — Stats, scopes, evidence, hypotheses, notebook

Companion to IMPLEMENTATION_SPEC_v1.1.1 Section 8 (statistics, data scopes,
research integrity), Section 10 (data model), Section 11 (API), Section 12 (UI
evidence/Events Explorer), Section 15 (milestone M5, T5.1–T5.5), and Section 16
(OD-8, OD-11, OD-12).

Decisions recorded in `docs/DECISIONS.md` as D-025..D-030; questions
OQ-18..OQ-20.

> Scope discipline (PD-9): M5 builds T5.1–T5.5 only. `POST /v1/event-studies`,
> CRUD `/v1/families`, variants, sweep runner and experiment tables are **M5b**
> (card line 628–630) — parked in `docs/BACKLOG.md` references below.

---

## 0. Acceptance-criteria mapping

| AC (M5) | Where it lands |
| --- | --- |
| scope invariants property tests pass | `tests/test_scopes_invariants.py` — frozen boundaries, clipping, embargo/purge order, segment assignment (§2) |
| every displayed stat shows grade + n_eff + CI + data_scope | Evidence panel + Events Explorer components render the triple; `tests/test_api_stats.py` asserts the payload carries it; `tests/test_grade_wording.py` asserts the wording rules |
| holdout cannot be opened twice | `POST /v1/hypotheses/{id}/open-holdout` second call → 409 `HOLDOUT_LOCKED` (§8); one HOLDOUT stats run per hypothesis |
| grade wording string test passes | `tests/test_grade_wording.py` greps `apps/web` + `engine/ai` templates for the banned words (8.3) |
| stats reproducible by ref_id | `tests/test_api_stats.py`: same request twice → identical `result` JSON; `GET /v1/stats/{ref_id}` returns the stored bytes |

---

## 1. Dataset scopes and boundary freeze (T5.1, spec 8.1)

### 1.1 What gets frozen

`config/dataset_scopes.yaml` gains one entry per dataset family:

```yaml
scopes:
  binance_spot_usdt_default:      # family id (D-025)
    universe: [BINANCE:BTCUSDT, BINANCE:ETHUSDT, BINANCE:SOLUSDT]   # OD-2 default
    timeframes: [1D, 4H, 1H]
    s_start: "2024-01-01T00:00:00Z"   # earliest first-bar open_time
    s_end:   "2026-10-07T23:00:00Z"   # latest closed bar at freeze time
    span_days: 1011
    oos_start:     "2025-07-20T00:00:00Z"   # S_start + 0.56 x span, floored to UTC date
    holdout_start: "2026-03-19T00:00:00Z"   # S_start + 0.80 x span, floored to UTC date
    frozen_at: "2026-10-09T…Z"
    frozen_by: "agent:m5"                   # logged to DECISIONS too
```

Computation (spec 8.1, verbatim):

- `S = [earliest first-bar open_time among instruments in the family, latest
  closed bar at freeze time]`.
- `holdout_start = S_start + 0.80 * span`; `oos_start = S_start + 0.56 * span`.
- **Rounding:** boundaries are calendar dates rounded to 00:00 UTC → **floor to
  the UTC calendar day containing the instant** (D-026). Flooring moves each
  boundary *earlier* to the start of its day, i.e. it never extends a segment
  into territory the raw instant would have kept it out of for the *earlier*
  segment's observations — the conservative direction for DEVELOPMENT (a
  boundary can only shrink DEVELOPMENT, never grow it).
- Freeze once: the freeze writer refuses to overwrite an existing scope entry
  (§8.1 "boundaries never move after freeze"; changing universe/family → a
  **new** scope entry + DECISIONS record). Recomputation with newer data must
  find the frozen dates unchanged → property test.

### 1.2 OD-8

Spec 16 OD-8 supplies the default: *80/20 per 8.1, boundaries frozen at M5,
holdout = latest 20% of calendar span*. Defaults let work proceed, so the
numbers above are computed and frozen now and **owner confirmation is requested
in the AC report** (open question OQ-18 until confirmed). A change after
confirmation requires a new scope entry (never an edit).

### 1.3 Segments

- `DEVELOPMENT` = `[s_start, holdout_start)`
  - `IS` = `[s_start, oos_start)` (first 70% of DEVELOPMENT)
  - `OOS` = `[oos_start, holdout_start)` (final 30% of DEVELOPMENT)
- `HOLDOUT` = `[holdout_start, ∞)` (new data after freeze lands here
  automatically).

Percentages are calendar boundaries only; per-instrument bar counts differ
(8.1). Assignment of any observation is by **origin time** (bar open_time of
the event / signal bar).

---

## 2. Data-access enforcement (T5.1, spec 8.1)

New module `engine/scopes.py` (pure, connection-free — the DB layer calls it):

```python
@dataclass(frozen=True)
class ScopeBoundaries: ...        # loaded from config/dataset_scopes.yaml
def load_boundaries(family: str) -> ScopeBoundaries        # fail-closed if absent
def clip_window(start_ms, end_ms, scope, H_bars, tf) -> (start_ms, end_ms, clipped: bool)
def embargo_purge(origins, *, boundaries, horizon_h, embargo_e, tf) -> list[Observation]
def segment_of(origin_ms, boundaries) -> "IS" | "OOS" | "HOLDOUT"
```

### 2.1 Request clipping (spec line 373)

`DEVELOPMENT` requests whose window intersects HOLDOUT are **clipped** to
`holdout_start` and flagged `SCOPE_CLIPPED` (warning code, spec 11) — the
response keeps the original window in `quality.scope = {requested, applied,
flag: "SCOPE_CLIPPED"}`. Never silently: PD-10/8.1.

`HOLDOUT` requests require `hypothesis_id` + the open-holdout flow (§8).

### 2.2 Embargo / purge — exact order (spec 365–371)

1. Fix the two boundary dates (`oos_start`, `holdout_start`).
2. `H` = analysis horizon in bars (event studies: max outcome horizon in view,
   default 50; backtests: `max_hold_bars`; instrument timeframe); `E` =
   `embargo_bars` (default = H, from request-or-config `stats.embargo_bars`).
3. **EMBARGO:** for each boundary `b` ∈ {oos_start, holdout_start}, bars with
   origin in `[b, b + E bars)` (the LATER segment's first E bars) are unused as
   observation origins.
4. **PURGE:** any observation with `origin < b` and `origin + H bars > b` is
   removed from the EARLIER segment (outcome windows may not cross a boundary).
5. Assign surviving observations by origin time to IS / OOS / HOLDOUT.

`b + E bars` in ms = `b + E * bar_ms[tf]`; `origin + H bars > b` in ms =
`origin + H * bar_ms[tf] > b`. Embargo/purge **remove** observations; they never
move boundaries or relabel segments.

Stats requests carry `data_scope ∈ {DEVELOPMENT (default, explicit in 8.1),
HOLDOUT}` — the default is spec-sanctioned (not a hidden default, PD-11);
`instrument_ids` is **required** (PD-12).

### 2.3 Property tests (`tests/test_scopes_invariants.py`)

Hypothesis-driven where useful, exact elsewhere:

- frozen file round-trips; re-freeze refuses; `oos_start < holdout_start`;
  both inside `[s_start, s_end]`; `holdout_start` ≥ 80% of span; recomputing
  boundaries from *newer* data does not alter the frozen ones;
- DEVELOPMENT window ending after `holdout_start` → clipped + flagged; no
  observation with origin ≥ `holdout_start` survives under DEVELOPMENT;
- embargo excludes exactly `[b, b+E)` origins; purge removes exactly the
  origins whose `[t, t+H]` window crosses `b`; every surviving observation
  lands in exactly one segment; embargo/purge never move a boundary;
- `segment_of` monotone: IS < OOS < HOLDOUT by origin time.

---

## 3. Stats module (T5.2, spec 8.2–8.3)

New package `engine/stats/`:

- `models.py` — existing `StatsResult` ORM row (append-only, PD-13).
- `compute.py` — pure function `compute_stats(...) -> dict` (the stored
  `result` JSON), **no I/O**: inputs are `(observations, boundaries,
  event_filter, cluster_models, seed, params, grade_thresholds, n_raw,
  bar_ms, family_id?)` so it is unit-testable and deterministic (PD-6).
  `observations` are the post-embargo/purge joined event+outcome records
  (`engine/stats/compute.py::Observation`: event/instrument/type identity
  for clustering, `origin_ms` for scope segments, per-horizon outcomes);
  segments are re-derived from `origin_ms` via `scopes.segment_of` so
  scopes.py stays the single segment authority. `bar_ms` is passed
  explicitly (SYMBOL_GAP/MARKET_EVENT need the instrument timeframe in ms).
  Quantiles everywhere are type-7 linear interpolation between closest
  ranks (median == p50 by construction).
- `clusters.py` — the five cluster models.
- `grade.py` — evidence grade + sensitivity.
- `ci.py` — Wilson + seeded cluster bootstrap.

### 3.1 Input path

No event table was created in M4 (D-024) — spec §10 defines `research.event`
and `research.event_outcome`, and M4's note said *persistence lands with M5*.
Migration `0004` (§11) creates both tables, INSERT-only (trigger rejects
UPDATE/DELETE — events are content-addressed facts). The stats/evidence path
computes events on demand (existing detector call) and **inserts them
idempotently** (`event_id` PK; `ON CONFLICT DO NOTHING`). Nothing is ever
updated (PD-13).

### 3.2 Per-horizon statistics (for each h in the view, default the full
`DEFAULT_HORIZONS` list)

From observations in scope (after 2.2), per h and per segment (ALL / IS / OOS):

- `n` (count), per-symbol breakdown `{instrument_id: n}`;
- `mean` / `median` of `fwd_ret_pct` **and** of `fwd_ret_r`;
- `win_rate` = share of `fwd_ret_pct > 0` (ties at 0 are not wins);
- `mfe_med` / `mae_med` = medians of `mfe_r` / `mae_r`;
- percentile table `{p10,p25,p50,p75,p90}` for **both** `fwd_ret_pct` and
  `fwd_ret_r` (spec says "percentile table" without naming the metric —
  shipping both avoids inventing a choice, D-027);
- histograms: 20 equal-width bins over `fwd_ret_pct` (`histogram_pct`) **and**
  over `fwd_ret_r` (`histogram_r`) — D-027 is metric-agnostic ("20 equal-width
  histogram bins") and spec UI 558 renders the R histogram, so both ship;
  `[min, max]` range, edges + counts stored (bin rule is engine-defined →
  D-027);
- intervals:
  - **Wilson 95%** for `win_rate` (`z = 1.959963984540054`, fixed): computed on
    the raw `(n, wins)` of the segment — the spec names Wilson, which is a
    binomial interval; cluster adjustment is what the bootstrap provides for
    the mean/median (documented interpretation, D-028);
  - **cluster bootstrap 95%** for `mean` and `median` of `fwd_ret_r` (the
    statistic the grade consumes) and of `fwd_ret_pct`: 5000 resamples,
    `random.Random(seed)` seeded from `config/defaults.yaml
    stats.bootstrap_seed`, resampling **clusters** with replacement (all
    observations of the drawn clusters ride along), percentile interval
    `[p2.5, p97.5]`; the seed is stored in `stats_result.seed` (spec 8.2).

Cross-asset consistency per horizon: share of symbols whose median
`fwd_ret_r` has the same sign as the pooled median.

IS vs OOS are shown side by side (segment blocks inside each horizon entry —
shape per spec line 539: `"is":{},"oos":{}`).

Multiple-testing block: `{family_id: null, variant_count: 1,
selection_method: "PRE_SPECIFIED"}` - families arrive in M5b; the shape ships
now so the UI never changes (spec line 540).

**CI matrix / headline model (D-032, owner-facing interpretation).** The
per-model triple the spec demands ("n_eff, CI and Evidence Grade under all
applicable cluster models") is reported in `cluster_models` at the anchor
horizon, pooled, for **every** model: `{n_eff, grade, ci_median_r,
ci_excludes_zero, oos_sign_agrees, symbols_agree_share, consistent_sign}`.
Every other interval (per-horizon `ci` + the IS/OOS side-by-side blocks,
which is what `horizons[].ci` in the Evidence payload reads) is computed
under the **headline model** = the model with the LOWEST grade (request order
breaks ties) - the same model whose `grade`/`n_eff`/`ci` the UI shows
together, so the displayed triple is always self-consistent. `model_sensitive`
is true when the per-model grades differ. Bootstrap draw-sets: `M` pooled at
the anchor (grade) + `3 segments x 10 horizons` under the headline model
(shared draws for mean/median within a draw-set); pure-stdlib, 5000 resamples
- fine at fixture scale; job-based stats for large universes go to BACKLOG.

Histogram degenerate rule: when `min == max`, the range is `[v-1, v+1]`
(the design note's `[-1, 1]` case is `v = 0`) and still takes 20 bins
(21 edges); otherwise 20 equal-width bins over `[min, max]` (last edge set
to `max` exactly so the maximum lands in the final bin).

Grade inputs are evaluated at the anchor horizon on the kept in-scope
observations **whether or not the anchor is in the requested horizon list**
(the grade is never silently skipped because a request narrowed `horizons`).

Top of result: `n_raw` (observations before embargo/purge), `n_eff` (cluster
count, below), `data_scope`, `boundaries {oos_start, holdout_start}`,
`cluster_models: {model: {n_eff, grade, ci…}}`, `grade`, `grade_reason`
(non-null only when `grade` is null), `model_sensitive`, `headline_model`
(the model whose grade/`n_eff`/CI the per-horizon intervals use, so the UI
can label the triple's source), `per_symbol`, `cross_asset`.

### 3.3 Cluster models (spec 8.2) — `engine/stats/clusters.py`

Every model partitions the in-scope observations into **clusters**; `n_eff` =
cluster count; the bootstrap resamples clusters.

| Model | Cluster definition |
| --- | --- |
| `SYMBOL_GAP` | within a symbol, consecutive observations are in the same cluster while their origin gap < H bars (max horizon in view — overlapping outcome windows are dependent); a gap ≥ H starts a new cluster. Clusters never span symbols. |
| `CALENDAR_DAY` | all observations whose origin falls on the same UTC calendar day, across symbols |
| `CALENDAR_WEEK` | same ISO week (ISO year + week), across symbols |
| `MARKET_EVENT` | bars on which ≥ k symbols emit the same event type form one cluster (k = max(3, 10% of universe), default k from `stats.market_event_k`); adjacent flagged bars merge (per instrument timeframe, origin-bar aligned across symbols); every observation outside a flagged bar is its own cluster |
| `CUSTOM` | block definition comes from the experiment config (M5b) → **422 `VALIDATION_ERROR` "CUSTOM cluster model requires an experiment (M5b)"** in M5 (PD-9, no invented block rule) |

Applicable models for a run = the request's `cluster_models[]`, defaulting
from `config/defaults.yaml stats.cluster_models_by_detector[detector_id]`
(e.g. `RVOL_CLIMAX: [SYMBOL_GAP, CALENDAR_WEEK]` — PD-11: config is an allowed
source, and it mirrors the detector's documented ASSUMPTION in its spec §15).

Sensitivity: grade is computed **per model**; final `grade` = the **lowest**
of the per-model grades; `model_sensitive = true` when the per-model grades
differ (spec 378).

### 3.4 Evidence grade (spec 8.3) — `engine/stats/grade.py`

Thresholds live in `config/validation.yaml` (spec: "deterministic; thresholds
in config") — filled from the spec text itself, not invented (D-029):

```yaml
evidence_grade:
  A: {min_n_eff: 100, symbols_agree: 0.60}
  B: {min_n_eff: 50}
  C: {min_n_eff: 30}
  ci_excludes_zero: [A, B]      # A and B require CI(median R) excludes 0
  consistent_sign_ci_includes: [C]
```

Rules, verbatim:

- **A**: `n_eff ≥ 100` AND CI(median R) excludes 0 AND OOS sign agrees AND
  `≥ 60%` symbols agree.
- **B**: `n_eff ≥ 50` AND CI excludes 0 AND OOS sign agrees.
- **C**: `n_eff ≥ 30` AND consistent sign, CI includes 0.
- **D**: otherwise → label "Insufficient evidence".

Interpretations pinned here (no spec text, recorded as decisions):

- "CI(median R)" = the cluster-bootstrap 95% CI of the median of
  `fwd_ret_r` under that cluster model, over the DEVELOPMENT scope's pooled
  IS+OOS observations, **taken at the anchor horizon h=10** (D-031,
  owner-confirmed 2026-10-09): section 8.3 names no horizon, and h=10 is the
  spec's canonical evidence horizon (Evidence payload example `h:10`,
  analogues `fwd_r_10`, section 6.4 Pine-parity horizon). The threshold lives
  in `config/validation.yaml evidence_grade.anchor_horizon`.
- "OOS sign agrees" = `sign(median fwd_ret_r over OOS) ==
  sign(median fwd_ret_r over IS)`; a zero median counts as disagreeing.
- "≥60% symbols agree" = share of symbols whose median `fwd_ret_r` shares the
  pooled median's sign, computed over DEVELOPMENT.
- "consistent sign" (C) = every segment (IS/OOS) whose median is non-zero has
  the pooled sign.
- Missing IS or OOS observations → grade **D** (cannot verify agreement).
- `sign(0) = 0` never agrees with anything except itself — D-028.

**HOLDOUT scope does not carry a grade** (D-030): grades are defined on the
IS/OOS structure of DEVELOPMENT (8.3), and a holdout result is judged by the
hypothesis's pre-registered criteria (8.4/9.4), not by A–D. The payload
carries `grade: null` with `grade_reason: "n/a — HOLDOUT scope is judged by
pre-registered criteria"` plus `n_eff`, CIs and `data_scope` as usual, so the
UI never shows a bare estimate. Per-model `grade` is likewise null under
HOLDOUT (with segment-dependent `oos_sign_agrees`/`consistent_sign` null —
not applicable without IS/OOS — while `n_eff`, `ci_median_r`,
`ci_excludes_zero` and `symbols_agree_share` remain factual), and
`model_sensitive` is null (no grades to compare); the headline model falls
back to the first requested model.

Allowed wording (8.3, string-tested): `"Meets pre-defined evidence criteria
(grade {X})"` for A–C; `"Insufficient evidence"` for D.
Banned in UI + AI templates: `works`, `proven`, `validated edge`,
`guaranteed` (word-boundary, case-insensitive) — see §10.

---

## 4. Stats API (T5.2, spec 11)

```
POST /v1/stats
  body: {filter{detector, event_type?, direction?, timeframe?, from?, to?},
         instrument_ids[], data_scope?, cluster_models?[], family_id?, bootstrap_seed?}
  200: StatsResult view {ref_id, filter, data_scope, boundaries, result,
                         grade, n_eff, ci…, provenance}
  422: missing instrument_ids / unknown detector / CUSTOM model (M5) /
       params not in config (PD-11)
  409: HOLDOUT without opened hypothesis; second HOLDOUT run (§8)
GET  /v1/stats/{ref_id}  -> stored row verbatim (404 unknown)
```

- `data_scope` default `DEVELOPMENT` (explicit in 8.1 line 373 — not hidden).
- Flow: validate → load boundaries (fail-closed if scope file lacks the family)
  → resolve detector params (PD-11) → compute events for `instrument_ids` +
  timeframe (detector call, per instrument) → persist events idempotently →
  embargo/purge/segment → `compute_stats` → insert `stats_result` row
  (`ref_id = uuid4`, `seed` stored) → return envelope.
- **Reproducibility by ref_id:** `compute_stats` is pure and seeded; same
  request ⇒ byte-identical `result` JSON (test: two POSTs deep-equal; the
  `ref_id`/`created_at` differ, the `result` does not). `GET` returns the
  stored bytes exactly (append-only row, PD-13).

---

## 5. Evidence payload + panel (T5.3, spec 11 line 532, UI 558)

### 5.1 `GET /v1/events/{id}`

`event_id` is a sha1 — not reversible — so lookup is by stored PK:

1. `SELECT` from `research.event` (+ outcomes).
2. On miss: **cache-miss recompute** — run every registered detector over the
   dataset family's `(instrument, timeframe)` grid (≤ 9 runs at M5 scale),
   insert any events found, re-select.
3. Still absent → 404 `NOT_FOUND` (fail-closed, never a substitute event).

Payload exactly per spec 532–544:

- `event` — envelope fields + `r_basis: "atr"`;
- `trigger` — checklist rows `{name, passed, value, threshold,
  provenance: "CALCULATED"}` (from the stored trigger block);
- `context` — `regime` tags + archetype (D-017 descriptive);
- `stats` — the **matching stored stats**: most recent `stats_result` whose
  `(filter ⊇ {detector, event_type, instrument…}, data_scope,
  resolved_params, dataset_version)` matches; if none exists, compute it once
  through the §4 path and reuse the stored row (determinism makes reuse safe;
  bounded row growth: one row per distinct filter/scope/params tuple).
  Block carries `ref_id, data_scope, grade (+reason), model_sensitive, n_raw,
  n_eff, horizons[], multiple_testing` — grade may be `null` under HOLDOUT
  (D-030) but `n_eff`, CI and `data_scope` are always present (AC).
- `analogues` — up to 5 most recent events with the same
  `detector@version + event_type`, any instrument in the family, strictly
  earlier `event_time`, each with `fwd_r_10` (h=10 `fwd_ret_r`, null when
  incomplete) (D-027 selection rule — nearest-in-time peers, no outcome
  conditioning);
- `data_quality` — state/completeness for that instrument/timeframe window;
- `dataset_version`.

### 5.2 Evidence panel (chart → click marker)

Right-dock component `apps/web/app/chart/evidence.tsx`, opened by clicking a
marker (the M4 hover card stays hover-only; click = Evidence per UI 558):

- header: type, symbol, tf, time, `data_scope` badge;
- "Why triggered" checklist (name/value/threshold/pass);
- invalidation (`ref`, `invalidation`, `r_basis`);
- Evidence Grade + `n_eff` + intervals + model-sensitive flag (the AC triple,
  one component, used by panel AND Explorer);
- horizons table (median/mean R, win%, CI; IS/OOS toggle);
- R histogram (from stored `stats.horizons[].histogram`);
- analogues (click → jump to that event);
- multiple-testing banner (shape present, static text for `variant_count: 1`);
- actions: *Open in Events* (→ `/events` with filter), *Save to Notebook*
  (POST `/v1/notes`), *Ask Copilot* (disabled until M9 — explicit
  "AI arrives M9" empty state, PD-9), *Add to Experiment* (disabled until
  M5b);
- states: loading / empty / error+retry / stale / partial per Master 62.

### 5.3 Events Explorer (UI 559)

New page `apps/web/app/events/`:

- filter bar: detector, event_type, instrument, timeframe, from/to, scope;
- events table (from `GET /v1/events` with the same query);
- live stats pane: `POST /v1/stats` for the current filter → grade triple,
  horizons table, histogram, IS/OOS columns;
- CSV export client-side with columns including `dataset_version` and
  `data_scope` (spec 559);
- loading/empty/error/stale/partial states.

---

## 6. Hypothesis registry (T5.4, spec 8.4, 8.1, 10, 11)

### 6.1 Table (migration `0004`)

`research.hypothesis(hyp_id uuid PK, title text, statement text, status text,
criteria jsonb, preregistered_at timestamptz, holdout_opened_at timestamptz,
holdout_result jsonb, autopsy text, locked boolean DEFAULT false)` — exactly
§10. A trigger rejects UPDATE/DELETE when `OLD.locked` (spec line 501).

### 6.2 Endpoints

```
POST   /v1/hypotheses            {title, statement, criteria{pass[], kill[]}}
GET    /v1/hypotheses[?status=]  list (status=REJECTED is the Graveyard query)
GET    /v1/hypotheses/{id}
PATCH  /v1/hypotheses/{id}       status / autopsy / title / statement
POST   /v1/hypotheses/{id}/open-holdout
```

Rules:

- **Pre-registration freeze:** `criteria` and `preregistered_at` are written
  at creation and immutable — any PATCH touching `criteria` → 422
  `VALIDATION_ERROR` *"criteria are pre-registered; edits create a new
  hypothesis"* (8.4 verbatim). A new row is the sanctioned path (there is no
  `supersedes` column on hypothesis by spec).
- `locked = true` freezes the whole row (DB trigger).
- Status enum: `IDEA, TESTING, PROMISING, SUPPORTED, WEAK, REJECTED,
  VALIDATED` — validated against the enum on every PATCH.
- `REJECTED` requires a non-empty `autopsy` (422 otherwise, 8.4 "autopsy
  required when REJECTED"); the Graveyard view is
  `GET /v1/hypotheses?status=REJECTED`.
- `VALIDATED` in M5 → 422 with detail `requires_frozen_experiment: "M5b"`
  (9.4 demands frozen experiments + holdout passed; experiments don't exist
  yet — failing loudly beats accepting an unverifiable state, PD-3).
- **Open-holdout once:** `UPDATE … SET holdout_opened_at = now() WHERE hyp_id
  = :id AND holdout_opened_at IS NULL` — zero rows affected → **409
  `HOLDOUT_LOCKED`** (atomic; no read-then-write race). Writes an
  `system.audit_log` row (`action = hypothesis.open_holdout`).

### 6.3 HOLDOUT stats gate (8.1 line 373)

- `POST /v1/stats` with `data_scope=HOLDOUT` requires `hypothesis_id` in the
  body (422 if missing) whose `holdout_opened_at IS NOT NULL`
  (else 409 `HOLDOUT_LOCKED`, "open the holdout first").
- **One HOLDOUT stats run per hypothesis** (D-030/OQ-19): after the first
  successful HOLDOUT run, `holdout_result` is set (`{ref_id, grade, n_eff,
  at, resolved_params}`); a second HOLDOUT run → 409 `HOLDOUT_LOCKED`. This is
  what makes "holdout cannot be opened twice" observable beyond the endpoint
  itself (AC). Every HOLDOUT run writes `system.audit_log`
  (`who, when, result ref_id, resolved_params` — spec 373 verbatim).

---

## 7. Notebook (T5.5, spec 10, 11, UI 549)

- Table `research.note(note_id uuid PK, title text, body_md text,
  experiment_id uuid NULL, chart_state jsonb, created_at timestamptz)` —
  §10 exactly. `experiment_id` stays NULL until M5b (column ships now so no
  migration churn later).
- Endpoints `CRUD /v1/notes`: list/create/get/patch/delete. Notes are not
  results — DELETE is allowed (PD-13 covers result tables).
- Page `apps/web/app/research/notebook/`: note list + markdown editor (plain
  textarea + rendered preview; no new dependencies), `Save to Notebook` from
  the Evidence panel writes `{title: "<type> <symbol> <tf> <time>", body_md:
  generated summary, experiment_id: null}` with the event ref ids in the text.
- Notebook links live in nav under **Research** (Notebook, Hypotheses,
  Graveyard) per UI 549.

---

## 8. Migrations (spec 10)

`infra/migrations/versions/0004_m5_research.py`:

1. `research.hypothesis` (+ `locked` UPDATE/DELETE trigger).
2. `research.note`.
3. `research.event` — PK `event_id`, all §10 columns, `UNIQUE(detector_id,
   event_type, version, params_hash, instrument_id, timeframe, event_time)`.
4. `research.event_outcome` — PK `(event_id, horizon)`.
5. Append-only triggers on `event` / `event_outcome` (reject UPDATE and DELETE
   outright — content-addressed observations, PD-7/PD-13).

Downgrade drops them. Existing `stats_result` trigger (0001) reused unchanged.

---

## 9. Determinism & provenance

- Every `stats_result` row stores `dataset_version`, `dataset_schema_version`,
  `engine_version`, `code_version`, `resolved_params`, `seed` (PD-6) — the
  ORM row already has the columns; the flow fills them from the manifest +
  `engine/versions.py`.
- `compute_stats` has no clock, no RNG except `random.Random(seed)`, no I/O →
  re-run identical (reproducibility test).
- Response envelopes carry the same provenance block the events use
  (`OBSERVED`/`CALCULATED`), never numbers without a source (PD-5).

---

## 10. Grade wording rules (UI 566, 8.3)

`tests/test_grade_wording.py`:

1. Allowed positive wording literal: `"Meets pre-defined evidence criteria (grade {X})"`;
   grade D literal: `"Insufficient evidence"`.
2. Banned tokens (case-insensitive, word boundaries): `works`, `proven`,
   `validated edge`, `guaranteed` — scanned over `apps/web/**/*.{ts,tsx}`
   (excluding `*.test.ts`) and `engine/ai/**` templates (empty until M9 — the
   scan still runs so M9 inherits the gate).
3. The shared `EvidenceGradeBadge` component is the only renderer of grade
   wording in the web app (import-site assertion), so the AC "every displayed
   stat shows grade + n_eff + CI + data_scope" is enforced by construction:
   the badge takes `{grade, n_eff, ci, data_scope}` as a required prop tuple.

---

## 11. Test plan (tests first)

| Suite | Covers |
| --- | --- |
| `tests/test_scopes_invariants.py` | §1.3 + §2.3 property tests (AC) |
| `tests/test_stats_compute.py` | pure `compute_stats`: hand-computed means/medians/percentiles/histogram; Wilson against the closed-form; bootstrap determinism (same seed twice → identical CI; different seed → different, documented); cluster partitions for all 5 models incl. `n_eff`; grade A–D branches + sensitivity (lowest wins, flag set); HOLDOUT grade null |
| `tests/test_api_stats.py` | POST happy path + provenance columns; 422s (no instrument_ids, unknown detector, CUSTOM, unknown scope family); SCOPE_CLIPPED flag; HOLDOUT gate 409; **reproducibility**: two identical POSTs deep-equal `result`; GET by ref_id verbatim |
| `tests/test_hypotheses.py` | CRUD; criteria freeze 422; enum 422; REJECTED needs autopsy; open-holdout first→200 + audit row, second→409; VALIDATED→422 M5b; graveyard query; locked row UPDATE rejected by trigger |
| `tests/test_events_evidence.py` | Evidence payload shape (all spec keys); cache-miss recompute path; 404 unknown id; analogues rule; stats block triple present |
| `tests/test_grade_wording.py` | §10 |
| `tests/test_notebook.py` | CRUD + evidence "Save to Notebook" payload |

Fixture strategy: reuse `tests/support.py` `FixtureAdapter` + `seed` path for
bars; the seeded window is small so boundaries come from a **test-local scope
entry** (not the frozen production entry) — the production file's freeze is
asserted read-only by §1.3 tests.

---

## 12. Out of scope (parked)

- `POST /v1/event-studies` (async) — same engine, job plumbing → M5b/M6.
  `docs/BACKLOG.md`.
- Families / variants / sweeps / multiple-testing banner with real
  `variant_count` — M5b (T5b.1).
- Experiment freeze / verify / lineage — M5b (T5b.2–3).
- Scanner rows with `grade`/`n_eff` (UI 560) — M8 (card), but the API it
  needs ships in M5.
- FDR / White's Reality Check / SPA — backlog (8.4).
- `CUSTOM` cluster model — needs experiment config (M5b).

---

## 13. Decisions to record

| ID | Decision |
| --- | --- |
| D-025 | Dataset-family id + universe for the M5 freeze (`binance_spot_usdt_default`, BTC/ETH/SOL, OD-2 default) |
| D-026 | Boundary rounding = floor to the UTC calendar day containing the raw instant |
| D-027 | Percentiles ship for both `fwd_ret_pct` and `fwd_ret_r`; 20 equal-width histogram bins; analogues = 5 most recent same detector+type peers |
| D-028 | Wilson on raw `(n, wins)`; sign conventions (`>0` wins, `sign(0)` never agrees) |
| D-029 | Evidence-grade thresholds copied from spec 8.3 into `config/validation.yaml`; `stats.*` defaults (seed, resamples, embargo, market-event k, per-detector cluster defaults) into `config/defaults.yaml` |
| D-030 | HOLDOUT results carry `grade: null` + reason; one HOLDOUT stats run per hypothesis; second → `HOLDOUT_LOCKED` (grade-null owner-confirmed 2026-10-09) |
| D-031 | Evidence Grade is anchored at **h=10**: the grade's CI(median R) is taken at `config/validation.yaml evidence_grade.anchor_horizon` (owner-confirmed 2026-10-09; spec 8.3 names no horizon) |
| D-032 | Headline triple consistency + CI matrix: per-model `{n_eff, CI, grade}` reported at the anchor horizon for every model; all other per-horizon/IS/OOS CIs under the headline model (worst grade, request order breaks ties) so grade + n_eff + CI are always one model's |

Open questions for the owner: **OQ-18** (confirm frozen boundary dates, OD-8),
**OQ-19** (one-run-per-hypothesis holdout reading). ~~OQ-20~~ resolved by
owner confirmation 2026-10-09: HOLDOUT results carry `grade: null` + reason.
