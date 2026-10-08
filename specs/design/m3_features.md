# M3 - Feature library + parity layer 1: design note

Companion to IMPLEMENTATION_SPEC_v1.1.1 Section 5 (feature library), Section
14.1 (property tests), Section 14.2 (TradingView parity, layer 1), Section 11
(`GET /v1/indicators`), Section 15 (milestone M3, T3.1-T3.2), and the reference
study `specs/reference/rvol_climax_pro_v5.pine` (script version `v5`).
Decisions recorded in docs/DECISIONS.md as D-015..D-019; questions OQ-13..OQ-15.

## 0. Acceptance-criteria mapping

| AC (M3) | Where it lands |
| --- | --- |
| feature parity within tolerance on BTCUSDT 1D | `make parity` -> `scripts/parity.py` (Section 14.2 layer 1): `tests/test_parity.py` covers the tool; the AC run itself needs the owner's TradingView export at `fixtures/parity/BINANCE_BTCUSDT_1D.csv` (owner confirmed 2026-10-08 they will provide it) |
| truncation tests pass | `tests/test_features_truncation.py` - hypothesis property suite over **every** registered feature and library function (Section 14.1) |
| T3.1 features per Section 5 | `engine/features/{primitives,regime}.py` + registry additions in `engine/features/indicators.py` |
| T3.2 parity tool: CSV import, run metadata capture, status categories | `engine/parity/` + `scripts/parity.py` + `make parity` |

## 1. Module layout (T3.1)

Section 5 says "pure functions in `engine/features` over Polars columns". The M2
registry (`indicators.py`) is one consumer of those functions; detectors (M4)
are another. Splitting keeps the contract honest:

| Module | Responsibility | Returns |
| --- | --- | --- |
| `engine/features/primitives.py` | numeric primitives moved verbatim from `indicators.py` (`column`, `rolling_mean`, `rolling_std_pop`, `ema`, `rma`, `true_range`) plus the new section 5 primitives (`highest`, `lowest`, `barssince`, `pivotlow`, `pivothigh`, `macd`, `adx`, `atr_percentile`) | `list[float \| int \| None]` |
| `engine/features/indicators.py` | the `/v1/indicators` registry (PD-11 resolution, validation, response envelope). Unchanged behaviour for the nine M2 indicators; every compute function now delegates to `primitives` | `Values = dict[str, list[float \| None]]` |
| `engine/features/regime.py` | section 5 "regime tags": `trend_state`, `vol_state`, `volume_state`; parameters resolve through `resolve_params(defaults, "features.<name>", params)` against a new `features:` block in `config/defaults.yaml` (PD-11, no signature defaults) | `list[str \| None]` |
| `engine/features/__init__.py` | re-exports the public feature API | - |

There is deliberately **no `library.py`**: the one non-numeric callable
(`barssince`) is a one-liner over booleans and sits in `primitives.py`, which
is where the test imports it from.

**D-015 (registry scope).** Every section 5 feature that yields a *numeric*
series is reachable from the registry (so `/v1/indicators` can serve it):
`rma`, `tr`, `highest`, `lowest`, `donchian_lo`, `donchian_hi`, `pivotlow`,
`pivothigh`, `macd`, `adx` join the nine M2 names. Two features stay library
only, and the reason is structural rather than a scope choice:
`barssince` takes a **boolean condition**, not a bar column (the frame carries
OHLCV only, so a `cond` parameter would have to invent a column vocabulary the
dataset does not have), and the regime tags return **labels**, which cannot
ride the float series contract (D-011).

## 2. Section 5 semantics actually pinned here

Reference for anything the reference study computes: `rvol_climax_pro_v5.pine`
lines 76-107 (`volMA = ta.sma(volume, volLen)[1]`, `rvol`, `hiN`, `loN`,
`emaR`, `cpos`, `upW`, `donLo/donHi`, `plv/phv`).

- `rma(x,n)` / `atr(n)` / `tr` / `sma` / `ema` / `rsi` / `bb` / `rvol` / `cpos`
  / `upper_wick` / `vwap`: already implemented and hand-verified in M2
  (`tests/test_features_m2.py`); M3 only exposes `rma` and `tr` as named
  registry entries (they were private helpers).
- `highest(x,n)` / `lowest(x,n)`: rolling max/min over the window of the last
  n bars **including** the current one, `null` until n values exist - the same
  warm-up rule section 5 states for `sma` ("null until n values exist", line
  229) applied to the whole feature family (line 228, "null during warm-up").
  `source` is a parameter over the bar columns, defaulted in
  `config/defaults.yaml` (`highest.source: high`, `lowest.source: low`, which
  is what section 6.3 and the reference study actually ask for). OQ-13: whether
  Pine returns `na` or the partial-extremum before bar n-1 is not pinned by the
  spec; layer 1 does not export this column, so it is recorded as an open
  question rather than guessed at in prose.
- `donchian_lo/hi(n)` = `lowest(low,n)` / `highest(high,n)`, incl. current bar
  (line 237) - thin wrappers, same warm-up rule.
- `barssince(cond)`: bars since `cond` was last true; `null` before the first
  true; `0` on a bar where it is true (line 235). Boolean input, library only.
- `pivotlow/pivothigh(src,L,R)`: value at bar `t` refers to bar `t-R` and is
  only known at `t` (no lookahead, line 236). **Tie handling (documented as
  required):** a candidate bar `p` is a pivot low iff `src[p] < src[p-k]` for
  every `k` in `1..L` **and** `src[p] < src[p+k]` for every `k` in `1..R`
  (strict on both sides); an equal neighbour disqualifies the pivot. Output is
  `src[p]` placed at index `p+R`, `null` everywhere else; the last R bars can
  therefore never carry a pivot (they cannot be confirmed yet). D-016 records
  the tie rule; OQ-14 asks for confirmation against Pine on an export that
  carries the pivots (layer 1 does not).
- `macd(12,26,9)`: `macd = ema(close,12) - ema(close,26)`,
  `signal = ema(macd,9)`, `histogram = macd - signal`, all seeded from the
  first value as section 5's `ema` definition requires, so no warm-up nulls.
- `adx(14)`: Wilder through `rma` - `tr -> rma(tr,n)`,
  `DI+ = 100*rma(+DM,n)/rma(tr,n)` (same for `DI-`), `DX = 100*|DI+ - DI-| /
  (DI+ + DI-)`, `ADX = rma(DX, n)`. A zero denominator yields `null`, never
  `inf` (the `rvol`/`rsi` precedent, M2 interpretation notes).
- Regime tags (line 242, "deterministic, documented, descriptive only"):
  - `trend_state` uses the reference study's own rule family
    (`inDown = close < emaR and emaR < emaR[5]`, line 123):
    `"down"` when `close < ema100 and ema100 < ema100[5]`,
    `"up"` when `close > ema100 and ema100 > ema100[5]`, otherwise
    `"neutral"`; `null` before index 5 (the slope lag needs five prior bars).
    Length defaults to 100 (the `regime_len` input of the reference study).
  - `vol_state` = percentile rank of `atr(14)[t]` inside the trailing 252
    bars (inclusive): `"low"` < 33, `"mid"` < 67, `"high"` otherwise; `null`
    until 252 ATR values exist.
  - `volume_state` = `rvol(20)` buckets: `"low"` < 0.5, `"normal"` < 1.0,
    `"elevated"` < 2.0, `"high"` < 3.0, `"climax"` >= 3.0. The top two edges
    are the section 6.3 spec thresholds (2.0 / 3.0); the descriptive edges are
    ours. **D-017:** these tags are descriptive metadata for the event
    `regime{}` payload (section 6.2), never a filter, threshold or trading
    rule, and they are labelled as engine-defined in the defaults file.

## 3. Truncation invariance suite (Section 14.1, AC)

`tests/test_features_truncation.py`:

- One hypothesis strategy generates a valid OHLCV frame (5..120 bars,
  monotonic `open_time`, `low <= min(open,close) <= max(open,close) <= high`,
  non-negative volume, finite floats drawn from a small decimal pool so
  equality is exact rather than tolerance-bound).
- For **every** registered indicator (with its documented defaults) and every
  library/regime function: compute on the full frame, cut at a random index
  `t` (`1 <= t < len`), compute on `frame[:t]`, assert the truncated result
  equals `full[:t]` element-wise (`None == None`). This is the spec's rule
  verbatim: "result on data cut at t == restriction to <=t of result on full
  data".
- A second property asserts the weaker but load-bearing PD-2 property on the
  same inputs: no output at index `i` changes when *future* bars change (edit
  bars after `i`, recompute, prefix must be identical). Cheap to check with
  the same strategy and it catches state that leaks forward rather than
  backward.
- Exact `==` is used (no tolerance): every implementation walks the series
  forward element-wise, so a prefix computation performs byte-identical
  operations. If a primitive ever violates this it is a bug, not rounding.

## 4. Parity tool (T3.2, Section 14.2)

### 4.1 Package

```
engine/parity/
  __init__.py              public API
  constants.py             WARMUP_BARS (500), REL_TOL (1e-6)
  metadata.py              TradingViewRunMetadata (required fields, fail-closed)
  importers.py             TradingView chart-data CSV -> ReferenceExport
  status.py                EXACT | EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE |
                           DATA_MISMATCH | ENGINE_BUG | UNKNOWN (+ worst-wins
                           overall())
  platform_differences.py  documented platform-difference table (line 592: each
                           entry carries its design-note anchor)
  compare.py               layer-1 comparison + report assembly
  run.py                   run_parity() -> report + gate exit code
scripts/parity.py          thin CLI behind `make parity`
```

### 4.2 CSV import

TradingView's chart-data export header spellings vary by locale/version, so the
importer maps columns through an **alias table** and refuses anything it does
not recognise (no silent coercion, PD-10 for data):

- required: `time` (epoch seconds or `YYYY-MM-DDTHH:MM:SSZ` /
  `YYYY.MM.DD HH:MM`), `open`, `high`, `low`, `close`, `volume`;
- required plots for layer 1: `rvol` (`RVOL`), `cpos` (`Close position`),
  `upper_wick` (`Upper wick share`), `ema_regime` (`Regime EMA`);
- `#`, `""`, `NaN`, `n/a` in a plot column -> `null` (Pine `na`);
- a missing required column, an unparsable time, or an unknown extra plot
  column -> the export is rejected with a precise error, and the run is
  classified `UNKNOWN` (it cannot be compared, and "cannot be compared" must
  never read as "passed").

OHLCV is compared first at `rel <= 1e-6`; a bar-level mismatch short-circuits
to `DATA_MISMATCH` and writes a bar diff report (first 20 differences) because
no feature comparison over mismatched bars means anything.

**Bar alignment (implementation decision).** Reference and engine bars are
matched by `open_time` (millisecond epoch). A reference bar outside the engine
dataset's range is reported as a warning and not compared (the export may be
longer than the fixture); a bar missing on either side *inside the overlap* -
the export skipping an engine bar, or the dataset missing an export bar - is a
`DATA_MISMATCH`, since each side claims to span that time. Comparison rows are
those where both sides' indices are `>= 500` (the warm-up skip applies to both
series); excluded rows are recorded as informational warm-up counts and can
never affect the status. An empty comparison window is `UNKNOWN`.

### 4.3 Comparison and status categories

- Compare only after the protocol warm-up: **skip the first 500 bars**
  (line 590). Plots are compared with relative tolerance `1e-6`
  (`|a-b| <= 1e-6 * max(|a|, |b|)`, both null = equal, one null = unequal).
- Per-plot and overall status; overall pass = **zero `ENGINE_BUG` and zero
  `UNKNOWN`** (line 592).
- Classification rules, in order:
  1. reference import failed / required metadata missing -> `UNKNOWN`;
  2. bar OHLCV differs beyond tolerance -> `DATA_MISMATCH` (+ diff report);
  3. engine raised -> `ENGINE_BUG` (an engine that cannot answer its own
     contract is a bug, never an unknown);
  4. plot differs and the difference matches an entry in the **documented
     platform-difference table** (`engine/parity/platform_differences.py`,
     each entry carrying the design-note anchor required by line 592) ->
     `EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE`;
  5. plot differs with no documented explanation -> `ENGINE_BUG`;
  6. otherwise -> `EXACT`.
- A mismatch is never automatically the engine's fault (line 592): the tool
  reports the numbers and the category, and a human decides whether a new
  platform-difference entry is warranted.
- Initial table entries: `tv_export_rounding` (TradingView writes ~6-8
  significant digits, so a value that differs only beyond the exported
  precision), `tv_na_vs_engine_null` (`na` in the export against a computed
  engine value at the same bar, only inside the first 500 bars).

### 4.4 Run metadata (line 593)

`TradingViewRunMetadata` is a frozen dataclass whose required fields are
`script_version`, `symbol`, `timeframe`, `date_range`, `bar_magnifier`
(expected `OFF`), `commission`, `slippage`, `fill_assumptions`, `export_date`.
It is read from a sidecar `fixtures/parity/<name>.meta.json` (or `--meta`);
a missing sidecar or a missing field fails closed as `UNKNOWN` - PD-11 has no
"reasonable default" for a TradingView run setting. The report embeds the
metadata verbatim plus the engine-side provenance PD-6 requires:
`dataset_version`, `engine_version`, `code_version`, `resolved_params`
(`seed` is not applicable to a deterministic comparison and is recorded as
`null`).

### 4.5 `make parity`

Replaces the D-005 placeholder (`NOT_IMPLEMENTED ... M6`):

- `make parity` -> `uv run python scripts/parity.py`; with no export present it
  exits non-zero printing `NO_REFERENCE_EXPORT` and the expected path (D-005's
  rule that a green gate must never imply unbuilt capability still holds);
- with an export it prints the per-plot table, the overall status and the
  report path. **D-019 (gate exit code):** the report's `pass` field is the
  spec's own rule verbatim (line 592: `pass = zero ENGINE_BUG and zero
  UNKNOWN`), while `make parity` exits 0 **only** for `EXACT` and
  `EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE` - `DATA_MISMATCH` therefore
  carries `pass: true` in the report but fails the gate, because a green gate
  must never print over bars that do not match;
- `make parity` stays the layer-1 entry point after M6 adds layer 2; D-005 is
  amended (D-018), not deleted.

## 5. Test and evidence plan

| Test file | Covers |
| --- | --- |
| `tests/test_features_m3.py` | hand-computed section 5 semantics for every new feature, registry/defaults contract (PD-11), regime-tag rules |
| `tests/test_features_truncation.py` | hypothesis truncation invariance + future-edit invariance over all features |
| `tests/test_features_m2.py` | untouched expectations, except the two tests that snapshot the M2 registry set (`macd` becomes a real indicator, so its "unknown name" probe moves to a name that stays unknown) |
| `tests/test_parity.py` | CSV import (aliases, na, time formats, rejections), tolerance/skip-500 comparison, status classification table, metadata fail-closed, report provenance, CLI exit codes |
| evidence | `uv run ruff check .`, `uv run mypy .`, `uv run pytest`, `pnpm -r typecheck`, `pnpm --filter web build`; the AC parity run against the owner's export with the printed table |

Browser evidence is not part of M3 (no chart surface changes; PD-9).

## 6. Out of scope (PD-9)

Layer 2 trade parity, event detectors (M4), stats/scopes (M5), any UI page for
the regime tags, and any re-export of the golden fixtures (D-009).
