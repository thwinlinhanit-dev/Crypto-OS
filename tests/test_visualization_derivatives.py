"""M15 — the derivatives tests (spec §16/§21/§36 M15).

The M15 gate: explicit model assumptions and numerical fixtures. The
centerpiece ACs: the liquidation map is a versioned, explicitly-declared
model; every level is INFERRED; the render graph stamps MODEL-DERIVED ---
NOT EXCHANGE-OBSERVED; and the map's arithmetic is pinned by a numerical
fixture so the model cannot silently drift.
"""

from __future__ import annotations

import pytest

from engine.errors import ValidationError
from engine.visualization import (
    MODEL_DERIVED_BANNER,
    FundingPoint,
    LiquidationModelSpec,
    OiPoint,
    OptionsModelSpec,
    estimated_levels,
    funding_series,
    maintenance_rate,
    normalize_chain,
    oi_change_pct,
    render_funding,
    render_gamma_curve,
    render_liquidation_map,
    render_oi,
)


def _spec() -> LiquidationModelSpec:
    return LiquidationModelSpec(version="1.0.0")


# --------------------------------------------------------------------------
# The liquidation model spec (spec §16/§21)
# --------------------------------------------------------------------------


def test_the_default_spec_declares_every_assumption() -> None:
    """§21: model assumptions must be visible and versioned."""
    spec = _spec()
    spec.validate()
    assert spec.version == "1.0.0"
    assert spec.leverage_ladder == (10, 25, 50, 100)
    assert spec.margin_source
    assert "not a per-position distribution" in spec.limitation


def test_a_spec_without_a_version_is_refused() -> None:
    spec = LiquidationModelSpec(version="")
    with pytest.raises(ValidationError):
        spec.validate()


def test_a_spec_without_a_ladder_is_refused() -> None:
    spec = LiquidationModelSpec(version="1.0.0", leverage_ladder=())
    with pytest.raises(ValidationError):
        spec.validate()


def test_a_spec_without_a_margin_source_is_refused() -> None:
    """§21: the maintenance-margin schedule must name its source."""
    spec = LiquidationModelSpec(version="1.0.0", margin_source="")
    with pytest.raises(ValidationError):
        spec.validate()


def test_a_bad_margin_tier_is_refused() -> None:
    spec = LiquidationModelSpec(
        version="1.0.0", maintenance_schedule=((50_000, 1.5),)
    )
    with pytest.raises(ValidationError):
        spec.validate()


# --------------------------------------------------------------------------
# The numerical fixture (spec §36 M15 gate)
# --------------------------------------------------------------------------


def test_the_estimated_level_matches_the_numerical_fixture() -> None:
    """The §36 M15 gate: a numerical fixture pins the model's arithmetic.

    A long at entry 100, notional 50,000 (the first tier's mmr = 0.004):
    - 10x:  100 * (1 - 0.1 + 0.004)  = 90.4
    - 25x:  100 * (1 - 0.04 + 0.004) = 96.4
    - 50x:  100 * (1 - 0.02 + 0.004) = 98.4
    - 100x: 100 * (1 - 0.01 + 0.004) = 99.4
    """
    levels = estimated_levels(_spec(), entry_price=100.0, side="LONG", notional=50_000.0)
    assert [level.liquidation_price for level in levels] == pytest.approx(
        [90.4, 96.4, 98.4, 99.4]
    )


def test_the_short_side_mirrors_the_long() -> None:
    """A short at 100, mmr 0.004: 100 * (1 + 1/L - mmr)."""
    levels = estimated_levels(_spec(), entry_price=100.0, side="SHORT", notional=50_000.0)
    assert levels[0].liquidation_price == pytest.approx(100.0 * (1.0 + 0.1 - 0.004))


def test_every_level_is_inferred_and_stamped_with_the_model_version() -> None:
    """Estimated liquidation levels are never labeled as observed exchange
    orders (spec section 16)."""
    levels = estimated_levels(_spec(), entry_price=100.0)
    assert all(level.provenance == "INFERRED" for level in levels)
    assert all(level.model_version == "1.0.0" for level in levels)


def test_a_bad_side_is_refused() -> None:
    with pytest.raises(ValidationError):
        estimated_levels(_spec(), entry_price=100.0, side="FLAT")


def test_a_bad_entry_is_refused() -> None:
    with pytest.raises(ValidationError):
        estimated_levels(_spec(), entry_price=0.0)
    with pytest.raises(ValidationError):
        estimated_levels(_spec(), entry_price=100.0, notional=0.0)


def test_the_map_cannot_be_built_without_a_declared_spec() -> None:
    """§16: the exact model requires an approved specification - a map
    without one is refused."""
    with pytest.raises(ValidationError):
        estimated_levels(
            LiquidationModelSpec(version="", leverage_ladder=()), entry_price=100.0
        )


# --------------------------------------------------------------------------
# The §16 banner
# --------------------------------------------------------------------------


def test_the_render_graph_stamps_the_model_derived_banner() -> None:
    """§16: the UI must state MODEL-DERIVED --- NOT EXCHANGE-OBSERVED."""
    graph = render_liquidation_map(_spec(), entry_price=100.0)
    assert graph["banner"] == MODEL_DERIVED_BANNER
    assert graph["model"]["limitation"]
    assert len(graph["levels"]) == 4


# --------------------------------------------------------------------------
# The maintenance-rate schedule
# --------------------------------------------------------------------------


def test_the_maintenance_rate_is_the_first_covering_tier() -> None:
    assert maintenance_rate(_spec(), 40_000.0) == 0.004
    assert maintenance_rate(_spec(), 600_000.0) == 0.01  # above the 500k tier
    # above every tier: the last tier covers it
    assert maintenance_rate(_spec(), 99_000_000.0) == 0.1


# --------------------------------------------------------------------------
# The observed OI + funding series (provenance OBSERVED)
# --------------------------------------------------------------------------


def _oi() -> list[OiPoint]:
    return [OiPoint(ts=f"t{i}", oi=float(1000 + i * 100)) for i in range(4)]


def test_oi_change_pct_is_the_calculated_transformation() -> None:
    series = oi_change_pct(_oi())
    assert series[0] == ("t1", 10.0)  # (1100 - 1000) / 1000 * 100


def test_oi_change_pct_drops_the_first_bar_not_a_fabricated_zero() -> None:
    assert len(oi_change_pct(_oi())) == 3


def test_a_zero_prior_oi_is_refused() -> None:
    points = [OiPoint(ts="t0", oi=0.0), OiPoint(ts="t1", oi=100.0)]
    with pytest.raises(ValidationError):
        oi_change_pct(points)


def test_render_oi_is_observed() -> None:
    graph = render_oi(_oi())
    assert graph["provenance"] == "OBSERVED"
    assert graph["label"] == "Open Interest"


def _funding() -> list[FundingPoint]:
    return [
        FundingPoint(ts="t0", rate=0.0001),
        FundingPoint(ts="t1", rate=-0.0002),
        FundingPoint(ts="t2", rate=0.0003),
    ]


def test_funding_series_long_pays_at_a_positive_rate() -> None:
    """The M10/D-070 convention: a positive rate means longs pay (D-099)."""
    series = funding_series(_funding(), side="LONG")
    assert [value for _, value in series] == pytest.approx([0.0001, -0.0001, 0.0002])


def test_funding_series_short_is_the_mirror() -> None:
    series = funding_series(_funding(), side="SHORT")
    assert series[0][1] == pytest.approx(-0.0001)


def test_a_bad_funding_side_is_refused() -> None:
    with pytest.raises(ValidationError):
        funding_series(_funding(), side="FLAT")


def test_render_funding_is_deterministic() -> None:
    assert render_funding(_funding()) == render_funding(_funding())


# --------------------------------------------------------------------------
# The options interface (spec §21, the future layer)
# --------------------------------------------------------------------------


def test_the_options_spec_declares_every_assumption() -> None:
    spec = OptionsModelSpec(
        version="1.0.0", pricing_model="black-scholes", assumptions={"r": 0.0}
    )
    spec.validate()
    assert spec.assumptions == {"r": 0.0}


def test_an_empty_options_spec_is_refused() -> None:
    with pytest.raises(ValidationError):
        OptionsModelSpec(version="", pricing_model="", assumptions={}).validate()


def test_a_non_empty_chain_is_not_implemented_not_fabricated() -> None:
    """D-100/§38.10: the chain feed is M16+; a non-empty claim is refused,
    never a fabricated GEX."""
    with pytest.raises(ValidationError) as excinfo:
        normalize_chain([{"strike": 100.0}])
    assert "not implemented" in excinfo.value.message


def test_the_gamma_curve_skeleton_names_the_uncomputed_outputs() -> None:
    """The shape is explicit: the outputs are named, not computed."""
    spec = OptionsModelSpec(version="1.0.0", pricing_model="bs", assumptions={"r": 0.0})
    graph = render_gamma_curve(spec, spot=100.0)
    assert graph["points"] == []
    assert "gamma_flip" in graph["outputs"]
    assert graph["computed"] == []


def test_a_bad_spot_is_refused() -> None:
    spec = OptionsModelSpec(version="1.0.0", pricing_model="bs", assumptions={"r": 0.0})
    with pytest.raises(ValidationError):
        render_gamma_curve(spec, spot=0.0)
