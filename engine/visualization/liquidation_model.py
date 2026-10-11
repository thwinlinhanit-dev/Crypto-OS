"""M15 — the OI-adjusted liquidation map (spec §16).

The reference model:

    Price + OI + volatility + leverage assumptions
                      |
                  position model
                      |
         10x / 25x / 50x / 100x
                      |
           estimated liquidation map

CRITICAL (§16):
- "The exact position distribution/liquidation model requires an approved
  model specification before research use."
- The UI must state: MODEL-DERIVED --- NOT EXCHANGE-OBSERVED
- "Estimated liquidation levels are never labeled as observed exchange
  orders."

So the map is a VERSIONED, EXPLICITLY-DECLARED model (D-097): the leverage
ladder, the maintenance-margin schedule and the version are all required
inputs, and every estimated level carries provenance INFERRED + the model
version. A map cannot be built without a declared spec.

DECLARED LIMITATION (D-098): OI is TOTAL open interest, not a per-position
distribution. The map is a SCENARIO SURFACE over declared leverage
assumptions - not a prediction of where liquidations will occur.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from engine.errors import ValidationError

#: The §16 banner every liquidation-map render graph must stamp.
MODEL_DERIVED_BANNER: Final[str] = "MODEL-DERIVED --- NOT EXCHANGE-OBSERVED"

#: The default leverage ladder (spec §16: 10x / 25x / 50x / 100x). A declared
#: input - a model may narrow it, never silently widen it.
DEFAULT_LEVERAGE_LADDER: Final[tuple[int, ...]] = (10, 25, 50, 100)

#: The default maintenance-margin schedule: the tiered rate (Binance's
#: published BTCUSDT schedule is the named source). A model may replace it,
#: but must name its source (D-097).
DEFAULT_MAINTENANCE_SCHEDULE: Final[tuple[tuple[int, float], ...]] = (
    (50_000, 0.004),
    (500_000, 0.005),
    (1_000_000, 0.01),
    (10_000_000, 0.025),
    (20_000_000, 0.05),
    (50_000_000, 0.1),
)

#: The named source of the default schedule.
DEFAULT_MARGIN_SOURCE: Final[str] = "binance-btcusdt-tiered-mmr-2026"


@dataclass(frozen=True)
class LiquidationModelSpec:
    """The versioned, explicitly-declared model (spec §16/§21).

    Every assumption is a field (§21: "Model assumptions must be visible and
    versioned"). An empty or partial spec is refused.
    """

    version: str
    leverage_ladder: tuple[int, ...] = DEFAULT_LEVERAGE_LADDER
    maintenance_schedule: tuple[tuple[int, float], ...] = DEFAULT_MAINTENANCE_SCHEDULE
    margin_source: str = DEFAULT_MARGIN_SOURCE
    #: The declared limitation (D-098): OI is total open interest, not a
    #: per-position distribution.
    limitation: str = (
        "OI is total open interest, not a per-position distribution; the map "
        "is a scenario surface over declared leverage assumptions, not a "
        "prediction of where liquidations will occur."
    )

    def validate(self) -> None:
        """Fail-closed (§16/§21): a map cannot be built without the declared
        leverage ladder, margin schedule, named source and version."""
        if not self.version or not self.version.strip():
            raise ValidationError("the liquidation model must declare a version", {})
        if not self.leverage_ladder:
            raise ValidationError("the leverage ladder is required (§16)", {})
        for leverage in self.leverage_ladder:
            if type(leverage) is not int or leverage < 1:
                raise ValidationError(
                    "every leverage must be a positive integer", {"leverage": leverage}
                )
        if not self.maintenance_schedule:
            raise ValidationError("the maintenance-margin schedule is required (§16)", {})
        if not self.margin_source or not self.margin_source.strip():
            raise ValidationError(
                "the maintenance-margin schedule must name its source (§21)",
                {"margin_source": self.margin_source},
            )
        for notional, rate in self.maintenance_schedule:
            if notional < 0 or not 0.0 <= rate < 1.0:
                raise ValidationError(
                    "invalid maintenance-margin tier",
                    {"notional": notional, "rate": rate},
                )

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "leverage_ladder": list(self.leverage_ladder),
            "maintenance_schedule": [list(tier) for tier in self.maintenance_schedule],
            "margin_source": self.margin_source,
            "limitation": self.limitation,
        }


def maintenance_rate(spec: LiquidationModelSpec, notional: float) -> float:
    """The maintenance-margin rate for a declared notional (the first tier
    whose cap covers it; the last tier covers everything above)."""
    for cap, rate in spec.maintenance_schedule:
        if notional <= cap:
            return rate
    return spec.maintenance_schedule[-1][1]


@dataclass(frozen=True)
class EstimatedLevel:
    """One estimated liquidation level: INFERRED, stamped with the model
    version (spec §16: never labeled as an observed exchange order)."""

    leverage: int
    entry_price: float
    liquidation_price: float
    provenance: str = "INFERRED"
    model_version: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "leverage": self.leverage,
            "entry_price": self.entry_price,
            "liquidation_price": self.liquidation_price,
            "provenance": self.provenance,
            "model_version": self.model_version,
        }


def estimated_levels(
    spec: LiquidationModelSpec,
    *,
    entry_price: float,
    side: str = "LONG",
    notional: float = 50_000.0,
) -> tuple[EstimatedLevel, ...]:
    """The estimated liquidation map for a declared entry (spec §16).

    The isolated-margin liquidation price for leverage L and maintenance
    rate mmr:

        long:  entry * (1 - 1/L + mmr)
        short: entry * (1 + 1/L - mmr)

    The arithmetic is pinned by a numerical fixture test (the §36 M15 gate).
    """
    spec.validate()
    normalized = side.strip().upper()
    if normalized not in ("LONG", "SHORT"):
        raise ValidationError("side must be LONG or SHORT", {"side": side})
    if entry_price <= 0:
        raise ValidationError(
            "entry_price must be positive", {"entry_price": entry_price}
        )
    if notional <= 0:
        raise ValidationError("notional must be positive", {"notional": notional})
    mmr = maintenance_rate(spec, notional)
    out: list[EstimatedLevel] = []
    for leverage in spec.leverage_ladder:
        if normalized == "LONG":
            price = entry_price * (1.0 - 1.0 / leverage + mmr)
        else:
            price = entry_price * (1.0 + 1.0 / leverage - mmr)
        out.append(
            EstimatedLevel(
                leverage=leverage,
                entry_price=entry_price,
                liquidation_price=price,
                model_version=spec.version,
            )
        )
    return tuple(out)


def render_liquidation_map(
    spec: LiquidationModelSpec,
    *,
    entry_price: float,
    side: str = "LONG",
    notional: float = 50_000.0,
) -> dict[str, Any]:
    """The §16 render graph: the estimated levels + the model spec + the
    MODEL-DERIVED --- NOT EXCHANGE-OBSERVED banner (mandatory, §16)."""
    levels = estimated_levels(
        spec, entry_price=entry_price, side=side, notional=notional
    )
    return {
        "kind": "LIQUIDATION_MAP",
        "banner": MODEL_DERIVED_BANNER,
        "side": side.strip().upper(),
        "entry_price": entry_price,
        "model": spec.as_dict(),
        "levels": [level.as_dict() for level in levels],
    }
