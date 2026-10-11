"""M11 — the a11y audit (spec M11 T11.1, design note m11_hardening
section 4, D-080).

The Master §61-62 states are already wired per page (loading/empty/error/
stale/partial); M11 adds the automated contrast check over the CSS vars
(D-080: the WCAG AA ratio pinned by a test) and the reduced-motion rule.
"""

from __future__ import annotations

import re
from pathlib import Path

GLOBALS_CSS = (
    Path(__file__).resolve().parents[1] / "apps" / "web" / "app" / "globals.css"
)

#: The WCAG AA contrast thresholds (D-080).
AA_NORMAL_TEXT = 4.5  # 4.5:1 for normal text
AA_LARGE_TEXT = 3.0  # 3:1 for large text / UI components


def _parse_vars(css: str) -> dict[str, str]:
    """The ``--name: #hex`` custom properties in the CSS (any indent).

    Keys keep the ``--`` prefix so they match the CSS source.
    """
    return {
        "--" + match.group("name"): match.group("value")
        for match in re.finditer(
            r"--(?P<name>[a-z-]+):\s*(?P<value>#[0-9a-fA-F]{6});", css
        )
    }


def _luminance(hex_color: str) -> float:
    """The relative luminance of a #rrggbb color (WCAG)."""
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))

    def _channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast(foreground: str, background: str) -> float:
    """The WCAG contrast ratio between two #rrggbb colors."""
    lum1, lum2 = _luminance(foreground), _luminance(background)
    lighter, darker = max(lum1, lum2), min(lum1, lum2)
    return (lighter + 0.05) / (darker + 0.05)


def test_the_theme_vars_exist() -> None:
    css = GLOBALS_CSS.read_text(encoding="utf-8")
    vars_ = _parse_vars(css)
    assert {"--bg", "--panel", "--text", "--muted", "--line"} <= set(vars_)


def test_body_text_meets_aa() -> None:
    """D-080: --text on --bg and --panel meets the 4.5:1 AA ratio."""
    css = GLOBALS_CSS.read_text(encoding="utf-8")
    vars_ = _parse_vars(css)
    assert _contrast(vars_["--text"], vars_["--bg"]) >= AA_NORMAL_TEXT
    assert _contrast(vars_["--text"], vars_["--panel"]) >= AA_NORMAL_TEXT


def test_muted_text_meets_aa() -> None:
    """--muted is used for secondary text; it must still meet AA."""
    css = GLOBALS_CSS.read_text(encoding="utf-8")
    vars_ = _parse_vars(css)
    assert _contrast(vars_["--muted"], vars_["--bg"]) >= AA_NORMAL_TEXT
    assert _contrast(vars_["--muted"], vars_["--panel"]) >= AA_NORMAL_TEXT


def test_status_colors_meet_large_text_aa() -> None:
    """The status colors (ok/bad/warn) are large-text / UI-component
    sized; they meet the 3:1 AA ratio."""
    css = GLOBALS_CSS.read_text(encoding="utf-8")
    vars_ = _parse_vars(css)
    for color in ("--ok", "--bad", "--warn"):
        if color in vars_:
            assert _contrast(vars_[color], vars_["--bg"]) >= AA_LARGE_TEXT, color


def test_the_reduced_motion_rule_exists() -> None:
    """The chart's transitions honor prefers-reduced-motion (D-080)."""
    css = GLOBALS_CSS.read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css


def test_interactive_controls_have_aria_labels() -> None:
    """The chart's non-canvas controls carry aria-labels (D-081: the canvas
    is keyboard-operable through the palette)."""
    chart_page = (
        Path(__file__).resolve().parents[1]
        / "apps"
        / "web"
        / "app"
        / "chart"
        / "page.tsx"
    )
    source = chart_page.read_text(encoding="utf-8")
    assert "aria-label" in source


def test_nav_is_a_landmark() -> None:
    """The primary nav is a nav landmark with an accessible name."""
    nav = (
        Path(__file__).resolve().parents[1] / "apps" / "web" / "app" / "nav.tsx"
    )
    source = nav.read_text(encoding="utf-8")
    assert '<nav className="nav" aria-label="Primary">' in source
