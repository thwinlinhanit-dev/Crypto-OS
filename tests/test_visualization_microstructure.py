"""M14 — the microstructure tests (spec §17/§18/§19/§36 M14).

The M14 gate: deterministic reconstruction and replay parity. The
centerpiece AC (D-092): the delta-dependent primitives REFUSE to fabricate
trade side - a bucket set without exchange-side taker volumes raises
DATA_INCOMPLETE, never estimating from candle direction (§17/§18).
"""

from __future__ import annotations

import pytest

from engine.errors import ValidationError
from engine.visualization import (
    OrderbookSnapshot,
    TradeBucket,
    absorption,
    apply_ops,
    build_footprint,
    cvd,
    delta,
    detect_zones,
    liquidity_walls,
    migration,
    render_cvd,
    render_delta,
    render_footprint,
    render_orderbook_heatmap,
    sweep,
    taker_decomposition,
    void,
)


def _taker_buckets(n: int = 4) -> list[TradeBucket]:
    """Exchange-side taker data present (the real §17 case)."""
    return [
        TradeBucket(
            ts=f"2026-01-0{i + 1}T00:00:00Z",
            price=100.0 + i,
            volume=10.0 + i,
            trades=5 + i,
            taker_buy_volume=6.0 + i,
            taker_sell_volume=4.0,
        )
        for i in range(n)
    ]


def _bare_buckets(n: int = 2) -> list[TradeBucket]:
    """The seeded parquet shape: a trades count, no taker volumes (the §17
    "do not estimate side from candle direction" case)."""
    return [
        TradeBucket(ts=f"2026-01-0{i + 1}T00:00:00Z", price=100.0 + i,
                    volume=10.0, trades=5)
        for i in range(n)
    ]


# --------------------------------------------------------------------------
# The no-fabricated-side rule (spec §17/§18, D-092)
# --------------------------------------------------------------------------


def test_cvd_refuses_without_taker_side() -> None:
    """The M14 AC: trade side is never estimated from candle direction."""
    with pytest.raises(ValidationError) as excinfo:
        cvd(_bare_buckets())
    assert "not estimated" in excinfo.value.message


def test_delta_refuses_without_taker_side() -> None:
    with pytest.raises(ValidationError):
        delta(_bare_buckets())


def test_taker_decomposition_refuses_without_taker_side() -> None:
    with pytest.raises(ValidationError):
        taker_decomposition(_bare_buckets())


def test_footprint_refuses_without_taker_side() -> None:
    """§18: "Do not estimate trade side when reliable exchange-side data
    exists." None exists, so the primitive refuses rather than fabricating."""
    with pytest.raises(ValidationError):
        build_footprint(_bare_buckets())


def test_absorption_refuses_without_taker_side() -> None:
    with pytest.raises(ValidationError):
        absorption(_bare_buckets())


# --------------------------------------------------------------------------
# The microstructure computations (with real taker data)
# --------------------------------------------------------------------------


def test_delta_is_buy_minus_sell() -> None:
    series = delta(_taker_buckets())
    assert series[0] == ("2026-01-01T00:00:00Z", 2.0)


def test_cvd_is_the_cumulative_delta() -> None:
    series = cvd(_taker_buckets())
    # deltas: 2, 3, 4, 5 -> cumulative 2, 5, 9, 14
    assert [value for _, value in series] == [2.0, 5.0, 9.0, 14.0]


def test_taker_decomposition_returns_buy_and_sell() -> None:
    series = taker_decomposition(_taker_buckets())
    assert series[0] == ("2026-01-01T00:00:00Z", 6.0, 4.0)


def test_render_cvd_is_deterministic() -> None:
    assert render_cvd(_taker_buckets()) == render_cvd(_taker_buckets())


def test_render_cvd_gates_at_as_of() -> None:
    """§23 replay parity: no data after as_of is visible."""
    graph = render_cvd(_taker_buckets(), as_of="2026-01-02T00:00:00Z")
    assert len(graph["points"]) == 2


def test_replay_parity_the_m14_gate() -> None:
    """§36 M14 gate: deterministic reconstruction and replay parity -
    rendering the full series with as_of=t equals rendering the truncated
    series."""
    full = render_cvd(_taker_buckets(), as_of="2026-01-02T00:00:00Z")
    truncated = render_cvd([b for b in _taker_buckets() if b.ts <= "2026-01-02T00:00:00Z"])
    assert full["points"] == truncated["points"]


def test_render_delta_is_deterministic() -> None:
    assert render_delta(_taker_buckets()) == render_delta(_taker_buckets())


def test_an_empty_cvd_render_is_refused() -> None:
    with pytest.raises(ValidationError):
        render_cvd([])


# --------------------------------------------------------------------------
# The footprint (spec §18)
# --------------------------------------------------------------------------


def test_footprint_cells_expose_the_section_18_fields() -> None:
    cells = build_footprint(_taker_buckets())
    cell = cells[0].as_dict()
    for field_name in ("price", "buy_volume", "sell_volume", "delta",
                       "total_volume", "trade_count", "imbalance"):
        assert field_name in cell


def test_footprint_aggregates_by_price() -> None:
    cells = build_footprint(_taker_buckets() + _taker_buckets())
    assert cells[0].trade_count == 10  # two buckets at price 100


def test_the_imbalance_is_none_not_zero_on_a_zero_denominator() -> None:
    """No fabricated value: a zero total volume is None, never 0.0."""
    from engine.visualization import FootprintCell

    cell = FootprintCell(price=100.0, buy_volume=0.0, sell_volume=0.0, trade_count=0)
    assert cell.imbalance is None


def test_the_imbalance_is_the_signed_ratio() -> None:
    from engine.visualization import FootprintCell

    cell = FootprintCell(price=100.0, buy_volume=6.0, sell_volume=4.0, trade_count=1)
    assert cell.imbalance == pytest.approx(0.2)


def test_render_footprint_modes() -> None:
    cells = build_footprint(_taker_buckets())
    for mode in ("bid_ask", "delta", "volume", "imbalance"):
        graph = render_footprint(cells, mode=mode)
        assert graph["kind"] == "FOOTPRINT"
        assert graph["mode"] == mode
    with pytest.raises(ValidationError):
        render_footprint(cells, mode="vibes")


# --------------------------------------------------------------------------
# The orderbook heatmap (spec §19)
# --------------------------------------------------------------------------


def _snapshot(ts: str = "t0") -> OrderbookSnapshot:
    return OrderbookSnapshot(
        ts=ts,
        levels=((100.0, 10.0), (101.0, 5.0), (102.0, 20.0)),
        dataset_version="dv",
        source="binance",
    )


def test_the_snapshot_is_immutable() -> None:
    """§19: "Raw orderbook data remains immutable" (D-093)."""
    snapshot = _snapshot()
    with pytest.raises(ValidationError):
        apply_ops(snapshot, [{"op": "remove", "price": 999.0, "size": 1.0}])


def test_apply_ops_never_mutates_the_input() -> None:
    """D-093: the ops fold into NEW snapshots; the input is untouched."""
    snapshot = _snapshot()
    before = snapshot.as_dict()
    grown = apply_ops(snapshot, [{"op": "add", "price": 100.0, "size": 5.0}])
    assert snapshot.as_dict() == before
    assert grown.levels[0] == (100.0, 15.0)


def test_apply_ops_remove_deletes_a_fully_consumed_level() -> None:
    snapshot = _snapshot()
    shrunk = apply_ops(snapshot, [{"op": "remove", "price": 101.0, "size": 5.0}])
    assert 101.0 not in [p for p, _ in shrunk.levels]


def test_an_unknown_op_is_refused() -> None:
    with pytest.raises(ValidationError):
        apply_ops(_snapshot(), [{"op": "vibes", "price": 1.0}])


def test_liquidity_walls_use_a_declared_threshold() -> None:
    """§15: a declared threshold, never an inferred score /100."""
    walls = liquidity_walls(_snapshot(), threshold=2.0)
    # the median of [5, 10, 20] is 10; 2x = 20 -> only the 102 level
    assert walls == (102.0,)


def test_a_non_positive_threshold_is_refused() -> None:
    with pytest.raises(ValidationError):
        liquidity_walls(_snapshot(), threshold=0)


def test_migration_reports_the_changed_levels() -> None:
    before = _snapshot("t0")
    after = apply_ops(before, [{"op": "add", "price": 102.0, "size": 5.0}])
    moved = migration(before, after)
    assert moved == ((102.0, 20.0, 25.0),)


def test_sweep_returns_the_levels_beyond_the_price() -> None:
    assert sweep(_snapshot(), price=101.0) == ((101.0, 5.0), (102.0, 20.0))


def test_void_reports_the_absent_levels() -> None:
    before = _snapshot("t0")
    after = apply_ops(before, [{"op": "remove", "price": 100.0, "size": 10.0}])
    assert void(before, after) == (100.0,)


def test_detect_zones_uses_a_declared_persistence() -> None:
    a = _snapshot("t0")
    b = apply_ops(a, [{"op": "add", "price": 100.0, "size": 1.0}])
    zones = detect_zones([a, b], min_persistence=0.5)
    assert any(z.price_low == 100.0 for z in zones)
    zone = zones[0].as_dict()
    for field_name in ("price_low", "price_high", "size", "persistence",
                       "source", "dataset_version", "provenance"):
        assert field_name in zone


def test_a_bad_persistence_is_refused() -> None:
    with pytest.raises(ValidationError):
        detect_zones([_snapshot()], min_persistence=1.5)


def test_render_orderbook_heatmap_is_price_time_liquidity() -> None:
    graph = render_orderbook_heatmap([_snapshot("t0"), _snapshot("t1")])
    assert graph["kind"] == "HEATMAP"
    assert len(graph["cells"]) == 6  # 3 levels x 2 snapshots
    assert {"ts", "price", "value"} <= set(graph["cells"][0])


# --------------------------------------------------------------------------
# The absorption heuristic (spec §17; PD-4)
# --------------------------------------------------------------------------


def test_absorption_detects_the_concept_state() -> None:
    """§17: aggressive buying + failure to advance → potential sell
    absorption."""
    buckets = [
        TradeBucket(ts="t0", price=100.0, volume=10.0, trades=5,
                    taker_buy_volume=6.0, taker_sell_volume=4.0),
        TradeBucket(ts="t1", price=100.0, volume=10.0, trades=5,
                    taker_buy_volume=8.0, taker_sell_volume=2.0),  # aggressive, no advance
        TradeBucket(ts="t2", price=105.0, volume=10.0, trades=5,
                    taker_buy_volume=9.0, taker_sell_volume=1.0),  # advanced: not absorption
    ]
    signals = absorption(buckets)
    # t0 (aggressive, the first bar has no prior to advance from) and t1
    # (aggressive, no advance) are both absorption; t2 advanced, so it is not
    assert [s.ts for s in signals] == ["t0", "t1"]
    assert signals[1].aggressive_buy == 6.0
    assert signals[1].price_change == 0.0


def test_every_absorption_signal_is_a_heuristic_not_a_detector() -> None:
    """D-094/PD-4: a visualization heuristic is not automatically a formal
    Event Engine detector."""
    buckets = [
        TradeBucket(ts="t0", price=100.0, volume=1.0, trades=1,
                    taker_buy_volume=6.0, taker_sell_volume=1.0),
        TradeBucket(ts="t1", price=100.0, volume=1.0, trades=1,
                    taker_buy_volume=6.0, taker_sell_volume=1.0),
    ]
    signals = absorption(buckets)
    assert signals
    assert all(s.heuristic_only for s in signals)
    assert all(s.as_dict()["provenance"] == "CALCULATED" for s in signals)


def test_a_negative_advance_threshold_is_refused() -> None:
    with pytest.raises(ValidationError):
        absorption(_taker_buckets(), advance_threshold=-1.0)


def test_an_empty_absorption_set_is_empty() -> None:
    assert absorption([]) == ()
