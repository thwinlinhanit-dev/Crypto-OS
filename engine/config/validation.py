"""PD-11 companion for ``config/validation.yaml`` (spec section 8.3).

Section 8.3 puts the Evidence Grade's deterministic thresholds "in config";
this module is the only reader. An empty or missing ``evidence_grade``
section is *configuration missing* (PD-4): callers fail closed with
:class:`ValidationError` rather than falling back to hard-coded numbers.
Changes to any threshold need a DECISIONS entry (section 9.3 rule).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from engine.errors import ValidationError

VALIDATION_PATH = Path("config") / "validation.yaml"

#: Keys section 8.3 needs to grade; a section missing any of them is unusable.
REQUIRED_GRADE_KEYS: tuple[str, ...] = (
    "anchor_horizon",
    "ci_level",
    "A",
    "B",
    "C",
    "ci_excludes_zero",
    "ci_includes_zero",
    "d_label",
)


def load_validation(path: Path = VALIDATION_PATH) -> dict[str, Any]:
    """Read ``config/validation.yaml`` (thresholds live only in that file)."""
    if not path.is_file():
        raise FileNotFoundError(f"config/validation.yaml not found at {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the top level")
    return raw


def load_evidence_grade(path: Path = VALIDATION_PATH) -> dict[str, Any]:
    """The ``evidence_grade`` thresholds, fail-closed when incomplete (PD-4)."""
    section = load_validation(path).get("evidence_grade")
    if not isinstance(section, Mapping) or not section:
        raise ValidationError(
            "evidence-grade thresholds are not configured in config/validation.yaml",
            {"path": str(path), "section": "evidence_grade"},
        )
    missing = [key for key in REQUIRED_GRADE_KEYS if key not in section]
    if missing:
        raise ValidationError(
            "evidence-grade thresholds are incomplete",
            {"path": str(path), "missing": missing},
        )
    return dict(section)
