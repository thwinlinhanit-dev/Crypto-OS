"""M12 — the visualization runtime tests (spec §30).

The M12-applicable subset of the §30 property suite:
- program validation (fail-closed: unknown dataset, forbidden permission,
  unknown render kind, underspecified metadata);
- determinism (same inputs → identical outputs, incl. program_hash);
- truncation invariance (as_of=t equals history truncated at t, §23);
- permission safety (an undeclared dataset is refused);
- the golden fixtures M12 covers (line, HUD, replay, missing data,
  unavailable data, permission violation, deterministic rerun, version
  change);
- the lifecycle transition table (§6).
"""

from __future__ import annotations

from typing import Any

import pytest

from engine.errors import ValidationError
from engine.visualization import (
    DatasetResponse,
    HudElement,
    LineSeries,
    ProvenanceValue,
    SandboxRequest,
    VisualizationProgram,
    assert_distinguishable,
    assert_no_forbidden_capabilities,
    assert_valid_lifecycle_transition,
    classify,
    program_hash,
    quality_of,
    render_hud,
    render_line,
    render_table,
    run_program,
    truncate_as_of,
    validate_program,
)
from engine.visualization.models import (
    ALLOWED_LIFECYCLE_TRANSITIONS,
    assert_valid_dataset,
    assert_valid_provenance,
    assert_valid_render_kind,
    is_terminal_lifecycle,
)


def _program(**over: Any) -> VisualizationProgram:
    """A valid program (spec §6); ``over`` replaces any field (the dataclass
    is frozen, so a replaced field builds a new instance)."""
    base: dict[str, Any] = {
        "visualization_id": "btc_line",
        "version": "1.0.0",
        "title": "BTC line",
        "source": "export default {}",
        "required_datasets": ("candles",),
        "outputs": ("LINE",),
        "created_by": "owner",
    }
    base.update(over)
    return VisualizationProgram(**base)


# --------------------------------------------------------------------------
# The lifecycle (spec §6)
# --------------------------------------------------------------------------


def test_the_lifecycle_is_the_spec_sequence() -> None:
    assert ALLOWED_LIFECYCLE_TRANSITIONS["DRAFT"] == ("VALIDATED", "DEPRECATED")
    assert ALLOWED_LIFECYCLE_TRANSITIONS["PUBLISHED"] == ("DEPRECATED",)
    assert is_terminal_lifecycle("DEPRECATED")
    assert not is_terminal_lifecycle("PUBLISHED")


def test_a_legal_lifecycle_transition_passes() -> None:
    assert_valid_lifecycle_transition("DRAFT", "VALIDATED")
    assert_valid_lifecycle_transition("VALIDATED", "PUBLISHED")


def test_an_illegal_lifecycle_transition_is_rejected() -> None:
    """§6: behavior cannot silently change under an existing version - a
    PUBLISHED program cannot jump back to DRAFT."""
    with pytest.raises(ValidationError):
        assert_valid_lifecycle_transition("PUBLISHED", "DRAFT")


def test_deprecated_is_terminal() -> None:
    with pytest.raises(ValidationError):
        assert_valid_lifecycle_transition("DEPRECATED", "VALIDATED")


# --------------------------------------------------------------------------
# The program validation (spec §3/§6, fail-closed)
# --------------------------------------------------------------------------


def test_a_valid_program_passes() -> None:
    validate_program(_program())


def test_an_underspecified_program_is_rejected() -> None:
    program = _program(title="")
    with pytest.raises(ValidationError) as excinfo:
        validate_program(program)
    assert "title" in str(excinfo.value.detail["missing_fields"])


def test_an_unknown_dataset_is_rejected() -> None:
    program = _program(required_datasets=("options",))
    with pytest.raises(ValidationError) as excinfo:
        validate_program(program)
    assert "unknown dataset" in excinfo.value.message


def test_a_forbidden_permission_is_refused() -> None:
    """§3: a visualization program cannot place/cancel/modify orders, touch
    credentials, the DB, arbitrary network or the filesystem."""
    program = _program(permissions={"network": True, "filesystem": False, "trading": False})
    with pytest.raises(ValidationError) as excinfo:
        validate_program(program)
    assert "forbidden permission" in excinfo.value.message


def test_a_trading_permission_is_refused() -> None:
    program = _program(permissions={"network": False, "filesystem": False, "trading": True})
    with pytest.raises(ValidationError):
        validate_program(program)


def test_a_missing_permission_declaration_is_rejected() -> None:
    program = _program(permissions={"network": False})
    with pytest.raises(ValidationError) as excinfo:
        validate_program(program)
    assert "missing_permission" in str(excinfo.value.detail)


def test_an_unknown_output_kind_is_rejected() -> None:
    program = _program(outputs=("HEATMAP",))
    with pytest.raises(ValidationError) as excinfo:
        validate_program(program)
    assert "unknown output render kind" in excinfo.value.message


def test_a_program_without_datasets_or_outputs_is_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_program(_program(required_datasets=()))
    with pytest.raises(ValidationError):
        validate_program(_program(outputs=()))


def test_a_bad_performance_budget_is_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_program(_program(performance_budget=0))


# --------------------------------------------------------------------------
# The program hash (determinism, D-078)
# --------------------------------------------------------------------------


def test_the_program_hash_is_deterministic() -> None:
    a = program_hash("src", visualization_id="x", version="1.0.0")
    b = program_hash("src", visualization_id="x", version="1.0.0")
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_the_program_hash_changes_with_the_version() -> None:
    """§6: a version change is a new hash - behavior cannot silently change
    under an existing version."""
    a = program_hash("src", visualization_id="x", version="1.0.0")
    b = program_hash("src", visualization_id="x", version="2.0.0")
    assert a != b


def test_the_program_hash_changes_with_the_source() -> None:
    a = program_hash("src A", visualization_id="x", version="1.0.0")
    b = program_hash("src B", visualization_id="x", version="1.0.0")
    assert a != b


# --------------------------------------------------------------------------
# The data API (spec §9/§23/§30)
# --------------------------------------------------------------------------


def _rows(n: int = 5) -> list[dict[str, Any]]:
    return [{"ts": f"2026-01-0{i + 1}T00:00:00Z", "close": 100.0 + i} for i in range(n)]


def test_truncate_as_of_removes_future_rows() -> None:
    rows = _rows()
    kept = truncate_as_of(rows, "2026-01-03T00:00:00Z")
    assert len(kept) == 3


def test_truncation_invariance_the_section_23_invariant() -> None:
    """§23: program(full_history, as_of=t) == program(history_truncated_at_t).
    The golden fixture: truncating at t then rendering equals rendering the
    full history with as_of=t."""
    rows = _rows()
    truncated = truncate_as_of(rows, "2026-01-03T00:00:00Z")
    series_full = [
        LineSeries(key="close", label="close", points=tuple((r["ts"], r["close"]) for r in rows))
    ]
    series_truncated = [
        LineSeries(key="close", label="close", points=tuple((r["ts"], r["close"]) for r in truncated))
    ]
    a = render_line(series_full, as_of="2026-01-03T00:00:00Z")
    b = render_line(series_truncated)
    assert a["series"][0]["points"] == b["series"][0]["points"]


def test_a_row_without_a_time_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        truncate_as_of([{"close": 1.0}], "2026-01-01T00:00:00Z")


def test_quality_unavailable_when_no_rows() -> None:
    quality = quality_of([])
    assert quality["state"] == "DATA_UNAVAILABLE"
    assert quality["completeness"] == 0.0


def test_quality_incomplete_when_short() -> None:
    quality = quality_of(_rows(2), expected=5)
    assert quality["state"] == "DATA_INCOMPLETE"
    assert quality["completeness"] == pytest.approx(0.4)


def test_quality_green_when_covered() -> None:
    assert quality_of(_rows(5), expected=5)["state"] == "GREEN"


def test_an_unusable_dataset_is_refused() -> None:
    """§3: missing data fails explicitly, never substitutes."""
    response = DatasetResponse(
        dataset="candles",
        dataset_version="dv",
        schema_version="1",
        source="binance",
        venue="BINANCE",
        instrument_id="i",
        timeframe="1D",
        as_of=None,
        quality={"state": "DATA_UNAVAILABLE", "completeness": 0.0},
        rows=(),
    )
    with pytest.raises(ValidationError):
        from engine.visualization.data_api import assert_quality_ok

        assert_quality_ok(response.quality)


# --------------------------------------------------------------------------
# Permission safety (spec §30)
# --------------------------------------------------------------------------


def _response(dataset: str = "candles", rows: tuple[Any, ...] | None = None) -> DatasetResponse:
    return DatasetResponse(
        dataset=dataset,
        dataset_version="dv",
        schema_version="1",
        source="binance",
        venue="BINANCE",
        instrument_id="i",
        timeframe="1D",
        as_of=None,
        quality={"state": "GREEN", "completeness": 1.0},
        rows=rows if rows is not None else tuple(_rows()),
    )


def test_an_undeclared_dataset_is_refused() -> None:
    """§30 permission safety: an undeclared dataset cannot be accessed - the
    M9 closed-registry pattern applied to datasets (D-079)."""
    request = SandboxRequest(program=_program(required_datasets=("candles",)))
    with pytest.raises(ValidationError) as excinfo:
        run_program(
            request,
            datasets={"orderbook": _response("orderbook")},
            compute=lambda gated: {"kind": "LINE"},
        )
    assert "undeclared dataset" in excinfo.value.message


def test_a_forbidden_capability_is_refused_before_execution() -> None:
    program = _program(permissions={"network": False, "filesystem": False, "trading": True})
    with pytest.raises(ValidationError):
        assert_no_forbidden_capabilities(program)


# --------------------------------------------------------------------------
# The sandbox pipeline (spec §8)
# --------------------------------------------------------------------------


def _compute(gated: Any) -> dict[str, Any]:
    rows = gated["candles"]
    return render_line(
        [LineSeries(key="close", label="close", points=tuple((r["ts"], r["close"]) for r in rows))]
    )


def test_the_sandbox_runs_the_deterministic_pipeline() -> None:
    request = SandboxRequest(program=_program())
    result = run_program(request, datasets={"candles": _response()}, compute=_compute)
    assert result["render"]["kind"] == "LINE"
    assert len(result["render"]["series"][0]["points"]) == 5


def test_the_sandbox_gates_the_data_at_as_of() -> None:
    """§23: no data after as_of may be visible to the program."""
    request = SandboxRequest(program=_program(), as_of="2026-01-03T00:00:00Z")
    result = run_program(request, datasets={"candles": _response()}, compute=_compute)
    assert len(result["render"]["series"][0]["points"]) == 3
    assert result["as_of"] == "2026-01-03T00:00:00Z"


def test_a_missing_declared_dataset_is_refused() -> None:
    request = SandboxRequest(program=_program())
    with pytest.raises(ValidationError):
        run_program(request, datasets={}, compute=_compute)


def test_a_compute_returning_a_bad_graph_is_refused() -> None:
    request = SandboxRequest(program=_program())
    with pytest.raises(ValidationError):
        run_program(request, datasets={"candles": _response()}, compute=lambda g: {"no": "kind"})


# --------------------------------------------------------------------------
# The renderer (spec §13; the §30 golden fixtures)
# --------------------------------------------------------------------------


def test_render_line_is_deterministic() -> None:
    series = [LineSeries(key="close", label="close", points=(("t1", 1.0), ("t2", 2.0)))]
    assert render_line(series) == render_line(series)


def test_render_line_requires_a_series() -> None:
    with pytest.raises(ValidationError):
        render_line([])


def test_render_hud_requires_an_element() -> None:
    with pytest.raises(ValidationError):
        render_hud([])


def test_render_hud_renders_the_elements() -> None:
    graph = render_hud([HudElement(key="price", value=100.0)])
    assert graph["kind"] == "HUD"
    assert graph["elements"][0]["key"] == "price"


def test_render_table_requires_rows_and_columns() -> None:
    with pytest.raises(ValidationError):
        render_table([], columns=["a"])
    with pytest.raises(ValidationError):
        render_table([], columns=[])


# --------------------------------------------------------------------------
# Provenance (spec §22/§3)
# --------------------------------------------------------------------------


def test_the_four_provenance_classes() -> None:
    for name in ("OBSERVED", "CALCULATED", "INFERRED", "AI"):
        assert_valid_provenance(name)
    with pytest.raises(ValidationError):
        assert_valid_provenance("VIBES")


def test_a_mixed_untagged_list_is_refused() -> None:
    """§3: observed/calculated/inferred/AI must remain distinguishable - a
    value without a tag is refused so a model value can never masquerade as
    an observation."""
    with pytest.raises(ValidationError):
        assert_distinguishable([ProvenanceValue(value=1.0, provenance="AI", source="m"), 1.0])  # type: ignore[list-item]


def test_a_tagged_list_passes() -> None:
    values = [
        classify(100.0, provenance="OBSERVED", source="candles"),
        classify(1.5, provenance="CALCULATED", source="compute"),
    ]
    assert_distinguishable(values)


def test_an_observed_value_is_tagged_with_its_source() -> None:
    value = classify(100.0, provenance="OBSERVED", source="candles")
    assert value.provenance == "OBSERVED"
    assert value.source == "candles"


# --------------------------------------------------------------------------
# The vocabularies
# --------------------------------------------------------------------------


def test_the_dataset_vocabulary_is_the_spec_list() -> None:
    for name in ("candles", "trades", "orderbook", "open_interest", "funding", "liquidations"):
        assert_valid_dataset(name)
    with pytest.raises(ValidationError):
        assert_valid_dataset("options")  # future (§9)


def test_the_render_kinds_are_the_m12_set() -> None:
    for kind in ("LINE", "HUD", "TABLE"):
        assert_valid_render_kind(kind)
    with pytest.raises(ValidationError):
        assert_valid_render_kind("HEATMAP")  # M13
