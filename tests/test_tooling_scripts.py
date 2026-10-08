"""Windows PowerShell 5.1 reads BOM-less files as ANSI.

A single non-ASCII character (for example an em dash) is then decoded as a
byte that PowerShell accepts as a *string delimiter*, which silently unbalances
the parse - observed once on ``scripts/setup.ps1``, where ``make setup`` failed
with "Missing closing '}'". Keeping tooling scripts and the Makefile ASCII-only
removes the whole class of failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TARGETS: list[Path] = [*sorted((ROOT / "scripts").glob("*.ps1")), ROOT / "Makefile"]


def test_the_guard_has_something_to_guard() -> None:
    assert len(TARGETS) > 5
    assert (ROOT / "scripts" / "setup.ps1") in TARGETS


@pytest.mark.parametrize("path", TARGETS, ids=lambda path: path.name)
def test_tooling_text_is_ascii(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    offenders = sorted({f"U+{ord(char):04X}" for char in text if ord(char) > 127})
    assert not offenders, f"{path.name} contains non-ASCII characters: {offenders}"
