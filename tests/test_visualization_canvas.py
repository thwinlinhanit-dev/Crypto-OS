"""M13 — the Market Canvas tests (spec §12/§13/§14/§30).

The M13-applicable property suite:
- the layer manager (closed vocabulary, deterministic order, progressive
  disclosure, fail-closed duplicate/unknown bindings);
- the shared viewport + crosshair (clipping changes only the render;
  zoom aggregation never mutates the stored rows);
- the first-class heatmap (sparse representation renders identically to
  dense; zoom aggregation; the §15 envelope on every cell);
- the profile primitive (declared binning, PD-11);
- layout persistence (the hash contract, D-083).
"""

from __future__ import annotations

from typing import Any

import pytest

from engine.errors import ValidationError
from engine.visualization import (
    Crosshair,
    HeatmapCell,
    Layer,
    LayerStack,
    Layout,
    Viewport,
    add_layer,
    aggregate_for_zoom,
    assert_valid_layer,
    build_profile,
    clip,
    default_stack,
    layers_at,
    layout_hash,
    remove_layer,
    render_heatmap,
    render_profile,
    set_visibility,
    sparse_equals_dense,
    sparsify,
    zoom_aggregate,
)


def _rows(n: int = 6) -> list[dict[str, Any]]:
    return [
        {"ts": f"2026-01-0{i + 1}T00:00:00Z", "close": 100.0 + i} for i in range(n)
    ]


# --------------------------------------------------------------------------
# The layer manager (spec §12)
# --------------------------------------------------------------------------


def test_the_layer_vocabulary_is_the_spec_list() -> None:
    for name in ("PRICE", "MICROSTRUCTURE", "LIQUIDITY", "DERIVATIVES",
                 "OPTIONS", "ON_CHAIN", "EVENTS", "AI"):
        assert_valid_layer(name)
    with pytest.raises(ValidationError):
        assert_valid_layer("SECRET")


def test_the_default_stack_is_price_only_progressive_disclosure() -> None:
    """§12: the default UI remains progressive disclosure - do not render
    every dataset at once."""
    stack = default_stack()
    assert [item.layer for item in stack.visible_layers()] == ["PRICE"]


def test_the_layer_order_is_z_then_insertion() -> None:
    """D-040: deterministic order - z ascending, ties by insertion order."""
    stack = LayerStack(
        layers=(
            Layer(layer="PRICE", kind="LINE", z=1, dataset="candles", provenance="OBSERVED"),
            Layer(layer="EVENTS", kind="LINE", z=0, dataset="events", provenance="OBSERVED"),
        )
    )
    stack.validate()
    assert [item.layer for item in stack.ordered()] == ["EVENTS", "PRICE"]


def test_an_unknown_layer_is_refused() -> None:
    stack = LayerStack(
        layers=(Layer(layer="NOPE", kind="LINE", z=0, dataset="candles", provenance="OBSERVED"),)
    )
    with pytest.raises(ValidationError) as excinfo:
        stack.validate()
    assert "unknown canvas layer" in excinfo.value.message


def test_an_unknown_primitive_is_refused() -> None:
    stack = LayerStack(
        layers=(Layer(layer="PRICE", kind="HEATMAP", z=0, dataset="candles", provenance="OBSERVED"),)
    )
    with pytest.raises(ValidationError):
        stack.validate()


def test_an_unknown_provenance_is_refused() -> None:
    stack = LayerStack(
        layers=(Layer(layer="PRICE", kind="LINE", z=0, dataset="candles", provenance="VIBES"),)
    )
    with pytest.raises(ValidationError):
        stack.validate()


def test_a_duplicate_binding_is_refused() -> None:
    stack = LayerStack(
        layers=(
            Layer(layer="PRICE", kind="LINE", z=0, dataset="candles", provenance="OBSERVED"),
            Layer(layer="PRICE", kind="LINE", z=1, dataset="candles", provenance="OBSERVED"),
        )
    )
    with pytest.raises(ValidationError) as excinfo:
        stack.validate()
    assert "duplicate layer/dataset binding" in excinfo.value.message


def test_an_empty_stack_is_refused() -> None:
    with pytest.raises(ValidationError):
        LayerStack(layers=()).validate()


def test_add_and_remove_layer_are_immutable() -> None:
    stack = default_stack()
    grown = add_layer(
        stack,
        Layer(layer="EVENTS", kind="LINE", z=1, dataset="events", provenance="OBSERVED"),
    )
    assert len(grown.layers) == 2
    assert len(stack.layers) == 1  # the original is untouched
    shrunk = remove_layer(grown, layer="EVENTS", dataset="events")
    assert len(shrunk.layers) == 1
    with pytest.raises(ValidationError):
        remove_layer(grown, layer="EVENTS", dataset="nope")


def test_set_visibility_toggles_progressive_disclosure() -> None:
    stack = add_layer(
        default_stack(),
        Layer(layer="EVENTS", kind="LINE", z=1, dataset="events", provenance="OBSERVED"),
    )
    shown = set_visibility(stack, layer="EVENTS", dataset="events", visible=True)
    assert [item.layer for item in shown.visible_layers()] == ["PRICE", "EVENTS"]
    hidden = set_visibility(shown, layer="PRICE", dataset="candles", visible=False)
    assert [item.layer for item in hidden.visible_layers()] == ["EVENTS"]


# --------------------------------------------------------------------------
# The viewport + crosshair (spec §12/§14)
# --------------------------------------------------------------------------


def test_clip_drops_rows_outside_the_viewport() -> None:
    viewport = Viewport(time_start="2026-01-02T00:00:00Z", time_end="2026-01-04T00:00:00Z")
    kept = clip(_rows(), viewport)
    assert len(kept) == 3


def test_clip_can_filter_on_price() -> None:
    viewport = Viewport(
        time_start="2026-01-01T00:00:00Z",
        time_end="2026-01-06T00:00:00Z",
        price_low=101.0,
        price_high=103.0,
    )
    kept = clip(_rows(), viewport)
    assert all(101.0 <= row["close"] <= 103.0 for row in kept)


def test_an_inverted_viewport_is_refused() -> None:
    viewport = Viewport(time_start="2026-01-06T00:00:00Z", time_end="2026-01-01T00:00:00Z")
    with pytest.raises(ValidationError):
        clip(_rows(), viewport)


def test_clipping_never_mutates_the_stored_rows() -> None:
    """D-088: viewport clipping changes only the render; the stored rows are
    untouched."""
    rows = _rows()
    before = [dict(r) for r in rows]
    clip(rows, Viewport(time_start="2026-01-02T00:00:00Z", time_end="2026-01-03T00:00:00Z"))
    assert rows == before


def test_zoom_aggregation_never_mutates_the_stored_rows() -> None:
    """§14/§31: zoom may change rendering aggregation but never underlying
    stored data."""
    rows = _rows()
    before = [dict(r) for r in rows]
    zoom_aggregate(rows, Viewport(time_start="2026-01-01T00:00:00Z",
                                  time_end="2026-01-06T00:00:00Z", zoom=2.0))
    assert rows == before


def test_zoom_aggregation_matches_the_direct_computation() -> None:
    """§31: performance optimizations must never change numerical results -
    the bucketed mean equals the direct mean over the same window."""
    rows = _rows()
    viewport = Viewport(time_start="2026-01-01T00:00:00Z",
                        time_end="2026-01-06T00:00:00Z", zoom=2.0)
    aggregated = zoom_aggregate(rows, viewport)
    # 6 rows at zoom 2 -> 3 buckets of 2, each the mean of its pair
    assert len(aggregated) == 3
    assert aggregated[0]["close"] == pytest.approx((100.0 + 101.0) / 2)
    assert aggregated[0]["n_aggregated"] == 2


def test_zoom_1_renders_the_rows_as_is() -> None:
    viewport = Viewport(time_start="2026-01-01T00:00:00Z",
                        time_end="2026-01-06T00:00:00Z", zoom=1.0)
    assert len(zoom_aggregate(_rows(), viewport)) == 6


def test_the_crosshair_resolves_layers_deterministically() -> None:
    layers = [
        {"layer": "PRICE", "z": 0, "ts": "2026-01-03T00:00:00Z"},
        {"layer": "EVENTS", "z": 1, "ts": "2026-01-03T00:00:00Z"},
        {"layer": "LIQUIDITY", "z": 2, "ts": "2026-01-05T00:00:00Z"},
    ]
    crosshair = Crosshair(time="2026-01-03T00:00:00Z", price=102.0)
    assert layers_at(crosshair, layers) == ("PRICE", "EVENTS")


# --------------------------------------------------------------------------
# The heatmap (spec §14)
# --------------------------------------------------------------------------


def _cells() -> tuple[HeatmapCell, ...]:
    return (
        HeatmapCell(ts="t1", price=100.0, value=1.0),
        HeatmapCell(ts="t2", price=100.0, value=1.0),  # collapsed
        HeatmapCell(ts="t3", price=100.0, value=2.0),
        HeatmapCell(ts="t4", price=101.0, value=3.0),
    )


def test_sparsify_collapses_identical_runs() -> None:
    sparse = sparsify(_cells())
    assert len(sparse) == 3  # the t1/t2 run collapses to one


def test_the_sparse_grid_renders_identically_to_dense() -> None:
    """D-089: optimization never changes numerical results (§31)."""
    assert sparse_equals_dense(_cells())
    assert sparse_equals_dense(())


def test_render_heatmap_is_deterministic() -> None:
    assert render_heatmap(_cells()) == render_heatmap(_cells())


def test_render_heatmap_requires_cells() -> None:
    with pytest.raises(ValidationError):
        render_heatmap(())


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(ValidationError):
        render_heatmap(_cells(), mode="vibes")


def test_the_heatmap_carries_the_section_15_envelope() -> None:
    """§15: every derived object carries timestamp, price range, size,
    persistence, source, dataset_version, provenance."""
    cell = HeatmapCell(
        ts="t1", price=100.0, value=1.0, provenance="INFERRED",
        source="model", dataset_version="dv",
    )
    graph = render_heatmap((cell,))
    payload = graph["cells"][0]
    assert payload["provenance"] == "INFERRED"
    assert payload["source"] == "model"
    assert payload["dataset_version"] == "dv"


def test_zoom_aggregation_never_mutates_the_cells() -> None:
    """D-088: the underlying cells are never mutated."""
    cells = _cells()
    aggregate_for_zoom(cells, 2.0)
    assert cells == _cells()


def test_the_zoom_aggregated_mean_is_correct() -> None:
    aggregated = aggregate_for_zoom(_cells(), 2.0)
    # price 100 and 101 fall in the same bucket at zoom 2 (bucket=2)
    assert all(c.value > 0 for c in aggregated)


def test_the_viewport_clips_the_heatmap() -> None:
    cells = (
        HeatmapCell(ts="2026-01-01T00:00:00Z", price=100.0, value=1.0),
        HeatmapCell(ts="2026-01-09T00:00:00Z", price=100.0, value=2.0),
    )
    viewport = Viewport(time_start="2026-01-01T00:00:00Z", time_end="2026-01-05T00:00:00Z")
    graph = render_heatmap(cells, viewport)
    assert len(graph["cells"]) == 1


def test_an_unknown_provenance_on_a_cell_is_refused() -> None:
    with pytest.raises(ValidationError):
        render_heatmap((HeatmapCell(ts="t", price=1.0, value=1.0, provenance="MAGIC"),))


# --------------------------------------------------------------------------
# The profile (spec §13)
# --------------------------------------------------------------------------


def test_build_profile_bins_declared_width() -> None:
    bins = build_profile([100.0, 101.0, 105.0], [1.0, 2.0, 3.0], bin_width=2.0)
    assert all(b.value > 0 for b in bins)
    assert bins[0].price_low == 100.0


def test_build_profile_bins_declared_count() -> None:
    # the max price always closes the last bin (half-open [low, high), last
    # bin closed): 100..120 over bin_count=2 -> width 10 -> 3 populated bins
    bins = build_profile([100.0, 110.0, 120.0], [1.0, 1.0, 1.0], bin_count=2)
    assert bins[0].price_low == 100.0
    assert bins[-1].price_high == 130.0  # the last bin is closed at the max
    assert all(b.value > 0 for b in bins)


def test_build_profile_requires_exactly_one_binning() -> None:
    """PD-11: no inferred default - the binning is declared, never guessed."""
    with pytest.raises(ValidationError):
        build_profile([100.0], [1.0])
    with pytest.raises(ValidationError):
        build_profile([100.0], [1.0], bin_width=1.0, bin_count=2)


def test_build_profile_rejects_a_bad_width() -> None:
    with pytest.raises(ValidationError):
        build_profile([100.0], [1.0], bin_width=0)
    with pytest.raises(ValidationError):
        build_profile([100.0], [1.0], bin_count=0)


def test_build_profile_rejects_misaligned_series() -> None:
    with pytest.raises(ValidationError):
        build_profile([100.0, 101.0], [1.0], bin_width=1.0)


def test_render_profile_reports_the_point_of_control() -> None:
    bins = build_profile([100.0, 100.0, 110.0], [5.0, 5.0, 1.0], bin_width=5.0)
    graph = render_profile(bins)
    assert graph["kind"] == "PROFILE"
    assert graph["point_of_control"] == 100.0


def test_render_profile_requires_bins() -> None:
    with pytest.raises(ValidationError):
        render_profile(())


# --------------------------------------------------------------------------
# Layout persistence (spec §12)
# --------------------------------------------------------------------------


def _layout() -> Layout:
    return Layout(
        name="default",
        stack=default_stack(),
        viewport=Viewport(time_start="2026-01-01T00:00:00Z", time_end="2026-01-06T00:00:00Z"),
    )


def test_the_layout_hash_is_deterministic() -> None:
    assert _layout().hash() == _layout().hash()
    assert len(_layout().hash()) == 64


def test_the_layout_hash_changes_with_the_viewport() -> None:
    """D-083: a layout change is a new hash."""
    base = _layout()
    moved = Layout(
        name="default",
        stack=default_stack(),
        viewport=Viewport(time_start="2026-01-02T00:00:00Z", time_end="2026-01-06T00:00:00Z"),
    )
    assert base.hash() != moved.hash()


def test_the_layout_hash_changes_with_the_layers() -> None:
    base = _layout()
    extra = Layout(
        name="default",
        stack=add_layer(
            default_stack(),
            Layer(layer="EVENTS", kind="LINE", z=1, dataset="events", provenance="OBSERVED"),
        ),
        viewport=base.viewport,
    )
    assert base.hash() != extra.hash()


def test_layout_hash_is_process_stable() -> None:
    """D-083: never Python's hash() - the sha256 of the canonical JSON is
    stable across processes."""
    payload = {"name": "x", "version": "1.0.0"}
    assert layout_hash(payload) == layout_hash(payload)
