"""M6 (T6.1) — Strategy definition (spec 7.1, versioned JSON).

The 7.1 JSON transcribed field-for-field: nested Pydantic models with the
spec's own vocabulary enums (stop.kind, trail.kind, side, fee/slippage
kinds). No hidden defaults (7.1): every field the spec shows is explicit;
unknown enum values 422 (PD-4/PD-11), reserved fee/slippage kinds raise
NOT_IMPLEMENTED.

Edits create a new version (7.1) — the CRUD layer appends and the stored
backtest always names `strategy_id@version` (PD-6).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StopKind(StrEnum):
    ATR = "ATR"
    STRUCTURE = "STRUCTURE"
    NONE = "NONE"


class TrailKind(StrEnum):
    ATR_CHANDELIER = "ATR_CHANDELIER"
    PERCENT = "PERCENT"
    DONCHIAN = "DONCHIAN"
    PIVOT_SWING = "PIVOT_SWING"
    PROFIT_GIVEBACK = "PROFIT_GIVEBACK"
    EMA_CLOSE = "EMA_CLOSE"


class Side(StrEnum):
    BOTH = "BOTH"
    LONG_ONLY = "LONG_ONLY"
    SHORT_ONLY = "SHORT_ONLY"


class FeeKind(StrEnum):
    PERCENT = "PERCENT"
    # Reserved (7.1): SPREAD_BASED, VOLATILITY_BASED, DEPTH_BASED, CUSTOM ->
    # NOT_IMPLEMENTED; a plain unknown kind is a 422.


class SlippageKind(StrEnum):
    FIXED_BPS = "FIXED_BPS"


class FillModel(StrEnum):
    PARITY = "PARITY"
    REALISTIC_OHLC = "REALISTIC_OHLC"
    # REALISTIC_INTRABAR is reserved (R8) -> NOT_IMPLEMENTED.


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Entry(Model):
    detector: str
    params: dict[str, Any] = Field(default_factory=dict)
    signals: list[str]
    side: Side
    # 7.6 benchmark mode — M7 validation surface; parked placeholder.
    mode: Literal["SIGNAL"] = "SIGNAL"


class Sizing(Model):
    mode: Literal["RISK", "FIXED"]
    risk_pct: float = 1.0
    max_alloc_pct: float = 25.0
    fixed_alloc_pct: float = 10.0


class Stop(Model):
    kind: StopKind
    atr_len: int = 14
    atr_mult: float = 3.0
    structure_buffer_atr: float = 0.25


class Breakeven(Model):
    enabled: bool = False
    # 7.1 wire name is trigger_R; alias accepts it on input and output.
    trigger_r: float = Field(default=1.0, alias="trigger_R", serialization_alias="trigger_R")


class Target(Model):
    R: float = 0


class PivotSpec(Model):
    left: int = 5
    right: int = 3
    buffer_atr: float = 0.25


class Giveback(Model):
    # 7.1 wire name is activate_R.
    activate_r: float = Field(default=2.0, alias="activate_R", serialization_alias="activate_R")
    pct: float = 35.0


class Trail(Model):
    enabled: bool = False
    kind: TrailKind = TrailKind.ATR_CHANDELIER
    atr_mult: float = 3.8
    percent: float = 12.0
    donchian_len: int = 20
    pivot: PivotSpec = Field(default_factory=PivotSpec)
    giveback: Giveback = Field(default_factory=Giveback)
    ema_len: int = 21


class TimeStop(Model):
    enabled: bool = False
    max_bars: int = 80


class Exits(Model):
    on_opposite: bool = False
    long_on_bearish_signal: bool = False


class FeeModel(Model):
    kind: FeeKind
    value: float = 0.10


class SlippageModel(Model):
    kind: SlippageKind
    value: float = 5.0


class Costs(Model):
    fee_model: FeeModel
    slippage_model: SlippageModel


class Execution(Model):
    fill_model: FillModel
    delay_bars: int = 0
    pyramiding: int = 0
    leverage: float = 1.0
    initial_capital: float = 10000
    close_open_at_end: bool = False
    max_hold_gap_bars: int = 3


class StrategyDefinition(Model):
    """7.1 strategy JSON, versioned. Edits create a new version."""

    strategy_id: str
    version: str
    entry: Entry
    sizing: Sizing
    stop: Stop
    breakeven: Breakeven = Field(default_factory=Breakeven)
    target: Target = Field(default_factory=Target)
    trail: Trail = Field(default_factory=Trail)
    time_stop: TimeStop = Field(default_factory=TimeStop)
    exits: Exits = Field(default_factory=Exits)
    costs: Costs
    execution: Execution
