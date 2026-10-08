# CryptoOS — Master Product, Software Engineering & UI/UX Specification

**Version:** 1.0  
**Product:** CryptoOS — Personal Crypto Research & Quantitative Trading Operating System  
**Strategy:** Free-first, local-first, research-grade, MCP-first  

## 1. Product vision

CryptoOS is a personal crypto research operating system combining:

- Multi-exchange real-time market data
- Historical data and automatic backfill
- Data-quality monitoring and provenance
- Canonical cross-exchange normalization
- Order-flow and derivatives analytics
- Proprietary event detection
- Quantitative event studies
- Strategy development and backtesting
- Professional validation and robustness testing
- AI research agents
- MCP-first external agent access
- Research notebooks and reproducible experiments
- A chart-first professional terminal
- Later: paper trading, portfolio/risk, and live execution

The product should feel simple and elegant while the underlying system behaves like a small quantitative research platform.

### Product thesis

> CandL-style simplicity + professional charting + multi-exchange data + order flow + derivatives + proprietary events + institutional-style validation + AI research agents.

### What it is not

Do not make v1 a TradingView clone, exchange, social network, signal marketplace, HFT platform, or giant all-chain indexer.

---

# 2. Non-negotiable principles

1. **Data correctness before UI polish.**
2. **Raw data is immutable.**
3. **Every derived object has lineage.**
4. **Exchange-specific schemas never leak into research code.**
5. **No silent data gaps.**
6. **No silent strategy-definition changes.**
7. **No lookahead bias.**
8. **Every research result is reproducible.**
9. **AI never invents numerical evidence.**
10. **Live trading is disabled by default and requires explicit authorization.**
11. **Free/local infrastructure is the default; paid services are optional adapters.**
12. **Prefer simple infrastructure until scale proves complexity is necessary.**

---

# 3. Core user journey

```text
Market observation
      ↓
Question
      ↓
Data
      ↓
Hypothesis
      ↓
Event discovery
      ↓
Statistical research
      ↓
Strategy
      ↓
Backtest
      ↓
Robustness validation
      ↓
Risk analysis
      ↓
AI critique
      ↓
Paper trading
      ↓
Live trading
      ↓
Continuous monitoring
      ↓
New research
```

---

# 4. Product modules

## 4.1 Home

Market overview, active events, data health, research activity, strategy alerts and AI tasks.

Widgets:
- BTC/ETH/SOL state
- movers
- volatility
- funding extremes
- OI changes
- liquidations
- proprietary events
- data health
- recent research

## 4.2 Market Workspace

Primary terminal screen.

Features:
- symbol / venue / market selector
- timeframe selector
- professional chart
- indicators
- volume
- OI
- funding
- liquidations
- CVD
- footprint/order flow
- event markers
- drawings
- multi-panel layouts
- synchronized crosshair
- saved layouts
- replay

## 4.3 Scanner

Composable scans across symbols using price, return, volatility, RVOL, trend, regime, OI, funding, liquidation activity, CVD, orderbook imbalance and proprietary events.

Example:

```text
TIMEFRAME = 1D
AND REGIME = DOWN
AND CAPIT = TRUE
AND FAST_RECLAIM = TRUE
AND RVOL > 1.5
AND OI_CHANGE > 5%
```

## 4.4 Events

Central event database.

Initial taxonomy:

### Long/bottom side
- CAPIT
- FAST_RECLAIM
- CAPIT_RECLAIM
- LIQUIDATION_CASCADE
- LIQUIDITY_EXHAUSTION

### Short/top side
- TOP_CAPIT
- TOP_CLIMAX
- TOPFAIL
- CAPITFAIL
- EXHAUSTION_RECLAIM_FAILURE

### Market structure
- REGIME_CHANGE
- TREND_BREAK
- VOLATILITY_EXPANSION
- VOLATILITY_COMPRESSION
- RVOL_SPIKE
- OI_DISLOCATION
- FUNDING_EXTREME
- BASIS_DISLOCATION

## 4.5 Research

- event studies
- hypothesis database
- notebooks
- forward returns
- MFE/MAE
- historical analogues
- cross-symbol analysis
- regime conditioning
- reports

## 4.6 Quant

- Strategy Lab
- Backtesting
- Validation
- Experiments
- parameter sweeps
- walk-forward
- Monte Carlo
- bootstrap
- stress tests

## 4.7 AI

- AI Copilot
- Research Agent
- Quant Agent
- Data Agent
- Strategy Agent
- Risk Agent
- Critic Agent
- Writer Agent
- Developer Agent

## 4.8 Portfolio / Risk

Later-phase:
- paper portfolio
- positions
- PnL
- exposure
- correlation
- drawdown
- leverage
- risk limits

---

# 5. System architecture

```text
                         CRYPTOOS UI
                              │
                    ┌─────────▼─────────┐
                    │ API + MCP Gateway │
                    └─────────┬─────────┘
                              │
       ┌──────────────────────┼──────────────────────┐
       ▼                      ▼                      ▼
  MARKET ENGINE         RESEARCH ENGINE         QUANT ENGINE
       │                      │                      │
       └──────────────────────┼──────────────────────┘
                              ▼
                     EVENT / FEATURE ENGINE
                              │
                     DATA QUALITY GUARDIAN
                              │
                    NORMALIZATION LAYER
                              │
                     RAW INGESTION LAYER
                              │
       ┌──────────────┬───────┼────────┬──────────────┐
       ▼              ▼       ▼        ▼              ▼
    Binance         Bybit    OKX  Hyperliquid      Deribit
```

The most important architectural boundary is:

```text
Exchange-native data
        ↓
Exchange adapter
        ↓
Canonical CryptoOS data
        ↓
Everything above is exchange-agnostic
```

---

# 6. Exchange adapter specification

Every venue implements a common interface:

```python
class ExchangeAdapter:
    name: str

    async def connect(): ...
    async def disconnect(): ...
    async def subscribe_trades(symbols): ...
    async def subscribe_orderbook(symbols): ...
    async def subscribe_ticker(symbols): ...
    async def subscribe_liquidations(symbols): ...
    async def subscribe_oi(symbols): ...
    async def subscribe_funding(symbols): ...

    async def fetch_historical_klines(...): ...
    async def fetch_historical_trades(...): ...
    async def fetch_orderbook_snapshot(...): ...
    async def health(): ...
```

Adapter responsibilities:
- WebSocket protocol
- REST API
- symbol mapping
- market metadata
- heartbeat
- reconnect
- rate limits
- native error handling
- sequence numbers
- checksum logic
- native-to-canonical transformation

Initial adapters:
1. Binance Futures
2. Bybit
3. OKX
4. Hyperliquid

Second wave:
5. Coinbase
6. Kraken
7. Bitget
8. Deribit

---

# 7. Canonical market identity

Never allow the rest of the application to depend on raw exchange symbols such as `BTCUSDT`, `BTC-USD-SWAP`, or `XBTUSD`.

Canonical identity:

```json
{
  "asset": "BTC",
  "quote": "USD",
  "market_type": "PERPETUAL",
  "venue": "BINANCE",
  "native_symbol": "BTCUSDT"
}
```

Canonical market types:
- SPOT
- PERPETUAL
- FUTURE
- OPTION
- INDEX
- MARK

---

# 8. Immutable raw ingestion

Every raw event gets an envelope:

```json
{
  "event_id": "evt_...",
  "venue": "BINANCE",
  "native_symbol": "BTCUSDT",
  "event_type": "trade",
  "exchange_timestamp": 1760000000123,
  "received_timestamp": 1760000000158,
  "sequence": 82347293,
  "raw_payload_hash": "..."
}
```

Rules:
- Never overwrite raw events.
- Store source timestamps and receive timestamps separately.
- Keep a payload hash when practical.
- Preserve native identifiers.
- Record the adapter version.

---

# 9. Canonical trade schema

```json
{
  "event_id": "...",
  "venue": "BINANCE",
  "symbol": "BTC",
  "native_symbol": "BTCUSDT",
  "market_type": "PERPETUAL",
  "exchange_timestamp": 1760000000123,
  "received_timestamp": 1760000000158,
  "sequence": 82347293,
  "price": 124500.5,
  "quantity": 0.25,
  "side": "SELL",
  "trade_id": "...",
  "raw_hash": "..."
}
```

Trade side must follow a frozen canonical convention. Exchange-specific buyer/maker semantics are translated once at the adapter boundary.

---

# 10. Canonical orderbook schema

```json
{
  "venue": "BINANCE",
  "symbol": "BTC",
  "market_type": "PERPETUAL",
  "exchange_timestamp": 1760000000123,
  "received_timestamp": 1760000000158,
  "sequence_start": 100,
  "sequence_end": 100,
  "bids": [],
  "asks": [],
  "checksum": "..."
}
```

Orderbook engine must support:
- initial snapshot
- incremental updates
- sequence validation
- checksum validation
- local book reconstruction
- automatic resynchronization

---

# 11. Data Guardian

This is a first-class service, not a logging feature.

Responsibilities:
- heartbeat monitoring
- stale feed detection
- sequence validation
- gap detection
- duplicate detection
- timestamp validation
- reconnect tracking
- REST backfill
- orderbook resync
- checksum verification
- completeness scoring
- provenance
- quarantine of invalid data

Quality states:

```text
GREEN   reliable
YELLOW  degraded/thin
ORANGE  incomplete
RED     invalid/unavailable
```

Example:

```text
BTC / Binance Futures
Connection      GREEN
Freshness       GREEN
Sequence        GREEN
Completeness    99.998%
Backfill        3 repaired gaps
Orderbook       GREEN
Timestamp       GREEN
```

---

# 12. Backfill engine

When a WebSocket drops:

```text
LIVE
 ↓
disconnect
 ↓
detect gap
 ↓
reconnect
 ↓
REST backfill
 ↓
deduplicate
 ↓
sequence validation
 ↓
insert missing data
 ↓
verify
 ↓
mark repaired
 ↓
LIVE
```

Store each repaired interval:

```json
{
  "gap_id": "...",
  "venue": "BINANCE",
  "symbol": "BTC",
  "start": "...",
  "end": "...",
  "duration_ms": 15000,
  "source": "REST",
  "status": "REPAIRED"
}
```

Live ingestion must not stop while backfill runs.

---

# 13. Multi-venue aggregation

Keep venue-specific data and consolidated data separate.

Example:

```text
BTC/USD PERPETUAL
        │
  ┌─────┼─────┐
  ▼     ▼     ▼
Binance Bybit OKX
  │     │     │
  └─────┼─────┘
        ▼
Aggregation engine
```

Do not blindly average prices.

Maintain:
- venue price
- venue volume
- venue liquidity
- venue timestamp
- venue quality

Then calculate consolidated products using explicit formulas.

Volume can be summed when units and market semantics are compatible. Price aggregation must use a documented methodology.

---

# 14. Data products

## Market
- OHLCV
- returns
- VWAP
- moving averages
- gaps

## Volatility
- ATR
- realized volatility
- volatility percentile
- volatility regime

## Volume/order flow
- RVOL
- delta
- CVD
- footprint
- volume profile
- trade imbalance

## Derivatives
- open interest
- OI change
- funding
- basis
- liquidations
- liquidation imbalance

## Orderbook
- spread
- mid price
- microprice
- depth
- bid/ask imbalance
- liquidity concentration
- liquidity additions/removals
- orderbook pressure

---

# 15. Storage architecture

Recommended initial stack:

```text
PostgreSQL
  metadata + events + strategies + experiments + validation

Parquet
  high-volume market history and analytical datasets

DuckDB
  local analytical queries over Parquet

Filesystem/object storage
  exports, reports, dataset snapshots
```

Retention model:

```text
Raw ticks/orderbook deltas       short/medium
Derived market data              long
Events                           permanent
Strategies                       permanent
Experiments                      permanent
Validation reports               permanent
```

For personal v1, everything can run locally.

---

# 16. PostgreSQL domains

Suggested schemas:

### `market`
- venues
- instruments
- symbols
- contracts

### `research`
- events
- event_outcomes
- hypotheses
- experiments
- research_notes

### `strategy`
- strategies
- strategy_versions
- parameters
- signals
- trades
- backtests

### `validation`
- validation_runs
- walk_forward_runs
- monte_carlo_runs
- bootstrap_runs
- robustness_tests

### `agents`
- agents
- tasks
- runs
- tool_calls
- approvals

### `system`
- data_sources
- data_health
- ingestion_sessions
- gaps
- alerts
- audit_log

---

# 17. Data lineage

Every derived object must record:

```text
source
venue
symbol
period
raw dataset version
derived dataset version
feature version
event version
strategy version
engine version
parameters
```

Example:

```text
FAST_RECLAIM
Dataset: crypto_2026_10_07_v12
Feature engine: 2.4.1
Event engine: fast_reclaim_v5
Parameters: frozen config
```

---

# 18. Event engine

Every event definition is versioned.

Examples:

```text
FAST_RECLAIM v5
FAST_RECLAIM v6
```

must remain independently reproducible.

Core API:

```python
detect(
    symbol,
    timeframe,
    start,
    end,
    event_definition,
    dataset_version,
)
```

---

# 19. Canonical event object

```json
{
  "event_id": "evt_...",
  "event_type": "FAST_RECLAIM",
  "version": "5.0",
  "symbol": "BTC",
  "venue": "BINANCE",
  "market_type": "PERPETUAL",
  "timeframe": "1D",
  "direction": "LONG",
  "event_time": "2026-10-07T00:00:00Z",
  "price": 102400,
  "confidence": 0.84,
  "regime": "DOWN",
  "volatility_regime": "HIGH",
  "trigger": {},
  "risk": {"invalidation_price": 96100},
  "historical_evidence": {},
  "data_quality": {},
  "lineage": {}
}
```

Events are research objects, not merely chart markers.

---

# 20. Research engine

For every event, calculate:

- forward returns at 1/2/3/5/10/15/20/30/40/50 bars
- MFE
- MAE
- time to MFE
- time to MAE
- stop-out rate
- target-hit rate
- expectancy
- R distributions

Condition results by:
- year
- symbol
- timeframe
- regime
- volatility regime
- funding regime
- OI regime
- liquidity regime
- market-cap bucket

---

# 21. Strategy engine

Lifecycle:

```text
Hypothesis
 ↓
Definition
 ↓
Implementation
 ↓
Backtest
 ↓
Debug
 ↓
Validation
 ↓
Walk-forward
 ↓
Stress testing
 ↓
Paper trading
 ↓
Live
```

Strategy must define:
- entry
- exit
- stop
- target
- trailing logic
- position sizing
- leverage
- fees
- slippage
- funding
- execution assumptions

---

# 22. Backtesting engine

Must support:
- event-driven simulation
- vectorized research
- multi-symbol
- multi-timeframe
- long/short
- partial exits
- pyramiding
- fees
- slippage
- funding
- liquidation
- position sizing
- portfolio constraints

No future information may enter a decision.

---

# 23. Validation engine

Metrics:
- total return
- CAGR
- profit factor
- expectancy
- Sharpe
- Sortino
- maximum drawdown
- Calmar
- win rate
- average win/loss
- payoff ratio
- trade count
- exposure
- turnover

Robustness:
- parameter sensitivity
- walk-forward
- out-of-sample
- Monte Carlo
- bootstrap
- trade-order randomization
- fee/slippage stress
- delayed execution
- missing-data stress
- regime segmentation
- symbol leave-one-out
- year leave-one-out

---

# 24. Strategy dependency analysis

Always measure:
- top 1/5/10 trade contribution
- top symbol contribution
- top year contribution
- regime contribution
- long/short contribution
- volatility-regime contribution

Example:

```text
Top 5 trades      41.7%
BTC               19.3%
2022              31.1%
High-vol regime   58.4%

Status: WARNING
```

---

# 25. AI architecture

AI is an orchestration layer, never the source of truth.

Agents:

### Research Agent
Finds and synthesizes evidence.

### Quant Agent
Runs quantitative experiments.

### Data Agent
Investigates source quality, gaps and provenance.

### Strategy Agent
Creates and modifies strategy definitions.

### Risk Agent
Stress-tests strategies.

### Critic Agent
Attempts to falsify conclusions.

### Writer Agent
Creates research reports.

### Developer Agent
Builds software through governed tools.

---

# 26. MCP-first architecture

```text
Claude Code
Codex
Cursor
Cline
OpenCode
Other agents
      │
      ▼
     MCP
      │
      ▼
CryptoOS Tool Gateway
      │
 ┌────┼──────────────┐
 ▼    ▼              ▼
Data Research      Quant
API  API            API
```

Core tools:

```text
search_market
get_candles
get_trades
get_orderbook
get_funding
get_oi
get_liquidations

search_events
get_event
run_event_study

create_hypothesis
run_experiment

run_backtest
run_walk_forward
run_monte_carlo
validate_strategy

get_data_health
get_lineage

create_research_note
generate_report
```

All tool calls are auditable.

---

# 27. Agent permissions

Permission tiers:

```text
READ_ONLY
RESEARCH
PAPER_TRADE
LIVE_TRADE
```

Specific permissions:

```text
READ_DATA
WRITE_RESEARCH
RUN_BACKTEST
CREATE_STRATEGY
MODIFY_STRATEGY
RUN_PAPER_ORDER
RUN_LIVE_ORDER
```

Live execution always requires explicit approval.

LLMs must never receive exchange API secrets directly.

---

# 28. AI Copilot

Two modes.

## Explanation mode

Example:

> Why did BTC trigger FAST_RECLAIM?

Retrieve actual:
- event conditions
- price structure
- volume/RVOL
- OI
- funding
- liquidations
- historical analogues

## Research mode

Example:

> Test whether FAST_RECLAIM works better after capitulation.

The system creates a reproducible experiment and returns:

```text
Question
Dataset
Method
Sample
Result
Robustness
Limitations
Conclusion
```

Every numerical statement must be tool-backed.

---

# 29. Research notebook

Fields:
- title
- hypothesis
- question
- dataset
- code
- charts
- tables
- events
- backtests
- AI analysis
- conclusion
- status

Statuses:

```text
IDEA
TESTING
PROMISING
SUPPORTED
WEAK
REJECTED
VALIDATED
```

---

# 30. UI/UX philosophy

1. Chart-first
2. Dark-first
3. Information-dense but calm
4. Minimal visual noise
5. Fast navigation
6. Context preservation
7. Progressive disclosure
8. Evidence before opinion
9. Every metric drillable
10. Keyboard-first for power users

The UI should borrow the **design philosophy** of simple chart-first products without copying proprietary interfaces.

---

# 31. Design system

### Base colors

```text
Background  #080A0F
Surface     #0E1118
Elevated    #141923
Border      #252B36
Primary     #F3F5F7
Secondary   #A6ADB8
Muted       #69717D
Positive    #35D07F
Negative    #FF5C6C
Warning     #F5B942
Info        #5DA9FF
```

### Typography

- Inter / Geist / IBM Plex Sans
- JetBrains Mono for numerical values

### Spacing

```text
4  8  12  16  24  32
```

### Radius

```text
6  10  14  18
```

### Motion

```text
120ms fast
200ms normal
320ms slow
```

All components must use design tokens rather than arbitrary values.

---

# 32. Desktop layout

```text
┌──────────────────────────────────────────────────────────────┐
│ CRYPTOOS   Research Analyze Trade  Search          AI ●     │
├──────┬───────────────────────────────────────────┬───────────┤
│ NAV  │                                           │ EVENT     │
│      │                 CHART                     │ ANALYSIS  │
│ Home │                                           │           │
│ Chart│             BTCUSDT · 1D                  │ FAST      │
│ Scan │                                           │ RECLAIM   │
│Events│                                           │           │
│      │                                           │ 84 / 100  │
│Research                                          │ Evidence  │
│Strategy                                          │           │
│Backtest                                          │ AI        │
│AI                                                │           │
└──────┴───────────────────────────────────────────┴───────────┘
```

Suggested dimensions:
- topbar 48–56px
- left navigation 64–220px
- right context panel 320–420px

Panels should collapse.

---

# 33. Main navigation

```text
HOME

MARKETS
  Market Workspace
  Scanner
  Watchlist
  Events

RESEARCH
  Research
  Notebook
  On-chain
  Derivatives
  Macro
  Sentiment

QUANT
  Strategy Lab
  Backtesting
  Validation
  Experiments

PORTFOLIO
  Portfolio
  Positions
  Risk

AI
  AI Copilot
  Research Agent
  Quant Agent
  Agent Runs

SYSTEM
  Data Health
  Data Sources
  Settings
```

---

# 34. Chart workspace

Top controls:

```text
BTCUSDT   BINANCE   PERPETUAL   1D
```

Secondary controls:

```text
Indicators   Events   Drawing   Compare   Layout   Replay
```

Chart layers:
1. price
2. indicators
3. event markers
4. volume
5. OI
6. funding
7. liquidations
8. CVD
9. order flow
10. strategy overlays

---

# 35. Event UX

Marker:

```text
       FAST RECLAIM
            ▲
────────────●──────────
          CAPIT
```

Hover card:

```text
FAST_RECLAIM
Confidence 84
Sample 31
Median forward +2.4R

[Open Evidence]
```

Click opens Event Analysis.

---

# 36. Event Analysis panel

Sections:

### Header
```text
FAST RECLAIM
BTCUSDT · 1D
84 / 100
```

### Why triggered
```text
✓ Capitulation detected
✓ Reclaim confirmed
✓ RVOL elevated
✓ Downtrend weakening
✓ Liquidity improving
```

### Historical evidence
```text
Sample       31
Win rate     71%
Median R     +2.4R
Median MFE   +3.8R
Median MAE   -0.9R
```

### Invalidation
```text
Price < $96,100
```

### Historical analogues
Clickable list.

---

# 37. Scanner UX

Columns:

```text
Symbol | Price | 24h | RVOL | OI Δ | Funding | Regime | Event | Confidence
```

Requirements:
- sorting
- filtering
- saved scans
- virtualized rows
- live updates
- click row → chart
- preserve scanner state after navigation

---

# 38. Data Health UX

```text
DATA HEALTH

Exchange      Connection   Latency   Completeness   Status
Binance       LIVE         18ms      99.999%        GREEN
Bybit         LIVE         31ms      99.997%        GREEN
OKX           LIVE         47ms      99.981%        GREEN
Hyperliquid   LIVE         24ms      99.992%        GREEN
```

Drill-down:
- reconnects
- gaps
- backfills
- sequence errors
- stale feeds
- API errors
- raw source metadata

---

# 39. Backtest UX

Workflow:

```text
1 Strategy
2 Universe
3 Timeframe
4 Period
5 Costs
6 Execution
7 Run
8 Results
9 Validation
```

Results:
- equity curve
- drawdown
- monthly returns
- trade distribution
- symbol contribution
- regime contribution
- MFE/MAE
- trade list

---

# 40. Validation dashboard

Example:

```text
STRATEGY VALIDATION

Overall: PROMISING / NOT YET VALIDATED

Statistical Edge       PASS
OOS Performance        PASS
Parameter Stability    PASS
Trade Dependency       WARNING
Regime Dependency      WARNING
Cost Sensitivity       PASS
Monte Carlo             PASS
```

Do not reduce validation to a single opaque score.

---

# 41. Research dashboard

Research cards show:
- hypothesis
- sample
- method
- result
- confidence
- status

Example:

```text
FAST_RECLAIM after CAPIT
Sample: 147
Median 10-bar: +2.1R
Win rate: 67.4%
Status: PROMISING
```

---

# 42. Mobile UX

Mobile is not a shrunken desktop terminal.

Primary mobile screens:
1. Watchlist
2. Market detail
3. Event detail
4. Alerts
5. Portfolio
6. AI Copilot
7. Research summaries

Desktop remains primary for deep research.

---

# 43. Component library

### Navigation
- Sidebar
- Topbar
- Command palette
- Breadcrumb
- Tabs

### Data
- Data table
- Metric card
- Sparkline
- Status badge
- Heatmap
- Timeline

### Trading
- Price selector
- Timeframe selector
- Position card
- Order ticket (later)

### Research
- Event card
- Evidence panel
- Experiment card
- Research notebook
- Validation report

### AI
- Chat panel
- Agent status
- Tool-call card
- Research result
- Evidence/source card

Every component needs loading, empty, error and stale-data states.

---

# 44. Command palette and keyboard UX

`Ctrl/Cmd + K` opens command palette.

Commands:
- Open BTC
- Search event
- Run backtest
- Open scanner
- Create research note
- Ask AI
- Open data health
- Run event study

Suggested shortcuts:

```text
Ctrl/Cmd + K   Command palette
/              Search
G C            Chart
G S            Scanner
G R            Research
G B            Backtest
G V            Validation
G A            AI
Esc            Close
```

---

# 45. API architecture

```text
Frontend
   ↓
API Gateway
   ↓
Domain services
```

REST domains:

```text
/api/market
/api/data
/api/events
/api/research
/api/strategies
/api/backtests
/api/validation
/api/portfolio
/api/agents
/api/system
```

WebSocket channels:

```text
/ws/market
/ws/orderbook
/ws/events
/ws/scanner
/ws/agent
/ws/system
```

---

# 46. Technology stack

## Frontend
- Next.js
- React
- TypeScript
- Tailwind CSS
- shadcn/ui or equivalent foundation
- Lightweight Charts initially
- Zustand or equivalent
- TanStack Query

## Backend
- Python for research/quant/data work
- FastAPI for APIs
- async WebSocket ingestion
- optional Go/Rust ingestion components later if proven necessary

## Data
- PostgreSQL
- Parquet
- DuckDB

## Infrastructure
- Docker
- Docker Compose initially
- Kubernetes only when justified by scale

---

# 47. Repository structure

```text
cryptoos/
├── apps/
│   ├── web/
│   ├── api/
│   └── worker/
├── packages/
│   ├── ui/
│   ├── schemas/
│   ├── config/
│   └── client/
├── services/
│   ├── ingestion/
│   ├── data_guardian/
│   ├── normalization/
│   ├── aggregation/
│   ├── features/
│   ├── events/
│   ├── backtesting/
│   ├── validation/
│   └── agents/
├── exchanges/
│   ├── binance/
│   ├── bybit/
│   ├── okx/
│   ├── hyperliquid/
│   └── ...
├── research/
│   ├── notebooks/
│   ├── experiments/
│   └── reports/
├── data/
│   ├── raw/
│   ├── normalized/
│   ├── derived/
│   └── exports/
├── infra/
│   ├── docker/
│   └── migrations/
└── docs/
```

---

# 48. Performance targets

## UI
- application shell <2 seconds on a normal desktop
- chart interactions target <50ms
- scanner updates without full-page rerender
- virtualize large tables

## Data
- live ingestion must not block UI
- backfill must not stop live ingestion
- derived calculations should run asynchronously

## Storage
- indexed symbol/timeframe/timestamp queries
- partition large datasets
- compressed Parquet
- batch writes

---

# 49. Reliability targets

Initial engineering targets:

```text
Data ingestion          99.9%+
API availability        99.9%+
Research reproducibility 100%
Silent corruption       0
Silent WebSocket gaps   0
```

Every failure must be observable.

---

# 50. Security

- secrets only in secure server-side configuration
- no exchange keys in frontend
- separate read-only and trading credentials
- encrypted secret storage
- IP restrictions where available
- audit log
- explicit live-trading approval
- agent permissions

---

# 51. Trading safety

Modes:

```text
RESEARCH
PAPER
LIVE
```

Live requires explicit user confirmation.

Never allow an LLM to independently decide to place live orders.

---

# 52. Testing strategy

## Unit
Every adapter, normalization function, indicator, feature and event detector.

## Integration

```text
exchange → adapter → canonical event → storage
```

## Replay

Record real exchange streams and replay them deterministically.

The same recording must produce the same:
- candles
- features
- events

## Data quality tests
Inject:
- missing messages
- duplicates
- out-of-order events
- bad timestamps
- disconnects
- sequence gaps

Verify recovery.

---

# 53. Golden dataset

Maintain a frozen test dataset, e.g.:

```text
BTC
2024-01-01 → 2024-03-01
```

Freeze expected:
- candle results
- event timestamps
- event count
- strategy trades

Every engine change must pass golden-data tests.

---

# 54. Experiment reproducibility

Each experiment records:

```text
experiment_id
dataset_version
code_version
parameters
random_seed
created_at
author/agent
```

Stochastic experiments use a fixed seed unless randomness is itself being tested.

---

# 55. Observability

System health screen:

```text
Ingestion          ●
Data Guardian      ●
Feature Engine     ●
Event Engine       ●
Backtest Worker    ●
API                ●
WebSocket Gateway  ●
AI Gateway         ●
```

Metrics:
- messages/sec
- events/sec
- latency
- queue depth
- reconnects
- gaps
- backfills
- CPU
- memory
- disk
- DB size

---

# 56. Alerts

System alerts:
- exchange disconnected
- feed stale
- gap detected
- backfill failed
- database unavailable
- disk nearly full
- event engine failure
- validation complete
- unusual market event

Initial channels:
- in-app
- Telegram
- optional email

---

# 57. Zero-cost / free-first strategy

Initial software and market-data target:

```text
Public exchange WebSockets    $0
Public exchange REST          $0
PostgreSQL                    $0
DuckDB                        $0
Parquet                       $0
React/Next.js                 $0
FastAPI                       $0
Docker                        $0
Local computation             $0
```

Possible future costs:
- VPS/cloud compute
- storage
- premium market feeds
- premium on-chain APIs
- LLM usage

All paid services should be optional adapters.

---

# 58. Development roadmap

## Phase 0 — Foundation

- monorepo
- Docker Compose
- PostgreSQL
- DuckDB
- Parquet structure
- configuration
- logging
- API skeleton
- UI shell

## Phase 1 — Data foundation

- Binance
- Bybit
- OKX
- Hyperliquid
- WebSockets
- REST
- reconnect
- backfill
- normalization
- Data Guardian

## Phase 2 — Chart terminal

- chart
- symbols
- timeframes
- indicators
- volume
- OI
- funding
- liquidations

## Phase 3 — Order flow

- trades
- CVD
- footprint
- volume profile
- orderbook
- imbalance
- heatmap

## Phase 4 — Event engine

- CAPIT
- FAST_RECLAIM
- TOP_CAPIT
- TOP_CLIMAX
- TOPFAIL
- CAPITFAIL
- regime
- RVOL
- liquidation events

## Phase 5 — Research

- event studies
- MFE/MAE
- forward returns
- hypotheses
- notebooks

## Phase 6 — Backtesting

- strategy engine
- trade simulator
- fees
- slippage
- funding
- portfolio

## Phase 7 — Validation

- walk-forward
- Monte Carlo
- bootstrap
- sensitivity
- dependency analysis
- stress tests

## Phase 8 — AI

- AI Copilot
- Research Agent
- Quant Agent
- Data Agent
- Critic Agent
- Writer Agent

## Phase 9 — MCP

- MCP gateway
- data tools
- research tools
- backtest tools
- validation tools
- permission system
- audit

## Phase 10 — Paper trading

- portfolio
- positions
- PnL
- risk

## Phase 11 — Live trading

Only after all safety and validation gates pass.

---

# 59. MVP definition

Do not wait for the complete vision.

The real MVP is:

```text
Binance + Bybit
      ↓
WebSocket
      ↓
Data Guardian
      ↓
Normalized candles/trades/OI/funding
      ↓
PostgreSQL + Parquet
      ↓
Chart
      ↓
Scanner
      ↓
CAPIT / FAST_RECLAIM
      ↓
Event analysis
```

MVP success means the system can stream reliable data, detect gaps, repair gaps, display the data, detect a proprietary event, and explain it.

---

# 60. AI-agent engineering workflow

```text
SPEC
 ↓
TASK
 ↓
IMPLEMENT
 ↓
UNIT TEST
 ↓
INTEGRATION TEST
 ↓
REPLAY/GOLDEN TEST
 ↓
REVIEW
 ↓
MERGE
```

Specialist agents:
- architect
- frontend
- backend
- data
- quant
- UI/UX
- DevOps
- testing
- security
- research

Agents should work through scoped tasks rather than broad uncontrolled changes.

---

# 61. Definition of Done

A feature is complete only when:

- implementation exists
- tests exist
- error handling exists
- telemetry exists
- documentation exists
- data lineage is handled
- UI loading state exists
- UI empty state exists
- UI error state exists
- stale/partial state exists where relevant
- MCP exposure is defined where relevant

---

# 62. UX state rules

Every screen must support:

### Loading
Skeleton/progressive loading.

### Empty
Explain what to do next.

### Error
Human-readable explanation and retry.

### Offline
Clearly show stale data.

### Partial
Never silently hide incomplete data.

Example:

```text
⚠ Data incomplete
BTCUSDT
14:32:01–14:32:14 unavailable
[Repair]
```

---

# 63. Research integrity

CryptoOS must distinguish:

```text
OBSERVED
CALCULATED
INFERRED
AI-GENERATED
```

Example:

```text
Observed:
Funding = +0.021%

Calculated:
Funding percentile = 97th

Inferred:
Crowded-long condition

AI interpretation:
Historically associated with ...
```

---

# 64. Event evidence graph

```text
EVENT
 │
 ├── Price evidence
 ├── Volume evidence
 ├── OI evidence
 ├── Funding evidence
 ├── Liquidation evidence
 ├── Regime evidence
 └── Historical evidence
```

Every evidence item should be drillable to underlying data/chart.

---

# 65. Signal quality model

Do not collapse everything into one misleading probability.

Separate:

```text
Signal strength
Data quality
Historical evidence
Model confidence
```

Example:

```text
Signal strength       84/100
Data quality          99.9%
Historical evidence   MEDIUM-HIGH
Model confidence      MEDIUM
```

---

# 66. AI research report format

Every report should contain:

```text
TITLE
QUESTION
HYPOTHESIS
DATASET
METHOD
SAMPLE
RESULTS
STATISTICAL EVIDENCE
ROBUSTNESS
FAILURE CASES
LIMITATIONS
CONCLUSION
NEXT EXPERIMENT
```

The Critic Agent should actively search for evidence against the hypothesis.

---

# 67. Future advanced modules

After v1:
- cross-exchange liquidity maps
- cross-venue arbitrage research
- options surface
- IV/skew/term structure
- on-chain integration
- wallet/entity tracking
- macro
- sentiment/news
- factor models
- portfolio optimizer
- regime classifier
- ML feature store
- online learning
- strategy ensembles
- execution simulator
- smart order routing

These are not MVP requirements.

---

# 68. First engineering sprint

The first sprint should produce:

### Infrastructure
- monorepo
- Docker Compose
- PostgreSQL
- DuckDB
- Parquet layout
- environment configuration
- logging

### Data
- Binance adapter
- Bybit adapter
- canonical trade schema
- canonical candle schema
- WebSocket reconnect
- REST backfill
- data-health service

### API
- `/market`
- `/candles`
- `/trades`
- `/health`

### UI
- app shell
- sidebar
- topbar
- chart workspace
- symbol selector
- timeframe selector
- data-status indicator

### Tests
- adapter tests
- normalization tests
- replay test
- gap-recovery test

### Sprint acceptance criterion

> CryptoOS can stream real market data, store it correctly, detect gaps, repair gaps, and display the result on a professional chart.

---

# 69. Final architecture

```text
                         ┌─────────────────────┐
                         │     CRYPTOOS UI     │
                         │                     │
                         │ Chart • Scanner     │
                         │ Research • AI       │
                         │ Strategy • Risk     │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │       API / MCP     │
                         └──────────┬──────────┘
                                    │
       ┌────────────────────────────┼───────────────────────────┐
       │                            │                           │
┌──────▼──────┐             ┌──────▼──────┐             ┌──────▼──────┐
│   MARKET    │             │  RESEARCH   │             │    QUANT    │
│   ENGINE    │             │   ENGINE    │             │   ENGINE    │
└──────┬──────┘             └──────┬──────┘             └──────┬──────┘
       │                            │                           │
       └────────────────────────────┼───────────────────────────┘
                                    │
                         ┌──────────▼──────────┐
                         │   EVENT / FEATURE   │
                         │       ENGINE        │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │ DATA QUALITY GUARDIAN│
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │ NORMALIZATION /     │
                         │ MULTI-VENUE LAYER   │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │   RAW INGESTION     │
                         └──────────┬──────────┘
                                    │
             ┌──────────────────────┼──────────────────────┐
             ▼                      ▼                      ▼
          Binance                 Bybit                   OKX
       Hyperliquid              Coinbase               Deribit
```

---

# 70. North-star user experience

Example question:

> Why did BTC trigger FAST_RECLAIM?

CryptoOS should answer from actual system evidence:

```text
FAST_RECLAIM · BTC · 1D

Signal strength:       84/100
Data quality:          99.98%
Historical sample:     147

WHY
✓ Capitulation detected
✓ Reclaim confirmed
✓ RVOL 2.1x
✓ OI flushed
✓ Funding normalized
✓ Liquidity recovered

HISTORICAL
Median 10-bar:         +2.1R
Win rate:              67.4%
Median MFE:             +3.8R
Median MAE:             -0.9R

REGIME
Current:               High-vol Downtrend
Best historical regime: High-vol Downtrend

RISKS
⚠ 31% of profit from BTC
⚠ sample weak in low-volume regime

AI RESEARCH AGENT
Evidence supports the signal, but I would not treat this as
validated until low-volume and symbol-concentration tests pass.

[Run Robustness Test]
[Open Historical Analogues]
[Backtest Strategy]
[Create Research Note]
```

---

# 71. Success criteria

CryptoOS v1 succeeds when the user can:

1. Open the terminal.
2. Select BTC/ETH/SOL or another supported market.
3. See reliable real-time and historical data.
4. See data-health status.
5. Inspect order flow and derivatives.
6. Detect proprietary events.
7. Click an event and see its evidence.
8. Query historical analogues.
9. Run an event study.
10. Create a strategy from a hypothesis.
11. Backtest it.
12. Run robustness validation.
13. Ask an AI agent to critique it.
14. Save the research.
15. Reproduce the experiment later.
16. Connect an external AI coding/research agent through MCP.

If these work reliably, the project is a real **Crypto Research OS**, not merely a dashboard.

---

# 72. Absolute build order

```text
1. Data schemas
2. Exchange adapters
3. Raw ingestion
4. Data Guardian
5. Backfill
6. Normalization
7. Storage
8. Basic API
9. Chart
10. Derived features
11. Events
12. Scanner
13. Research engine
14. Backtesting
15. Validation
16. AI Copilot
17. MCP
18. Paper trading
19. Portfolio/risk
20. Live trading
21. Advanced data sources
```

Do not reverse this order merely because UI work is more exciting.

---

# 73. Final engineering principle

> **CryptoOS is a trustworthy data and research engine with a beautiful terminal on top — not a beautiful terminal with a data engine underneath.**

The real product is:

```text
DATA
 ↓
TRUST
 ↓
EVIDENCE
 ↓
RESEARCH
 ↓
VALIDATION
 ↓
DECISION
```

That foundation should govern every future CryptoOS capability.
