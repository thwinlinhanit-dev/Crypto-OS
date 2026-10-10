"""M6 T6.1 — strategy definition tests (spec 7.1 transcription + versioning).

The 7.1 JSON is transcribed field-for-field; these tests pin the vocabulary
(422 on unknown kinds — PD-4/PD-11), the no-hidden-defaults rule, and the
content-addressed version hash (PD-6).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from engine.strategy.hash import canonical_hash, canonical_json
from engine.strategy.models import (
    FillModel,
    StrategyDefinition,
)


def _definition(**overrides: object) -> StrategyDefinition:
    body: dict[str, object] = {
        "strategy_id": "rvol_climax_pro",
        "version": "5.0",
        "entry": {
            "detector": "RVOL_CLIMAX@5.0",
            "params": {},
            "signals": ["CONT", "CAPIT"],
            "side": "LONG_ONLY",
        },
        "sizing": {"mode": "RISK", "risk_pct": 1.0, "max_alloc_pct": 25.0},
        "stop": {"kind": "ATR", "atr_len": 14, "atr_mult": 3.0},
        "costs": {
            "fee_model": {"kind": "PERCENT", "value": 0.10},
            "slippage_model": {"kind": "FIXED_BPS", "value": 5},
        },
        "execution": {"fill_model": "REALISTIC_OHLC", "initial_capital": 10000},
    }
    body.update(overrides)
    return StrategyDefinition.model_validate(body)


def test_seven_one_json_transcribes_verbatim() -> None:
    """Every field the spec 7.1 example shows is accepted as-is."""
    strategy = _definition()
    assert strategy.strategy_id == "rvol_climax_pro"
    assert strategy.entry.side.value == "LONG_ONLY"
    assert strategy.entry.signals == ["CONT", "CAPIT"]
    assert strategy.sizing.mode == "RISK"
    assert strategy.stop.kind.value == "ATR"
    assert strategy.stop.atr_mult == 3.0
    assert strategy.costs.fee_model.kind.value == "PERCENT"
    assert strategy.execution.fill_model is FillModel.REALISTIC_OHLC
    assert strategy.execution.initial_capital == 10000


def test_spec_wire_names_preserved_on_serialization() -> None:
    """7.1 field names (trigger_R, activate_R) survive model_dump."""
    strategy = _definition()
    dumped = strategy.model_dump(by_alias=True)
    assert dumped["breakeven"]["trigger_R"] == 1.0
    assert dumped["trail"]["giveback"]["activate_R"] == 2.0


def test_no_hidden_defaults_extra_field_rejected() -> None:
    """7.1 'no hidden defaults': an unknown field is refused, not stored."""
    with pytest.raises(ValidationError):
        _definition(entry={"detector": "X@1.0", "signals": ["CONT"], "side": "LONG_ONLY",
                           "params": {}, "mystery": 1})


def test_unknown_stop_kind_422() -> None:
    with pytest.raises(ValidationError):
        _definition(stop={"kind": "VOLATILITY", "atr_mult": 2.0})


def test_unknown_fee_kind_422() -> None:
    with pytest.raises(ValidationError):
        _definition(costs={
            "fee_model": {"kind": "DEPTH_BASED", "value": 1},
            "slippage_model": {"kind": "FIXED_BPS", "value": 5},
        })


def test_reserved_fill_model_rejected() -> None:
    """R8: REALISTIC_INTRABAR is reserved -> validation error (NOT_IMPLEMENTED
    is raised by the API layer on the reserved names; the enum itself has
    no such member, so an unknown value is a 422 here)."""
    with pytest.raises(ValidationError):
        _definition(execution={"fill_model": "REALISTIC_INTRABAR"})


def test_parity_fill_model_accepted() -> None:
    strategy = _definition(execution={"fill_model": "PARITY", "initial_capital": 10000})
    assert strategy.execution.fill_model is FillModel.PARITY


def test_short_side_parsed_but_runnable_rejected_by_api() -> None:
    """R11/D-043: SHORT_ONLY parses at the model layer; the API rejects the run."""
    strategy = _definition(
        entry={"detector": "RVOL_CLIMAX@5.0", "params": {}, "signals": ["CONT"],
               "side": "SHORT_ONLY"},
    )
    assert strategy.entry.side.value == "SHORT_ONLY"


def test_version_hash_is_content_addressed_and_stable() -> None:
    """PD-6/D-037: same definition -> same hash; any edit -> a new hash."""
    a = _definition()
    b = _definition()
    assert canonical_hash(a.model_dump()) == canonical_hash(b.model_dump())
    c = _definition(version="5.1")
    assert canonical_hash(c.model_dump()) != canonical_hash(a.model_dump())


def test_canonical_json_is_key_order_independent() -> None:
    """Canonical JSON: sorted keys, tight separators — byte-stable (D-037)."""
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert canonical_json({"a": 1}) == '{"a":1}'


def test_spec_defaults_only_where_the_spec_shows_them() -> None:
    """7.1 defaults: only the fields the spec example carries defaults."""
    strategy = _definition(
        entry={"detector": "RVOL_CLIMAX@5.0", "params": {}, "signals": ["CONT"],
               "side": "LONG_ONLY"},
    )
    assert strategy.breakeven.enabled is False
    assert strategy.breakeven.trigger_r == 1.0
    assert strategy.target.R == 0
    assert strategy.time_stop.enabled is False
    assert strategy.time_stop.max_bars == 80
    assert strategy.execution.delay_bars == 0
    assert strategy.execution.close_open_at_end is False
    assert strategy.execution.max_hold_gap_bars == 3
