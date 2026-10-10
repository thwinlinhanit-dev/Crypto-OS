# M10 design note — Perp derivatives + live candles

Companion to IMPLEMENTATION_SPEC_v1.1.1 Sections 4.5/4.7/4.8/7.7/11/15
and the M10 milestone row.

Decisions recorded in `docs/DECISIONS.md` as D-067..D-074; open questions
OQ-26.

> Scope discipline (PD-9/PD-10): M10 builds the perp adapter (funding, OI,
> liquidations), the live kline WebSocket with reconnect/gap detection/REST
> backfill/forming-bar updates, and the funding cost model. Spot requests
> before M10 already return NOT_IMPLEMENTED (R2/4.8); perp now becomes
> available — **never** substituted for spot in either direction (PD-10).

---

## 0. Acceptance-criteria mapping

| AC (M10) | Where it lands |
| --- | --- |
| killing the socket mid-bar yields a detected gap, automatic repair and GREEN state; no silent gaps | `engine/exchanges/perp.py` (funding/OI/liquidations) + `engine/market/live.py` (the WebSocket supervisor) + migration 0010 (`market.funding_rate`, `market.open_interest`, `market.liquidation`, `system.gap` already exists from M1) + `tests/test_live_ws.py` (the AC: kill-mid-bar → gap detected → REST backfill repairs → GREEN) |

---

## 1. Engine layout

```
engine/exchanges/perp.py    Binance Perp adapter: funding history, OI, liquidations
engine/market/live.py       the live kline WebSocket supervisor (T10.2)
engine/strategy/funding.py  the funding cost model (T10.3)
```

## 2. Perp adapter (T10.1, §4.5/§7.7)

`ExchangeAdapter` (Master §6) extended with the perp methods (R2: Binance
only; Bybit/OKX/Hyperliquid = v1.1 via the same interface):

- `fetch_funding_rates(instrument, from, to)` — funding every 8h; persisted
  to `market.funding_rate` (instrument_id, ts, rate).
- `fetch_open_interest(instrument, tf)` — OI history; `market.open_interest`.
- `fetch_liquidations(instrument, from, to)` — forced liquidations;
  `market.liquidation`.
- `fetch_instrument_metadata` extended with the perp fields (contract
  type, funding interval, settlement).

Fail-closed (PD-10): a perp request on a spot instrument (or vice versa)
errors; perp requests before M10 would have returned NOT_IMPLEMENTED —
that guard is lifted here and replaced by the market-type checks.

## 3. Live kline WebSocket (T10.2, §11)

`engine/market/live.py` supervises the Binance kline WebSocket:

- **Reconnect** with capped exponential backoff (jittered; never a tight
  loop — R6: rate-limit aware).
- **Gap detection**: on every disconnect or missed update, compare the
  last closed-bar time against the socket's next expected bar; a jump is a
  gap, inserted into `system.gap` (M1's table: `status IN
  ('OPEN','REPAIRED','UNREPAIRABLE')`, `detected_at`, `repaired_at`) —
  **never silently ignored** (the AC: no silent gaps; PD-10).
- **REST backfill**: `fetch_historical_klines` refills the gap, marks the
  gap `REPAIRED` and the data-quality row GREEN (the AC: automatic
  repair and GREEN state).
- **Forming-bar updates**: the current bar is emitted as an update event
  (`forming: true`); the bar is **never** written to the store until it
  closes (§17: "the forming bar never feeds features, events or
  backtests").
- The WebSocket front end `GET /v1/ws` exposes
  `candle.{instrument}.{tf}` (spec §11 line 531), plus `events.new`,
  `jobs.{id}`, `data.health`.

## 4. Funding cost model (T10.3, §7.7)

`engine/strategy/funding.py`: funding accrues every 8h while a position is
open; the cost model charges the position's notional at the funding rate
(positive rate = longs pay shorts). The M6 simulator takes an optional
funding schedule and deducts it from the trade's realized PnL; the backtest
records the total funding paid per trade (D-041: append-only, part of the
experiment's lineage).

**SHORT-side enablement** (T10.3): the FAIL/TOP (and CAPIT-family) short
strategies become runnable **only** on perp instruments (R2/§7.7); the
strategy registry rejects a SHORT-side strategy on spot (fail-closed,
PD-10).

## 5. API + UI (spec §11, §12)

```
GET  /v1/funding/{instrument}      funding history
GET  /v1/oi/{instrument}           open interest
GET  /v1/liquidations/{instrument} liquidations
WebSocket /v1/ws                    candle.{instrument}.{tf}, events.new, jobs.{id}, data.health
```

The chart gains perp sub-panes (funding, OI, liquidations) under the
candles (spec §12 "derivatives/OI/funding/liquidation overlays"); the
Strategy Lab enables SHORT strategies on perp instruments only.

## 6. Out of scope (parked)

- Bybit/OKX/Hyperliquid adapters (v1.1 via the same ExchangeAdapter
  interface).
- Orderbook heatmap / footprint / CVD visualizations (the Master §9
  reference; must NOT copy the proprietary implementation — spec line
  101).

## 7. Test plan (tests first)

| Suite | Covers |
| --- | --- |
| `tests/test_perp_adapter.py` | funding/OI/liquidation fetch + persist; spot↔perp fail-closed |
| `tests/test_live_ws.py` | reconnect backoff; **kill mid-bar → gap detected → REST backfill → REPAIRED → GREEN**; forming bar never stored |
| `tests/test_funding_model.py` | 8h accrual; longs pay at positive rates; total funding recorded per trade; SHORT on perp only |

## 8. Decisions to record

| ID | Decision |
| --- | --- |
| D-067 | Perp methods live on the same ExchangeAdapter interface (R2); Bybit/OKX = v1.1 |
| D-068 | Gaps are rows in system.gap (OPEN → REPAIRED), never silent skips (PD-10) |
| D-069 | The forming bar is an update event, never a store write (§17) |
| D-070 | Funding accrues every 8h on notional; positive rate = longs pay shorts |
| D-071 | SHORT-side strategies run on perp only; spot rejects them (fail-closed) |
| D-072 | The live supervisor backs off with jittered capped exponential (never a tight loop) |
| D-073 | The WebSocket front end is `/v1/ws` with the spec §11 channels |
| D-074 | Perp sub-panes render funding/OI/liquidations under the candles (§12) |

Open questions for the owner: **OQ-26** — the perp universe (which
`*USDT` perpetuals beyond BTC to subscribe live; defaults to BTC for
development).
