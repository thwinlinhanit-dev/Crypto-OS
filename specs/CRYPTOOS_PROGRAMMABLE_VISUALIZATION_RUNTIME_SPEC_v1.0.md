# CRYPTOOS --- PROGRAMMABLE VISUALIZATION RUNTIME & MARKET CANVAS SPEC v1.0

**Status:** Proposed implementation specification\
**Companion:** CryptoOS Master Spec v1.0 + IMPLEMENTATION_SPEC v1.1.1\
**Purpose:** Add a wrun-inspired programmable market-visualization
subsystem using CryptoOS-owned APIs, runtime, renderer, security model,
provenance, and tests.\
**Precedence:** Existing implementation rules remain authoritative.
Conflicts must be recorded in `docs/DECISIONS.md`.

## 1. Executive summary

CryptoOS shall add two closely related capabilities:

1.  **Programmable Visualization Runtime (PVR)** --- a sandboxed
    TypeScript-compatible environment for deterministic market
    computations and visualizations.
2.  **Market Canvas** --- a synchronized, layer-based workspace that can
    render candles, heatmaps, footprint, liquidity, derivatives, events,
    HUDs, and future on-chain data.

Core architecture:

``` text
DATA
 ↓
FEATURES / EVENTS / COMPUTATION
 ↓
VISUALIZATION DATA API
 ↓
PROGRAMMABLE RUNTIME
 ↓
RENDER GRAPH
 ↓
MARKET CANVAS
 ↓
AI / RESEARCH / MCP
```

The runtime is not a replacement for Data Guardian, Event Engine,
Research, Quant, Validation, AI, or MCP. It is a controlled projection
layer over those systems.

## 2. Reverse-engineering boundary

Public wrun material and supplied examples support these product-level
observations:

-   richer datasets than conventional OHLCV indicators;
-   TypeScript-friendly authoring;
-   browser/high-performance execution;
-   dynamic liquidation and liquidity visualizations;
-   absorption/order-flow visualization;
-   reusable Session/Market/Order Flow HUDs;
-   options positioning dashboards;
-   heatmaps, profiles, curves, dashboards and other non-line
    visualizations;
-   AI-assisted creation/conversion.

CryptoOS does **not** claim knowledge of wrun's private source code,
compiler, internal APIs, database, proprietary algorithms, or exact WASM
implementation. These are reference concepts only. Do not copy
proprietary source or UI.

## 3. Non-negotiable rules

All existing PD-1..PD-13 remain active.

Additional visualization rules:

-   A visualization program cannot place/cancel/modify orders.
-   Programs cannot access exchange credentials.
-   Programs cannot access the database directly.
-   Programs cannot use arbitrary network or filesystem access.
-   Replay programs receive no data after `as_of`.
-   AI-generated programs are untrusted code and must be validated
    before execution.
-   Observed, calculated, inferred, and AI values must remain
    distinguishable.
-   A visual heuristic is not automatically a trading event.
-   Missing data must fail explicitly; never substitute another venue,
    market type, timeframe, or dataset.
-   No arbitrary `signal strength /100`.

## 4. Scope

### Foundation

-   visualization schema and registry;
-   deterministic data API;
-   sandbox;
-   provenance;
-   program hashing;
-   line/candle/HUD/table primitives;
-   replay/as-of;
-   chart context.

### Market Canvas

-   synchronized panes;
-   layer manager;
-   heatmap;
-   profile;
-   footprint;
-   orderbook/liquidity layers;
-   programmable HUDs.

### Advanced

-   trades/CVD/delta;
-   liquidity intelligence;
-   OI/funding/liquidations;
-   OI-adjusted liquidation model;
-   options/GEX;
-   future on-chain layers.

### AI

-   natural language → visualization program;
-   program validation/debugging;
-   Pine conversion later;
-   research linkage.

Live execution remains outside scope.

## 5. Architecture

``` text
                         CRYPTOOS DATA FABRIC
                                  │
          ┌───────────────────────┼───────────────────────┐
          │                       │                       │
       Candles               Microstructure          Derivatives
          │                 Trades / L2 / Flow       OI / Funding
          │                       │                 Liquidations
          │                       │                   Options
          └───────────────────────┼───────────────────────┘
                                  │
                         COMPUTATION ENGINES
                                  │
                  Features / Events / Research / Quant
                                  │
                         VISUALIZATION DATA API
                                  │
                  PROGRAMMABLE VISUALIZATION RUNTIME
                         │                 │
                      Compute           Render
                         │                 │
                         └───────┬─────────┘
                                 ▼
                           MARKET CANVAS
                                 │
                  Charts / HUDs / Heatmaps / Profiles
                                 │
                           AI COPILOT / MCP
```

## 6. Visualization Program

A program is a versioned first-class object.

``` yaml
visualization_id: btc_liquidity_map
version: 1.0.0
status: DRAFT
runtime: cryptoos-ts
program_hash: <sha256>
required_datasets:
  - candles
  - orderbook
  - open_interest
permissions:
  network: false
  filesystem: false
  trading: false
```

Required metadata:

-   id;
-   version;
-   title/description;
-   source;
-   runtime;
-   program hash;
-   datasets;
-   features/events;
-   parameters;
-   permissions;
-   outputs;
-   performance budget;
-   creation/update metadata.

Lifecycle:

``` text
DRAFT → VALIDATED → PUBLISHED → DEPRECATED
```

Behavior cannot silently change under an existing version.

## 7. TypeScript-compatible authoring

The initial language is a restricted TypeScript-compatible DSL.

Allowed initially:

-   variables/functions;
-   arrays/objects/maps;
-   deterministic math/statistics;
-   approved dataset APIs;
-   approved visualization APIs;
-   functional operations such as map/filter/reduce.

Forbidden:

-   `eval`;
-   dynamic imports;
-   arbitrary HTTP;
-   filesystem;
-   process execution;
-   environment access;
-   credentials;
-   arbitrary WebSocket connections.

Conceptual API:

``` typescript
export default defineVisualization({
  id: "btc_market_state",
  version: "1.0.0",

  inputs: {
    candles: data.candles(),
    trades: data.trades(),
    orderbook: data.orderbook(),
    oi: data.openInterest()
  },

  parameters: {
    lookback: 500
  },

  compute(ctx) {
    // deterministic computation
  },

  render(ctx) {
    // declarative rendering
  }
});
```

This is a CryptoOS-owned interface, not a copy of wrun syntax.

## 8. Runtime pipeline

``` text
Source
 ↓
Parser
 ↓
AST
 ↓
Static validation
 ↓
CryptoOS IR
 ↓
Permission/resource checks
 ↓
Sandbox
 ↓
Data API
 ↓
Compute graph
 ↓
Render graph
 ↓
Canvas
```

The IR enables:

-   deterministic execution;
-   caching;
-   dependency inspection;
-   optimization;
-   provenance;
-   AI validation.

### Execution backends

MVP may use an isolated worker runtime.

A WASM backend should be added when performance gates require it:

``` text
TypeScript-compatible source
 ↓
CryptoOS IR
 ├── Worker backend
 └── WASM backend
```

Equivalent inputs must produce equivalent numerical outputs within
declared tolerance.

## 9. Data API

Programs never access PostgreSQL/Parquet directly.

Initial APIs:

``` text
data.candles(...)
data.trades(...)
data.orderbook(...)
data.openInterest(...)
data.funding(...)
data.liquidations(...)
```

Future:

``` text
data.options.chain(...)
data.onchain.transfers(...)
data.onchain.entities(...)
data.onchain.labels(...)
```

Every dataset response carries:

``` json
{
  "dataset_version": "...",
  "schema_version": "...",
  "source": "...",
  "venue": "...",
  "instrument_id": "...",
  "timeframe": "...",
  "as_of": "...",
  "quality": {
    "state": "GREEN",
    "completeness": 1.0
  }
}
```

Unavailable/incomplete data returns explicit status such as
`DATA_UNAVAILABLE` or `DATA_INCOMPLETE`.

## 10. Permissions and sandbox

Each program receives declared capabilities:

``` yaml
datasets:
  - candles
  - trades
  - orderbook
visualization:
  - chart
  - heatmap
  - hud
network: false
filesystem: false
secrets: false
trading: false
```

Runtime limits:

-   CPU;
-   memory;
-   execution time;
-   maximum rows;
-   maximum render primitives;
-   concurrency.

Required protections:

-   worker isolation;
-   capability checks;
-   watchdog;
-   dependency allowlist;
-   CSP;
-   no credential access;
-   no arbitrary network;
-   no arbitrary filesystem.

## 11. Stateful visualizations

Some market visualizations require state.

Allowed state:

-   previous/current bar;
-   rolling windows;
-   session state;
-   active liquidity levels;
-   replay state;
-   current OI model state.

State must be reconstructible from declared inputs. Hidden global
mutable state is forbidden.

## 12. Market Canvas

The Market Canvas is the flagship workspace.

Layers:

``` text
PRICE
MICROSTRUCTURE
LIQUIDITY
DERIVATIVES
OPTIONS
ON-CHAIN
EVENTS
AI
```

All layers share:

-   time axis;
-   price axis where relevant;
-   viewport;
-   crosshair;
-   replay clock;
-   instrument/timeframe context.

Default UI remains progressive disclosure: do not render every dataset
at once.

## 13. Rendering primitives

Core:

``` text
Line
Area
Bar
Candlestick
Histogram
Scatter
Point
Label
Rectangle
Polyline
Heatmap
Profile
Table
Gauge
Card
HUD
Curve
ReferenceLine
Zone
```

Advanced:

``` text
Footprint
DepthProfile
LiquidityWall
LiquidationLevel
OrderbookHeatmap
VolumeProfile
GammaCurve
FlowRibbon
EntityFlow
```

Every primitive supports data binding, visibility, z-order,
tooltip/inspection, and provenance.

## 14. Heatmap

Heatmap is first-class:

``` typescript
render.heatmap({
  x: time,
  y: price,
  value: liquidity,
  mode: "continuous"
});
```

Required:

-   sparse representation;
-   viewport clipping;
-   incremental updates;
-   zoom-dependent aggregation;
-   LOD;
-   GPU/WebGL where justified.

Zoom may change rendering aggregation but never underlying stored data.

## 15. Liquidity Intelligence

Supported concepts:

-   liquidity wall;
-   liquidity zone;
-   concentration;
-   persistence;
-   migration;
-   addition/removal;
-   sweep;
-   void;
-   absorption.

Derived objects carry:

``` text
timestamp
price range
size
persistence
source
dataset_version
provenance
```

No arbitrary score /100.

## 16. OI-adjusted liquidation map

Reference advanced visualization:

``` text
Price + OI + volatility + leverage assumptions
                  ↓
          position model
                  ↓
     10x / 25x / 50x / 100x
                  ↓
       estimated liquidation map
```

The exact position distribution/liquidation model requires an approved
model specification before research use.

UI must state:

> MODEL-DERIVED --- NOT EXCHANGE-OBSERVED

Estimated liquidation levels are never labeled as observed exchange
orders.

## 17. Absorption

Where actual taker-side data exists, use it rather than estimating side
from candle direction.

Minimum inputs:

``` text
taker_buy_volume
taker_sell_volume
price movement
volume
```

Conceptual state:

``` text
aggressive buying + failure to advance
→ potential sell absorption
```

A visualization heuristic is not automatically a formal Event Engine
detector. Formal event logic requires `specs/events/<id>.md` under PD-4.

## 18. Footprint

Footprint cells may expose:

``` text
price
buy_volume
sell_volume
delta
total_volume
trade_count
imbalance
```

Modes:

-   bid/ask;
-   delta;
-   volume;
-   imbalance.

Do not estimate trade side when reliable exchange-side data exists.

## 19. Orderbook heatmap

Support:

``` text
price × time × displayed liquidity
```

Operations:

-   add;
-   remove;
-   persistence;
-   wall detection;
-   migration;
-   sweep;
-   void.

Raw orderbook data remains immutable. Derived heatmap state is
versioned.

## 20. HUD framework

### Session HUD

-   active session;
-   elapsed;
-   range;
-   range vs historical average;
-   VWAP;
-   prior high/low.

### Market HUD

-   trend;
-   RSI;
-   volume;
-   volatility;
-   taker buy share;
-   OI.

### Order Flow HUD

-   taker buy/sell;
-   net delta;
-   CVD;
-   liquidations;
-   absorption.

### Liquidity HUD

-   nearest liquidity zone;
-   largest wall;
-   concentration;
-   sweep;
-   void.

### Derivatives HUD

-   OI;
-   OI change;
-   funding;
-   basis;
-   liquidations.

### Options HUD

-   GEX;
-   gamma flip;
-   max pain;
-   put/call;
-   skew;
-   vega;
-   dealer delta.

Unavailable inputs must show `DATA_UNAVAILABLE`, not fake zeros.

## 21. Options computation

Future options layer:

``` text
live options chain
 ↓
contract normalization
 ↓
pricing/Greeks
 ↓
spot-grid evaluation
 ↓
dealer exposure model
 ↓
GEX / gamma flip / max pain / skew / vega / delta
```

Model assumptions must be visible and versioned.

## 22. Provenance

Every output is traceable:

``` text
program
 ↓
computation node
 ↓
dataset
 ↓
dataset_version
 ↓
venue/source
```

Provenance classes:

``` text
OBSERVED
CALCULATED
INFERRED
AI
```

Example:

``` text
BTC price              OBSERVED
OI delta               CALCULATED
estimated liquidation  INFERRED
AI explanation         AI
```

## 23. Replay and causality

Every run can declare:

``` text
as_of = timestamp
```

No data after `as_of` may be visible.

Required invariant:

``` text
program(full_history, as_of=t)
==
program(history_truncated_at=t)
```

This is mandatory for research/replay mode.

## 24. Research bridge

Visualization must not silently become a trading rule.

Correct workflow:

``` text
Visualization
 ↓
Observation
 ↓
Hypothesis
 ↓
Formal event/feature specification
 ↓
Event study
 ↓
Experiment
 ↓
Validation
```

A selected canvas state can be saved with:

``` text
instrument
timeframe
viewport
layers
program_refs
dataset_version
as_of
resolved_params
```

## 25. AI program generation

Flow:

``` text
User request
 ↓
AI identifies required datasets
 ↓
availability check
 ↓
assumption proposal
 ↓
program generation
 ↓
parse/type/security/resource checks
 ↓
fixture test
 ↓
sandbox execution
 ↓
render
 ↓
explanation
```

AI cannot invent observed numbers, bypass permissions, silently
substitute datasets, or hide model assumptions.

Generated programs are untrusted until validated.

## 26. AI / MCP tools

Add:

``` text
list_visualizations
get_visualization
create_visualization_draft
validate_visualization
run_visualization
get_visualization_data
get_visualization_provenance
save_visualization
clone_visualization
compare_visualizations
get_market_canvas
get_chart_context
```

Every tool result contains:

``` json
{
  "ref_id": "...",
  "dataset_version": "...",
  "program_hash": "...",
  "runtime_version": "...",
  "data_quality": {},
  "result": {}
}
```

Every MCP call is audited.

Permission tiers:

``` text
READ_ONLY
RESEARCH
VISUALIZATION_WRITE
```

No trading tier.

## 27. API

Initial:

``` text
GET  /v1/visualizations
POST /v1/visualizations
GET  /v1/visualizations/{id}
PATCH /v1/visualizations/{id}
POST /v1/visualizations/{id}/validate
POST /v1/visualizations/{id}/run
GET  /v1/visualizations/{id}/runs/{run_id}
GET  /v1/visualizations/{id}/provenance
POST /v1/visualizations/{id}/publish
POST /v1/visualizations/{id}/clone

GET  /v1/chart/context
GET  /v1/chart/layers
POST /v1/chart/snapshots
GET  /v1/chart/snapshots/{id}
```

## 28. Database

### `visualization.program`

``` text
visualization_id
version
title
description
source_code
program_hash
runtime
status
permissions
required_datasets
parameters_schema
render_schema
created_by
created_at
updated_at
```

### `visualization.run`

``` text
run_id
visualization_id
program_hash
dataset_version
engine_version
runtime_version
instrument_scope
timeframe
as_of
resolved_params
result_ref
quality_state
execution_ms
created_at
```

### `visualization.snapshot`

``` text
snapshot_id
run_id
instrument
timeframe
viewport
layers
layout
program_refs
dataset_version
as_of
created_at
```

## 29. Repository layout

``` text
engine/visualization/
  registry.py
  schema.py
  compiler/
  runtime/
  sandbox/
  data_api/
  compute/
  state/
  provenance/
  cache/
  render/

apps/web/components/
  market-canvas/
  hud/
  heatmap/
  footprint/
  profile/
  visualization/

specs/visualization/
  RUNTIME.md
  PROGRAM_SCHEMA.md
  DATA_API.md
  RENDERING.md
  SECURITY.md
  PERFORMANCE.md
  TESTING.md
```

## 30. Testing

Required unit tests:

-   parser;
-   type checking;
-   IR;
-   data API;
-   computations;
-   render schema;
-   provenance;
-   permissions.

Required property tests:

### Determinism

Same inputs → identical outputs.

### Truncation invariance

`as_of=t` equals history truncated at `t`.

### Backend parity

Worker and WASM outputs match within declared tolerance.

### Permission safety

Undeclared datasets cannot be accessed.

### Resource safety

Expensive/infinite programs terminate.

Golden fixtures must include:

1.  line;
2.  candle overlay;
3.  HUD;
4.  heatmap;
5.  orderbook heatmap;
6.  replay;
7.  missing data;
8.  unavailable data;
9.  provenance;
10. permission violation;
11. deterministic rerun;
12. version change;
13. backend parity.

Visual regression tests must cover the Market Canvas and major
HUD/heatmap states.

## 31. Performance

Existing CryptoOS targets remain authoritative:

-   5,000-candle API \<300 ms;
-   indicators for 5,000 bars \<150 ms;
-   chart interaction \<50 ms;
-   scanner 100 symbols 1D \<2 s.

Additional runtime goals:

-   cached simple visualization startup \<200 ms;
-   incremental updates preferred;
-   expensive calculations moved to worker/WASM/server;
-   heatmap uses sparse data, clipping and LOD.

Performance optimizations must never change numerical results.

## 32. Failure states

Required states:

``` text
LOADING
READY
STALE
PARTIAL
DATA_UNAVAILABLE
DATA_INCOMPLETE
PROGRAM_INVALID
RUNTIME_ERROR
PERMISSION_DENIED
RESOURCE_LIMIT
REPLAY_LIMIT
NOT_IMPLEMENTED
```

Each must state what happened, whether the output is trustworthy, and
the next user action.

## 33. Security threat model

Threats:

-   malicious code;
-   AI-generated unsafe code;
-   infinite loops;
-   memory exhaustion;
-   data exfiltration;
-   credential access;
-   arbitrary network;
-   renderer abuse;
-   cross-program state leakage.

Controls:

-   sandbox;
-   capability permissions;
-   no arbitrary network/filesystem;
-   no secrets;
-   parser/type checker;
-   watchdog;
-   resource limits;
-   dependency allowlist;
-   audit log.

## 34. Pine compatibility --- later

Do not implement first.

Future architecture:

``` text
Pine
 ↓
Pine parser
 ↓
CryptoOS IR
 ↓
semantic mapping
 ↓
Visualization Program
```

Conversion reports must identify unsupported functions, semantic
differences, timeframe differences, execution differences, and
lookahead/repaint risks.

No "exact conversion" claim without parity evidence.

## 35. Reference programs

Build in this order:

1.  Session HUD.
2.  Market HUD.
3.  Order Flow HUD.
4.  Liquidity Heatmap.
5.  OI-Adjusted Liquidation Map.
6.  Footprint.
7.  Options Dashboard.
8.  On-chain Flow HUD.

Do not build a reference program before its required underlying data
engine exists.

## 36. Milestones

### M12 --- Foundation

-   schema;
-   registry;
-   runtime interface;
-   sandbox;
-   provenance;
-   deterministic data API;
-   basic line/HUD renderer;
-   replay.

**Gate:** deterministic execution, permission enforcement, replay tests
green.

### M13 --- Market Canvas

-   layer manager;
-   synchronized crosshair;
-   HUD;
-   heatmap;
-   profile;
-   layout persistence.

**Gate:** performance + visual regression green.

### M14 --- Microstructure

-   trades;
-   CVD;
-   delta;
-   footprint;
-   orderbook;
-   liquidity zones.

**Gate:** deterministic reconstruction and replay parity.

### M15 --- Derivatives

-   OI;
-   funding;
-   liquidations;
-   liquidation model;
-   options interface/GEX.

**Gate:** explicit model assumptions and numerical fixtures.

### M16 --- AI Visualization

-   natural language builder;
-   editor;
-   validator;
-   debugger;
-   explanation.

**Gate:** unsafe generated code is blocked and valid programs are
reproducible.

### M17 --- Research Bridge

-   visualization → hypothesis;
-   experiment linkage;
-   chart snapshots;
-   feature extraction;
-   provenance.

**Gate:** observation never silently becomes validated signal.

## 37. Definition of Done

-   [ ] schema versioned;
-   [ ] deterministic program hash;
-   [ ] permissioned data API;
-   [ ] sandbox;
-   [ ] no arbitrary network/filesystem;
-   [ ] replay/as-of enforcement;
-   [ ] truncation tests;
-   [ ] backend parity when WASM exists;
-   [ ] provenance;
-   [ ] data quality;
-   [ ] heatmap/HUD tests;
-   [ ] resource-limit tests;
-   [ ] AI sandbox;
-   [ ] MCP audit;
-   [ ] no trading capability;
-   [ ] immutable program versions;
-   [ ] visual regression;
-   [ ] performance targets;
-   [ ] documentation.

## 38. Agent kickoff

The coding agent must:

1.  Read `AGENTS.md`.
2.  Read Master Spec.
3.  Read `IMPLEMENTATION_SPEC_v1.1.1.md`.
4.  Read this specification.
5.  Read `specs/events/*` and obey PD-4.
6.  Inspect existing chart code.
7.  Create `specs/design/VISUALIZATION_RUNTIME_DESIGN.md`.
8.  Record decisions/conflicts in `docs/DECISIONS.md`.
9.  Build M12 only.
10. Do not implement orderbook, liquidation, options, on-chain, or AI
    code generation until their dependencies exist.

### First implementation task

Build only:

``` text
Visualization Program schema
+
registry
+
sandbox interface
+
deterministic data API abstraction
+
provenance contract
+
simple line/HUD renderer
+
replay/as_of enforcement
```

## 39. Final principle

The Data/Research/Quant engines remain the source of truth.

The Visualization Runtime is a programmable projection of that truth:

``` text
RAW DATA
 ↓
DATA GUARDIAN
 ↓
NORMALIZATION
 ↓
FEATURES
 ↓
EVENTS
 ↓
RESEARCH / QUANT / VALIDATION
 ↓
PROGRAMMABLE VISUALIZATION
 ↓
MARKET CANVAS
 ↓
AI / MCP
```

**CryptoOS should be able to visualize almost anything its trusted
computation engines can deterministically produce, without turning the
UI into a second business-logic engine.**
