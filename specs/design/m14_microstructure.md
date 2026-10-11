# Visualization Runtime — M14 Design Note (Microstructure)

**Source:** `specs/CRYPTOOS_PROGRAMMABLE_VISUALIZATION_RUNTIME_SPEC_v1.0.md` §17 absorption, §18 footprint, §19 orderbook heatmap, §36 M14.

**M14 scope:** trades · CVD · delta · footprint · orderbook · liquidity zones. **Gate:** deterministic reconstruction and replay parity.

**Builds on M12/M13**: the §6 program/lifecycle vocabularies, the §9 data API + `truncate_as_of`, the §22 provenance classes, the §10 sandbox, the line/HUD/table renderers, and the M13 canvas layer manager + viewport clipping.

## The critical spec rule (§17)

> "Where actual taker-side data exists, use it rather than estimating side from candle direction."

The seeded parquet bars carry a **`trades` count** (Binance kline field) but **no `taker_buy_volume`/`taker_sell_volume`** — so M14 must **refuse to fabricate trade side**. The engine distinguishes:

- **exchange-side inputs present** (`taker_buy_volume` + `taker_sell_volume` per bucket) → the real taker decomposition, provenance `OBSERVED`;
- **absent** → the delta-dependent primitives raise `DATA_INCOMPLETE` (never estimated from candle direction, never silently zeroed).

This is the §3 "missing data must fail explicitly" rule applied to microstructure. The concept-level "aggressive buying + failure to advance → potential sell absorption" state is implemented as a **declared deterministic rule** with provenance `CALCULATED`, and the §17 warning is enforced: "A visualization heuristic is not automatically a formal Event Engine detector. Formal event logic requires `specs/events/<id>.md` under PD-4."

## Reuse

| Need | Existing asset |
|---|---|
| replay/as_of gating | M12 `truncate_as_of` + `engine/scanner/replay.py` (D-055/D-008) |
| provenance on every derived object | M12 `ProvenanceValue`/`classify` (§22) |
| quality fail-closed | M12 `quality_of` + `assert_quality_ok` (§9) |
| viewport clipping | M13 `clip` (D-088: render only) |
| the canvas layer + render graph | M13 `Layer`/`LayerStack` + `render_heatmap` (§13) |
| deterministic order | D-040 (M13's z-then-insertion) |
| immutable raw data | §19: "Raw orderbook data remains immutable. Derived heatmap state is versioned." |

## The new modules (engine/visualization/)

### `microstructure.py` — trades, CVD, delta (§17/§18)

- **`TradeSideInputs`** — the per-bucket exchange-side volumes (`taker_buy_volume`, `taker_sell_volume`, `volume`, `trades`); `from_rows` **refuses** a bucket set without both taker fields (`DATA_INCOMPLETE` — never estimated)
- **`cvd`** — the cumulative volume delta: `Σ(buy − sell)`, deterministic, provenance `CALCULATED` (it is a transformation of OBSERVED inputs)
- **`delta`** — the per-bucket `buy − sell`
- **`taker_decomposition`** — the side split; with real data → `OBSERVED`; without → refused
- **`render_cvd` / `render_delta`** — the deterministic render graphs (line primitives, viewport-clippable)

### `footprint.py` — the footprint (§18)

- **`FootprintCell`** — the §18 exposed fields (price, buy_volume, sell_volume, delta, total_volume, trade_count, imbalance) + provenance
- **`build_footprint`** — the per-price-cell aggregation from taker-side rows; **refuses without taker data** (§18: "Do not estimate trade side when reliable exchange-side data exists" — and we have none, so we refuse)
- **`imbalance`** — `(buy − sell) / (buy + sell)`; a zero denominator is `None`, never `0.0`
- **`render_footprint`** — the §18 modes (bid/ask, delta, volume, imbalance) as the deterministic render graph

### `orderbook.py` — the orderbook heatmap (§19)

- **`OrderbookSnapshot`** — price × displayed liquidity; the raw snapshot is **immutable** (frozen; §19)
- **`apply_ops`** — the §19 operations (add, remove) as an immutable fold: each op returns a NEW snapshot (the raw data is never mutated); derived heatmap state is versioned
- **`liquidity_walls`** — the §15 liquidity-wall detection (a price level whose displayed size exceeds a declared threshold × the window's median)
- **`migration` / `sweep` / `void`** — the §19 derived states; each is a pure function of two snapshots + a declared threshold
- **`render_orderbook_heatmap`** — the §19 price × time × liquidity heatmap (reusing M13's `render_heatmap` contract)

### `liquidity.py` — the liquidity zones (§15/§19)

- **`LiquidityZone`** — the §15 derived-object envelope (timestamp, price range, size, persistence, source, dataset_version, provenance) — the same envelope as the M13 heatmap cell
- **`detect_zones`** — the concentration/persistence detection from a sequence of snapshots (a level present in ≥ a declared fraction of the window); **no arbitrary score /100** (§3)
- **`absorption`** — the §17 concept state: aggressive buying + failure to advance → potential sell absorption; a declared deterministic rule over taker-side rows + price movement, provenance `CALCULATED`, with the §17 PD-4 warning enforced (a heuristic, never an event)

## Determinism & the gate

- **Deterministic reconstruction (§36 M14 gate):** every primitive is a pure function of (rows, params, as_of) — same inputs → identical output.
- **Replay parity (the M14 gate):** `microstructure(full, as_of=t) == microstructure(truncated_at_t)` — asserted directly via M12's `truncate_as_of`.
- **No fabricated side (§17/§18):** the delta-dependent primitives refuse taker-absent input rather than estimating from candle direction. This is the AC that distinguishes M14 from a naive CVD overlay.
- **Immutable raw orderbook (§19):** the ops fold returns new snapshots; a property test asserts the input snapshots are untouched.
- **PD-4:** the absorption state is a visualization heuristic, never an Event Engine detector; the docstring + a test assert it carries the warning.

## Deferred (M15+ / spec §38.10)

- The **derivatives** layers (OI/funding/liquidations, the OI-adjusted model — M15), **options/GEX** (M15), **on-chain** (future).
- The **orderbook data feed** itself (the §19 "raw orderbook data" source) — the engine consumes whatever the API supplies; the live feed is M15+.
- The **visual-regression** suite + the canvas UI (the API/canvas slice), the **§28 DB tables**.

## Decisions

- D-092: M14 refuses to fabricate trade side — the delta-dependent primitives raise `DATA_INCOMPLETE` when `taker_buy_volume`/`taker_sell_volume` are absent, never estimating from candle direction (§17/§18).
- D-093: the raw orderbook snapshot is immutable; the §19 operations fold into new snapshots, and the derived heatmap state is versioned.
- D-094: the §17 absorption state is a declared deterministic rule with provenance `CALCULATED` and the PD-4 warning enforced — a visualization heuristic, never an Event Engine detector.
- D-095: M14 ships pure engine modules; the microstructure data feed and the §28 DB tables land with the API/canvas slice.
