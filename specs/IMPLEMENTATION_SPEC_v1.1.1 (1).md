CRYPTOOS — IMPLEMENTATION SPEC v1.1.1
Merged: v1.1 + patch v1.1.1 + five clarifications (scope proportions/ordering, data-gap exclusion interval, frozen-experiment immutability, multiple-testing mechanism, Hypothesis->Experiment hierarchy).
Companion to: CryptoOS Master Spec v1.0 (vision, UX, principles).
Reference source: specs/reference/rvol_climax_pro_v5.pine (authoritative for detector/strategy logic until parity passes).
Precedence: THIS DOCUMENT > Master Spec. v1.1 and the v1.1.1 patch are SUPERSEDED. Log every deviation from the Master Spec in docs/DECISIONS.md.

====================================================
0. AGENT OPERATING RULES (also save as AGENTS.md)
====================================================
PD-1 NO ORDER PLACEMENT. No code path, endpoint, MCP tool or credential for placing orders exists in v1. Public read-only market endpoints only. Live trading needs a separate owner-approved spec.
PD-2 NO LOOKAHEAD. Every feature/event/signal uses only bars closed at or before its timestamp (truncation-invariance tests).
PD-3 HONESTY OVER GREEN. Never loosen a test, tolerance, threshold or pre-registered criterion to pass. Report failures plainly.
PD-4 SPEC BEFORE CODE. Ambiguous or missing definition -> stop and ask. Never invent trading rules or thresholds. Event rules come only from specs/events/*.md or the reference Pine file.
PD-5 NUMBERS COME FROM COMPUTATION. UI/AI display only values produced by engine modules, each carrying ref_id + dataset_version.
PD-6 REPRODUCIBILITY. Every result stores dataset_version, dataset_schema_version, engine_version, code_version, resolved_params, seed. Same inputs -> identical outputs.
PD-7 IMMUTABLE DATA. Raw/normalized bars are never edited in place; corrections create a new dataset_version.
PD-8 NO SECRETS IN REPO. .env (gitignored) + .env.example.
PD-9 SCOPE DISCIPLINE. Build only the current milestone. Park ideas in docs/BACKLOG.md.
PD-10 NO SILENT DATA SUBSTITUTION. If the requested (venue, market_type, instrument, timeframe, dataset) is unavailable or incomplete, return DATA_UNAVAILABLE / DATA_INCOMPLETE / NOT_IMPLEMENTED. Never fall back to another venue, spot<->perp, timeframe or dataset.
PD-11 NO SILENT PARAMETER DEFAULTS. Parameters resolve only from (a) the request or (b) config/defaults.yaml. Missing and undocumented -> HTTP 422. Research-relevant Pydantic fields carry no hidden defaults. Every result stores resolved_params.
PD-12 NO SILENT UNIVERSE CHANGES. Stats, event studies, backtests and validations require explicit instrument_ids, data_scope and (for backtests) fill_model. Results store them. A changed universe is a new run.
PD-13 NO RESULT OVERWRITE. Result tables are append-only (only lock/freeze flags may change). Recalculation creates a new ref_id with supersedes. Result identity = (ref_id, dataset_version, engine_version, code_version).
Task loop: read task card -> design note if >150 LOC (specs/design/) -> tests first for logic -> implement -> make check -> CHANGELOG entry -> report with acceptance-criteria evidence.
Reference implementation rule: external repositories are learning references, not drop-in dependencies. Do not copy proprietary code; verify licenses before reuse; reproduce behavior only through CryptoOS-owned interfaces and deterministic tests.
Stop-and-ask triggers: conflicting specs, missing owner input (Section 16), parity mismatch classified UNKNOWN, any need to relax a test.
BLOCKING CI GATES (no merge if red; no skip/xfail without a DECISIONS entry): ruff, mypy --strict, pytest, truncation-invariance suite, golden suite (incl. cooldown fixtures 6.7), reproducibility test (re-run == identical numbers), parity suite (from M3 once fixtures exist), immutability tests (from M5b), web typecheck + build, e2e.

====================================================

DECISIONS THAT OVERRIDE / CLARIFY THE MASTER SPEC
R1 Vertical slice first: DATA -> FEATURES -> EVENTS -> EVENT STUDIES -> STRATEGIES -> BACKTEST -> VALIDATION -> UI -> AI -> MCP. UI never contains business logic.
R2 Venue scope v1: Binance only. Identity supports SPOT and PERPETUAL from M0; implementation is SPOT candles only for M0-M9; PERP derivatives + live candles at M10 (see 4.8). Bybit/OKX/Hyperliquid = v1.1 via the same adapter interface.
R3 Storage: PostgreSQL (metadata, events, experiments, jobs, audit), Parquet (bars), DuckDB (read-only analytics over Parquet). Repository interfaces everywhere.
R4 Stricter than Master section 51: no LIVE mode in v1. RESEARCH only; PAPER in v1.1.
R5 No single "signal strength /100" in v1 (no defined formula). Show raw rule values + Evidence Grade. Master section 65 four-axis display kept; signal-strength axis shows "n/a (v1)".
R6 AI = one Copilot panel with modes (Research, Quant, Data, Critic, Writer); modes are tool subsets, not separate pages. Nav shows Copilot + Agent Runs only.
R7 Event naming comes from the Pine script: detector RVOL_CLIMAX, event types CONT, CAPIT, FAIL, TOP. Master names FAST_RECLAIM, TOP_CAPIT, TOP_CLIMAX, TOPFAIL, CAPITFAIL, EXHAUSTION_RECLAIM_FAILURE, CAPIT_RECLAIM have NO supplied rules -> status SPEC_REQUIRED; agents must not implement or approximate them. TOP != TOP_CAPIT != TOP_CLIMAX.
R8 Fill models: PARITY (replicates TradingView; verification only) and REALISTIC_OHLC (default for research claims; conservative OHLC approximation, NOT true intrabar execution). REALISTIC_INTRABAR is reserved (needs trade/orderbook data) and raises NOT_IMPLEMENTED. Every result is labeled with its fill model.
R9 Research unit = pooled multi-asset universe. Single-symbol results are shown but flagged "low-N".
R10 Backend Python 3.12; frontend Next.js + TypeScript (Master section 46).
R11 SHORT-side strategies are disabled until M10 (spot shorts forbidden). FAIL/TOP are research events and event-study subjects only until then.
R12 Object hierarchy: HYPOTHESIS (the question) 1:N EXPERIMENT (a reproducible attempt to answer it) -> VALIDATION (assessment of that experiment's result). Experiments belong to an EXPERIMENT FAMILY that records the search space (multiple testing).
====================================================
2. PHASES IN SCOPE
====================================================
v1: Phase 0 foundation | Phase 1 (candles, spot) | Phase 2 chart | Phase 4 events | Phase 5 research | Phase 6 backtesting | Phase 7 validation | Phase 8 AI Copilot | Phase 9 MCP (read/research) | M10 derivatives + live candles (perp)
v1.1: trades/CVD/footprint/orderbook, liquidity intelligence/heatmap, extra venues, paper trading, alerts, report export, randomized-timing baseline, benchmark-strategy baselines, FDR/Reality-Check corrections
Later: on-chain, macro, sentiment, portfolio/risk, live execution (separate spec)
Do not create UI pages for unbuilt modules.

====================================================
3. STACK & REPO
====================================================
Backend: Python 3.12, uv, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, Polars, DuckDB, PyArrow, httpx, websockets, pytest, hypothesis, ruff, mypy --strict.
Frontend: Next.js (App Router), React, TypeScript strict, Tailwind, shadcn/ui, TanStack Query, Zustand, TradingView Lightweight Charts (v5, multi-pane; keep attribution), Playwright.
Infrastructure/runtime: Windows-native development is first-class and Docker is optional. Core services (PostgreSQL, API, worker, web) must run directly on the host OS. Docker Compose may be provided as an optional reproducible packaging/deployment path, but Docker/WSL2 must NOT be required for development, tests, CI, or AI-agent workflows. Pin all dependency/runtime versions in lockfiles at M0.
API contract: OpenAPI from FastAPI -> generated TS client (packages/client); contract change = regenerate + commit.
Repo:
apps/{web,api,worker} packages/{schemas,client,ui}
engine/{data,features,events,strategy,stats,validation,experiments,ai,mcp}
exchanges/binance
specs/{reference,events,design} fixtures/ config/{defaults.yaml,dataset_scopes.yaml,validation.yaml}
data/{raw,normalized,derived} (gitignored) infra/{docker,migrations,scripts} docs/{DECISIONS.md,BACKLOG.md,CHANGELOG.md}
scripts/{setup.ps1,dev.ps1,dev-api.ps1,dev-worker.ps1,dev-web.ps1,test.ps1,health.ps1}
Docker Compose is optional; native Windows scripts are the primary local-development path.
Make targets: dev, check, test, ingest, seed-fixtures, parity.

====================================================
3.1 LOCAL RUNTIME / WINDOWS-NATIVE DEVELOPMENT
CryptoOS MUST support a Docker-free local development environment. Docker Desktop, WSL2, Docker Engine, and Docker Compose are optional tooling, not prerequisites.

Primary local runtime:
PostgreSQL -> localhost:5432 (native Windows installation)
FastAPI -> localhost:8000 (native Python process)
Worker -> native Python process
Next.js -> localhost:3000 (native Node.js process)
DuckDB -> embedded/local analytics
Parquet -> local filesystem under data/

Required developer scripts:
scripts/setup.ps1 bootstrap/check prerequisites
scripts/dev.ps1 start the complete native development stack
scripts/dev-api.ps1 start API
scripts/dev-worker.ps1 start worker
scripts/dev-web.ps1 start web
scripts/test.ps1 run test/check gates
scripts/health.ps1 verify service/database health

The native runtime is the reference development environment. Docker Compose, when present, MUST reproduce the same application contracts and MUST NOT introduce behavior that is unavailable in the native runtime. Tests and AI coding-agent workflows MUST be executable without Docker. Redis and other auxiliary infrastructure MUST NOT become mandatory unless a later approved spec explicitly requires them.

====================================================
3.2 REFERENCE ARCHITECTURES — MICROSTRUCTURE / LIQUIDITY / ON-CHAIN
RA-1 PURPOSE
The following external projects are REFERENCE ARCHITECTURES only. They inform product behavior, data abstractions, visualization patterns, performance targets and investigation workflows. They do NOT override this specification, the Master Spec, Pine parity rules, research-integrity rules, or licensing constraints.

RA-2 REFERENCES
A) Liquidity Mapper Fast: https://liquiditymapperfast.github.io/
Use as a product/interaction reference for liquidity mapping and market-depth visualization. Before implementing any borrowed behavior, agents MUST document the observed behavior, data required, provenance and whether the behavior is merely visual or creates a research-grade derived metric.

B) kline-orderbook-chart: https://github.com/tapedelta/kline-orderbook-chart
Reference for a unified high-performance market canvas combining candles, orderbook heatmap, footprint, liquidation heatmap, volume profile, CVD, OI/funding, large-trade visualization, replay, synchronized crosshair and drawing/interaction. The project describes a price-level x time heatmap, footprint bid/ask volume with delta/POC/imbalance, and a native high-performance rendering engine. It is proprietary/commercial; CryptoOS MUST NOT copy or vendor its proprietary implementation or require its commercial SDK unless the owner explicitly approves licensing.

C) DeepLens (Alanisme): https://github.com/alanisme/deeplens
Reference for EVM on-chain intelligence: real-time block streaming, local analysis, address profiling, entity mapping, fund-path tracing, token analytics, watchlists and structured evidence-bearing alerts. The repository is Apache-2.0. CryptoOS may learn from its architecture and concepts; any direct code reuse MUST preserve applicable license/notice requirements and must be reviewed before inclusion.

RA-3 MARKET CANVAS REQUIREMENT
The future Microstructure Chart MUST be architected as one synchronized market canvas/state model rather than unrelated widgets. Candles, liquidity heatmap, footprint, large trades, liquidations, events and indicator panes MUST share a common time/price coordinate model, viewport, crosshair and replay state.
Initial v1 implementation remains candle-first. This requirement becomes active when M10/v1.1 orderbook/trade scope begins.

RA-4 ORDERBOOK HEATMAP DATA MODEL
Design the orderbook visualization around a canonical price-level x time representation:
heatmap_column = {timestamp, price_step, price_origin, levels[]}
level = {price, bid_size, ask_size, total_size, quality/provenance}
The renderer MUST support incremental append/update of columns and bounded historical windows. It MUST NOT require re-rendering the entire matrix for every update.
Heatmap intensity MUST be based on a documented normalization/windowing rule. Raw liquidity values remain available for research; visual color intensity is NOT itself a research metric.

RA-5 FOOTPRINT DATA CONTRACT
A footprint bar MUST support price-level bid volume, ask volume, delta, POC and documented imbalance/absorption markers.
Canonical trade inputs MUST retain aggressor-side semantics when the venue provides them. If aggressor side cannot be established reliably, the system MUST mark the field unknown rather than infer it silently.
Footprint aggregation MUST be deterministic for the same trades, tick size, interval and dataset version.

RA-6 LIQUIDITY INTELLIGENCE
When orderbook data becomes in scope, implement a separate Liquidity Engine from the renderer. Minimum research-capable concepts:
- liquidity level / zone
- liquidity added
- liquidity removed
- persistence/dwell time
- liquidity concentration
- liquidity migration
- sweep
- liquidity void
- imbalance
- absorption (only when the evidence definition is explicitly specified)
Every derived liquidity event MUST carry detector_id, detector_version, dataset_version, source venue/instrument, timestamp, price range, raw inputs and resolved parameters.
Terms such as “spoof”, “manipulation”, “absorption” or “smart money” MUST NOT be asserted from a single heuristic unless a separate approved detector specification defines the evidence and false-positive limitations.

RA-7 LIQUIDITY EVENT RESEARCH
Liquidity-derived events MUST enter the same Event Engine and Event Study framework as RVOL_CLIMAX events. They MUST support forward returns, MFE, MAE, time-to-extreme and conditioning by regime, symbol, venue, volatility, volume and other available features.
Example research questions are hypotheses only and MUST NOT be implemented as conclusions:
- Does a liquidity sweep followed by reclaim change CAPIT expectancy?
- Does persistent liquidity near a level predict reaction versus continuation?
- Does liquidity removal combined with delta/OI change event resolution?

RA-8 ON-CHAIN INTELLIGENCE ARCHITECTURE
The future on-chain layer MUST separate raw chain ingestion from interpretation:
chain/RPC -> blocks -> transactions/logs -> normalized transfers/swaps -> addresses -> entities -> flows -> events -> research
Address identity MUST remain distinct from entity identity. One entity may contain multiple addresses with role + chain metadata.
Entity mapping MUST support human review/approval and provenance. Automatic clustering is advisory until approved by the user or an explicit deterministic rule.

RA-9 FUND-PATH MODEL
Future fund-path tracing MUST support forward, backward and bidirectional traversal with bounded hop depth. Each path result MUST expose the complete hop sequence and a decomposed score rather than only one opaque score.
Candidate score dimensions may include value retention, temporal proximity, directness, label/entity matches and behavioral anomaly. Exact formulas require a separate specification before use in research claims.
Behavioral labels such as relay, accumulator, distributor, exchange, bridge or hub MUST be evidence-backed and versioned.

RA-10 ON-CHAIN ALERT EVIDENCE
Every on-chain alert MUST store structured evidence: rule id/version, threshold, observed value, contributing records, chain, block number, transaction hash(es), address/entity identifiers, dataset/source version and timestamp.
UI/AI may summarize this evidence but MUST NOT invent missing causal explanations.

RA-11 MARKET + ON-CHAIN JOIN
When on-chain data is introduced, joins to market data MUST use explicit UTC timestamps and documented alignment windows. The system MUST record the join policy, chain latency assumptions and whether a value was known at the market-event timestamp.
No on-chain feature may leak information that was only available after the event under study.

RA-12 PERFORMANCE / RENDERING REQUIREMENTS
Microstructure visualization is a high-volume workload. The architecture MUST separate computation/storage from rendering and support incremental updates, bounded memory, viewport-level detail-of-detail/LOD and disposal of chart engine resources.
Target: smooth interactive rendering under the approved M10/v1.1 benchmark dataset. Exact FPS/memory thresholds MUST be benchmarked on the user's supported hardware before being promoted to a blocking gate.

RA-13 AI / AGENT INTEGRATION
AI agents MUST access microstructure and on-chain intelligence through deterministic tools, not by scraping pixels from charts.
Required future tool families:
get_orderbook_snapshot
get_orderbook_heatmap
get_footprint
get_liquidity_events
get_large_trades
get_entity
get_address_profile
trace_fund_path
get_onchain_alert_evidence
Every numerical tool result MUST include ref_id, dataset_version/source version, engine version and resolved parameters where applicable.

RA-14 IMPLEMENTATION ORDER
Do NOT pull these systems into v1 candle scope. Preserve the existing v1 vertical slice.
M10/v1.1 expansion order:
1. canonical trades
2. orderbook reconstruction + Data Guardian extensions
3. footprint aggregation
4. liquidity heatmap storage/streaming
5. Liquidity Engine + liquidity events
6. microstructure chart integration
7. derivatives/OI/funding/liquidation overlays
8. on-chain ingestion foundation
9. address/entity model
10. fund-path + evidence alerts
11. market/on-chain event joins
Each item requires fixtures, deterministic tests and provenance before UI exposure.

RA-15 REFERENCE-TO-SPEC RULE
For every feature inspired by an external reference, create a short entry in docs/DECISIONS.md containing: reference URL, observed capability, CryptoOS interpretation, data contract, implementation status, license assessment and deviations. “Looks similar” is not sufficient acceptance evidence.

====================================================
4. DATA LAYER (candles first)
====================================================
4.1 Instrument identity (Master section 7): {asset, quote, market_type, venue, native_symbol}. Research code never sees native symbols.
4.2 Bars: instrument_id, timeframe, open_time, close_time, o,h,l,c, volume (base units), quote_volume, trades, source, ingested_at. UTC only. Daily bars open 00:00 UTC. Only CLOSED bars enter features/events/backtests; the forming bar is display-only.
4.3 Timeframes and resampling:
Native: 1D, 4H, 1H (lower opt-in). Native 4H/1H are never resampled in v1.
1W from 1D: week = Monday 00:00 UTC to Sunday 23:59:59.999 UTC; open=first daily open, high=max, low=min, close=last daily close, volume=SUM, quote_volume=SUM, trades=SUM.
A weekly bar is COMPLETE only if all 7 daily bars exist and none is ORANGE/RED; otherwise flagged INCOMPLETE and excluded from features, events and backtests (never interpolated or filled). The forming week is never used in any historical calculation. Weekly event_time = close_time of the Sunday bar. Resampling carries truncation-invariance tests.
4.4 Storage and dataset manifest:
Parquet partitioned by venue/market_type/symbol/timeframe/year, zstd, deterministic writer settings (sorted rows, fixed row-group size). Writes atomic (temp + rename); files read-only after write.
Per-file hash = content_sha256 over canonical row serialization (not file bytes).
Manifest (table market.dataset_manifest, Section 10) holds: dataset_version, venue, market_type, instruments, timeframes, start/end, source_files [{path, content_sha256, rows}], row_counts, missing_ranges, quality_summary, dataset_schema_version, bar_semantics_version ("closed-bars-utc-v1"), timezone_policy_version ("utc-v1"), instrument_metadata_version, created_at.
dataset_version = sha256(canonical JSON of {source_files, dataset_schema_version, bar_semantics_version, timezone_policy_version, instrument_metadata_version}).
Every result stores dataset_version, dataset_schema_version, engine_version, code_version. "What exactly did this backtest use?" must be answerable from stored ids alone (GET /v1/datasets/{dataset_version}).
4.5 Ingest: Binance REST klines backfill (resumable, idempotent, rate-limit aware, adapter-isolated). ExchangeAdapter (Master section 6) with candle methods only in v1: fetch_historical_klines, fetch_instrument_metadata, health; live kline subscription at M10.
4.6 Data Guardian (candle scope):
Checks: duplicate bars, gaps (missing expected open_time), non-monotonic time, OHLC sanity (l<=o,c<=h, l<=h), zero-volume anomalies, stale feed (no new closed bar within 2 bar-lengths + grace).
States per (instrument, timeframe): GREEN reliable | YELLOW degraded (zero-volume bars <0.1%) | ORANGE incomplete (open gaps) | RED invalid (OHLC violations/duplicates unresolved).
Repair: refetch gap range via REST -> dedupe -> validate -> new dataset_version -> mark REPAIRED (gap_id, range, source, status).
EXCLUSION INTERVALS: a gap or invalid range defines data_gap_exclusion_start/end = [first missing/invalid bar open_time, last missing/invalid bar close_time]. ONLY that interval is excluded from events/backtests; the rest of the instrument's history is untouched. Events whose declared lookback_bars window overlaps an exclusion interval carry lookback_gap=true; stats report their count and, by default (config exclude_lookback_gap_events=true), exclude them. Reports list all exclusion intervals.
Completeness % = present closed bars / expected closed bars in the requested range.
4.7 Orderbook/trades/footprint (Master sections 8-10, 12-13): out of scope for v1; design notes only.
4.8 Spot vs perp: schema, identity, API and UI support SPOT and PERPETUAL from M0. M0-M9 implement SPOT candles ONLY: no perp adapter, funding, OI or liquidation code before M10. Perp requests before M10 return NOT_IMPLEMENTED (never substitute spot).

====================================================
5. FEATURE LIBRARY (TradingView/Pine-parity semantics)
====================================================
Pure functions in engine/features over Polars columns; null during warm-up. Parity tests compare against TradingView exports (Section 14).
sma(x,n) mean of last n values; null until n values exist.
ema(x,n) alpha=2/(n+1); first output = x[0]; e_t = alpha*x_t + (1-alpha)*e_{t-1} (Pine ta.ema, no SMA seed).
rma(x,n) alpha=1/n; seeded with sma(x,n) at index n-1; null before (Pine ta.rma).
tr t=0: high-low; else max(high-low, |high-close[1]|, |low-close[1]|).
atr(n) rma(tr, n).
highest/lowest(x,n) window of last n bars INCLUDING current; "[1]" = shifted one bar (prior n bars only).
barssince(cond) bars since cond last true; null before first true; 0 on a bar where it is true.
pivotlow/pivothigh(src,L,R) per Pine docs; value at bar t refers to bar t-R and is known only at t (no lookahead). Document tie handling; verify by parity.
donchian_lo/hi(n) lowest(low,n)/highest(high,n) incl. current bar.
rvol(n=20) volume_t / sma(volume,n)_{t-1}; null if denominator null or 0.
cpos range>0 ? (close-low)/range : 0.5
upper_wick range>0 ? (high - max(open,close))/range : 0.0
Chart indicators (standard definitions): VWAP (UTC-day anchor intraday), RSI (Wilder/rma), MACD(12,26,9), Bollinger(20,2, population stdev), ADX(14), SMA/EMA overlays. Server-side only.
Regime tags (deterministic, documented, descriptive only): trend_state via EMA(100) position+slope (same rule as inDown), vol_state via ATR percentile over trailing 252 bars, volume_state via rvol buckets.

====================================================
6. EVENT ENGINE
====================================================
6.1 Detector interface:
class Detector: id, version, params_model, required_inputs, warmup_bars, lookback_bars, default_cluster_model
detect(bars_df, params) -> list[Event] # causal
Registry autoloads engine/events/detectors/. Each detector ships: spec (specs/events/<id>.md), unit tests, truncation-invariance property test, golden fixture.
6.2 Event object: event_id = sha1(detector_id|version|params_hash|instrument_id|timeframe|event_time); detector_id, event_type, version, params_hash, instrument_id, timeframe; event_time = CLOSE time of the bar on which the rule became true; direction; ref_price = close of event bar; invalidation_price; regime{}; features{rvol, cpos, upper_wick, atr, ema_regime, archetype...}; trigger{rules:[{name,passed,value,threshold}]}; lookback_gap flag; dataset_version; engine_version; data_quality{state, exclusion_intervals}. Every displayed field has provenance_class OBSERVED|CALCULATED|INFERRED|AI (Master section 63).
6.3 Detector RVOL_CLIMAX v5.0 (port of Pine; emits ALL four types independent of strategy flags).
Params (defaults in config/defaults.yaml): vol_len=20, climax_mult=3.0, top_mult=2.0, range_len=20, trend_len=5, close_pos_lo=0.30, close_pos_hi=0.70, regime_len=100, bounce_len=10, bounce_atr=1.5, wick_min=0.40, cooldown=5, atr_len=14, fail_mode="failed_bounce" (alt "legacy_v1"). lookback_bars=100.
Derived per bar:
atr=atr(14); rvol; hiN=highest(high,20)[1]; loN=lowest(low,20)[1]; emaR=ema(close,100); swingLo=lowest(low,10)[1]
trendU=close>close[5]; atHi=close>=hiN; atLo=close<=loN; nearHi=high>=hiN; isClimax=rvol>=3.0
inDown=close<emaR AND emaR<emaR[5]; bounced=(high-swingLo)>=1.5atr; rejected=close<open AND upper_wick>=0.40
topReject=close<open AND (upper_wick>=0.40 OR cpos<=0.30)
Raw conditions:
rawTop=rvol>=2.0 AND close>emaR AND nearHi AND topReject
rawCont=isClimax AND trendU AND atHi AND NOT rawTop
rawCap=isClimax AND NOT trendU AND cpos<=0.30 AND atLo
newFail=isClimax AND inDown AND bounced AND rejected AND NOT atLo ; legacyFail=isClimax AND NOT trendU AND cpos>0.30
rawFail=(fail_mode=="failed_bounce") ? newFail : legacyFail
Cooldown (parity-critical): sig_X = raw_X AND gap_X >= cooldown, gap_X = nz(barssince(raw_X)[1], 999). barssince counts RAW occurrences (not emitted signals). With cooldown=5 a signal needs >=5 raw-free bars between it and the previous raw occurrence. State is independent per event type.
Event types: CONT(LONG)=sigLongCont, CAPIT(LONG)=sigLongCap, FAIL(SHORT)=sigShortFail, TOP(SHORT)=sigShortTop.
Archetype feature (climax bars): trendU: cpos>=0.70 "top·drive high", cpos<=0.30 "top·sell pressure", else "top·mid"; not trendU: cpos<=0.30 "bot·drive low", cpos>=0.70 "bot·cover press", else "bot·mid". Untraded-climax layer: isClimax bars without a signal.
invalidation_rule (event-study R only; not the strategy stop): LONG close-3.0ATR, SHORT close+3.0ATR; R_basis="atr".
Detector spec file must state: TOP is NOT TOP_CAPIT/TOP_CLIMAX/TOPFAIL or any distribution/exhaustion concept; future splits (climax-at-top, failed upside continuation, distribution, exhaustion, reversal, failed reclaim) each need their own spec. Default cluster_model: SYMBOL_GAP + CALENDAR_WEEK (documented ASSUMPTION).
6.4 Outcomes, horizons h in [1,2,3,5,10,15,20,30,40,50] bars:
fwd_ret_pct(h) direction-adjusted: LONG (c_{t+h}-ref)/ref100; SHORT (ref-c_{t+h})/ref100. h=10 must equal the Pine study table's per-signal return.
R=|ref-invalidation|; fwd_ret_R(h); mfe_R(h), mae_R(h) from highs/lows of bars t+1..t+h; bars_to_mfe/mae; hit_invalidation(h); complete=false when t+h > last closed bar (never partially counted).
Same-bar ambiguity: stop first.
6.5 Versioning: any change to rules/defaults = new version (5.0 -> 5.1); old versions stay runnable. Events/outcomes recompute incrementally and fully from (dataset_version, detector version, params).
6.6 Pending detectors: specs/events/_TEMPLATE.md + placeholders FAST_RECLAIM, TOP_CAPIT, TOP_CLIMAX, TOPFAIL, CAPITFAIL, CAPIT_RECLAIM with status SPEC_REQUIRED. Do not guess logic.
6.7 COOLDOWN GOLDEN FIXTURES (blocking CI gate; expected outputs stored as JSON, compared exactly; cooldown=5 unless stated):
A: bar 0..10, raw 0 1 1 1 0 0 0 0 0 1 0 -> emit 0 1 0 0 0 0 0 0 0 1 0
B: raw at bars 1 and 7 (5 free bars) -> emit 1 and 7
C: raw at bars 1 and 6 (4 free bars) -> emit 1 only
D: raw run of 10 consecutive bars -> exactly one emit (first); next raw after 5 free bars emits
E: first-ever raw, or raw after >=999 bars silence -> emit
F: independent state per type (CONT does not suppress CAPIT)
G: cooldown=0 -> every raw bar emits

====================================================
7. STRATEGY DEFINITION & SIMULATOR
====================================================
7.1 Strategy definition (versioned JSON, hashed; edits create a new version; no hidden defaults):

{
 "strategy_id":"rvol_climax_pro","version":"5.0",
 "entry":{"detector":"RVOL_CLIMAX@5.0","params":{},"signals":["CONT","CAPIT"],"side":"LONG_ONLY"},
 "sizing":{"mode":"RISK","risk_pct":1.0,"max_alloc_pct":25.0,"fixed_alloc_pct":10.0},
 "stop":{"kind":"ATR","atr_len":14,"atr_mult":3.0,"structure_buffer_atr":0.25},
 "breakeven":{"enabled":false,"trigger_R":1.0},
 "target":{"R":0},
 "trail":{"enabled":true,"kind":"ATR_CHANDELIER","atr_mult":3.8,"percent":12.0,"donchian_len":20,
          "pivot":{"left":5,"right":3,"buffer_atr":0.25},"giveback":{"activate_R":2.0,"pct":35.0},"ema_len":21},
 "time_stop":{"enabled":true,"max_bars":80},
 "exits":{"on_opposite":false,"long_on_bearish_signal":false},
 "costs":{"fee_model":{"kind":"PERCENT","value":0.10},"slippage_model":{"kind":"FIXED_BPS","value":5}},
 "execution":{"fill_model":"REALISTIC_OHLC","delay_bars":0,"pyramiding":0,"leverage":1.0,"initial_capital":10000,
              "close_open_at_end":false,"max_hold_gap_bars":3}
}
stop.kind: ATR|STRUCTURE|NONE. trail.kind: ATR_CHANDELIER|PERCENT|DONCHIAN|PIVOT_SWING|PROFIT_GIVEBACK|EMA_CLOSE. side: BOTH|LONG_ONLY|SHORT_ONLY (short sides rejected until M10). Strategy-layer flags (signals list, side) live here, not in the detector.
fee_model kinds: PERCENT (per side on notional). slippage_model kinds: FIXED_BPS (adverse, per fill). Reserved SPREAD_BASED, VOLATILITY_BASED, DEPTH_BASED, CUSTOM -> NOT_IMPLEMENTED; unknown kind -> 422. PARITY runs use the Pine script's own settings (0.10% commission, 0 slippage, delay_bars must be 0).
7.2 Sizing and initial stop (computed at signal bar s):
  risk_dist: ATR -> atr_mult*ATR_s ; STRUCTURE -> long close_s-(min(low_s,low_{s-1})-buf*ATR_s), short (max(high_s,high_{s-1})+buf*ATR_s)-close_s ; NONE -> atr_mult*ATR for sizing only. risk_dist=max(risk_dist, tick_size).
  stop_price: ATR kind long close_s-risk_dist / short close_s+risk_dist ; STRUCTURE the structure level ; NONE null.
  qty: RISK = min(equity*risk_pct/100/risk_dist, equity*max_alloc_pct/100/entry_price); fixed = equity*fixed_alloc_pct/100/entry_price. Round DOWN to lot_step; qty<=0 -> skip. equity = account equity at decision (flat -> cash).
7.3 Fill models and time fields
  Time fields on every trade: signal_bar_index, signal_time (close_time of signal bar), decision_time (= signal_time unless latency modeled), fill_bar_index, fill_time. Exits: exit_signal_time (bar in which stop/target touched or discretionary rule triggered), exit_decision_time, exit_fill_time. Replay, event studies, AI explanations and parity reports display them.
  PARITY (verification only): entries and discretionary exits fill at the CLOSE of the signal/decision bar (TradingView process_orders_on_close=true); entry_price=close_s; fill_time=signal_time.
  REALISTIC_OHLC: fill bar = signal bar + 1 + delay_bars, fill at that bar's OPEN (adverse slippage). qty is recomputed with the actual fill price and the stop fixed at signal time (risk_dist_actual=fill-stop for long).
  Stops: resting orders. Long stop fills at stop price if bar low<=stop and open>stop; if open<=stop (gap) fill at OPEN. Mirrored for shorts. Target (limit) fills at limit, at open if gapped favorably.
  Same-bar stop+target: REALISTIC_OHLC = stop first. PARITY = replicate the TradingView broker-emulator path heuristic (if high is closer to open than low: O->H->L->C else O->L->H->C); verify against TradingView docs and record in a design note.
  Fees on notional of each fill. Pyramiding 0.
7.3.1 Execution edge cases (every outcome is recorded; no signal disappears silently):
  E1 No next bar (end of data): NOT_FILLED/NO_NEXT_BAR. Open positions at end of data are marked to market, reported OPEN, excluded from closed-trade stats (close_open_at_end=false) and listed separately.
  E2 Fill bar missing or inside an exclusion interval (ORANGE/RED): SKIPPED/ENTRY_DATA_GAP. Never filled on a later bar.
  E3 Fill price at or beyond the stop (long fill<=stop; short fill>=stop, equality included): SKIPPED/GAP_THROUGH_STOP. If any bar between signal and fill touched the stop level (delay_bars>0): SKIPPED/STOP_TOUCHED_BEFORE_FILL.
  E4 Missing/excluded bars while a position is open: stops cannot be evaluated inside the gap. On the first valid bar after the gap, test the stop against that bar's OPEN first (gap rule), then OHLC. Trade flagged data_gap_during_hold=true. If the gap exceeds max_hold_gap_bars, close at that first valid open with exit_reason DATA_GAP. The exclusion applies ONLY to the data_gap_exclusion_start..end interval (4.6), never to the rest of the instrument's history. Reports show flagged-trade counts; config exclude_flagged_trades (default false).
  E5 Same-bar stop+target: per fill model above.
  E6 Every backtest result displays: fill_model, fee_model, slippage_model, delay_bars, close_open_at_end, flagged-trade count, skipped-signal counts by reason.
7.4 Per-bar order of operations (closed bar t, per instrument):
  1. Update features for bar t.
  2. EXIT FILLS: if a position is open with resting stop S (and target T) placed at close of t-1, test bar t's O/H/L (7.3); if filled close at the fill price (exit_reason STOP/TARGET).
  3. If still open, update management with bar t:
     - first management bar after entry (bar s+1 in PARITY): hi_since/lo_since start from that bar's high/low (NOT the signal bar); entry_R=risk_dist at entry; stop_keep=initial stop.
     - hi_since=max(hi_since, high_t) (long) / lo_since=min(lo_since, low_t) (short)
     - gain_R=(close_t-entry_price)/entry_R (long); (entry_price-close_t)/entry_R (short)
     - stop_t=stop_keep; if breakeven.enabled and gain_R>=trigger_R: stop_t=max(stop_t, entry_price) (long)/min (short)
     - if trail.enabled compute cand by kind:
         ATR_CHANDELIER  long hi_since-mult*ATR_t ; short lo_since+mult*ATR_t
         PERCENT         long hi_since*(1-p/100) ; short lo_since*(1+p/100)
         DONCHIAN        long donchian_lo(len) ; short donchian_hi(len) (incl. current bar)
         PIVOT_SWING     long: last confirmed pivot low with pivot bar > signal bar, minus buf*ATR ; short mirrored ; null if none yet
         PROFIT_GIVEBACK peak=hi_since-entry (long)/entry-lo_since (short); active when peak>=activate_R*entry_R; cand=hi_since-pct/100*peak (long)/lo_since+pct/100*peak (short)
         EMA_CLOSE       no stop level; armed once close is on the favorable side of EMA(len); when armed and close crosses to the other side -> exit decision at close t
       clamp: long cand=min(cand, close_t-tick); short cand=max(cand, close_t+tick)
       ratchet: long stop_t=max(stop_t,cand); short stop_t=min(stop_t,cand) (stops only tighten)
     - stop_keep=stop_t; resting stop for bar t+1=stop_t; target T=entry +/- target_R*entry_R if target_R>0
  4. DISCRETIONARY EXITS decided at close t, filled per fill model: time stop when bars_since_signal>=max_bars; EMA_CLOSE cross; on_opposite; long_on_bearish_signal.
  5. ENTRIES: if flat (after steps 2-4; same-bar re-entry allowed) and an allowed signal fires at t -> size per 7.2, fill per 7.3. Resting initial stop is active from the first bar after the fill.
  6. Mark-to-market equity at close t; record per-bar equity.
  PORTFOLIO mode: one shared equity; instruments processed in a fixed deterministic order each bar; constraints max_concurrent_positions, max_portfolio_heat, per-asset cap; sizing uses shared equity.
7.5 Trade record: instrument, side, all time fields (7.3), entry/exit price, qty, fees, pnl, pnl_pct, r_multiple, mfe_r, mae_r, bars_held, entry_tag (CONT/CAPIT/FAIL/TOP/BENCH), exit_reason (STOP/TARGET/TIME/EMA_TRAIL/OPPOSITE/TOP_EXIT/DATA_GAP/END_OF_DATA), data_gap_during_hold, straddles_boundary, fill_model.
7.6 Benchmark mode: entry.mode="RANDOM_BASELINE" (see 9.2).
7.7 Funding and liquidation modeling: M10. Until then perp backtests do not exist (R2).

====================================================
8. STATISTICS, DATA SCOPES & RESEARCH INTEGRITY
====================================================
8.1 DATA SCOPES (replaces per-instrument splits; enforced in the data-access layer)
  Calendar axis: S = [earliest first-bar open_time among instruments in the dataset family, latest closed bar at freeze time]. Dataset family and universe are defined in config/dataset_scopes.yaml.
  Boundaries are CALENDAR DATES (rounded to 00:00 UTC), computed ONCE at M5, frozen in dataset_scopes.yaml and logged in DECISIONS:
    holdout_start = S_start + 0.80 x span
    oos_start     = S_start + 0.56 x span      (= 0.80 x 0.70)
  FULL HISTORY (percentages of calendar span, before any embargo/purge):
    DEVELOPMENT = first 80%
      IS  = first 70% of DEVELOPMENT  (= first 56% of full span)
      OOS = final 30% of DEVELOPMENT  (= 56%-80% of full span, ~24%)
    HOLDOUT = final 20% (80%-100%)
  Percentages define calendar boundaries only; per-instrument bar counts differ (later listings have fewer bars). Boundaries never move after freeze: new data after the freeze belongs to HOLDOUT automatically. Changing the universe/dataset family requires a new dataset_scopes entry + DECISIONS record.
  Embargo and purge are applied in this exact order:
    1. Fix the two boundary dates (oos_start, holdout_start).
    2. Let H = analysis horizon in bars (event studies: max outcome horizon in view, default 50; backtests: max_hold_bars; use the instrument timeframe) and E = embargo_bars (default = H).
    3. EMBARGO: for each boundary b, bars in [b, b+E bars) of the LATER segment are unused as observation origins (no events, no entries).
    4. PURGE: any observation with origin t < b and t + H > b is removed from the EARLIER segment (outcome windows may not cross a boundary).
    5. Assign remaining observations by origin time to IS / OOS / HOLDOUT.
  Embargo and purge REMOVE observations; they do not move boundaries or re-label segments.
  Backtests with data_scope=DEVELOPMENT end at holdout_start minus H bars (positions open at that end are handled per E1). Trades whose possible lifetime crosses oos_start are purged from IS strict stats and flagged straddles_boundary.
  Enforcement: stats/event-study/backtest/validation take data_scope = DEVELOPMENT (default) | HOLDOUT. DEVELOPMENT requests intersecting the holdout are clipped and flagged. HOLDOUT scope requires hypothesis_id + the open-holdout flow, ONCE per hypothesis (logged: who, when, result, resolved_params). Walk-forward, sweeps, baselines, cluster-model sensitivity and all tuning run inside DEVELOPMENT only. Every result shows data_scope and boundaries used.
8.2 StatsResult (immutable; queryable by ref_id) for any event filter:
  n_raw; n_eff; per-symbol breakdown; per horizon: mean/median fwd_ret_pct and R, win rate, MFE/MAE medians, percentile table (p10/25/50/75/90), histogram bins. Intervals: Wilson 95% for win rate; seeded cluster-bootstrap 95% CI (5000 resamples, seed stored) for mean/median. IS vs OOS shown side by side. Cross-asset consistency (share of symbols with same-sign median). Multiple-testing block (8.4).
  cluster_model (enum): SYMBOL_GAP | CALENDAR_DAY | CALENDAR_WEEK | MARKET_EVENT | CUSTOM.
    SYMBOL_GAP: per-symbol minimum gap = max horizon in view. CALENDAR_DAY / CALENDAR_WEEK: bootstrap resamples blocks of calendar days / ISO weeks across symbols. MARKET_EVENT: bars on which >=k symbols (default k=max(3, 10% of universe)) emit the same event type form one cluster; adjacent flagged bars merge. CUSTOM: block definition in the experiment config.
    Each detector spec documents its default cluster_model as an ASSUMPTION. Every StatsResult reports n_eff, CI and Evidence Grade under all applicable cluster models; if grades differ, show the LOWEST grade + a "model-sensitive" flag.
8.3 EVIDENCE GRADE (deterministic; thresholds in config):
  A: n_eff>=100 AND CI(median R) excludes 0 AND OOS sign agrees AND >=60% symbols agree
  B: n_eff>=50 AND CI excludes 0 AND OOS sign agrees
  C: n_eff>=30 AND consistent sign, CI includes 0
  D: otherwise -> "Insufficient evidence"
  UI always shows grade + n_eff + interval + data_scope together; never a bare point estimate or win rate. Label is "Evidence Grade". Allowed wording: "Meets pre-defined evidence criteria (grade B)". Banned UI/AI wording for grades: "works", "proven", "validated edge", "guaranteed" (string test greps UI and AI templates).
8.4 RESEARCH OBJECTS & MULTIPLE TESTING
  HYPOTHESIS = the question: statement, universe, timeframe, PRE-REGISTERED pass/kill criteria (frozen with timestamp BEFORE the first test; edits create a new hypothesis), status (IDEA, TESTING, PROMISING, SUPPORTED, WEAK, REJECTED, VALIDATED), autopsy (required when REJECTED). REJECTED stays visible in a Graveyard view.
  EXPERIMENT = one reproducible attempt: dataset, detector@version, resolved_params, universe (instrument_ids), data_scope, event study, strategy, backtests, validations, conclusion, selection_method. A hypothesis has many experiments. REJECTED requires a frozen experiment meeting a pre-registered kill criterion; VALIDATED requires frozen experiment(s) per 9.4.
  VALIDATION = assessment of an experiment's result (9.3).
  EXPERIMENT FAMILY = the search that produced candidate experiments: experiment_family_id, search_space (parameters, ranges, detectors, exits, regimes, symbols), hypothesis_count, variant_count.
  Mechanism: every evaluated variant (sweep cell, parameter set, detector threshold, cooldown, hold, exit model, regime filter, symbol subset) is recorded in research.variant under a family. All sweeps run through the sweep runner; ad-hoc evaluation endpoints create a singleton family, never bypass recording. hypothesis.trials_count = sum of variant_count over its families (killed variants included).
  selection_method on every experiment: PRE_SPECIFIED | SWEEP_BEST | MANUAL_CHOICE.
  Display rule (API + UI banner component): when variant_count>1 or selection_method != PRE_SPECIFIED show exactly: "Multiple-testing warning: {variant_count} variants evaluated; this result was selected after parameter search." plus search_space summary. Backlog (v1.1): FDR correction, White's Reality Check, SPA.
8.5 IMMUTABILITY OF EXPERIMENTS AND RESULTS
  Frozen experiments cannot: be updated, be deleted, change referenced results, change resolved_params, change dataset_version, change universe, change data_scope, or change detector/strategy versions. Follow-ups create a new experiment with `supersedes`.
  A result referenced by a frozen experiment must itself be immutable: FREEZE runs in one transaction that (a) sets locked=true on every referenced row (stats_result, backtest, trades, validation runs, baseline runs, hypothesis criteria), (b) records result_hashes = sha256 of each referenced result's canonical JSON / Parquet content, (c) sets frozen=true, frozen_at. DB triggers reject UPDATE/DELETE on locked rows. Artifacts (equity curves, histograms) are content-addressed and read-only. GET /v1/experiments/{id}/verify recomputes hashes and returns INTACT or TAMPERED.
  Notebook notes link to experiment_id, never to live queries.
  Lineage view (read-only, rendered from stored ids): Dataset -> Features -> Detector -> Events -> Stats -> Hypothesis -> Experiment -> Strategy -> Backtest -> Validation -> Conclusion. Each node links to the stored object.


## Sections 9–19

====================================================
9. VALIDATION SUITE
====================================================
9.1 Metrics: net return, CAGR, profit factor, expectancy (R), win rate, avg win/loss, payoff ratio, Sharpe, Sortino, max drawdown, Calmar, exposure, turnover, trades, avg/median R, top-1/5/10 trade share, longest losing streak. Per-symbol AND portfolio-level (shared equity). Per-symbol Sharpe is never the headline.
9.2 Random-entry baseline (port of the Pine BENCHMARK mode): seeds 1..N (default 1000); at each flat bar enter long with probability p using the SAME exits, sizing, costs, fill model, data_scope. Calibrate p per universe so mean trade count matches the strategy within +/-10%. Output: distributions of PF / return / expectancy / maxDD; strategy percentile. Pine's RNG is not reproducible; compare distributions, not bars.
9.3 Tests grouped in three levels (API + UI). Each returns PASS | WARN | FAIL | INSUFFICIENT | NOT_RUN with numeric basis; thresholds in config/validation.yaml (changes need a DECISIONS entry). All Level 2/3 tests run in DEVELOPMENT scope.
  LEVEL 1 IMPLEMENTATION VALIDITY (engine correctness)
    TradingView parity (status per P10 categories in 14.2; N/A only when no reference export exists for that strategy)
    Truncation invariance (CI gate result for the engine_version)
    Deterministic replay / reproducibility (re-run identical)
    Accounting invariants (equity closes; stops only tighten; no unprotected position when stop.kind != NONE)
  LEVEL 2 STATISTICAL VALIDITY
    Edge          cluster-bootstrap CI of expectancy R excludes 0 -> PASS; includes 0 -> FAIL; <30 trades -> INSUFFICIENT
    OOS           same sign and >=50% of IS expectancy -> PASS; same sign weaker -> WARN; flips -> FAIL
    Walk-forward  rolling windows (default 3y train / 1y test) inside DEVELOPMENT; >=60% windows positive expectancy -> PASS
    Clustering sensitivity   grade under every applicable cluster_model; differences -> WARN
    Multiple testing         banner per 8.4; variant_count and selection_method reported (informational; WARN when selection_method != PRE_SPECIFIED)
    Random baseline          strategy PF >= P95 of baseline -> PASS; P80-P95 WARN; <P80 FAIL
    Param stability          grid of +/-1 and +/-2 steps per parameter; >=70% neighbors keep PF>=1.0 -> PASS; 50-70% WARN; else FAIL
    Trade dependency         expectancy after removing top 5 trades (or top 5%) >0 -> PASS else FAIL; top-5 share >40% -> WARN
    Concentration            top symbol >30% of profit -> WARN; leave-one-symbol-out and leave-one-year-out positive in >=80% -> PASS
    Regime split             per-regime n and expectancy; any regime with n<20 INSUFFICIENT
  LEVEL 3 ECONOMIC VALIDITY
    Cost stress   at 2x fee+slippage: PF>1.0 AND expectancy_R>0 AND OOS expectancy_R>0 -> PASS
    Delay stress  at +1 bar delay_bars: PF>1.0 AND expectancy_R>0 AND OOS expectancy_R>0 -> PASS
    Gap risk      counts/size of GAP_THROUGH_STOP, ENTRY_DATA_GAP, DATA_GAP exits and worst gap loss in R (reported; WARN above config threshold)
    Monte Carlo   trade-order shuffle + bootstrap (seeded, 5000): P5 final equity > start -> PASS; report maxDD percentiles
    Liquidity/funding (M10; NOT_RUN before)
  If any Level 1 test (other than N/A parity) is FAIL or NOT_RUN, Level 2/3 results are labeled UNTRUSTED.
9.4 Overall status is a list, never a composite score. Mapping: pre-registered kill criterion met -> REJECTED; no FAIL and Edge PASS -> PROMISING; VALIDATED requires all applicable tests PASS (no FAIL/INSUFFICIENT/NOT_RUN), pooled trades >=100, Level 1 trusted, a frozen experiment, and the holdout opened once and passed. Validation language follows 8.3 wording rules.
9.5 Every backtest/validation run stores: strategy_version hash, dataset_version, engine_version, code_version, resolved_params, universe, data_scope, fill_model, seed. "Re-run" must reproduce identical numbers (test).
9.6 Baselines in v1: random-entry only. Backlog v1.1: randomized signal timing (preserve trade count/holding/symbol distribution); simple benchmark strategies (buy-and-hold, EMA trend, Donchian) for context only.

====================================================
10. DATA MODEL (PostgreSQL; Alembic; Parquet for bars/equity curves)
====================================================
```sql
CREATE SCHEMA market; CREATE SCHEMA research; CREATE SCHEMA strategy;
CREATE SCHEMA validation; CREATE SCHEMA system; CREATE SCHEMA agents;

market.instrument(instrument_id uuid PK, venue text, market_type text, asset text, quote text, native_symbol text,
  tick_size numeric, lot_step numeric, listed_at timestamptz, UNIQUE(venue, market_type, native_symbol));
market.dataset_manifest(dataset_version text PK, venue text, market_type text, instruments jsonb, timeframes jsonb,
  start_time timestamptz, end_time timestamptz, source_files jsonb, row_counts jsonb, missing_ranges jsonb,
  quality_summary jsonb, dataset_schema_version text, bar_semantics_version text, timezone_policy_version text,
  instrument_metadata_version text, created_at timestamptz);
market.watchlist(watchlist_id uuid PK, name text); market.watchlist_item(watchlist_id, instrument_id);

system.gap(gap_id uuid PK, instrument_id, timeframe text, exclusion_start timestamptz, exclusion_end timestamptz,
  status text CHECK (status IN ('OPEN','REPAIRED','UNREPAIRABLE')), source text, detected_at timestamptz, repaired_at timestamptz);
system.data_quality(id bigserial PK, instrument_id, timeframe, state text, completeness numeric, detail jsonb, computed_at timestamptz);
system.job(job_id uuid PK, kind text, status text, progress numeric, params jsonb, error text, created_at, started_at, finished_at);
system.audit_log(id bigserial PK, actor text, action text, target text, detail jsonb, at timestamptz);

research.event_definition(detector_id text, version text, params_hash text, params jsonb, status text, spec_path text,
  PRIMARY KEY(detector_id, version, params_hash));
research.event(event_id text PK, detector_id, event_type text, version, params_hash, instrument_id, timeframe,
  event_time timestamptz, direction text, ref_price numeric, invalidation_price numeric, regime jsonb, features jsonb,
  trigger jsonb, lookback_gap boolean, dataset_version text, engine_version text,
  UNIQUE(detector_id, event_type, version, params_hash, instrument_id, timeframe, event_time));
research.event_outcome(event_id, horizon int, fwd_ret_pct numeric, fwd_ret_r numeric, mfe_r numeric, mae_r numeric,
  bars_to_mfe int, bars_to_mae int, hit_invalidation boolean, complete boolean, PRIMARY KEY(event_id, horizon));

research.hypothesis(hyp_id uuid PK, title text, statement text, status text, criteria jsonb, preregistered_at timestamptz,
  holdout_opened_at timestamptz, holdout_result jsonb, autopsy text, locked boolean DEFAULT false);
research.experiment_family(family_id uuid PK, hypothesis_ids jsonb, description text, search_space jsonb,
  hypothesis_count int, variant_count int DEFAULT 0, created_at timestamptz);
research.variant(variant_id uuid PK, family_id, experiment_id uuid NULL, params_hash text, resolved_params jsonb,
  result_ref uuid, selected boolean DEFAULT false, created_at timestamptz);   -- append-only
research.experiment(experiment_id uuid PK, hypothesis_id uuid, family_id uuid, title text, status text, conclusion text,
  assessment jsonb, universe jsonb,  -- exact instrument_ids
  timeframe text, data_scope text CHECK (data_scope IN ('DEVELOPMENT','HOLDOUT')),
  dataset_version text, dataset_schema_version text, engine_version text, code_version text,
  detector_refs jsonb, resolved_params jsonb,
  selection_method text CHECK (selection_method IN ('PRE_SPECIFIED','SWEEP_BEST','MANUAL_CHOICE')),
  seed int, refs jsonb,         -- {stats:[ref_id], backtests:[id], validations:[id], baselines:[id]}
  result_hashes jsonb, frozen boolean DEFAULT false, frozen_at timestamptz, supersedes uuid, created_at timestamptz);
research.stats_result(ref_id uuid PK, experiment_id uuid, filter jsonb, data_scope text, dataset_version text,
  dataset_schema_version text, engine_version text, code_version text, resolved_params jsonb, result jsonb, seed int,
  supersedes uuid, locked boolean DEFAULT false, created_at timestamptz);
research.note(note_id uuid PK, title text, body_md text, experiment_id uuid NULL, chart_state jsonb, created_at timestamptz);

strategy.strategy_version(strategy_id text, version text, definition jsonb, definition_hash text, PRIMARY KEY(strategy_id, version));
strategy.backtest(backtest_id uuid PK, experiment_id uuid NOT NULL, strategy_id, version, universe jsonb,
  period tstzrange, data_scope text, fill_model text, dataset_version text, dataset_schema_version text,
  engine_version text, code_version text, resolved_params jsonb, seed int, status text, metrics jsonb,
  skipped jsonb, supersedes uuid, locked boolean DEFAULT false, created_at timestamptz);
strategy.trade(backtest_id, trade_no int, instrument_id, side text, signal_time timestamptz, decision_time timestamptz,
  fill_time timestamptz, exit_signal_time timestamptz, exit_decision_time timestamptz, exit_fill_time timestamptz,
  entry_price numeric, exit_price numeric, qty numeric, fees numeric, pnl numeric, pnl_pct numeric, r_multiple numeric,
  mfe_r numeric, mae_r numeric, bars_held int, entry_tag text, exit_reason text, data_gap_during_hold boolean,
  straddles_boundary boolean, PRIMARY KEY(backtest_id, trade_no));
strategy.equity_curve: Parquet per backtest_id, content-addressed (time, equity, drawdown).

validation.run(run_id uuid PK, experiment_id uuid, backtest_id, level int, kind text, params jsonb, status text,
  result jsonb, seed int, locked boolean DEFAULT false, created_at timestamptz);

agents.run(run_id uuid PK, mode text, user_prompt text, created_at timestamptz);
agents.tool_call(id bigserial PK, run_id, tool text, args jsonb, result_ref text, created_at timestamptz);
-- Triggers: BEFORE UPDATE/DELETE on any row with locked=true (or experiment.frozen=true) raises. Result tables expose no UPDATE path except setting locked.

====================================================
11. API (FastAPI; typed; cursor pagination; ISO-8601 UTC)
====================================================
Error envelope: {"error":{"code":"DATA_INCOMPLETE","message":"...","detail":{}}}. Codes include DATA_UNAVAILABLE, DATA_INCOMPLETE, NOT_IMPLEMENTED, VALIDATION_ERROR (422), IMMUTABLE_RESULT, HOLDOUT_LOCKED, SCOPE_CLIPPED (warning).
Jobs: POST returns {"job_id","status":"queued"}; GET /v1/jobs/{id} -> status, progress, result_ref, logs.
Market/data:
  GET /v1/instruments ; CRUD /v1/watchlists
  GET /v1/candles?instrument_id&tf&from&to&limit&as_of -> {"dataset_version","quality":{state,completeness,exclusions},"t":[],"o":[],"h":[],"l":[],"c":[],"v":[]}
  GET /v1/indicators?instrument_id&tf&name&params&from&to
  GET /v1/datasets/{dataset_version} (manifest) ; GET /v1/data/health ; GET /v1/data/gaps ; POST /v1/data/ingest ; POST /v1/data/repair
Events/research:
  GET /v1/events?detector&event_type&instrument_id&tf&from&to&regime&limit
  GET /v1/events/{id} -> Evidence payload (below)
  POST /v1/stats {filter, instrument_ids, data_scope, cluster_models[], family_id?} -> StatsResult (ref_id)
  GET /v1/stats/{ref_id} ; POST /v1/event-studies (async)
  CRUD /v1/hypotheses ; POST /v1/hypotheses/{id}/open-holdout
  CRUD /v1/families ; GET /v1/families/{id}/variants
  POST /v1/experiments ; GET /v1/experiments[/{id}] ; PATCH (only while unfrozen) ; POST /v1/experiments/{id}/freeze ; GET /v1/experiments/{id}/verify ; GET /v1/experiments/{id}/lineage
  CRUD /v1/notes
Scanner/MTF:
  GET /v1/scanner?watchlist&tf&event_types[]&min_grade&filters -> rows {instrument, price, chg_24h, rvol, last_event{type,time,age_bars}, regime, grade, n_eff}
  GET /v1/mtf?instrument_id
Strategy/validation:
  CRUD /v1/strategies (+/versions) ; POST /v1/backtests (async; requires experiment_id, instrument_ids, data_scope, fill_model) ; GET /v1/backtests/{id}[/trades|/equity|/metrics|/skipped]
  POST /v1/validation (async, kinds[]) ; POST /v1/baselines/random (async) ; POST /v1/sweeps (async; records variants)
  POST /v1/parity/runs (upload TradingView CSVs + run metadata) -> parity report
AI/MCP: POST /v1/ai/chat (SSE) ; GET /v1/ai/tools ; GET /v1/agents/runs
System: GET /v1/status
WebSocket /v1/ws: candle.{instrument}.{tf} (M10), events.new, jobs.{id}, data.health.
Evidence payload (GET /v1/events/{id}):
```json
{
 "event":{"id":"...","detector":"RVOL_CLIMAX","type":"CAPIT","version":"5.0","instrument":"BTC/USDT spot","tf":"1D","time":"...","direction":"LONG","ref_price":0,"invalidation":0,"r_basis":"atr","lookback_gap":false},
 "trigger":[{"name":"isClimax","passed":true,"value":3.4,"threshold":3.0,"provenance":"CALCULATED"}],
 "context":{"regime":{"trend":"down","vol":"high"},"archetype":"bot·drive low"},
 "stats":{"ref_id":"...","data_scope":"DEVELOPMENT","grade":"C","model_sensitive":false,"n_raw":0,"n_eff":0,
   "horizons":[{"h":10,"median_r":0,"ci":[0,0],"win":0,"win_ci":[0,0],"is":{},"oos":{}}],
   "multiple_testing":{"family_id":"...","variant_count":1,"selection_method":"PRE_SPECIFIED"}},
 "analogues":[{"event_id":"...","time":"...","instrument":"...","fwd_r_10":0}],
 "data_quality":{"state":"GREEN","completeness":1.0},
 "dataset_version":"..."
}

====================================================
12. UI SPEC (Master sections 30-44; tokens per Master section 31)
====================================================
Nav v1: Home | Chart | Scanner | Events | Research (Notebook, Hypotheses, Graveyard) | Experiments | Strategy Lab | Validation | Data Health | AI | Settings. Left nav collapsible; right dock 320-420px; bottom dock collapsed by default; max 3 panels visible by default; layout persisted.
Chart workspace:
  header: symbol/venue/market/timeframe, indicator menu, event-layer toggle, replay, layout, data-quality badge.
  main pane: candles + overlays (EMA/SMA/VWAP/BB). Event markers: CONT = teal triangle-up below bar + "CONT"; CAPIT = lime label-up below + "CAPIT"; FAIL = orange triangle-down above + "FAIL"; TOP = red triangle-down above + "TOP"; optional "climax (no trade)" dots.
  trade overlay (when a backtest is selected): entry x-cross (aqua long/red short), active stop as orange step line, target line, exit marker with reason + R; skipped signals shown hollow with reason on hover.
  sub-panes (collapsible, reorderable; default Volume only): Volume (+RVOL), RSI; OI/Funding/Liquidations at M10. Crosshair synced across panes.
  hover data window: rvol, cpos, upper_wick, ATR, regime EMA.
  performance: 5,000 candles at 60fps pan/zoom; lazy history; server computes indicators/events.
  exclusion intervals shown as shaded bands with tooltip.
Evidence panel (click marker): header (type, symbol, tf, time, data_scope badge); "Why triggered" checklist with values vs thresholds; invalidation; Evidence Grade + n_eff + intervals + model-sensitive flag; horizons table (median/mean R, win%, CI; IS/OOS toggle); R histogram; analogues (click -> jump); multiple-testing banner; actions: Open in Events / Save to Notebook / Ask Copilot / Add to Experiment. Signal-strength axis: "n/a (v1)".
Events Explorer: filterable table + live StatsResult + distributions + CSV export (columns include dataset_version, data_scope).
Scanner: Symbol | Price | 24h | RVOL | Last event | Age | Regime | Grade | n_eff; virtualized; saved scans; state preserved; click row -> Chart focused on event.
Strategy Lab: wizard per Master section 39; fill-model/fee/slippage/delay selectors with persistent labels on every result; results: equity+drawdown, monthly returns, trade table (all time fields), symbol/year/regime contribution, MFE/MAE, skipped signals by reason, parity badge when applicable. Every backtest is created inside an experiment.
Validation dashboard: three level panels (Implementation / Statistical / Economic) with PASS/WARN/FAIL/INSUFFICIENT/NOT_RUN + numeric basis; UNTRUSTED banner when Level 1 fails; random-baseline distribution chart with strategy marker; never a single score.
Experiments page: list (status, hypothesis, family, variants, grade summary, frozen badge) and detail: hypothesis, universe, dataset_version (link to manifest), detector@version, resolved_params, selection_method, multiple-testing banner, results by validation level, conclusion, Freeze button (confirm dialog explaining irreversibility), Verify-integrity button, read-only Lineage view (nodes link to stored objects). Hypothesis page lists its experiments.
Data Health: per (venue, instrument, tf) state, completeness, exclusion intervals, repair button, dataset_version history, manifest viewer.
Replay: controls prev/next/play/pause/speed; API as_of = replay time; UI shows no bars/events/outcomes after as_of; events appear when event_time is reached; signal_time/decision_time/fill_time visible for trades.
Every component implements loading, empty (with next action), error (message + retry), stale/offline, partial-data states (Master section 62). Provenance badge component (OBSERVED/CALCULATED/INFERRED/AI). Keyboard map per Master section 44. WCAG AA; never color-only. Footer: "Research tool. Not financial advice."

====================================================
13. AI COPILOT & MCP
====================================================
LLMProvider interface (stream_chat(messages, tools)); adapters selected by env. Modes: Research, Quant, Data, Critic, Writer (system prompt + allowed tool subset).
Tools (deterministic JSON; each result includes ref_id + dataset_version): search_instruments, get_candles, get_data_health, search_events, get_event, run_event_study, compute_stats, compare_event_sets, create_hypothesis, list_hypotheses, create_experiment, get_experiment, freeze_experiment, get_lineage, run_backtest, get_backtest, run_validation, run_random_baseline, create_note, create_report, get_chart_context. No tool may take a holdout-scope action without the open-holdout flow.
Number-integrity rule (enforced in code): the model emits numbers only as placeholders {{ref:<ref_id>#<json_path>}} resolved server-side from stored tool results. Raw numerals in free text without a ref are flagged in the UI and logged. Reports follow Master section 66 plus: n_eff, intervals, data_scope, OOS, multiple-testing block, limitations. Critic mode must list at least three ways the conclusion could be wrong. Grade wording per 8.3.
AI may not: predict prices, give trade instructions, touch exchanges/keys, write outside notes/reports/experiments, modify frozen objects.
MCP server: exposes the READ and RESEARCH tools over MCP (stdio + HTTP), same schemas; every call written to agents.tool_call + audit_log. Tiers in v1: READ_ONLY, RESEARCH. Order tools do not exist.

====================================================
14. TESTING & PARITY PROTOCOL
====================================================
14.1 Test types
  Unit (pytest): features, detectors, simulator steps, stats functions.
  Property (hypothesis): TRUNCATION INVARIANCE for every detector, feature and resampler (result on data cut at t == restriction to <=t of result on full data); stats invariants (CI contains point estimate; n_eff<=n_raw; grade monotonic); simulator invariants (equity closes; stops only tighten; no unprotected position when stop.kind != NONE); scope invariants (no observation in two segments; no outcome window crosses a boundary; embargo/purge order per 8.1).
  Golden: frozen fixtures (BTCUSDT 1D full history; synthetic cooldown fixtures 6.7; gap/E1-E4 fixtures); expected events, trades, stats stored as JSON; changes need a PR note.
  Replay: incremental as_of replay events == batch events.
  Data quality injection: duplicates, gaps, out-of-order, bad OHLC -> Guardian detects, repair restores expected dataset; exclusion applies only to the affected interval (test asserts untouched history).
  Immutability: frozen experiment/result UPDATE and DELETE attempts fail; verify endpoint detects tampering; locked rows cannot change.
  Fail-closed tests: spot request must never return perp data (and vice versa); unavailable timeframe must error.
  String tests: banned grade wording; required multiple-testing banner text.
14.2 TradingView parity (M3 and M6; `make parity`)
  Layer 1 Features: export TradingView chart data (OHLCV + Data Window plots rvol, cpos, upW, Regime EMA) for BINANCE:BTCUSDT 1D; compare to engine; relative tolerance 1e-6 after warm-up (skip first 500 bars).
  Layer 2 Trades: run with fill_model=PARITY on the same data/settings as the owner's latest TradingView "List of trades" export (same script version); compare entry/exit times (exact), prices (rel 1e-6), tags/exit reasons, qty (rel 1e-3 after lot rounding), PnL (rel 0.5%).
  Parity status per trade and overall: EXACT | EXACT_WITH_DOCUMENTED_PLATFORM_DIFFERENCE (needs design-note entry describing TradingView behavior and engine handling) | DATA_MISMATCH (needs bar-level data diff report) | ENGINE_BUG (blocks) | UNKNOWN (blocks). Pass = zero ENGINE_BUG and zero UNKNOWN. Failure never automatically means the engine is wrong; classify first.
  Record TradingView run metadata with each parity run: script version, symbol/timeframe, date range, Bar Magnifier setting (expected OFF), commission/slippage settings, fill assumptions, export date. Possible platform differences to check: broker-emulator intrabar path, bar magnifier, symbol metadata/qty rounding, commission handling, session boundaries, historical data revisions.
  Use only exports from the CURRENT script version as golden.
14.3 CI and performance
  CI gates per Section 0. e2e core flow: open BTC 1D -> toggle events -> click CAPIT marker -> Evidence panel shows grade + n_eff + CI -> add to experiment -> run backtest -> open validation -> freeze experiment -> verify INTACT.
  Targets (laptop): 5,000-candle API <300ms; indicators 5,000 bars <150ms; events+stats for 30-symbol daily universe <5s; single-symbol 10y daily backtest <5s; scanner 100 symbols 1D <2s; chart interactions <50ms.

====================================================
15. MILESTONES & TASK CARDS (in order; each ends with demo + `make check` green)
====================================================
M0 Foundation
  T0.1 Monorepo + uv/pnpm + native PostgreSQL + Windows-native development scripts + Makefile + CI gates (Section 0). Docker Compose packaging is optional and must not be a prerequisite.
  T0.2 FastAPI skeleton, /v1/status, Alembic baseline, OpenAPI -> TS client, error envelope + codes.
  T0.3 Next.js shell: topbar, nav, design tokens, state components.
  T0.4 docs/DECISIONS.md, config/defaults.yaml (PD-11), append-only result-table pattern + lock triggers (PD-13).
  AC: `make dev` runs all services; CI green; client regenerates cleanly; defaults loader rejects undocumented params.
M1 Candle data + Guardian
  T1.1 Instrument registry (SPOT + PERP identity); Binance spot adapter behind ExchangeAdapter; perp returns NOT_IMPLEMENTED.
  T1.2 Deterministic Parquet writer (content_sha256), dataset_manifest + semantic versions, resumable idempotent ingest.
  T1.3 Guardian checks + states + exclusion intervals + gap repair job; Data Health screen + manifest viewer.
  T1.4 Weekly resampling with INCOMPLETE flag; seed universe BTC, ETH, SOL (1D, 4H, 1H) + fixtures.
  AC: re-ingest yields identical dataset_version; manifest answers "what exactly did this use?"; injected gap detected, repaired, and only the affected interval excluded; spot->perp substitution fails closed; storage budget enforced.
M2 Chart
  T2.1 /v1/candles + indicators. T2.2 Chart workspace (symbol/tf, overlays, volume + collapsible sub-panes, crosshair sync, lazy history, exclusion bands). T2.3 Layout persistence, command palette skeleton.
  AC: performance targets met; quality badge visible.
M3 Feature library + parity layer 1
  T3.1 Features per Section 5 + truncation tests. T3.2 Parity tool: CSV import, run metadata capture, status categories.
  AC: feature parity within tolerance on BTCUSDT 1D; truncation tests pass.
M4 Event engine
  T4.1 Detector framework + registry + versioning + lookback_gap flag. T4.2 RVOL_CLIMAX v5.0 with cooldown fixtures A-G as golden tests. T4.3 Outcomes (6.4). T4.4 Chart markers + hover cards + climax-no-trade layer. T4.5 SPEC_REQUIRED placeholders + RVOL_CLIMAX spec incl. naming note and default cluster_model.
  AC: truncation invariance passes; h=10 outcomes equal the Pine study per-signal returns on parity data; markers render.
M5 Stats, scopes, evidence, hypotheses
  T5.1 dataset_scopes.yaml (freeze boundaries per 8.1; owner confirms OD-8) + data-access enforcement (DEVELOPMENT/HOLDOUT, embargo/purge order).
  T5.2 Stats module (8.2-8.3: cluster models, sensitivity, seeded, golden-tested).
  T5.3 Evidence panel + Events Explorer. T5.4 Hypothesis registry (pre-registration freeze, graveyard, open-holdout once). T5.5 Notebook.
  AC: scope invariants property tests pass; every displayed stat shows grade + n_eff + CI + data_scope; holdout cannot be opened twice; grade wording string test passes; stats reproducible by ref_id.
M5b Experiments, families, immutability, lineage
  T5b.1 experiment_family/variant/experiment tables; sweep runner records every variant; multiple-testing banner. T5b.2 Freeze transaction (lock rows, result_hashes), verify endpoint, trigger tests. T5b.3 Experiments page + lineage view. T5b.4 Copilot/MCP experiment tools.
  AC: frozen experiment and all referenced results reject UPDATE/DELETE; verify detects tampering; every stored result resolves to its dataset manifest; lineage nodes all link; singleton family auto-created for ad-hoc runs.
M6 Backtest + Strategy Lab + parity layer 2
  T6.1 Simulator (Section 7) with PARITY and REALISTIC_OHLC, time fields, E1-E6, fee/slippage models. T6.2 Strategy definition CRUD/versioning. T6.3 Portfolio mode. T6.4 Strategy Lab UI + trade overlay + skipped signals. T6.5 Parity layer 2 tool.
  AC: parity pass (zero ENGINE_BUG/UNKNOWN) on the owner's current-version TradingView export; reproducibility test passes; no signal disappears (all recorded as trade, SKIPPED or NOT_FILLED with reason); every backtest belongs to an experiment.
M7 Validation
  T7.1 Metrics + dependency/concentration. T7.2 Walk-forward, Monte Carlo, param stability, cost/delay stress (tightened criteria). T7.3 Random baseline. T7.4 Validation dashboard (3 levels, UNTRUSTED labeling). T7.5 Holdout flow end to end.
  AC: all tests return a status with numeric basis; baseline distribution renders; Level 1 failure marks Level 2/3 UNTRUSTED.
M8 Scanner, watchlists, MTF, replay
  AC: scanner 100 symbols <2s; replay leaks no future data (test); MTF matches regime module.
M9 AI Copilot + MCP
  AC: numbers without ref flagged; MCP calls audited; tool-registry test asserts no order-related tools and that experiment tools respect immutability.
M10 Perp derivatives + live candles
  T10.1 Perp adapter: funding, OI, liquidations; sub-panes. T10.2 Live kline WebSocket with reconnect, gap detection, REST backfill, forming-bar updates. T10.3 Funding cost model; enable SHORT-side strategies (FAIL/TOP) on perp only.
  AC: killing the socket mid-bar yields a detected gap, automatic repair and GREEN state; no silent gaps.
M11 Hardening: accessibility, performance, e2e suite, backup/export of notebook + registry + experiments, docs.

====================================================
16. OWNER INPUTS (agent must ask; defaults let work proceed)
====================================================
OD-1  Rules for FAST_RECLAIM, TOP_CAPIT, TOP_CLIMAX, TOPFAIL, CAPITFAIL, CAPIT_RECLAIM (no default; blocks those detectors only).
OD-2  Universe: default BTC, ETH, SOL for M0-M5; expand to the owner's 30-symbol list before M7 (ask for the list).
OD-3  Costs: default fee_model PERCENT 0.10 per side; REALISTIC_OHLC slippage_model FIXED_BPS 5 (placeholder; owner confirms).
OD-4  Default LLM provider/model (blocks M9 only).
OD-5  Storage budget GB (default 20; ingest refuses to exceed).
OD-6  Parity reference: default spot BINANCE:*USDT (matches the TradingView chart used so far).
OD-7  Import events/features from an external research system in v1.1 (default no).
OD-8  Holdout: default 80/20 per 8.1 with boundaries frozen at M5; owner confirms the dates and that holdout is the latest 20% of calendar span.
OD-9  TradingView parity settings: confirm Bar Magnifier OFF and standard OHLC emulation; agent records them per run.
OD-10 close_open_at_end default false.
OD-11 Default cluster_model per detector (agent proposes in the detector spec; owner approves).
OD-12 Multiple-testing: confirm banner threshold (default shown whenever variant_count>1 or selection_method != PRE_SPECIFIED).

====================================================
17. KNOWN TRAPS (check before debugging parity)
====================================================
- barssince cooldown counts RAW occurrences, shifted by one bar.
- PARITY entries fill at the signal bar's CLOSE; management starts the NEXT bar; hi_since excludes the signal bar; time stop counts from the signal bar.
- A stop hit on bar t leaves the account flat at close of t, so same-bar re-entry is allowed.
- EMA seeds differ from SMA-seeded implementations; skip warm-up in parity.
- TradingView stop orders placed at the prior close; gaps fill at OPEN, not the stop.
- Same-bar stop/target ordering differs between PARITY (TradingView heuristic) and REALISTIC_OHLC (stop first).
- Spot volume is base units; never mix venue volume definitions in RVOL.
- Daily bars align to 00:00 UTC; never local time.
- The forming bar (and forming week) never feeds features, events or backtests.
- Single-symbol Sharpe can mislead for multi-asset strategies; use portfolio mode.
- Boundaries are calendar dates for the whole universe; never per-instrument cutoffs.
- Exclusion intervals cover only the affected bars, never whole instruments.

====================================================
18. DEFINITION OF DONE (every task)
====================================================
Code + tests + types + lint pass; acceptance criteria demonstrated with evidence; docs/specs updated; DECISIONS entry for any deviation; no hard-coded statistics; no lookahead; truncation-invariance test for any new detector/feature; results immutable and lineage-complete; loading/empty/error/stale/partial UI states (Master section 61-62).

====================================================
19. KICKOFF PROMPT
====================================================
"Read AGENTS.md (Section 0), CryptoOS Master Spec v1.0 and IMPLEMENTATION_SPEC_v1.1.1.md (Parts 1-2) fully, plus specs/reference/rvol_climax_pro_v5.pine. Confirm PD-1..PD-13 back in 13 bullets. List every ambiguity or conflict you found and which Section 16 inputs you need. Then execute M0 only: propose the file tree and wait for my OK before writing code. After M0, proceed milestone by milestone and stop at each acceptance gate with evidence."

Two things to settle before M5, since they decide how much of your history you can tune on:
- **OD-8:** holding out the latest 20% means you can't tune on the most recent regime. Confirm the 80/20 split, or give me the percentage you prefer.
- **OD-12:** I set the multiple-testing banner to show whenever more than one variant was evaluated. If that gets noisy for single-run experiments, say so and I'll tighten it.