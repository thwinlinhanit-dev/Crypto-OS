"""M5 AC - grade wording rules (spec 8.3, design note m5_stats section 10).

1. The allowed wordings live in the shared ``EvidenceGradeBadge`` component:
   ``Meets pre-defined evidence criteria (grade X)`` for A-C and
   ``Insufficient evidence`` for D (plus the HOLDOUT null-grade reason).
2. Banned tokens (case-insensitive, word boundaries) appear nowhere in UI
   sources or AI templates: works, proven, validated edge, guaranteed.
3. The badge is the only renderer of grade wording (import-site assertion):
   no other web source may carry the allowed literals.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = REPO_ROOT / "apps" / "web"
AI_ROOT = REPO_ROOT / "engine" / "ai"
BADGE = WEB_ROOT / "components" / "EvidenceGradeBadge.tsx"

BANNED = (r"works", r"proven", r"validated edge", r"guaranteed")
BANNED_RES = [re.compile(r"\b" + token + r"\b", re.IGNORECASE) for token in BANNED]


def _web_sources() -> list[Path]:
    files: list[Path] = []
    for suffix in (".ts", ".tsx"):
        files.extend(WEB_ROOT.rglob(f"*{suffix}"))
    return [
        path
        for path in files
        if "node_modules" not in path.parts
        and ".next" not in path.parts
        and not path.name.endswith(".test.ts")
        and not path.name.endswith(".test.tsx")
    ]


def test_allowed_wording_lives_in_the_badge() -> None:
    source = BADGE.read_text(encoding="utf-8")
    assert "Meets pre-defined evidence criteria (grade " in source
    assert "Insufficient evidence" in source
    assert "HOLDOUT scope is judged by pre-registered criteria" in source


def test_badge_is_the_only_wording_renderer() -> None:
    holders = [
        str(path.relative_to(REPO_ROOT))
        for path in _web_sources()
        if "Meets pre-defined evidence criteria" in path.read_text(encoding="utf-8")
        or "Insufficient evidence" in path.read_text(encoding="utf-8")
    ]
    assert holders == [str(BADGE.relative_to(REPO_ROOT))]


def test_banned_wording_absent() -> None:
    scanned: list[Path] = list(_web_sources())
    if AI_ROOT.is_dir():
        scanned.extend(p for p in AI_ROOT.rglob("*") if p.is_file())
    violations: list[str] = []
    for path in scanned:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in BANNED_RES:
            if pattern.search(text):
                violations.append(f"{path.relative_to(REPO_ROOT)}: {pattern.pattern}")
    assert violations == []
