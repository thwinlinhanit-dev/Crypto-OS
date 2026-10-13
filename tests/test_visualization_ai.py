"""M16 — the AI visualization tests (spec §25/§26/§36 M16).

The M16 gate: unsafe generated code is blocked and valid programs are
reproducible. The centerpiece ACs: a generated program is untrusted until
validated (D-102); a forbidden capability, an undeclared dataset, a hidden
assumption or an over-budget resource claim is BLOCKED before sandbox
execution; no trading tier ever exists (§3/§26); and the §26 envelope is
mandatory on every tool result.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from engine.errors import ValidationError
from engine.visualization import (
    FORBIDDEN_TIER,
    M9_REUSED_TOOLS,
    TOOL_NAMES,
    TOOL_TIERS,
    GenerationRequest,
    VisualizationProgram,
    assert_no_trading_tier,
    availability_check,
    debug_program,
    edit_program,
    explain,
    fixture_datasets,
    fixture_test,
    generate_program,
    identify_datasets,
    pipeline,
    program_hash,
    propose_assumptions,
    validate_generated,
    validate_program,
)


def _request(text: str = "show the funding rate line") -> GenerationRequest:
    return GenerationRequest(request=text, visualization_id="gen_1", version="1.0.0")


@pytest.fixture(autouse=True)
def _restore_registry() -> Iterator[None]:
    """Snapshot the shared M9 registry and restore it after each test.

    ``register_visualization_tools()`` mutates the module-level ``_REGISTRY``
    (the extend-not-replace pattern, D-103), so the 11 §26 tools would leak into
    a later M9 test that asserts the registry holds exactly the 21 §9 tools
    (``test_ai_mcp::test_tool_schemas_match_the_registry``). The tests that grow
    the registry own the cleanup (PD-9): we never relax the M9 count to fit M16.
    """
    from engine.ai import registry as registry_module

    saved = dict(registry_module._REGISTRY)
    try:
        yield
    finally:
        registry_module._REGISTRY.clear()
        registry_module._REGISTRY.update(saved)


# --------------------------------------------------------------------------
# Dataset identification (spec §25 step 2)
# --------------------------------------------------------------------------


def test_the_generator_maps_the_request_to_the_closed_vocabulary() -> None:
    assert identify_datasets("show the funding rate") == ("funding",)
    assert identify_datasets("open interest and liquidations") == (
        "liquidations",
        "open_interest",
    )
    assert identify_datasets("a candle price line") == ("candles",)


def test_an_unmappable_request_returns_no_datasets() -> None:
    """No silent substitution: an unmappable request returns (), refused at
    the availability step (§25)."""
    assert identify_datasets("something entirely unrelated") == ()


# --------------------------------------------------------------------------
# The availability check (spec §25 step 3)
# --------------------------------------------------------------------------


def test_a_missing_dataset_is_refused_never_substituted() -> None:
    missing = availability_check(("candles", "funding"), available=("candles",))
    assert missing == ("funding",)


def test_a_supplied_dataset_is_not_missing() -> None:
    assert availability_check(("candles",), available=("candles", "funding")) == ()


# --------------------------------------------------------------------------
# The assumption proposal (spec §25 step 4)
# --------------------------------------------------------------------------


def test_the_liquidation_assumption_is_explicit() -> None:
    """§16: the liquidation map is MODEL-DERIVED; the assumption is stated,
    never hidden."""
    assumptions = propose_assumptions("liquidation map", ("liquidations",))
    assert assumptions, "the liquidation assumption must be explicit"
    assert any("MODEL-DERIVED" in a for a in assumptions)


def test_a_no_dataset_request_states_the_assumption() -> None:
    assumptions = propose_assumptions("vibes", ())
    assert assumptions == ("no dataset was identified for this request",)


# --------------------------------------------------------------------------
# The program generation (spec §25 step 5; D-102)
# --------------------------------------------------------------------------


def test_the_generated_program_passes_the_same_validation() -> None:
    """D-102: a generated program is untrusted until validated - it passes
    the same validate_program a human's does."""
    program = generate_program(_request(), ("funding",))
    assert program.required_datasets == ("funding",)
    assert program.created_by == "ai"


def test_a_request_with_no_datasets_is_refused() -> None:
    with pytest.raises(ValidationError):
        generate_program(_request("vibes"), ())


# --------------------------------------------------------------------------
# The validation gate (spec §25 steps 6-7; the M36 M16 gate)
# --------------------------------------------------------------------------


def _program(**over: Any) -> VisualizationProgram:
    base: dict[str, Any] = {
        "visualization_id": "g",
        "version": "1.0.0",
        "title": "t",
        "source": "s",
        "required_datasets": ("funding",),
        "outputs": ("LINE",),
        "created_by": "ai",
        "performance_budget": 1000,
    }
    base.update(over)
    return VisualizationProgram(**base)


def test_a_valid_generated_program_passes() -> None:
    validate_generated(_program(), datasets=("funding",), available=("funding",))


def test_a_forbidden_capability_is_blocked() -> None:
    """§36 M16 gate: unsafe generated code is blocked before execution."""
    program = _program(permissions={"network": True, "filesystem": False, "trading": False})
    with pytest.raises(ValidationError) as excinfo:
        validate_generated(program, datasets=("funding",), available=("funding",))
    assert "forbidden capability" in excinfo.value.message


def test_an_undeclared_dataset_is_blocked() -> None:
    """§25: no silent substitution - the sandbox cannot supply a declared
    dataset."""
    with pytest.raises(ValidationError) as excinfo:
        validate_generated(
            _program(required_datasets=("orderbook",)),
            datasets=("orderbook",),
            available=("funding",),
        )
    assert "no silent substitution" in excinfo.value.message


def test_a_missing_performance_budget_is_blocked() -> None:
    """§17.2: the resource check requires a declared budget."""
    with pytest.raises(ValidationError) as excinfo:
        validate_generated(
            _program(performance_budget=None), datasets=("funding",), available=("funding",)
        )
    assert "performance budget" in excinfo.value.message


# --------------------------------------------------------------------------
# The §25 pipeline (the whole flow)
# --------------------------------------------------------------------------


def test_the_pipeline_generates_and_validates() -> None:
    result = pipeline(_request("show the funding rate line"), available_datasets=("funding",))
    assert result.blocked is None
    assert result.program is not None
    assert result.datasets == ("funding",)
    assert result.ref_id


def test_the_pipeline_blocks_an_unavailable_dataset() -> None:
    result = pipeline(
        _request("show the orderbook depth"), available_datasets=("funding",)
    )
    assert result.blocked == "unavailable"
    assert result.program is None
    assert "orderbook" in result.explanation


def test_the_pipeline_blocks_an_unmappable_request() -> None:
    result = pipeline(_request("vibes"), available_datasets=("funding",))
    assert result.blocked == "no-datasets"


def test_the_pipeline_is_reproducible() -> None:
    """§36 M16 gate: valid programs are reproducible - the same request
    produces the same ref_id (the canonical sha256)."""
    a = pipeline(_request("show the funding rate"), available_datasets=("funding",))
    b = pipeline(_request("show the funding rate"), available_datasets=("funding",))
    assert a.ref_id == b.ref_id


# --------------------------------------------------------------------------
# The §26 envelope (D-104)
# --------------------------------------------------------------------------


def test_the_tool_result_carries_the_mandatory_envelope() -> None:
    """§26: every tool result contains ref_id, dataset_version,
    program_hash, runtime_version, data_quality, result."""
    result = pipeline(_request("show the funding rate"), available_datasets=("funding",))
    envelope = result.envelope()
    for key in ("ref_id", "dataset_version", "program_hash", "runtime_version",
                "data_quality", "result"):
        assert key in envelope
    assert envelope["runtime_version"] == "1.0.0"


# --------------------------------------------------------------------------
# The §26 tiers (D-103)
# --------------------------------------------------------------------------


def test_the_tool_tiers_are_the_spec_vocabulary() -> None:
    """§26: READ_ONLY, RESEARCH, VISUALIZATION_WRITE. No trading tier."""
    assert set(TOOL_TIERS.values()) <= {"READ_ONLY", "RESEARCH", "VISUALIZATION_WRITE"}
    assert TOOL_TIERS["create_visualization_draft"] == "VISUALIZATION_WRITE"
    assert TOOL_TIERS["get_visualization_data"] == "READ_ONLY"
    # get_chart_context is the M9 §9 tool, reused (not re-registered)
    assert "get_chart_context" in M9_REUSED_TOOLS


def test_no_trading_tier_ever_exists() -> None:
    """§3/§26 (D-103): no component may authorize live trading."""
    assert FORBIDDEN_TIER not in TOOL_TIERS.values()
    assert_no_trading_tier()


def test_the_tool_names_are_the_spec_list() -> None:
    assert "list_visualizations" in TOOL_NAMES
    assert "create_visualization_draft" in TOOL_NAMES
    assert "get_market_canvas" in TOOL_NAMES
    # 11 owned + get_chart_context reused from M9 = the §26 list of 12
    assert len(TOOL_NAMES) == 11
    assert M9_REUSED_TOOLS == ("get_chart_context",)


# --------------------------------------------------------------------------
# The explanation (spec §25 step 10)
# --------------------------------------------------------------------------


def test_the_explanation_states_the_provenance_chain() -> None:
    """§22: observed/calculated/inferred/AI stay distinguishable; the
    explanation is AI."""
    text = explain(("funding",), ("the map is model-derived",))
    assert "OBSERVED" in text
    assert "CALCULATED" in text
    assert "INFERRED" in text
    assert "AI" in text


# --------------------------------------------------------------------------
# The M9 registry extension (extend, not replace)
# --------------------------------------------------------------------------


def test_the_tools_register_into_the_existing_registry() -> None:
    from engine.ai.registry import names
    from engine.visualization import register_visualization_tools

    registered = register_visualization_tools()
    assert len(registered) == 11
    # extend, not replace: the M9 tools remain
    assert "search_instruments" in names()
    assert "create_visualization_draft" in names()
    assert "get_chart_context" in names()  # the reused M9 tool


def test_the_write_tier_holds_exactly_the_three_write_tools() -> None:
    """D-103/§26: VISUALIZATION_WRITE is exactly the draft/save/clone set."""
    from engine.ai.registry import tools
    from engine.visualization import register_visualization_tools

    register_visualization_tools()
    write_tools = {t.name for t in tools("VISUALIZATION_WRITE")}
    assert write_tools == {
        "create_visualization_draft",
        "save_visualization",
        "clone_visualization",
    }


# --------------------------------------------------------------------------
# The §36 reproducibility gate: the same request -> the same program_hash
# --------------------------------------------------------------------------


def test_the_pipeline_result_carries_the_program_hash() -> None:
    """§6 required metadata + D-104: a generated draft carries its hash."""
    result = pipeline(_request("show the funding rate"), available_datasets=("funding",))
    assert result.program is not None
    assert result.program_hash is not None
    assert result.program_hash == program_hash(
        result.program.source,
        visualization_id=result.program.visualization_id,
        version=result.program.version,
        runtime=result.program.runtime,
    )
    assert result.envelope()["program_hash"] == result.program_hash


def test_the_same_request_yields_the_same_program_hash() -> None:
    a = pipeline(_request("show the funding rate"), available_datasets=("funding",))
    b = pipeline(_request("show the funding rate"), available_datasets=("funding",))
    assert a.program_hash == b.program_hash
    assert a.program_hash is not None


# --------------------------------------------------------------------------
# The §25 closed-vocabulary match on short keywords (D-106)
# --------------------------------------------------------------------------


def test_a_short_keyword_matches_on_word_boundaries_only() -> None:
    """D-106: "point"/"voice" must never silently declare open_interest."""
    assert identify_datasets("show me a point of control") == ()
    assert identify_datasets("the voice of the data") == ()


def test_a_short_keyword_still_matches_standalone() -> None:
    assert identify_datasets("oi") == ("open_interest",)
    assert identify_datasets("OI trend") == ("open_interest",)
    assert identify_datasets("cvd delta") == ("trades",)


# --------------------------------------------------------------------------
# The request params reach the program (D-110, PD-11)
# --------------------------------------------------------------------------


def test_the_request_parameters_reach_the_program() -> None:
    request = GenerationRequest(
        request="show funding",
        visualization_id="gen_params",
        params={"lookback": 48},
    )
    program = generate_program(request, ("funding",))
    assert program.parameters == {"lookback": 48}


def test_a_request_without_params_yields_empty_parameters() -> None:
    program = generate_program(_request(), ("funding",))
    assert program.parameters == {}


# --------------------------------------------------------------------------
# The §25 fixture test (D-109): the program executes before it is trusted
# --------------------------------------------------------------------------


def test_the_fixture_datasets_are_marked_as_fixtures() -> None:
    """D-109: the fixture rows carry an unmistakable non-manifest marker."""
    for name, response in fixture_datasets(("candles", "funding")).items():
        assert response.dataset == name
        assert response.dataset_version == "FIXTURE"
        assert response.quality["state"] == "GREEN"


def test_the_fixture_test_executes_the_program_in_the_sandbox() -> None:
    program = generate_program(_request(), ("funding",))
    result = fixture_test(program)
    assert result["render"]["kind"] == "LINE"  # the first declared output
    assert result["program_hash"]  # the sandbox result carries the hash too


def test_the_validator_blocks_a_program_that_cannot_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§25: the fixture step is a real gate — if execution fails, validation fails."""
    import engine.visualization.ai_generation as ag

    def _boom(_program: VisualizationProgram) -> dict[str, Any]:
        raise ValidationError("fixture exploded", {"step": "fixture"})

    monkeypatch.setattr(ag, "fixture_test", _boom)
    with pytest.raises(ValidationError, match="fixture exploded"):
        validate_generated(_program(), datasets=("funding",), available=("funding",))


# --------------------------------------------------------------------------
# The M16 editor (§36; §6 semantics, D-107)
# --------------------------------------------------------------------------


def test_a_metadata_edit_needs_no_new_version() -> None:
    base = _program()
    edited = edit_program(base, {"title": "A better title"})
    assert edited.title == "A better title"
    assert edited.version == base.version
    assert edited.status == base.status


def test_a_behavior_edit_requires_a_new_version() -> None:
    """§6: behavior cannot silently change under an existing version."""
    base = _program()
    with pytest.raises(ValidationError) as excinfo:
        edit_program(base, {"source": "// rewritten compute"})
    assert "new version" in excinfo.value.message


def test_a_behavior_edit_with_a_new_version_returns_a_validated_draft() -> None:
    base = _program(status="VALIDATED")
    edited = edit_program(base, {"source": "// v2"}, version="2.0.0")
    assert edited.version == "2.0.0"
    assert edited.status == "DRAFT"
    assert edited.source == "// v2"
    assert base.source == "s"  # the frozen base is untouched
    validate_program(edited)  # the edit re-entered validation (D-102)


def test_a_version_bump_inside_changes_is_refused() -> None:
    with pytest.raises(ValidationError, match="version argument"):
        edit_program(_program(), {"version": "9.9.9"})


def test_the_identity_is_not_editable() -> None:
    with pytest.raises(ValidationError, match="identity"):
        edit_program(_program(), {"visualization_id": "other"}, version="2.0.0")


def test_an_unknown_edit_field_is_refused() -> None:
    """Fail-closed: no silently-ignored edit fields (PD-4)."""
    with pytest.raises(ValidationError, match="unknown program edit field"):
        edit_program(_program(), {"backdoor": True})


def test_a_deprecated_program_cannot_be_edited() -> None:
    """§6: DEPRECATED is the only terminal lifecycle state."""
    base = _program(status="DEPRECATED")
    with pytest.raises(ValidationError, match="DEPRECATED"):
        edit_program(base, {"title": "resurrected"})


def test_an_edit_cannot_smuggle_a_forbidden_permission() -> None:
    """D-102: the edited program re-enters the same validation a human's does."""
    base = _program()
    with pytest.raises(ValidationError, match="forbidden"):
        edit_program(
            base,
            {"permissions": {"network": True, "filesystem": False, "trading": False}},
            version="2.0.0",
        )


def test_a_sequence_edit_is_normalized_to_the_tuple_field() -> None:
    edited = edit_program(_program(), {"required_datasets": ["funding"]}, version="2.0.0")
    assert edited.required_datasets == ("funding",)


# --------------------------------------------------------------------------
# The M16 debugger (§36; §32 semantics, D-108)
# --------------------------------------------------------------------------


def _validator_ok(program: VisualizationProgram, datasets: Any, available: Any) -> bool:
    try:
        validate_generated(program, datasets=datasets, available=available)
    except ValidationError:
        return False
    return True


def test_the_debugger_reports_ok_for_a_valid_program() -> None:
    report = debug_program(_program(), datasets=("funding",), available=("funding",))
    assert report.ok is True
    assert report.findings == ()


def test_the_debugger_agrees_with_the_validator() -> None:
    """D-108: ok=True iff validate_generated would not raise — never weaker."""
    cases = [
        (_program(), ("funding",), ("funding",)),
        (
            _program(permissions={"network": True, "filesystem": False, "trading": False}),
            ("funding",),
            ("funding",),
        ),
        (_program(required_datasets=("orderbook",)), ("orderbook",), ("funding",)),
        (_program(performance_budget=None), ("funding",), ("funding",)),
        (
            _program(permissions={"network": False, "filesystem": True, "trading": False}),
            ("funding",),
            ("funding",),
        ),
    ]
    for program, datasets, available in cases:
        report = debug_program(program, datasets=datasets, available=available)
        assert report.ok is _validator_ok(program, datasets, available), (report, program)


def test_a_finding_states_what_happened_trust_and_the_next_action() -> None:
    """§32: each state states what happened, trustworthiness, next user action."""
    report = debug_program(
        _program(required_datasets=("orderbook",)),
        datasets=("orderbook",),
        available=("funding",),
    )
    assert report.ok is False
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.state == "DATA_UNAVAILABLE"
    assert finding.what_happened  # what happened
    assert finding.trustworthy is False  # whether the output is trustworthy
    assert finding.next_action  # the next user action


def test_the_debugger_collects_every_problem_not_just_the_first() -> None:
    report = debug_program(
        _program(required_datasets=("orderbook",), performance_budget=None),
        datasets=("orderbook",),
        available=("funding",),
    )
    states = sorted(f.state for f in report.findings)
    assert states == ["DATA_UNAVAILABLE", "PROGRAM_INVALID"]
    assert all(not f.trustworthy for f in report.findings)
    assert all(f.next_action for f in report.findings)


def test_a_forbidden_capability_is_reported_as_permission_denied() -> None:
    report = debug_program(
        _program(permissions={"network": True, "filesystem": False, "trading": False}),
        datasets=("funding",),
        available=("funding",),
    )
    assert report.ok is False
    assert [f.state for f in report.findings] == ["PERMISSION_DENIED"]
    assert "§3" in report.findings[0].next_action


def test_a_fixture_failure_is_reported_as_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import engine.visualization.ai_generation as ag

    def _boom(_program: VisualizationProgram) -> dict[str, Any]:
        raise ValidationError("fixture exploded", {"step": "fixture"})

    monkeypatch.setattr(ag, "fixture_test", _boom)
    report = debug_program(_program(), datasets=("funding",), available=("funding",))
    assert report.ok is False
    assert [f.state for f in report.findings] == ["RUNTIME_ERROR"]
    assert report.findings[0].what_happened == "fixture exploded"


def test_the_debugger_never_runs_the_fixture_step_for_an_unsafe_program() -> None:
    """The fixture step runs only after the static checks pass (D-108)."""
    report = debug_program(
        _program(permissions={"network": True, "filesystem": False, "trading": False}),
        datasets=("funding",),
        available=("funding",),
    )
    assert [f.state for f in report.findings] == ["PERMISSION_DENIED"]


# --------------------------------------------------------------------------
# The §26 schemas: every registered tool advertises a real contract (never a
# stub — the M9 review Phase-1 finding applied to the new layer)
# --------------------------------------------------------------------------


def test_every_registered_tool_advertises_a_real_schema() -> None:
    from engine.visualization.mcp import schemas

    assert set(schemas()) == set(TOOL_NAMES)
    for name, schema in schemas().items():
        assert schema["type"] == "object"
        assert schema["title"] == name


def test_the_registration_never_falls_back_to_a_stub() -> None:
    from engine.ai.registry import tool as registry_tool
    from engine.visualization import register_visualization_tools
    from engine.visualization.mcp import schemas

    for name in register_visualization_tools():
        assert registry_tool(name).schema == schemas()[name]


def test_the_draft_schema_advertises_the_params_argument() -> None:
    """The handler reads args["params"]; the schema must show the contract."""
    from engine.visualization.mcp import schemas

    assert "params" in schemas()["create_visualization_draft"]["properties"]
