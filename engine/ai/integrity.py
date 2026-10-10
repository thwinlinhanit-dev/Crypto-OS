"""M9 — number integrity (spec §9, design note m9_ai section 3, D-059).

The model emits numbers only as placeholders
``{{ref:<ref_id>#<json_path>}}`` resolved server-side from stored tool
results (PD-5/PD-6). Raw numerals in free text (outside placeholders) are
flagged in the UI and logged (the M9 AC).

Fail-closed rules:
- a placeholder whose ref_id is unknown is removed and flagged;
- a placeholder whose json_path is unknown is removed and flagged;
- an unresolved placeholder is never rendered as a raw number.
"""

from __future__ import annotations

import re
from typing import Any, Final

PLACEHOLDER: Final = r"\{\{ref:(?P<ref>[A-Za-z0-9_\-:.]+)#(?P<path>[^}]+)\}\}"
_PLACEHOLDER_RE: Final = re.compile(PLACEHOLDER)
_NUMERAL_RE: Final = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")


def resolve_placeholders(
    text: str,
    tool_results: dict[str, Any],
) -> tuple[str, list[str]]:
    """Substitute every ``{{ref:<id>#<path>}}`` from ``tool_results``.

    Returns ``(resolved_text, flagged)``: unresolvable placeholders are
    removed from the text and listed in ``flagged`` (D-059: never rendered
    raw).
    """
    flagged: list[str] = []

    def _substitute(match: re.Match[str]) -> str:
        ref = match.group("ref")
        path = match.group("path")
        result = tool_results.get(ref)
        if not isinstance(result, dict):
            flagged.append(f"{ref}#{path}")
            return ""
        value: Any = result
        for segment in path.split("."):
            if isinstance(value, dict) and segment in value:
                value = value[segment]
            else:
                flagged.append(f"{ref}#{path}")
                return ""
        return str(value)

    return _PLACEHOLDER_RE.sub(_substitute, text), flagged


def scan_free_text(text: str) -> list[str]:
    """Numerals in free text outside placeholders (the AC: flagged + logged).

    Placeholders are stripped first, so a resolved number is not double
    flagged.
    """
    stripped = _PLACEHOLDER_RE.sub("", text)
    return [str(match) for match in _NUMERAL_RE.findall(stripped)]


def validate_report(
    text: str,
    *,
    tool_results: dict[str, Any],
) -> dict[str, Any]:
    """The report gate: placeholders resolved, free-text numerals flagged.

    Reports must also carry n_eff / intervals / data_scope / OOS /
    multiple-testing / limitations (spec §9); the presence check is the
    report's own responsibility (the M6a/e shape), this gate covers the
    number-integrity half.
    """
    resolved, flagged = resolve_placeholders(text, tool_results)
    numerals = scan_free_text(resolved)
    return {
        "ok": not flagged and not numerals,
        "resolved": resolved,
        "unresolved_placeholders": flagged,
        "free_text_numerals": numerals,
    }
