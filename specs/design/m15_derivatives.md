# Visualization Runtime — M15 Design Note (Derivatives)

**Source:** `specs/CRYPTOOS_PROGRAMMABLE_VISUALIZATION_RUNTIME_SPEC_v1.0.md` §15 liquidity, §16 OI-adjusted liquidation map, §21 options, §36 M15.

**M15 scope:** OI · funding · liquidations · liquidation model · options interface/GEX. **Gate:** explicit model assumptions and numerical fixtures.

**Builds on M12/M13/M14**: the §6/§9/§10/§22 foundation, the canvas layer manager + viewport, the microstructure primitives, the §15 derived-object envelope.

## The critical spec rules (§16)

1. **The liquidation map is MODEL-DERIVED.** "The exact position distribution/liquidation model requires an approved model specification before research use."
2. **The UI must state:** `MODEL-DERIVED — NOT EXCHANGE-OBSERVED`.
3. **"Estimated liquidation levels are never labeled as observed exchange orders."**

So M15 implements the map as a **declared, versioned model with every assumption explicit** (the §21 rule: "Model assumptions must be visible and versioned"):
- the leverage ladder (10x/25x/50x/100x) is a declared input, never inferred;
- the maintenance-margin schedule is a declared input with a named source;
- **the OI→position-size assumption is a declared assumption with a stated limitation** — OI is total open interest, not a per-position distribution; the map is a *scenario surface*, not a prediction;
- **every level carries provenance `INFERRED`** and the model version, and the render graph stamps `MODEL-DERIVED — NOT EXCHANGE-OBSERVED` (the §16 banner);
- the funding accrual reuses the M10 `funding_charges` convention (D-070: positive rate = longs pay, entry-exclusive/exit-inclusive).

**Exchange-observed** (the actual perp feed) stays `OBSERVED`: the funding-rate series, the OI series, the actual liquidation orders. Only the *estimated map* is `INFERRED`.

## Reuse

| Need | Existing asset |
|---|---|
| funding/OI/liquidation shapes | `engine/exchanges/perp.py` `FundingRate`/`OpenInterest`/`Liquidation` (M10) |
| the funding convention | `engine/strategy/funding.py` D-070 (positive = longs pay) |
| the OI change series | M10 `fetch_open_interest`; the visualization derives `oi_change_pct` |
| provenance / quality / replay | M12 `ProvenanceValue`, `quality_of`, `truncate_as_of` |
| the derived-object envelope | M13/M14 `LiquidityZone`/heatmap cell (§15) |
| deterministic render | M13 `render_heatmap` (price × value) |

## The new modules (engine/visualization/)

### `derivatives.py` — OI + funding (the observed series)

- **`OiPoint`** — (ts, oi) + provenance OBSERVED
- **`oi_change_pct`** — the per-bar OI change % (a CALCULATED transformation of the OBSERVED series)
- **`render_oi`** — the deterministic render graph (the line primitive)
- **`FundingPoint`** — (ts, rate) + provenance OBSERVED; the §16/M10 convention (positive = longs pay)
- **`funding_series`** — the cumulative funding accrual for a declared position (reusing the M10 `funding_charges` boundary convention: entry-exclusive, exit-inclusive)
- **`render_funding`** — the deterministic render graph

### `liquidation_model.py` — the OI-adjusted liquidation map (§16)

- **`LiquidationModelSpec`** — the versioned, explicit model: the leverage ladder, the maintenance-margin schedule (with its named source), the OI→size assumption, the version. **Every assumption is a field; an empty spec is refused.**
- **`estimated_levels`** — the map: for each leverage, the estimated liquidation price from the entry price + the maintenance margin; provenance **INFERRED**, each level stamped with the model version
- **`render_liquidation_map`** — the render graph carrying the **§16 banner** (`MODEL-DERIVED — NOT EXCHANGE-OBSERVED`) and the model spec
- **`assert_model_declared`** — fail-closed: a map without a declared leverage ladder / margin schedule / version is refused (the §21 "assumptions must be visible" rule)
- **The declared limitation** (in the docstring + the model spec): OI is total open interest, not a per-position distribution; the map is a scenario surface, not a prediction.

### `options.py` — the options interface (§21, the future layer)

§21 is explicitly the *future* layer ("live options chain → …"). M15 ships the **interface contract only** (spec §38.10): the normalization + pricing hook points as an abstract, data-less shape whose absence is an explicit `NOT_IMPLEMENTED` — never a fabricated GEX. The options chain feed is M16+.

- **`OptionsModelSpec`** — the versioned interface (the pricing model name + version + the declared assumptions)
- **`normalize_chain`** — refuses a non-empty claim without the feed (the future layer)
- **`render_gamma_curve`** — the deterministic render graph skeleton for when the feed exists (the gamma-flip/max-pain outputs are named, not computed)

## Determinism & the gate

- **Explicit model assumptions (§36 M15 gate):** `LiquidationModelSpec` requires the leverage ladder, the margin schedule, the version — a map cannot be built without them.
- **Numerical fixtures (the M15 gate):** the `estimated_levels` computation is pinned by a numerical fixture test (a known entry price + margin + leverage → the exact expected level), so the model's arithmetic is regression-locked.
- **The §16 banner:** the render graph stamps `MODEL-DERIVED — NOT EXCHANGE-OBSERVED` on every map; a test asserts it.
- **Provenance separation:** observed (funding/OI/actual liquidations) vs INFERRED (the map) — the §22 classes stay distinguishable.
- **Replay parity:** the observed series are as_of-gated (M12 `truncate_as_of`).

## Deferred (M16+ / spec §38.10)

- The **live options chain feed** + the real pricing/Greeks/GEX (§21) — the interface ships, the computation does not.
- The **AI visualization** (M16), **research bridge** (M17).
- The **visual-regression** suite + the canvas UI, the **§28 DB tables**.

## Decisions

- D-097: the liquidation map is a versioned, explicitly-declared model (leverage ladder + margin schedule + version, all required); every level is INFERRED and the render graph stamps the §16 banner.
- D-098: OI is total open interest, not a per-position distribution — the map is a scenario surface, and that limitation is declared in the model spec (not buried).
- D-099: the funding accrual reuses the M10 `funding_charges` convention (D-070) rather than a second implementation.
- D-100: the options layer ships the interface contract only; the chain feed + pricing/Greeks/GEX are M16+ (§38.10).
