# M2 - Chart: design note

Companion to IMPLEMENTATION_SPEC v1.1.1 Section 12 (chart workspace), Section 11
(GET /v1/candles, GET /v1/indicators), Section 4 (candle storage), Section 5
(feature/indicator semantics), Section 14.3 (performance targets), milestone M2
(T2.1-T2.3). Decisions recorded in docs/DECISIONS.md as D-011..D-014;
questions in OQ-8..OQ-12.

## 0. Acceptance-criteria mapping

| AC (M2) | Where it lands |
| --- | --- |
| 5,000-candle API < 300 ms | `tests/test_api_candles.py::test_5000_candle_request_meets_the_spec_budget` (Section 14.3) |
| indicators 5,000 bars < 150 ms | `tests/test_api_indicators.py::test_indicator_over_5000_bars_meets_the_spec_budget` |
| chart interactions < 50 ms | client-side: hover/pan read from already-loaded arrays; measured evidence in the demo (Section 14.3) |
| quality badge visible | `apps/web/app/chart` header badge fed by `/v1/candles` `quality` (Section 12) |
| T2.1 | `engine/features/` + `apps/api/market.py` (`GET /v1/candles`, `GET /v1/indicators`) |
| T2.2 | `apps/web/app/chart` workspace: symbol/tf, indicator menu, overlays, Volume + RSI sub-panes, crosshair sync, lazy history, exclusion bands |
| T2.3 | layout persistence (zustand + localStorage) and command palette skeleton (Ctrl/Cmd-K) |

## 1. Candle read path (Section 11, 4.2, 4.6, 4.8, AC via Section 12 badge)

`GET /v1/candles?instrument_id&tf&from&to&limit&as_of` returns exactly the
Section 11 contract:

```json
{"dataset_version":"...","quality":{"state":"GREEN","completeness":1.0,"exclusions":[]},
 "t":[],"o":[],"h":[],"l":[],"c":[],"v":[]}
```

Resolution order (fail-closed, PD-10; no substitution of venue, market_type,
timeframe or dataset):

1. `instrument_id` -> `get_instrument` (unknown -> 404 NOT_FOUND, as M1).
2. `market_type == PERPETUAL` -> 501 NOT_IMPLEMENTED before any read
   (Section 4.8; parity with the M1 ingest refusal).
3. `tf` -> `parse_timeframe`; anything outside the `Timeframe` enum -> 422.
4. Latest `market.dataset_manifest` whose `instruments`/`timeframes` arrays
   cover the request (`ORDER BY created_at DESC`) -> else 400 DATA_UNAVAILABLE.
   The manifest, not the directory listing, is the authority: a dataset that was
   never registered is not served (Section 4.4 "what exactly did this use").
5. Bars are read **only from the source files recorded in that manifest**
   (path -> rows -> year pruning via the path components), through
   `parquet.read_part`. Provenance stays answerable from the stored id alone.
6. `system.data_quality` latest row for (instrument, tf) -> `quality.state` and
   `quality.completeness`; `system.gap` open rows via `load_exclusions
   (active_only=True)` -> `quality.exclusions` (start/end/kind). No assessment
   row -> 400 DATA_UNAVAILABLE (unassessed data is not served).

Window semantics (PD-11: every knob documented in `config/defaults.yaml`):

- `from`/`to`: ISO-8601 UTC instants, inclusive; filter on `open_time`.
- `limit`: bars kept from the **most recent** end of the window. Default and
  maximum both come from `config/defaults.yaml` under `candles:` (5,000 = the
  Section 14.3 working set). A request never returns more than `max_limit`.
- `as_of`: only bars with `close_time <= as_of` (closed at that instant,
  Section 4.2 forming-bar rule, PD-2); also applied against now when absent -
  the forming bar is never served (display-only in the UI is a later concern).
- Window containing gaps: bars inside an exclusion interval are simply absent;
  the `quality.exclusions` payload tells the client where to shade. No
  DATA_INCOMPLETE error for partial windows - Section 12 requires the chart to
  *show* exclusion bands, which presupposes partial data (decision D-013).
- No overlap at all between window and available data -> 400 DATA_UNAVAILABLE.
- `t` is ISO-8601 UTC strings (API convention, Section 11); the client converts
  to the UNIX seconds lightweight-charts expects.

`1W` (derived): the read path supports any timeframe that has a covering
manifest, but the M1 seed writes no RESAMPLE manifest (m1_data Section 7:
"a 1W manifest comes from an explicit RESAMPLE job"), so `1W` answers
DATA_UNAVAILABLE today and is not offered in the chart timeframe menu
(OQ-12, BACKLOG).

## 2. Indicators (Section 11, Section 5)

`GET /v1/indicators?instrument_id&tf&name&params&from&to&limit&as_of` reuses the
same instrument/window/dataset resolution as Section 1 (same bars, same
`dataset_version`, same `as_of` cutoff - one dataset_version per response so PD-6
holds).

- `name` must exist in the engine registry; unknown -> 422 VALIDATION_ERROR.
- `params`: a JSON object string, e.g. `params={"length":20}`.
  - Allowed keys per indicator live in the registry (`engine/features/`).
  - Values resolve from the request, else from `config/defaults.yaml`
    `indicators:` (PD-11). Unknown key, or a key with no documented default ->
    422; never an invented default (PD-4/PD-11).
- Response schema is not written in Section 11 (only the query line) - design
  decision D-011 / OQ-9:

```json
{"name":"bb","params":{"length":20,"mult":2},"instrument_id":"...","tf":"1D",
 "dataset_version":"...","t":["..."],"series":[{"key":"upper","values":[null,...]},
 {"key":"middle","values":[...]},{"key":"lower","values":[...]}]}
```

  Single-output indicators carry one series; the keyed shape scales to MACD
  later without a contract change. Warm-up values are `null` (Section 5).

M2 registry set (only what the Section 12 chart needs; the rest of Section 5
lands with M3 - PD-9):

| name | outputs | source |
| --- | --- | --- |
| `sma` | sma | Section 5 |
| `ema` | ema | Section 5 (Pine `ta.ema`, no SMA seed) |
| `vwap` | vwap | Section 5 (UTC-day anchor, intraday) |
| `bb` | upper/middle/lower | Section 5 Bollinger(20,2, population stdev) |
| `rsi` | rsi | Section 5 (Wilder/rma); length required (OQ-8) |
| `atr` | atr | Section 5 `atr(n)=rma(tr,n)` |
| `rvol` | rvol | Section 5 `volume_t / sma(volume,n)_{t-1}` |
| `cpos` | cpos | Section 5 |
| `upper_wick` | upper_wick | Section 5 |

Supporting primitives (`rma`, `tr`) are internal. MACD/ADX/donchian/pivot/
barssince/regime tags stay for M3 (feature library + parity layer 1).

The Section 12 hover data window (rvol, cpos, upper_wick, ATR, regime EMA)
arrives as one indicator request per series, issued **in parallel when the bar
window loads**, not per hover: hover then reads already-loaded client arrays,
which keeps interactions inside the <50 ms budget.

## 3. API wiring (Section 11)

- New router `apps/api/market.py` mounted alongside the M1 data router; same
  error envelope, same `now_ms()` seam (M1 test pattern; API never reads wall
  time ad hoc).
- `resolved_params` echo: candle and indicator responses carry the resolved
  window (`from`, `to`, `limit`, `as_of`) and indicator `params`, so every
  number on screen is reproducible (PD-6/PD-11).
- OpenAPI -> regenerate `packages/client` (Section 3 contract rule).

## 4. Feature/indicator engine (`engine/features`)

- Pure Polars/Python column functions over bar columns; no I/O, no globals.
- Causality: every output at bar t uses only inputs <= t (PD-2). Warm-up is
  null, never forward-filled.
- Definitions follow Section 5 literally (ema alpha, rma seed at index n-1,
  rvol denominator at t-1, population stdev for BB, Wilder rsi via rma).
- Truncation-invariance property tests for the full library are M3 scope; M2
  ships unit tests with hand-computed expectations plus a truncation smoke test
  for each M2 indicator.

## 5. Chart workspace (Section 12, T2.2/T2.3)

Stack per Section 3: TradingView **Lightweight Charts v5** (multi-pane, keep
attribution), zustand, TanStack Query, React 19/Next 16 App Router. Styling
stays on the existing M0 token CSS (Tailwind/shadcn adoption is not M2 scope).

Route `/chart` (nav gains "Chart" - the module now exists, so PD-9 is satisfied):

- **Header**: symbol + venue + market + timeframe (from `/v1/instruments`),
  timeframe menu (1D/4H/1H), indicator menu (overlays + sub-pane toggles),
  layout persistence control, **data-quality badge** (state word + color +
  completeness % + exclusion count; never color-only, WCAG AA, tooltip lists
  exclusion intervals).
  Event-layer toggle and replay are NOT built here: they belong to M4/M8 and
  would be dead controls now (PD-9); parked in BACKLOG.
- **Main pane**: candlestick series + overlay line series for each enabled
  indicator (EMA/SMA/VWAP/BB). Server-computed (Section 12 "server computes
  indicators/events").
- **Sub-panes**: Volume default on (+ rvol as a line), RSI toggleable;
  collapsible. Each pane is its own lightweight-charts instance; panes share the
  visible time range (`subscribeVisibleLogicalRangeChange` -> `setVisibleLogical
  Range`) and the crosshair (`subscribeCrosshairMove` -> `setCrosshairPosition`
  on the peer panes' anchor series).
- **Lazy history**: when the visible range approaches the loaded window edge,
  fetch the adjacent window (`to` = oldest loaded open_time) and prepend;
  scroll position preserved. Loading <= `max_limit` (5,000) per request.
- **Exclusion bands**: DOM overlay layer above the time scale, positioned with
  `timeScale.timeToCoordinate` for each exclusion start/end, re-anchored on
  visible-range change; shaded rectangle + native tooltip with kind and range
  (Section 12). Chosen over per-series price lines because bands are time ranges
  spanning panes (decision D-014).
- **Hover data window**: fixed panel reading the preloaded arrays for the
  crosshair bar: rvol, cpos, upper_wick, ATR, regime EMA (Section 12).
- **States**: loading / empty (no instruments -> next action) / error (envelope
  message + Retry) / stale (M1 banner pattern reused) / partial (exclusions
  shaded) - Master section 62, as on the Data Health page.
- **T2.3 layout persistence**: zustand store persisted to localStorage
  (instrument, timeframe, enabled overlays, open/collapsed panes, pane order).
  The spec says "layout persisted" without naming a medium (OQ-10): localStorage
  is client-local, reversible, and needs no new API surface.
- **T2.3 command palette skeleton**: Ctrl/Cmd-K opens a filterable action list
  (switch instrument, switch timeframe, toggle overlay, toggle pane, go to Data
  Health). Skeleton only - no research actions until their milestones exist.

## 6. Performance plan (Section 14.3)

- API candles: pytest builds >= 5,000 closed bars in a temp data dir, asserts
  the endpoint answers within the 300 ms budget, records measured p50/p95.
- API indicators: same corpus, 5,000-bar indicator within 150 ms.
- Both run in the default pytest gate; failures are reported plainly (PD-3),
  never relaxed. Targets are "laptop" targets - if CI noise appears later, the
  fix is a DECISIONS entry, not a skip (Section 0).
- Chart interaction <50 ms and 60fps pan/zoom: hover reads are local array
  lookups; pan/zoom is canvas rendering inside Lightweight Charts over <=5,000
  loaded candles. Demo evidence measures load-to-interactive and a programmatic
  range change; recorded in the M2 report with the numbers actually observed.

## 7. Test plan (tests first)

`tests/test_features_m2.py` - per-indicator hand-computed values, warm-up nulls,
causality/truncation smoke (input truncated at t == restriction of full output).
`tests/test_api_candles.py` - contract shape; closed-only (forming bar absent);
`as_of` cutoff; window+limit semantics; unknown instrument 404; unknown tf 422;
perp 501; no manifest 400 DATA_UNAVAILABLE; no assessment 400; exclusions echoed;
manifest file selection (only files the manifest declares are read); perf budget.
`tests/test_api_indicators.py` - registry rejection 422 (unknown name / unknown
param / undocumented default); resolved params echo; null warm-up; dataset_version
matches the candles response for the same window; perf budget.
Web: typecheck + build gates; browser evidence for badge, bands, panes, palette,
persistence across reload (screenshots + console-error check).

## 8. Open questions (parked, not blocking M2)

- OQ-8: Section 5 gives no RSI default length - `indicators.rsi.length` stays
  `null` (required, 422 without a request value) until M3 parity confirms
  whether 14 (TradingView default) becomes a documented default.
- OQ-9: indicator response schema absent from Section 11 - D-011 above.
- OQ-10: "layout persisted" medium unspecified - localStorage, D-012.
- OQ-11: partial-window candle behavior - D-013 (serve + shade, no error).
- OQ-12: `1W` needs a RESAMPLE manifest before the chart can offer it.
