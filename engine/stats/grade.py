"""Evidence Grade (spec 8.3; design note m5_stats §3.4).

Deterministic ladder; thresholds live in ``config/validation.yaml`` (read via
``engine.config.validation.load_evidence_grade``) and are never hard-coded
here (PD-4). Tier structure is transcribed from spec 8.3 verbatim:

- **A**: ``n_eff >= 100`` AND CI(median R) excludes 0 AND OOS sign agrees AND
  ``>= 60%`` symbols agree.
- **B**: ``n_eff >= 50`` AND CI excludes 0 AND OOS sign agrees.
- **C**: ``n_eff >= 30`` AND consistent sign, CI includes 0.
- **D**: otherwise -> "Insufficient evidence".

Interpretations pinned by the design note (D-028/D-031):

- "CI(median R)" = the cluster-bootstrap 95% CI of the median of ``fwd_ret_r``
  under one cluster model, over the DEVELOPMENT scope's pooled IS+OOS
  observations at the anchor horizon h=10.
- "OOS sign agrees" = ``sign(median OOS) == sign(median IS)``; a zero median
  counts as disagreeing (``sign(0)`` never agrees).
- "consistent sign" (C) = every segment (IS/OOS) whose median is non-zero has
  the pooled sign.
- Missing IS or OOS observations -> grade **D** (agreement unverifiable).
- HOLDOUT scope carries no grade (D-030): ``(None, reason)``; a holdout result
  is judged by the hypothesis's pre-registered criteria, not by A-D.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from engine.config.validation import REQUIRED_GRADE_KEYS
from engine.errors import ValidationError
from engine.scopes import HOLDOUT

#: Grade payload under HOLDOUT (design note §3.4, D-030).
HOLDOUT_GRADE_REASON = "n/a — HOLDOUT scope is judged by pre-registered criteria"

#: Display order, best first. The final grade is the LOWEST (worst) per-model grade.
GRADE_ORDER: tuple[str, ...] = ("A", "B", "C", "D")


def grade_rank(grade: str) -> int:
    """Rank for worst-grade selection (A best, D worst)."""
    try:
        return GRADE_ORDER.index(grade)
    except ValueError as exc:
        raise ValidationError(f"unknown evidence grade '{grade}'", {"grade": grade}) from exc


def _sign(value: float) -> int:
    return (value > 0.0) - (value < 0.0)


def _tier_min(thresholds: Mapping[str, Any], tier: str) -> int:
    entry = thresholds.get(tier)
    if not isinstance(entry, Mapping):
        raise ValidationError(
            "evidence-grade tier is not configured",
            {"tier": tier, "entry": entry},
        )
    minimum = entry.get("min_n_eff")
    if not isinstance(minimum, int) or isinstance(minimum, bool):
        raise ValidationError(
            "evidence-grade min_n_eff must be an integer",
            {"tier": tier, "min_n_eff": minimum},
        )
    return minimum


def evidence_grade(
    *,
    n_eff: int,
    ci_median_r: tuple[float, float] | None,
    pooled_median_r: float | None,
    is_median_r: float | None,
    oos_median_r: float | None,
    symbols_agree_share: float | None,
    thresholds: Mapping[str, Any],
    data_scope: str,
) -> tuple[str | None, str | None]:
    """``(grade, reason)`` for one cluster model; ``grade`` is None under HOLDOUT."""
    if data_scope == HOLDOUT:
        return (None, HOLDOUT_GRADE_REASON)
    missing = [key for key in REQUIRED_GRADE_KEYS if key not in thresholds]
    if missing:
        raise ValidationError(
            "evidence-grade thresholds are incomplete",
            {"missing": missing},
        )
    if is_median_r is None or oos_median_r is None:
        return ("D", None)  # agreement cannot be verified without both segments
    pooled_sign = _sign(pooled_median_r) if pooled_median_r is not None else None
    oos_agrees = _sign(is_median_r) != 0 and _sign(oos_median_r) == _sign(is_median_r)
    consistent = pooled_sign is not None and all(
        _sign(segment) == pooled_sign for segment in (is_median_r, oos_median_r) if segment != 0.0
    )
    excludes_zero = ci_median_r is not None and (ci_median_r[0] > 0.0 or ci_median_r[1] < 0.0)
    includes_zero = ci_median_r is not None and ci_median_r[0] <= 0.0 <= ci_median_r[1]

    min_a = _tier_min(thresholds, "A")
    agree_needed = thresholds["A"].get("symbols_agree")
    if not isinstance(agree_needed, (int, float)) or isinstance(agree_needed, bool):
        raise ValidationError(
            "evidence-grade A.symbols_agree must be a number",
            {"symbols_agree": agree_needed},
        )
    min_b = _tier_min(thresholds, "B")
    min_c = _tier_min(thresholds, "C")

    if (
        n_eff >= min_a
        and excludes_zero
        and oos_agrees
        and symbols_agree_share is not None
        and symbols_agree_share >= agree_needed
    ):
        return ("A", None)
    if n_eff >= min_b and excludes_zero and oos_agrees:
        return ("B", None)
    if n_eff >= min_c and consistent and includes_zero:
        return ("C", None)
    return ("D", None)
