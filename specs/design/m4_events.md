# M4 - Event engine: design note

Companion to IMPLEMENTATION_SPEC_v1.1.1 Section 6 (event engine), Section 11
(`GET /v1/events`, Evidence payload), Section 12 (chart markers, hover cards,
climax-no-trade layer), Section 15 (milestone M4, T4.1-T4.5), the reference
study `specs/reference/rvol_climax_pro_v5.pine` (script version `v5`), and the
owner's wrun-ts port (same detection logic, epsilon-comparison runtime;
cross-check source, not the port target). Decisions recorded in
docs/DECISIONS.md as D-022..D-024.

## 0. Acceptance-criteria mapping

| AC (M4) | Where it lands |
| --- | --- |
| truncation invariance passes | `tests/test_events_truncation.py` - hypothesis property over `detect()` (events on a prefix == restriction of full-data events) and over `compute_outcomes()` (complete outcomes identical; incomplete stay `complete=false` until enough future bars exist) |
| h=10 outcomes equal the Pine study per-signal returns on parity data | `tests/test_events_parity_h10.py`: detector + outcomes on the engine dataset behind `fixtures/parity/BINANCE_BTCUSDT_1D.csv` (D-020); per signal type (CONT/CAPIT/FAIL/TOP) the exact float tuple `(n, sum(fwd_ret_pct[10]), win_count)` must equal the reference study's table row read from the owner's TradingView chart via tvcontrol (`data_get_pine_tables`, study `RVOL Climax Pro`), same symbol/tf/range; plus exact-formula unit tests. Evidence chain and the aggregate-vs-per-signal limitation are pinned in Section 7 |
| markers render | `GET /v1/events` serves the envelopes; the chart events layer sets lightweight-charts markers with the Section 12 shapes/colors/labels; unit test pins the event->marker serialization; demo evidence in the milestone report |

## 1. Module layout (T4.1)

| Module | Responsibility |
| --- | --- |
| `engine/events/base.py` | `Detector` protocol (id, version, params_model keys, required_inputs, warmup_bars, lookback_bars, default_cluster_model, `detect`), `Event` dataclass (Section 2), `event_id` hash, provenance constants |
| `engine/events/cooldown.py` | the parity-critical cooldown gate (Section 3); shared by every future detector that uses the barssince form |
| `engine/events/registry.py` | autoload `engine/events/detectors/*.py`, key `(detector_id, version)`, `resolve_detector`, `known_detectors` |
| `engine/events/detectors/rvol_climax.py` | RVOL_CLIMAX v5.0 (Section 4) |
| `engine/events/outcomes.py` | Section 6.4 outcome computation (Section 5) |
| `engine/events/__init__.py` | public re-exports |
| `apps/api/events.py` | `GET /v1/events` (list, filtered) - on-demand compute, no persistence yet (Section 6) |
| `apps/web/app/chart/events.ts` + panes integration | marker serialization + events layer + hover card (Section 6) |
| `specs/events/RVOL_CLIMAX.md` | the full detector spec (T4.5, template gate complete) |

The six `SPEC_REQUIRED` placeholders stay exactly as they are (PD-4). A
registry test asserts none of their names resolves; there is deliberately no
stub module for them - a module would be an invitation to implement.

## 2. Detector framework and event object (T4.1)

### 2.1 Detector contract (section 6.1)

```python
class Detector(Protocol):
    detector_id: str          # e.g. "RVOL_CLIMAX"
    version: str              # e.g. "5.0"
    required_inputs: tuple[str, ...]   # bar columns + feature names needed
    warmup_bars: int          # bars before the first bar a rule may fire
    lookback_bars: int        # longest window any derived input reaches back
    default_cluster_model: str        # documented ASSUMPTION (stats, M5)
    def detect(frame, resolved_params, exclusions) -> list[Event]: ...
```

Parameters resolve through `resolve_params(defaults, "detectors.<key>",
request_params)` exactly like indicators (PD-11): the defaults key for
RVOL_CLIMAX is `detectors.rvol_climax_v5` (already present in
`config/defaults.yaml` with every Section 6.3 parameter plus
`lookback_bars: 100`). Undocumented request parameters raise `ParamError`
(HTTP 422); nothing is invented.

Registry keys are `(detector_id, version)`. Any rule/param/default change
bumps the version (Section 6.5); old versions stay registered and runnable.
Autoload imports every module under `engine/events/detectors/` that exposes a
`DETECTOR` attribute; a module that fails to import is a build error, never a
silent skip.

### 2.2 Event object (section 6.2)

Frozen dataclass; serialized by the API with every field the spec names:

- `event_id = sha1(f"{detector_id}|{version}|{params_hash}|{instrument_id}|{timeframe}|{event_time}")`
  (sha1 per spec; content-addressed, so recompute == same id, which is what
  makes on-demand compute safe before any DB table exists).
- `detector_id`, `event_type`, `version`, `params_hash`, `instrument_id`,
  `timeframe`, `event_time` (CLOSE time of the bar on which the rule became
  true), `direction`, `ref_price` (close of the event bar),
  `invalidation_price`, `regime` (the Section 5 tags, descriptive only,
  D-017), `features` (rvol, cpos, upper_wick, atr, ema_regime, archetype),
  `trigger` (`rules: [{name, passed, value, threshold}]`),
  `lookback_gap`, `dataset_version`, `engine_version`,
  `data_quality` (`{state, exclusion_intervals}`), `resolved_params` (PD-6
  stores it on every result; `params_hash` is its canonical-JSON sha256).
- Provenance (Master section 63): `ref_price`/`event_time` OBSERVED (copied
  from the bar); every derived number and the archetype label CALCULATED;
  nothing in v1 is INFERRED or AI.

`params_hash = sha256(canonical_json(resolved_params))` with sorted keys.

### 2.3 lookback_gap (section 4.3 of the bar spec, T4.1)

`detect()` receives the dataset's exclusion intervals (the M1 guardian
output). An event whose `[event_index - lookback_bars, event_index]` window
overlaps any exclusion interval carries `lookback_gap=true`. The flag is
reported, never silently dropped; stats later exclude those by default
(config `exclude_lookback_gap_events=true`, M5). Truncation-invariance is
unaffected: exclusions are a property of the dataset, not of the frame cut.

## 3. Cooldown gate (parity-critical, section 6.7)

One shared, golden-tested implementation used by every raw-signal stream:

```text
barssince_raw[t] = 0 if raw[t] else (t - last_true_index)   # primitives.barssince
gap[t]           = 999 if t == 0 or barssince_raw[t-1] is None else barssince_raw[t-1]
emit[t]          = raw[t] and gap[t] >= cooldown
```

This is exactly Pine `sig = raw and nz(barssince(raw)[1], 999) >= cooldown`:
the lag `[1]` is what makes fixture A work (a raw bar never suppresses
*itself*; the current bar's `barssince==0` is not yet visible). State is
**independent per event type** - four separate `barssince` states for
RVOL_CLIMAX (fixture F). `cooldown=0` emits every raw bar (fixture G) because
`gap >= 0` always holds.

`barssince` counts **raw** occurrences, not emitted signals (spec line 265).

Fixtures A-G (Section 6.7 of the spec) are boolean synthetic series stored as
JSON under `fixtures/events/cooldown/*.json` and compared **exactly**; they
are a blocking CI gate. wrun-ts cross-check: `prev_gap_*` is read before
`since_*.update(...)` - the same lag - and its fixture behaviour matches.

## 4. RVOL_CLIMAX v5.0 (T4.2)

Port of the Pine detection block (lines 75-146) onto the M2/M3 primitives -
one implementation, no re-derivation. Every formula below is pinned to both
reference sources (Pine line numbers; wrun-ts `onBar` blocks agree):

| Quantity | Definition | Implementation |
| --- | --- | --- |
| `atr` | `ta.atr(atr_len)` | `rma(true_range(...), atr_len)` |
| `volMA` | `ta.sma(volume, vol_len)[1]` | `rolling_mean(volume, vol_len)` lagged 1 |
| `rvol` | `volMA > 0 ? volume / volMA : na` | same as M2 `rvol` indicator |
| `hiN` / `loN` | `ta.highest(high, range_len)[1]` / `ta.lowest(low, range_len)[1]` | `highest(...)[t-1]` / `lowest(...)[t-1]` |
| `emaR` | `ta.ema(close, regime_len)` (NOT lagged) | `ema(close, regime_len)` |
| `swingLo` | `ta.lowest(low, bounce_len)[1]` | `lowest(...)[t-1]` |
| `cpos` / `upW` | Pine 86-88 (flat bar -> 0.5 / 0.0) | M2 `cpos` / `upper_wick` |
| `trendU` | `close > close[trend_len]` | direct index |
| `atHi`/`atLo`/`nearHi` | `close >= hiN` / `close <= loN` / `high >= hiN` | direct |
| `isClimax` | `rvol >= climax_mult` | direct |
| `inDown` | `close < emaR and emaR < emaR[trend_len]` (Pine uses `[5]` = `trend_len`) | direct |
| `bounced` | `high - swingLo >= bounce_atr * atr` | direct |
| `rejected` | `close < open and upW >= wick_min` | direct |
| `topReject` | `close < open and (upW >= wick_min or cpos <= close_pos_lo)` | direct |

Raw conditions (Pine 127-135):

```text
rawTop = rvol >= top_mult and close > emaR and nearHi and topReject
rawCont = isClimax and trendU and atHi and not rawTop
rawCap  = isClimax and not trendU and cpos <= close_pos_lo and atLo
rawFail = (fail_mode == "failed_bounce") ? newFail : legacyFail
  newFail    = isClimax and inDown and bounced and rejected and not atLo
  legacyFail = isClimax and not trendU and cpos > close_pos_lo
```

`fail_mode` vocabulary: `"failed_bounce" | "legacy_v1"` (defaults.yaml).
Each raw stream passes through the Section 3 cooldown gate independently;
emitted signals become events: CONT (LONG), CAPIT (LONG), FAIL (SHORT),
TOP (SHORT).

**Comparisons are exact float `>=`/`>`** (Pine semantics). The 1e-10
epsilons in the owner's wrun-ts port are that runtime's f64-comparison
hardening, NOT part of the rule; they are not ported. Threshold ties are
deterministic either way and golden fixtures use values away from ties.

**Naming note (spec 269, goes verbatim into the detector spec):** event type
`TOP` is NOT `TOP_CAPIT`, `TOP_CLIMAX`, `TOPFAIL`, or any distribution /
exhaustion concept; future splits each need their own spec. Strategy-layer
signal flags (`use_long_cont` etc.) and side mode belong to the strategy
definition (Section 7), never to the detector: the detector emits **all four
types independently of strategy flags** (spec 252).

Archetype (climax bars only; Pine 96-102): `trendU`: cpos>=0.70
"top·drive high", cpos<=0.30 "top·sell pressure", else "top·mid"; not
trendU: cpos<=0.30 "bot·drive low", cpos>=0.70 "bot·cover press", else
"bot·mid". Non-climax bars: no archetype (null).

Invalidation (event-study only, NOT the strategy stop): LONG
`close - 3.0*atr`, SHORT `close + 3.0*atr`, `R_basis="atr"` (spec 268).

`warmup_bars = lookback_bars = 100` (longest window: `regime_len` EMA plus
margin; defaults pin `lookback_bars: 100`). No event may fire on a bar whose
lookback window is not fully inside the frame - the window check uses the
*feature* windows (vol_len, range_len, regime_len, bounce_len, atr_len,
trend_len), and `lookback_bars` is the envelope that also drives
`lookback_gap`.

Default `cluster_model: "SYMBOL_GAP + CALENDAR_WEEK"` - documented ASSUMPTION
in the detector spec (stats consumes it in M5; the detector does not apply
clustering).

## 5. Outcomes (T4.3, section 6.4)

`compute_outcomes(events, frame, horizons=(1,2,3,5,10,15,20,30,40,50))` -
pure function, no parameter defaults beyond the spec list (PD-11).

For event at bar index `t`, `ref = close[t]`, `R = |ref - invalidation|`
(= `3.0 * atr[t]` by construction), `move(h) = c_{t+h} - ref` (LONG) or
`ref - c_{t+h}` (SHORT):

- `fwd_ret_pct(h) = move(h) / ref * 100`
- `fwd_ret_R(h) = move(h) / R`
- **`fwd_ret_*` always uses the full horizon h** - no stop truncation. This
  is what the Pine study computes (it pops each signal at `bar_index - s >= h`
  using that bar's close, unconditionally: Pine 321-363) and what the h=10 AC
  compares against.
- `hit_invalidation(h)`: true iff, scanning bars `t+1..t+h` in order, the
  invalidation price is touched (LONG: `low <= inv`; SHORT: `high >= inv`).
- **Same-bar ambiguity: stop first** (spec 273). The first touching bar ends
  the excursion window at that bar (inclusive); `mfe_R`/`mae_R` and
  `bars_to_mfe`/`bars_to_mae` are measured over `[t+1, min(t+h, stop_bar)]`.
  Ties (equal extremes) resolve to the earliest bar. `fwd_ret_*` is
  unaffected (see above) - the stop-first rule governs the excursion metrics
  and `hit_invalidation`, which is the only reading under which the h=10 AC
  (Pine study equality) can hold when a stop prints before h=10.
- `mfe_R` / `mae_R` (direction-adjusted, in R):
  LONG `(max high - ref)/R` and `(min low - ref)/R` over the window;
  SHORT `(ref - min low)/R` and `(ref - max high)/R`.
- `bars_to_mfe` / `bars_to_mae`: 1-based bar offset of the first bar that
  prints the window extreme.
- `complete = false` when `t + h > last_closed_bar`; an incomplete horizon
  carries `null` values and is **never partially counted** (spec 272).

## 6. API and chart markers (T4.4)

### 6.1 `GET /v1/events` (on-demand compute, no DB table in M4)

Query: `detector`, `event_type`, `instrument_id`, `tf`, `from`, `to`,
`limit` (section 11). The handler resolves the dataset behind
`(instrument_id, tf)` the same way `/v1/candles` does, runs the detector with
request-or-defaults params (PD-11), attaches `dataset_version` /
`engine_version` / `data_quality` from the manifest, and returns the event
envelopes.

**No events table in M4** (design decision, recorded): `event_id` is
content-addressed, so recompute is idempotent and stable; persistence lands
with M5 stats where `ref_id` lineage needs rows. This keeps M4 inside the
milestone's scope (PD-9) without inventing a migration the milestone card
does not ask for. The response shape is the Section 6.2 envelope, so adding
persistence later is additive.

`GET /v1/events/{id}` (Evidence payload with stats) is **M5** (T5.3 Evidence
panel); M4 serves the list endpoint the chart layer needs.

### 6.2 Chart layer

- Events layer toggle in the chart header (spec line 551), default ON once
  data is present.
- Markers on the candle series via lightweight-charts `setMarkers`, exact
  Section 12 styling: CONT = teal triangle-up below bar + text "CONT";
  CAPIT = lime label-up below + "CAPIT"; FAIL = orange triangle-down above +
  "FAIL"; TOP = red triangle-down above + "TOP".
- Climax-no-trade layer (optional dots, default ON): bars where `isClimax`
  and no signal fired on that bar, colored per Pine 383/384: red when
  `trendU`, else lime when `cpos <= close_pos_lo`, red when
  `cpos >= close_pos_hi`, gray otherwise. Rendered as tiny circle markers
  above the bar.
- Hover card (DOM overlay, same pattern as the M2 exclusion bands): when the
  crosshair sits on a bar carrying an event, a card near the cursor shows
  type, time, direction, ref price, invalidation, rvol, cpos, archetype and
  the trigger checklist (name / value / threshold / passed). Click opens the
  same card pinned. The full Evidence panel (stats, grades, horizons) is M5;
  M4 shows only detection evidence - numbers come from the event envelope,
  never recomputed in the UI (PD-5).

## 7. Golden fixtures and the h=10 AC evidence chain

1. **Cooldown A-G** (`fixtures/events/cooldown/`): synthetic boolean series,
   exact JSON expected emits; blocking CI gate (spec 6.7).
2. **Detector golden** (`fixtures/events/rvol_climax_v5/`): synthetic OHLCV
   bars engineered to fire each of the four types (and one bar to fire none),
   expected event list as JSON - event_type, bar index, event_time, ref,
   invalidation, archetype, trigger pass/fail vector. Compared exactly.
3. **Formula unit tests**: hand-computed tiny series for rvol/hiN/swingLo lag
   semantics, cooldown lag (fixture A explained bar-by-bar in the test
   docstring), outcome formulas incl. stop-first and incomplete horizons.
4. **h=10 AC on parity data**: run the detector + outcomes on the engine bars
   behind the D-020 parity range; for each signal type compare
   `(n, sum(fwd_ret_pct[10]), win_count)` against the reference study table
   row read from the owner's TV chart (tvcontrol `data_get_pine_tables`) on
   the same symbol/tf/range with `study_horizon=10` and the reference
   defaults. **Limitation, recorded honestly:** the stock study table
   publishes aggregates, not the per-signal arrays (they live in Pine
   arrays the script does not export), so aggregate equality on exact float
   sums plus the independent exact-formula unit tests is the strongest
   evidence available without modifying the reference script (which is the
   golden source and must not be changed). If the owner later exports
   per-signal rows, the test upgrades to per-signal comparison without a
   rule change.
5. **Truncation properties**: `detect` and `compute_outcomes` over hypothesis
   frames (same generator style as `tests/test_features_truncation.py`,
   including flat bars and zero volume).

## 8. Spec files (T4.5)

- `specs/events/RVOL_CLIMAX.md`: full template gate, every section filled
  from Sections 3-5 above; identity `RVOL_CLIMAX` / `5.0` / status READY /
  direction BOTH (FAIL/TOP are research events; R11: short *strategies*
  disabled until M10, the events themselves are not); naming note verbatim
  (spec 269); default cluster_model ASSUMPTION; parity criteria = the AC
  chain of Section 7; implementation-gate checklist all checked.
- The six `SPEC_REQUIRED` placeholders unchanged; registry test asserts their
  names are absent from `known_detectors()`.

## 9. Test plan (tests-first; RED before GREEN per the task loop)

| Test file | Covers |
| --- | --- |
| `tests/test_events_cooldown.py` | fixtures A-G exact; lag semantics; independence; cooldown=0 |
| `tests/test_events_detector.py` | derived features vs hand values; four raw rules; archetype; invalidation; warmup; lookback_gap; registry (incl. SPEC_REQUIRED absence); param resolution 422s; event_id stability |
| `tests/test_events_outcomes.py` | formulas, stop-first, ties, incomplete horizons, all four horizons list |
| `tests/test_events_truncation.py` | detect + outcomes properties |
| `tests/test_events_parity_h10.py` | AC: aggregates vs TV study table (skips with a clear reason when no table export is present locally; the CI gate is the golden fixtures) |
| `tests/test_events_api.py` | GET /v1/events contract, filters, 422 on undocumented params |

## 10. Open questions

- OQ-16: M4 ships no `events` DB table (Section 6.1); M5 stats will need
  persisted events or stable recompute-at-query - confirm recompute-on-demand
  remains acceptable through M5b lineage, or schedule the migration there.
- OQ-17: tvcontrol access to the owner's chart running the reference study
  for the h=10 table read - needed once for the AC evidence; the test skips
  honestly without it.
