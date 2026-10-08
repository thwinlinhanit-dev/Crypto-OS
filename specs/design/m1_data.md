# M1 — Candle data + Guardian: design note

Companion to `specs/IMPLEMENTATION_SPEC_v1.1.1 (1).md` §4 (Data layer), §10 (Data model),
§11 (API), §15 (task cards T1.1–T1.4), §16 (OD-5), §14.1 (data-quality injection tests).

Task-loop rule: design note required because M1 is far above 150 LOC. Section numbers below
cite the implementation spec. Every choice here is either (a) quoted from the spec, or
(b) marked ASSUMPTION / INTERPRETATION and mirrored in `docs/DECISIONS.md`.

---

## 0. Acceptance-criteria mapping

| AC (§15 M1) | Where it is proven |
|---|---|
| re-ingest yields identical `dataset_version` | `tests/test_ingest.py::test_reingest_is_idempotent_and_identical` |
| manifest answers "what exactly did this use?" | `GET /v1/datasets/{dataset_version}` + `tests/test_api_data.py` |
| injected gap detected, repaired, only the affected interval excluded | `tests/test_guardian.py`, `tests/test_ingest.py::test_gap_repair_…` |
| spot→perp substitution fails closed | `tests/test_api_data.py::test_perp_requests_fail_closed…` |
| storage budget enforced | `tests/test_ingest.py::test_storage_budget_refuses…` |

## 1. Bar model (§4.2, §4.3)

- Logical row: `instrument_id, timeframe, open_time, close_time, o, h, l, c, volume (base
  units), quote_volume, trades, source, ingested_at`. UTC only; daily bars open 00:00 UTC.
- `Timeframe` enum — native: `1D`, `4H`, `1H` (§4.3 "Native: 1D, 4H, 1H"); derived: `1W`
  (from `1D` only). Any other string is rejected with `VALIDATION_ERROR` (422) — an
  unavailable timeframe must *error*, never substitute (§14.1 fail-closed, PD-10).
- Bar lengths are whole divisors of a day, so expected `open_time` values are exact
  multiples of `bar_ms` from the Unix epoch — this is what the Guardian uses to detect gaps.
- Only **closed** bars count. A bar is closed when `close_time <= now`; the forming bar is
  never written by ingest, never counted as "expected", and never feeds features/events.
- ASSUMPTION: storage uses `float64` for `o,h,l,c,volume,quote_volume` and `int64` for
  `trades`. IEEE-754 round-trips Binance's decimal strings within ~15 significant digits,
  which covers every symbol field Binance publishes; the canonical hash (§3) serialises
  floats with Python's shortest round-trip `repr`, which is stable across CPython runs.
- `close_time` is the **exclusive upper bound**: `close_time = open_time + bar_length`, so
  the milliseconds a bar covers are `[open_time, close_time)`. §4.3's "Monday 00:00 UTC to
  Sunday 23:59:59.999 UTC" therefore describes the week's *covered range* (its last ms is
  `close_time - 1`), and "event_time = close_time of the Sunday bar" resolves to Monday
  00:00:00.000. INTERPRETATION (D-008/OQ): corroborated by the Master spec's example
  `"event_time": "2026-10-07T00:00:00Z"` (a midnight boundary), and it matches the
  TradingView convention that a bar's close timestamp is `open + interval`. Binance's raw
  kline `close_time` (`open + interval − 1 ms`) is canonicalised to this bound by the
  adapter; the raw value never leaves the parse function. Exclusion intervals then tile the
  axis exactly: `[first_open, last_close)` with no overlap or 1 ms gap.

## 2. Instrument identity (§4.1, §10)

- Identity tuple `{asset, quote, market_type, venue, native_symbol}`, table
  `market.instrument`, `UNIQUE(venue, market_type, native_symbol)`.
- `instrument_id` is **uuid5** over `venue|market_type|native_symbol` in a fixed CryptoOS
  namespace: re-running registry seeding never duplicates an instrument, and two machines
  derive the same id (ASSUMPTION — the spec says `uuid PK` but not how it is minted;
  deterministic ids are required for reproducible fixtures, PD-6).
- Research code takes `instrument_id`; native symbols appear only in the registry, the
  storage layout and adapter calls (§4.1 "Research code never sees native symbols").
- `market_type` is `SPOT` or `PERPETUAL` from M0 (§4.8). The registry *stores* both; the
  data plane serves SPOT only until M10 — a `PERPETUAL` request returns `NOT_IMPLEMENTED`
  (HTTP 501) and never falls back to spot (§4.8, PD-10, AC4).

## 3. Deterministic Parquet writer (§4.4, T1.2)

Layout (partition order fixed):

```
data/{normalized|derived}/venue=<VENUE>/market_type=<MT>/symbol=<NATIVE>/tf=<TF>/year=<Y>/part-<hash16>.parquet
```

- `normalized` = native bars fetched from the venue; `derived` = `1W` resamples.
- Writer settings, pinned as constants: `compression="zstd"`, rows sorted by `open_time`
  ascending (strictly increasing — duplicates are a Guardian failure, not a write),
  `row_group_size=8192`, `statistics=True`. AMENDMENT (polars 2.0.0): `write_parquet`
  takes `compression`/`statistics`/`row_group_size` natively; the earlier draft's
  `write_statistics`/`data_page_version`/`version` are pyarrow-FileWriter parameters
  polars does not expose, so they are not pinned (zstd + row groups + statistics cover
  the determinism intent). Determinism is required for *content*, not for file bytes:
  file bytes are not hashed (§4.4 "Per-file hash = content_sha256 over canonical row
  serialization (not file bytes)").
- **Content-addressed file name**: `part-<first 16 hex of content_sha256>.parquet`.
  Consequences: re-ingesting identical bars finds the same path already present and skips
  the write (idempotent, AC1); a correction yields a different hash and therefore a
  *different* file — nothing is ever edited in place (PD-7, §4.4 "files read-only after
  write"). Old files stay so the superseded `dataset_version` remains reproducible.
- Writes are atomic: write `*.tmp`, `fsync`, `os.replace`. After the replace the file is
  made read-only (`stat.S_IREAD` / Windows read-only attribute). Overwriting an existing
  content-addressed path is refused with `IMMUTABLE_RESULT`.

### 3.1 Canonical row serialization → `content_sha256`

One UTF-8 line per row, `"\n"`-joined, **after** sorting by `open_time`:

```
{"close_time":<int ms>,"close":<float>,"high":<float>,"instrument_id":"<uuid>",
 "low":<float>,"open":<float>,"open_time":<int ms>,"quote_volume":<float>,
 "source":"<str>","timeframe":"<TF>","trades":<int>,"volume":<float>}
```

- `json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True)` per row;
  floats therefore serialize with `repr` (shortest round-trip, stable).
- Fields are exactly the §4.2 identity fields **minus `ingested_at`**. `ingested_at` is
  ingest bookkeeping; including it would change the hash on every run and break AC1.
  It is still stored as a column so the provenance question stays answerable.
- Row fields contain no raw tabs/newlines by construction (uuid / enum / numbers /
  venue-fixed source string), so the line framing cannot be forged; a unit test asserts it.
- `content_sha256 = sha256(canonical_bytes).hexdigest()`.

## 4. Manifest and `dataset_version` (§4.4, AC1/AC2)

- `dataset_version = sha256(canonical JSON of {source_files, dataset_schema_version,
  bar_semantics_version, timezone_policy_version, instrument_metadata_version})` where
  each entry of `source_files` is `{path, content_sha256, rows}`.
- Canonical JSON: keys sorted, `separators=(",", ":")`, `ensure_ascii=True`, UTF-8,
  `source_files` sorted by `path`.
- `path` is **repository-relative with `/` separators and without the repo root**
  (`normalized/venue=BINANCE/...`), so the version hash does not depend on which machine
  or which checkout produced it (PD-6 reproducibility).
- Version constants (all from §4.4, verbatim):
  `dataset_schema_version` = `engine.versions.DATASET_SCHEMA_VERSION` (`"1"`),
  `bar_semantics_version = "closed-bars-utc-v1"`,
  `timezone_policy_version = "utc-v1"`.
- ASSUMPTION: `instrument_metadata_version` = `"binance-spot-exchangeinfo-v1"` — the
  spec names the field but not a vocabulary; it versions the tick/lot/listed_at metadata
  that came with the bars. Recorded as OQ for the owner.
- One manifest row = one ingest run over one `(venue, market_type, instruments[],
  timeframes[])` scope (§10 gives `market.dataset_manifest` singular `venue`/`market_type`
  but jsonb `instruments`/`timeframes`). Re-running the same scope over the same bytes
  recomputes the same `dataset_version`; the `INSERT` is idempotent on the PK.
- "What exactly did this backtest use?" = `GET /v1/datasets/{dataset_version}` returning
  the stored manifest (§4.4).

## 5. Ingest pipeline (§4.5, T1.2, AC1/AC5)

```
POST /v1/data/ingest  ->  system.job(kind=INGEST, status=queued)
  claim (FOR UPDATE SKIP LOCKED) -> execute_job:
    1. resolve scope (venue, market_type, instrument_ids, timeframes, from, to)  [request only]
    2. pre-flight storage budget: bytes(data/) + bytes(to write) <= storage_budget_gb
       -> refused with 422 VALIDATION_ERROR before anything is written (AC5)
    3. per (instrument, timeframe, year): skip a chunk already covered by an existing
       content-addressed file; otherwise fetch from the adapter (rate-limit aware)
    4. drop bars outside the requested window and any bar that is not yet closed
    5. Guardian validation of the assembled frame (rejects bad rows, PD-10)
    6. write Parquet (atomic, content-addressed) -> progress on the job row (resumable:
       a re-run re-enters at step 3 and skips what already exists)
    7. compute manifest + dataset_version -> INSERT market.dataset_manifest
    8. Guardian assessment -> system.gap (exclusion intervals), system.data_quality
    9. if 1D was in scope -> derive 1W (§4.3) and write to data/derived
   10. audit_log row; job status=succeeded, result_ref=dataset_version
```

- Adapter isolation: everything above talks to the `ExchangeAdapter` protocol
  (`fetch_historical_klines`, `fetch_instrument_metadata`, `health` — §4.5 lists exactly
  these three for v1). Tests inject a fixture-backed fake; only `exchanges/binance` knows
  HTTP. httpx `MockTransport` covers the HTTP layer itself.

### 5.1 ExchangeAdapter contract (§4.5, Master §6, T1.1)

- `exchanges/base.py` defines the v1 protocol as **exactly** the three candle methods plus
  `name` and `market_type` (§4.5); Master §6's `subscribe_*`/`connect` methods are live
  market surface and land at M10 (PD-9).
- `fetch_historical_klines(native_symbol, timeframe, start_ms, end_ms) -> list[Kline]`.
  `Kline` is the **canonical** row after native→canonical transformation (Master §6):
  `close_time = open_time + BAR_MS[timeframe]` (design note §1), numeric fields parsed
  from the venue's JSON strings (`float`/`int`, never a silent partial parse).
  `instrument_id`/`timeframe`/`source`/`ingested_at` are stamped by ingest, not by the
  adapter (the adapter never learns instrument ids).
- Native interval tokens (`1d`/`4h`/`1h`) live in `exchanges/binance` — venue knowledge
  stays behind the adapter. `1W` is refused with `ValueError`: it is never fetched
  (§4.3 "1W from 1D", PD-10).
- Pagination: `limit=1000` (the venue's klines page maximum), cursor advances to
  `last_open_time + 1`, so pages never overlap; a page that fails to advance the cursor is
  a `DATA_UNAVAILABLE` failure (fail closed — never loop, never emit partial guesses).
- Rate limiting (§4.5 "rate-limit aware"): a rolling 60 s request-weight window in front
  of every call — `acquire(weight)` sleeps until the budget allows the request. Weights:
  klines 2, exchangeInfo 20, time 1; budget 6000/min (ASSUMPTION: Binance public REST
  defaults, OQ to confirm against the live API when fixtures are recorded). On HTTP 429
  the adapter sleeps `Retry-After` (default 1 s) and retries, at most 3 attempts, then
  fails with `DATA_UNAVAILABLE`.
- Fail closed: transport errors, non-2xx responses, wrong JSON shapes and unparsable rows
  all raise `DataUnavailableError` — the adapter never returns a truncated or padded
  series (PD-10). `market_type=PERPETUAL` raises `NotImplementedMarketError` (501) at
  construction — defence in depth; the API validates the request before a job exists
  (AC4).
- `health()` = `GET /api/v3/time`; `fetch_instrument_metadata()` = `GET /api/v3/exchangeInfo`
  filtered to requested symbols (status, base/quote, tick/lot filters, onboard date).
- The storage budget value is a documented PD-11 parameter: `data.storage_budget_gb`
  (default 20, OD-5) in `config/defaults.yaml`, overridable per request. It is *not* an
  environment variable, because PD-11 allows only "the request, or config/defaults.yaml"
  as parameter sources (D-006).
- Jobs are executed either by the API's background task or by `apps/worker`, both through
  the same claim/execute functions with `FOR UPDATE SKIP LOCKED`, so a process that dies
  mid-run leaves `status=failed` (or a stale `running` that the worker recovers) and a
  re-run resumes at step 3 (§11 jobs contract: `{job_id,status}`, `GET /v1/jobs/{id}` →
  status, progress, result_ref, logs).

### 5.2 Implementation decisions (amendments made while building `engine/data/ingest.py`)

- **Budget gate, two doors (OD-5, AC5).** Entry: `bytes(data/) >= storage_budget_bytes`
  refuses *before the first fetch* with `422 VALIDATION_ERROR`,
  `detail = {reason: "storage_budget_exceeded", used_gb, budget_gb}`. Write path: each
  part is estimated with `len(canonical_bytes(frame))` (uncompressed, no `ingested_at` —
  conservative: it over-estimates the zstd file) and `running + estimate > budget`
  refuses before that file lands; `running` then tracks actual `stat().st_size`.
  The gate counts *every* byte under `data/`, not only parts.
- **Chunk resume semantics.** A chunk whose expected closed opens are all present in a
  stored part is reused as-is without contacting the venue (that is the AC1 no-refetch
  path); when several stored parts both cover, the pick is deterministic (most rows,
  path tie-break). When *not* covered, the fresh fetch is the whole truth for the chunk:
  the older partial part is orphaned (content-addressed files are never edited, PD-7)
  and simply stops being referenced by new manifests. Consequence (INTERPRETATION): a
  reused part may carry rows outside a narrower re-request's window; the manifest's
  scope fields record the requested window and `row_counts` report the file's true
  rows — M2 range-reads clip to the request.
- **Derived bars are not part of an ingest manifest.** Step 9 writes `data/derived`
  (budget-checked, content-addressed, `extra_columns=("incomplete",)`), but the ingest
  `dataset_version` covers only normalized source files; a `1W` manifest comes from an
  explicit `RESAMPLE` job (`run_resample`), whose source files are the derived parts
  and whose `quality_summary` carries `weeks_total`/`weeks_incomplete`.
- **`dataset_version` does not hash scope** (§4.4 lists only source files + the four
  version fields). Therefore a later ingest whose bytes equal a repair's merged bytes
  legitimately reproduces the repair's `dataset_version` — same inputs, same identity
  (PD-6), asserted in `tests/test_ingest.py::test_repair_fills_the_gap_...`.
- **Repair details.** The refetch window is `[exclusion_start, exclusion_end)` and the
  assessment window is the merged data span (first→last open). Stored rows and fetched
  rows are assembled **sorted by `open_time`** (arrival order is meaningless across two
  sources; OHLC/duplicate defects are still detected on values). Repair re-derives `1W`
  after the status transition, so an active exclusion keeps flagging its week and a
  repaired one clears. Retry: `OPEN` and `UNREPAIRABLE` gaps may be retried;
  any other status (`REPAIRED`) is refused with `422 VALIDATION_ERROR` (INTERPRETATION —
  the spec defines no double-repair rule).

## 6. Guardian (§4.6, T1.3, AC3)

Checks per `(instrument, timeframe)` over the **requested window** (the manifest's
`[start, end]`, passed explicitly to `assess`) — all thresholds are quoted from §4.6 and
are **constants, not parameters** (PD-11 applies to tunables; these are spec text):

| Check | Rule |
|---|---|
| duplicate bars | repeated `open_time` in stored rows |
| gaps | missing expected closed `open_time` inside the requested window |
| non-monotonic time | `open_time[i] <= open_time[i-1]` on the rows **as received** (nothing is sorted first) |
| OHLC sanity | exactly §4.6's triple: `l <= o`, `c <= h`, `l <= h` — nothing more (PD-4; see OQ below) |
| zero-volume anomalies | fraction of *closed* bars with `volume == 0` |
| stale feed | no new *closed* bar within 2 bar-lengths + grace |

- Window semantics: gaps are detected against the window the caller requested (the
  manifest range), not against the frame's own first/last bar — otherwise leading or
  trailing absence would be invisible to completeness, which §4.6 defines over "the
  requested range". A window with no closed bars yet expects nothing.
- OHLC: §4.6 lists three relations. `o <= h` and `l <= c` are **not** part of that list
  and are not checked — adding a fourth relation would invent a rule (PD-4); carried as
  OQ below. `l <= h` is in the list even though the other two imply it, and is checked
  verbatim.
- `detail["causes"]` records **every** condition observed (e.g. a RED range that also
  has gaps lists both); the state itself is picked by the precedence below. Detection is
  order-sensitive by design: out-of-order arrivals must be visible, so `assess` never
  sorts its input (the *writer* sorts; guardian inspects before the write).

States (§4.6, evaluated in this order because the definitions are cause-specific):

1. `RED` — any unresolved OHLC violation, duplicate, or non-monotonic order (documented
   cause covers "OHLC violations/duplicates"; INTERPRETATION: ordering corruption of the
   same kind is invalid, carried as OQ below).
2. `ORANGE` — one or more OPEN gaps (documented cause: "incomplete (open gaps)").
3. `YELLOW` — zero-volume fraction > 0, or the series is stale (see interpretations).
4. `GREEN` — otherwise.

INTERPRETATION (OQ-5): §4.6 writes `YELLOW degraded (zero-volume bars <0.1%)` and gives no
state for a zero-volume fraction ≥ 0.1%. `RED`/`ORANGE` have different documented causes, so
assigning either would be an invention; the series is reported `YELLOW` (degraded) with the
exact fraction in `detail`. Owner to confirm.

INTERPRETATION (staleness): §4.6 defines the stale *check* but not which state it maps to,
and a frozen historical range is not a stale feed. Rule implemented:
`stale = (now - last_bar.close_time) > (2 + STALE_GRACE_BAR_LENGTHS) * bar_length` **and**
`(ingested_at - last_bar.close_time) <= 3 * bar_length` — i.e. only a range whose ingestion
claimed to reach "now" can be stale. Stale ⇒ `YELLOW`, detail carries both timestamps.
`STALE_GRACE_BAR_LENGTHS = 1` (ASSUMPTION: the spec says "+ grace" without a value).

Completeness (§4.6) = present closed bars / expected closed bars in the requested range,
computed for the manifest's `[start, end]`.

### 6.1 Exclusion intervals (§4.6)

- A gap writes `system.gap` with
  `exclusion_start = first missing bar open_time`, `exclusion_end = last missing bar
  close_time`, status `OPEN`, source, `detected_at`.
- Only that interval is excluded; the rest of the instrument's history is untouched
  (§17 "Exclusion intervals cover only the affected bars, never whole instruments").
- INTERPRETATION: after a successful repair the gap row becomes `REPAIRED` and stops
  excluding, while remaining listed forever so reports can still enumerate it (§4.6
  "Reports list all exclusion intervals"). **Active = `status IN ('OPEN','UNREPAIRABLE')`**
  (amendment, §4.6 "a gap that cannot be refetched ... stays excluded" — an
  UNREPAIRABLE range keeps excluding until data arrives; only `REPAIRED` stops).
  Implemented as `load_exclusions(active_only=True)`; it is what the weekly resampler
  receives and what `/v1/data/gaps?active=1` serves.
- Events whose `lookback_bars` window overlaps an OPEN exclusion carry `lookback_gap=true`
  and are excluded from stats by default (§4.6) — the flag is produced by the event engine
  (M4); M1 stores the intervals it needs.

### 6.2 Repair (§4.6, AC3)

`POST /v1/data/repair {gap_id}` → job `REPAIR`: refetch the exact excluded range from the
adapter → dedupe → validate → write new content-addressed file → **new**
`dataset_version` → mark the gap `REPAIRED` (gap_id, range, source, status, repaired_at) →
re-run Guardian. If the refetch cannot be validated the gap becomes `UNREPAIRABLE` and stays
excluded. A `gap_id` that does not exist → 404 `NOT_FOUND`.

## 7. Weekly resampling (§4.3, T1.4)

- Week = Monday 00:00 UTC … Sunday 23:59:59.999 UTC. `open` = first daily open, `high` =
  max, `low` = min, `close` = last daily close, `volume`/`quote_volume`/`trades` = SUM.
- A weekly bar is `incomplete=false` only if all 7 daily bars exist **and** none of those
  days is `ORANGE`/`RED` (§4.3) — so the resampler consumes Guardian day states. Otherwise
  `incomplete=true`; such bars are still written (to `data/derived`) so a chart can render
  them, but §4.3 excludes them from features/events/backtests (consumers filter
  `incomplete == false`; enforced from M2 onward).
- The forming week is never written (its Sunday bar is not closed yet).
- `1W` is never fetched from the venue (§4.3); `POST /v1/data/ingest {timeframe:"1W"}`
  runs a `RESAMPLE` job over stored `1D` data instead, and returns `DATA_UNAVAILABLE` if no
  `1D` data exists.
- Truncation invariance (§4.3, §14.1): `resample(data cut at t) == restrict(resample(full)
  to <= t)` — property test with hypothesis over random daily series.
- `close_time` of the weekly bar = `open_time + 7d` (the week boundary "Sunday
  23:59:59.999 UTC", §4.3). For a complete week that equals the Sunday daily bar's
  `close_time` — §4.3's "event_time = close_time of the Sunday bar" — and it keeps a
  week's identity independent of which days happened to be present (an incomplete week
  still closes at its boundary; it is excluded anyway).
- **Guardian day states enter as OPEN exclusion intervals**: the caller passes the active
  `system.gap` rows (kind GAP/INVALID) for the `1D` series; a daily `open_time` covered by
  an interval (`start <= open < end`) marks its week `incomplete`. Gap intervals cover
  absent days (already caught by the 7-day count); invalid intervals cover present-but-
  corrupt days. YELLOW/GREEN days carry no interval (§4.6), so no state vocabulary is
  re-invented here (PD-4).
- **Input contract (fail closed)**: exactly `BAR_COLUMNS`, all rows `timeframe == "1D"`,
  day-aligned (`open_time % 86_400_000 == 0`, `close_time - open_time == 86_400_000`),
  exactly one `instrument_id`. Any violation is a `ValueError` — resampling corrupt or
  foreign input must never produce bars (PD-10).

### 7.1 Writer extension for derived columns (AMENDMENT to §3)

- `1W` rows carry one extra column beyond §4.2, `incomplete: bool` (the §4.3 flag). The
  writer therefore takes `extra_columns: tuple[str, ...] = ()`: they are **written**,
  **hash** and **read back** alongside the canonical fields; the default keeps the §3
  behaviour byte-identical (goldens unchanged).
- `incomplete` participates in `content_sha256` (it is row identity, unlike
  `ingested_at`): two weekly datasets that differ only in the flag are different
  content and must not collide on one content-addressed path (§4.4 "content_sha256 over
  canonical row serialization" — serialization of *the rows stored*).
- Frames holding columns outside `BAR_COLUMNS + extra_columns` are now **refused**
  instead of silently dropped by the writer's `select` (fail closed; a silent drop would
  discard the flag after a caller forgot to pass `extra_columns`).
- `extra_columns` may not name a canonical field or `ingested_at` (identity rules in §3.1
  stay absolute — AC1).

## 8. Data Health UI + manifest viewer (T1.3)

- Nav gains `Data Health` (`/data`) and the manifest viewer lives at
  `/data/datasets/[dataset_version]`.
- `/data`: one row per `(instrument, timeframe)` — state badge (GREEN/YELLOW/ORANGE/RED
  *with* a text label, never colour alone, §12 WCAG), completeness %, open gaps,
  exclusion intervals (start/end/status), last check; gap list with a `Repair` action that
  posts `/v1/data/repair`; `Ingest` action for the seed universe.
- Manifest viewer: dataset_version, venue/market_type, instruments, timeframes, range,
  source_files (path, content_sha256, rows), row_counts, missing_ranges, quality_summary,
  the four version fields, created_at — i.e. exactly §4.4 so the manifest answers
  "what exactly did this use?".
- Every component implements the five states of §12 (loading / empty with next action /
  error with message + retry / stale-offline / partial data).

## 9. Fixtures and seed universe (T1.4)

- `fixtures/binance/{SYMBOL}/{TF}.json` — recorded once from Binance REST by
  `scripts/record_fixtures.py`, committed, never re-recorded (§14.1 golden fixtures; a
  change needs a PR note). Fixture metadata records source URL, request parameters, range
  and `recorded_at`.
- **Recording decisions (owner-approved):** window = `2024-01-01T00:00:00Z` → the last
  bar closed at record time (2026-10-07; recorded 2026-10-08) — 1011 / 6066 / 24264 bars
  per 1D / 4H / 1H symbol, exactly contiguous, ~11.1 MB total. Rows are slim
  `[open_time, open, high, low, close, volume, quote_volume, trades]` (the last two are
  required by `Kline`); the forming bar is excluded via `endTime`, pagination dedupes by
  `open_time`, and `tests/test_seed.py` pins the window (counts may only grow).
- `make seed-fixtures` writes them through the *same* writer/manifest/Guardian code path
  as live ingest (`scripts/seed_fixtures.py` → `engine/data/seed.py:run_seed` →
  `run_ingest`; no parallel code path), producing the seed universe **BTC, ETH, SOL ×
  1D, 4H, 1H** (OD-2) offline and deterministically: same fixtures → the same
  `dataset_version`, second run reuses every file and refetches nothing (PD-6, AC1).
- `make ingest ARGS="--start ... --end ..."` runs the live Binance backfill (network).
  Both window ends are **required request arguments** — no hidden default (PD-11); the
  budget comes from `config/defaults.yaml`. PERPETUAL / non-BINANCE fail closed before
  any fetch. Tests never touch the network.

## 10. API surface added in M1 (§11)

| Endpoint | Behaviour |
|---|---|
| `GET /v1/instruments` | registry list; filters `venue`, `market_type`, `asset` |
| `GET /v1/datasets/{dataset_version}` | manifest, 404 `NOT_FOUND` when unknown |
| `GET /v1/data/health` | latest `system.data_quality` per (instrument, timeframe) + exclusions; `market_type=PERPETUAL` → 501 |
| `GET /v1/data/gaps` | `system.gap` list; filters `instrument_id`, `timeframe`, `status` |
| `POST /v1/data/ingest` | → `{job_id, status:"queued"}` (INGEST or RESAMPLE); `PERPETUAL` → 501; bad timeframe → 422; budget exceeded → 422 |
| `POST /v1/data/repair` | → `{job_id, status:"queued"}` (REPAIR) |
| `GET /v1/jobs/{job_id}` | `{job_id, kind, status, progress, result_ref, params, logs, error, timestamps}`; 404 when unknown |

Not built here (later milestones, PD-9): `/v1/candles` (M2), watchlists (M8), any perp
data (M10).

## 11. Storage layout & budget guard

- `data/` is gitignored and immutable (PD-7). Sub-trees: `raw/` (untouched venue payloads,
  kept for provenance), `normalized/` (canonical bars), `derived/` (weekly).
- Budget = total bytes under `data/` (all sub-trees) compared against
  `data.storage_budget_gb` **before** any fetch or write; over budget ⇒ 422
  `VALIDATION_ERROR` with `{reason:"storage_budget_exceeded", used_gb, budget_gb}` (OD-5
  "ingest refuses to exceed"). Nothing is truncated or deleted to make room.

## 12. Test plan (tests first)

| Test module | Covers |
|---|---|
| `test_instruments.py` | uuid5 determinism, unique constraint, market_type vocabulary, identity fields |
| `test_timeframes.py` | native/derived vocabulary, bar lengths, alignment, closed-bar rule, unknown tf rejects |
| `test_parquet_writer.py` | content hash golden, row-order invariance, sort order, atomic+read-only, overwrite refused, partition path |
| `test_manifest.py` | dataset_version golden, path-independence, sensitivity to each version field, canonical ordering |
| `test_guardian.py` | each check fires, state precedence, completeness, exclusion interval spans only the affected bars, untouched history intact |
| `test_resample.py` | §4.3 aggregation, INCOMPLETE rules (missing day / ORANGE day), forming week excluded, hypothesis truncation invariance |
| `test_adapter.py` | httpx MockTransport: pagination, rate limiter, PERPETUAL → `NotImplementedMarket`, HTTP failure surfaces as `DATA_UNAVAILABLE` |
| `test_ingest.py` | AC1 idempotent re-ingest, resumability, AC5 budget, AC3 repair round-trip |
| `test_jobs.py` | enqueue/claim lifecycle, SKIP LOCKED ordering, stale recovery, failed-job envelope, AC2 manifest after success |
| `test_api_data.py` | endpoints + envelopes, AC2 manifest fetch, AC4 fail-closed, job lifecycle |
| `test_worker.py` | worker CLI claims the oldest queued job, empty queue no-op, build failure records FAILED without raising |
| `test_seed.py` | golden fixture validity + recorded window, malformed/missing fixture fail-closed, seed determinism (same `dataset_version`, no refetch) |

Golden values (hashes) are frozen in the test files; changing one requires a PR note
(§14.1).

## 13. Open questions carried into M1

- OQ-5: state for zero-volume fraction ≥ 0.1% (§4.6 silent) — implemented `YELLOW`.
- OQ-6: §4.6's OHLC triple omits `o <= h` / `l <= c` — confirmed not checked (PD-4);
  owner to confirm the spec text is complete.
- OQ-7: §4.6 names no state for strictly non-monotonic (decreasing) time — implemented
  `RED` (INTERPRETATION: ordering corruption is invalid alongside duplicates).
- `instrument_metadata_version` vocabulary (§4.4 names the field, not values).
- `STALE_GRACE_BAR_LENGTHS = 1` (§4.6 says "+ grace" without a value).
- Whether exclusion intervals stay active after `REPAIRED` (implemented: no).
- Whether `system.job` should grow `result_ref`/`logs` columns (§11 requires them, §10 DDL
  does not carry them — added in migration 0002, D-007).
