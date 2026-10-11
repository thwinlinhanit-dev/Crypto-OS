# Visualization Runtime — M13 Design Note (Market Canvas)

**Source:** `specs/CRYPTOOS_PROGRAMMABLE_VISUALIZATION_RUNTIME_SPEC_v1.0.md` §11 state, §12 canvas layers, §13 primitives, §14 heatmap, §31 performance, §30 testing, §36 M13.

**M13 scope:** layer manager · synchronized crosshair · HUD · heatmap · profile · layout persistence. **Gate:** performance + visual regression green.

**Builds on M12** (`258e385`): the §6 program/lifecycle vocabularies, the §9 data API + `truncate_as_of`, the §22 provenance classes, the §10 sandbox, the line/HUD/table renderers. M13 extends — never re-implements — that foundation.

## Reuse

| Need | Existing asset |
|---|---|
| replay/as_of gating | M12 `truncate_as_of` + `engine/scanner/replay.py` (D-055) |
| provenance on every layer | M12 `ProvenanceValue`/`classify` (§22) |
| quality fail-closed | M12 `quality_of` + `assert_quality_ok` (§9) |
| render-graph determinism | M12 `render_line/render_hud` (§13; PD-5) |
| immutable layers | M12 lifecycle table (§6); a PUBLISHED layer cannot silently change |
| time axis / bar close | `engine/scanner/replay.py::replay_bars` (D-008 close bound) |

## The new modules (engine/visualization/)

### `canvas.py` — the layer manager (§12)

- **`LAYERS`** — the §12 layer vocabulary: `PRICE, MICROSTRUCTURE, LIQUIDITY, DERIVATIVES, OPTIONS, ON_CHAIN, EVENTS, AI` (closed; a layer outside it is refused)
- **`Layer`** — name, kind (the §13 primitive), visibility, z-order, data binding, provenance; frozen
- **`LayerStack`** — the ordered layer list + `add/remove/visible_layers`; deterministic ordering (z-order then insertion order, D-040's deterministic-order lesson)
- **`validate_stack`** — fail-closed: an unknown layer, a duplicate z-order collision without a tiebreak, an unknown primitive kind, or a layer whose data binding is not declared is refused
- **§12 progressive disclosure:** `visible_layers()` returns only what is on — "do not render every dataset at once"

### `viewport.py` — the shared viewport + crosshair (§12)

- **`Viewport`** — time range + price range + zoom; the shared context every layer reads (time axis, price axis where relevant, viewport)
- **`clip`** — viewport clipping for a render graph (§14 required: "viewport clipping"): rows outside the viewport are dropped before the graph is built; the stored data never changes (zoom "may change rendering aggregation but never underlying stored data")
- **`Crosshair`** — the synchronized crosshair state (time + price + which layers are under it); `layers_at(crosshair)` resolves the layer set deterministically
- **`zoom_aggregate`** — §14's "zoom-dependent aggregation" + LOD: the render aggregation changes with zoom; a property test asserts the *stored rows* are untouched

### `heatmap.py` — the first-class heatmap (§14)

- **`HeatmapCell`** — (x=time, y=price, value) + provenance (the §15 derived-object envelope: timestamp, price range, size, persistence, source, dataset_version, provenance)
- **`sparsify`** — the §14 "sparse representation": adjacent identical values collapse; a property test asserts the sparsified grid renders identically to the dense one (§14: optimization never changes results)
- **`render_heatmap`** — the deterministic render graph (mode continuous/step; clipped to the viewport)
- **`aggregate_for_zoom`** — §14 zoom-dependent aggregation (a coarser zoom aggregates cells; the underlying cells are never mutated)

### `profile.py` — the volume/market profile (§13 Profile primitive)

- **`ProfileBin`** — price bin + value + provenance
- **`build_profile`** — the deterministic binning over a price series (fixed bin width or count; the width is a declared parameter, never inferred)
- **`render_profile`** — the render graph (histogram bars at each price bin)

### `layout.py` — layout persistence (§12; OQ-10's medium)

- **`Layout`** — the persisted canvas state: the layer stack + viewport + visible set; a versioned, hashable object (the M12 `program_hash` pattern)
- **`layout_hash`** — sha256 canonical JSON (D-083); a layout change is a new hash
- **OQ-10 carries over:** the §12 "layout persisted" store remains `localStorage` in the web app (D-012); the engine ships the serializable `Layout` contract, not the browser store

## Determinism & the gates

- **Determinism (§30):** every render graph is a pure function of (rows, viewport, layers, as_of) — same inputs → identical output.
- **Truncation invariance (§23):** `canvas(full, as_of=t) == canvas(truncate_at_t)` — asserted directly, reusing M12's `truncate_as_of`.
- **Performance (§31):** the §31 targets stay the API's; the engine's contract is **"optimizations must never change numerical results"** — asserted by the sparsification and zoom-aggregation property tests (sparse render == dense render; zoomed render == unzoomed over the same window).
- **Progressive disclosure (§12):** `visible_layers()` — the default UI never renders every dataset at once.

## Deferred (M14+ / spec §38.10)

- The **microstructure** layers (trades/CVD/footprint/orderbook — M14), **liquidity/derivatives** detail (M15), **options/GEX** (M15), **on-chain** (future) — the layer vocabulary *names* them, but M13 implements only the PRICE-layer primitives + the heatmap/profile/HUD the gate requires.
- The **OI-adjusted liquidation map** (§16) — needs the approved position model; M15.
- The **visual-regression** suite (§30 "Visual regression tests must cover the Market Canvas") — lands with the web canvas component; M13's engine ships the deterministic render graphs it will snapshot.
- The **§28 DB tables** — M13 ships the engine + tests (the same sequencing as M12); the API/canvas slice adds the migration.

## Decisions

- D-087: the canvas layer vocabulary is closed (§12); an unknown layer is refused before the stack is built.
- D-088: viewport clipping and zoom aggregation change only the *render graph*, never the stored rows (§14/§31) — asserted by property tests.
- D-089: the heatmap's sparse representation must render identically to the dense grid (§14 "optimization never changes results").
- D-090: M13 ships pure engine modules; the canvas UI component and the §28 DB tables land with the API/canvas slice.
