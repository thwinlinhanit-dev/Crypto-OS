# RVOL_CLIMAX

> **Status: READY**

## 1. Identity
- `event_id`: `RVOL_CLIMAX`
- `detector_id`: `RVOL_CLIMAX`
- `version`: `5.0`
- `status`: `READY`
- `direction`: `BOTH` (CONT/CAPIT are LONG; FAIL/TOP are SHORT — R11: short *strategies* disabled until M10; the events themselves are research events, not strategy entries)
- `source`: `specs/reference/rvol_climax_pro_v5.pine` (lines 75–146)

**Naming note (spec line 269, verbatim):** event type `TOP` is NOT `TOP_CAPIT`, `TOP_CLIMAX`, `TOPFAIL` or any distribution/exhaustion concept. Strategy flags (`use_long_cont` etc.) and side mode belong to the strategy definition (Section 7), never to the detector: this detector emits all four types independently of strategy flags (spec line 252).

## 2. Purpose
### Intended meaning
Detect relative-volume climax bars that mark exhaustion or continuation extremes on the daily chart, classified into four raw streams (`CONT`, `CAPIT`, `FAIL`, `TOP`) then gated by an independent per-stream cooldown.

### Non-goals / exclusions
- No strategy entry or exit logic (R11; short strategies disabled until M10).
- No clustering applied (default `cluster_model` is a documented ASSUMPTION for M5 stats; the detector does not cluster).
- No re-derivation of M2/M3 features; every derived quantity is pinned to the Pine line that defines it.

## 3. Required Inputs
| Input | Exact definition | Warmup | Causal |
|---|---|---:|---|
| `open` | bar open price | 0 | YES |
| `high` | bar high price | 0 | YES |
| `low` | bar low price | 0 | YES |
| `close` | bar close price | 0 | YES |
| `volume` | bar volume | 0 | YES |
| `open_time` | bar open time (ms) | 0 | YES |
| `timeframe` | bar timeframe string | 0 | YES |

## 4. Parameters
All parameters resolve from `config/defaults.yaml` section `detectors.rvol_climax_v5` (PD-11: no hidden defaults; undocumented parameters raise `ParamError`).

| Parameter | Type | Required | Default | Constraints | Meaning |
|---|---|---|---|---|---|
| `vol_len` | int | NO | 20 | >= 1 | volume SMA window for `volMA` (lagged) |
| `climax_mult` | float | NO | 2.5 | >= 0 | `rvol >= climax_mult` → climax bar |
| `top_mult` | float | NO | 2.0 | >= 0 | additional `rvol >= top_mult` for raw TOP |
| `range_len` | int | NO | 20 | >= 1 | `hiN`/`loN` window (lagged) |
| `trend_len` | int | NO | 5 | >= 1 | `trendU` lookback; `inDown` emaR lag |
| `close_pos_lo` | float | NO | 0.25 | [0, 1] | cpos lower bound for CAPIT/legacy-FAIL |
| `close_pos_hi` | float | NO | 0.75 | [0, 1] | cpos upper bound for archetype 1/5 |
| `regime_len` | int | NO | 50 | >= 1 | `emaR` period (NOT lagged) |
| `bounce_len` | int | NO | 10 | >= 1 | `swingLo` window (lagged) |
| `bounce_atr` | float | NO | 1.0 | >= 0 | bounce threshold in ATR multiples |
| `wick_min` | float | NO | 0.30 | >= 0 | upper-wick minimum for reject/rejected |
| `cooldown` | int | NO | 3 | >= 0 | independent per-stream cooldown gap |
| `atr_len` | int | NO | 14 | >= 1 | ATR period (RMA of true range) |
| `fail_mode` | str | NO | `"failed_bounce"` | `"failed_bounce"` or `"legacy_v1"` | which FAIL rule to use |
| `lookback_bars` | int | NO | 100 | >= 1 | bars before first eligible event bar |

`close_pos_lo <= close_pos_hi` is enforced at resolve time.

## 5. Derived Features
```text
volMA   = lag(SMA(volume, vol_len), 1)          # Pine [1] lag
rvol    = volume / volMA (null when volMA is None or <= 0)
hiN     = lag(highest(high, range_len), 1)
loN     = lag(lowest(low, range_len), 1)
emaR    = EMA(close, regime_len)                # NOT lagged (Pine: current bar included)
swingLo = lag(lowest(low, bounce_len), 1)
cpos    = (close - low) / (high - low)          # 0.5 when range = 0
upW     = (high - max(open, close)) / (high - low)  # 0.0 when range = 0
trendU  = close > close[trend_len]              # null when index < trend_len
atHi    = close >= hiN                          # null when hiN is None
atLo    = close <= loN                          # null when loN is None
nearHi  = high >= hiN                           # null when hiN is None
inDown  = close < emaR and emaR < emaR[trend_len]
bounced = high - swingLo >= bounce_atr * atr
rejected     = close < open and upW >= wick_min
topReject    = close < open and (upW >= wick_min or cpos <= close_pos_lo)
```

Archetype (Pine 96–102; null on non-climax bars):
```text
isClimax = rvol >= climax_mult
if isClimax:
  if trendU:  c >= close_pos_hi → "top·drive high";  c <= close_pos_lo → "top·sell pressure";  else → "top·mid"
  else:       c <= close_pos_lo → "bot·drive low";   c >= close_pos_hi → "bot·cover press";   else → "bot·mid"
```

## 6. Trigger Rule
```text
rawCONT = isClimax and trendU is True and atHi is True and not rawTOP
rawCAPIT = isClimax and trendU is False and cpos <= close_pos_lo and atLo is True
rawTOP = isClimax and nearHi and close > emaR and topReject and rvol >= top_mult
rawFAIL_new = isClimax and inDown and bounced and rejected and atLo is not True
rawFAIL_legacy = isClimax and trendU is False and cpos > close_pos_lo
rawFAIL = rawFAIL_new if fail_mode == "failed_bounce" else rawFAIL_legacy
```
All comparisons are exact float `>=`/`>` (Pine semantics, no epsilons).

## 7. Cooldown / De-duplication
Section 6.7 shared cooldown (D-022): each of the four streams passes through `apply_cooldown` independently. The gap is read **before** the `barssince` update; seed is 999; `cooldown=0` disables gating.

## 8. Invalidation
```text
invalidation_price = close - 3.0 * atr   (LONG:  CONT, CAPIT)
invalidation_price = close + 3.0 * atr   (SHORT: FAIL, TOP)
R_basis = "atr"
R = |ref_price - invalidation_price| = 3.0 * atr
```
Constant `INVALIDATION_ATR_MULT = 3.0` (spec line 268; Pine `atrStopMult` default).

## 9. Event Object
Populates the standard envelope: `event_id`, `detector_id`, `event_type`, `version`, `params_hash`, `instrument_id`, `timeframe`, `event_time` (bar close time), `direction`, `ref_price`, `invalidation_price`, `regime`, `features` (rvol, cpos, upper_wick, atr, ema_regime, archetype), `trigger.rules`, `lookback_gap`, `dataset_version`, `engine_version`, `data_quality`, `resolved_params`.

Provenance: `ref_price`/`event_time` = OBSERVED; `invalidation_price`/`regime`/`features`/`trigger` = CALCULATED.

## 10. Outcomes
Default horizons `[1,2,3,5,10,15,20,30,40,50]` (spec 6.4).
- `fwd_ret_pct` / `fwd_ret_r`: full-horizon, no stop truncation.
- `mfe_r` / `mae_r`: direction-adjusted; excursion window ends at the stop bar (inclusive) when `hit_invalidation`.
- `hit_invalidation`: first bar in `(t, t+h]` touching the invalidation; ties → earliest bar.
- Incomplete horizons (`t+h > last closed bar`): `complete=false`, null values, never partially counted.
- `ref_price == 0`: `fwd_ret_pct = None` (division undefined).

## 11. Causality / Lookahead
Only bars closed at or before `event_time` affect detection. `volMA`/`hiN`/`loN`/`swingLo` are lagged `[1]` so they use data through `t-1`; `emaR` is not lagged (Pine includes the current bar); all other features use the current bar. Truncation-invariance properties (`tests/test_events_truncation.py`) assert PD-2.

## 12. Edge Cases
- Warmup: no event fires before `lookback_bars`.
- Zero-range bars: `cpos = 0.5`, `upW = 0.0`.
- Zero-volume bars: `volMA` may be 0 → `rvol = None` → no climax.
- Missing/excluded ranges: `lookback_gap=True` when `[event_index - lookback, event_index]` overlaps an exclusion interval.
- Flat closes: `trendU = False` after warmup; `inDown` requires strictly decreasing `emaR`.

## 13. Golden Fixtures
- `fixtures/events/rvol_climax_v5/golden.json`: 30-bar series firing CONT@10, CAPIT@15, FAIL@20, TOP@25 under SMALL params. Expected events frozen.
- `fixtures/events/cooldown/{A..G}.json`: cooldown lag semantics.

## 14. Parity / Reference Validation
h=10 AC (`tests/test_events_parity_h10.py`): compare `(n, sum(fwd_ret_pct[10]), win_count)` per signal type against the owner's TradingView study table (`study_horizon=10`, reference defaults) on `fixtures/parity/BINANCE_BTCUSDT_1D.csv`. Skips with a clear reason when no table export is present locally; the CI gate is the golden fixtures.

## 15. Versioning
Any rule, parameter, default, feature, cooldown, invalidation, or outcome change requires a new event-spec version. Old versions remain runnable.

## 16. Implementation Gate
- [x] Purpose and non-goals explicit
- [x] Direction explicit
- [x] Inputs explicit
- [x] Parameters/defaults explicit
- [x] Derived features deterministic
- [x] Trigger exact
- [x] Cooldown exact
- [x] Invalidation exact
- [x] Outcomes defined
- [x] Lookahead/causality defined
- [x] Edge cases defined
- [x] Golden fixtures exist
- [x] Truncation-invariance test exists
- [x] Parity criteria exist where applicable
- [x] Owner approval recorded (spec section 6.3, M4 card)
