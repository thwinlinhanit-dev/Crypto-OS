"""M9 — number-integrity tests (spec §9, design note m9_ai section 3,
D-059): the placeholder resolver + the free-text numeral flagging.
"""

from __future__ import annotations

from engine.ai.integrity import resolve_placeholders, scan_free_text, validate_report

RESULTS: dict[str, object] = {
    "s1": {"grade": "A", "n_eff": 23, "baseline": {"p": 0.03}},
    "s2": {"rows": 17},
}


def test_resolves_a_placeholder_from_the_stored_result() -> None:
    text = "Grade {{ref:s1#grade}} with n_eff {{ref:s1#n_eff}}."
    resolved, flagged = resolve_placeholders(text, RESULTS)
    assert resolved == "Grade A with n_eff 23."
    assert flagged == []


def test_resolves_a_nested_path() -> None:
    text = "Baseline p {{ref:s1#baseline.p}}."
    resolved, flagged = resolve_placeholders(text, RESULTS)
    assert resolved == "Baseline p 0.03."
    assert flagged == []


def test_unknown_ref_is_removed_and_flagged() -> None:
    """D-059: a placeholder that cannot be resolved is removed and flagged,
    never rendered as a raw number."""
    text = "Grade {{ref:missing#grade}} here."
    resolved, flagged = resolve_placeholders(text, RESULTS)
    assert resolved == "Grade  here."
    assert flagged == ["missing#grade"]


def test_unknown_path_is_removed_and_flagged() -> None:
    text = "n {{ref:s1#absent}} here."
    resolved, flagged = resolve_placeholders(text, RESULTS)
    assert resolved == "n  here."
    assert flagged == ["s1#absent"]


def test_scalar_path_through_leaf_fails_closed() -> None:
    text = "{{ref:s1#grade.deeper}}"
    resolved, flagged = resolve_placeholders(text, RESULTS)
    assert resolved == ""
    assert flagged == ["s1#grade.deeper"]


def test_multiple_placeholders_resolve_in_one_pass() -> None:
    text = "{{ref:s1#n_eff}} events, {{ref:s2#rows}} rows."
    resolved, flagged = resolve_placeholders(text, RESULTS)
    assert resolved == "23 events, 17 rows."
    assert flagged == []


def test_free_text_numerals_are_flagged() -> None:
    """The M9 AC: numbers without ref are flagged (and the UI logs them)."""
    text = "The strategy made 42 trades at 1.5 R."
    assert scan_free_text(text) == ["42", "1.5"]


def test_a_model_that_resolved_its_own_placeholder_is_still_flagged() -> None:
    """The scan runs on what the model *emitted*: a number the model
    substituted itself (instead of leaving the placeholder for the server)
    is raw output and is flagged. This is the leak the rule exists to
    catch."""
    resolved, _ = resolve_placeholders("{{ref:s1#n_eff}}", RESULTS)
    assert resolved == "23"
    assert scan_free_text(resolved) == ["23"]


def test_prose_without_numerals_passes() -> None:
    assert scan_free_text("No numbers in this sentence.") == []


def test_validate_report_ok_when_all_numbers_carry_refs() -> None:
    text = "Grade {{ref:s1#grade}}; n_eff is hidden from the text."
    report = validate_report(text, tool_results=RESULTS)
    assert report["ok"] is True
    assert report["unresolved_placeholders"] == []
    assert report["free_text_numerals"] == []


def test_validate_report_flags_both_failure_modes() -> None:
    text = "n_eff 99 (grade {{ref:s1#grade}})."
    report = validate_report(text, tool_results=RESULTS)
    assert report["ok"] is False
    assert report["free_text_numerals"] == ["99"]
