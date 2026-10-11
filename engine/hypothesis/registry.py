"""HF Phase 1 — the hypothesis registry (spec §6/§8/§12/§18.2).

Deterministic dedup keys first (spec §8: "Use deterministic feature and
target comparisons first"); the AI-assisted semantic comparison is an
optional supplement (Phase 5). Relationships form the evidence graph; the
research ledger declares the testing family BEFORE the inferential analysis
(HF-005, spec §12).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Any, Final
from uuid import UUID

from sqlalchemy import Connection, text

from engine.errors import NotFoundError, ValidationError
from engine.hypothesis.models import (
    assert_valid_domain,
    assert_valid_origin,
    assert_valid_relationship,
    assert_valid_status,
    assert_valid_transition,
)

#: The registered feature names (the M3 registry) — an agent cannot invent a
#: feature (spec §7.1). Phase 3 wires this to the real feature registry.
KNOWN_FEATURES: Final[frozenset[str]] = frozenset(
    {
        "rvol", "cpos", "upper_wick", "atr", "ema", "sma", "vwap", "bb",
        "close", "open", "high", "low", "volume",
        "funding_rate", "open_interest", "liquidation_qty",
        "trend_state", "vol_state", "volume_state",
        "oi_change_pct", "funding_percentile", "event_volume_z",
    }
)

REQUIRED_VERSION_FIELDS: Final[tuple[str, ...]] = (
    "null_hypothesis",
    "alternative_hypothesis",
    "independent_vars",
    "dependent_vars",
    "features",
    "population",
    "timeframe",
    "observation_ts_rule",
    "outcome_horizon",
    "expected_direction",
    "data_requirements",
    "proposed_statistical_method",
    "falsification_criteria",
)


def dedup_key(spec: Mapping[str, Any]) -> str:
    """The deterministic dedup key: the sha256 of the canonical JSON of the
    normalized (features + target + horizon) claim (spec §8 exact-dup
    level)."""
    normalized = {
        "features": sorted(str(f) for f in spec.get("features", [])),
        "independent_vars": sorted(
            json.dumps(v, sort_keys=True, default=str)
            for v in spec.get("independent_vars", [])
        ),
        "dependent_vars": sorted(
            json.dumps(v, sort_keys=True, default=str)
            for v in spec.get("dependent_vars", [])
        ),
        "horizon": str(spec.get("outcome_horizon", "")),
        "direction": str(spec.get("expected_direction", "")),
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_definition(spec: Mapping[str, Any]) -> None:
    """Reject an underspecified hypothesis (spec §6/§10: "must reject
    incomplete or ambiguous specifications")."""
    missing = [field for field in REQUIRED_VERSION_FIELDS if not spec.get(field)]
    if missing:
        raise ValidationError(
            "hypothesis definition is underspecified",
            {"missing_fields": missing},
        )
    unknown_features = [
        str(f) for f in spec.get("features", []) if str(f) not in KNOWN_FEATURES
    ]
    if unknown_features:
        raise ValidationError(
            "unknown feature in hypothesis definition (spec 7.1)",
            {"unknown_features": unknown_features, "known": sorted(KNOWN_FEATURES)},
        )
    direction = str(spec.get("expected_direction", ""))
    if direction not in ("HIGHER", "LOWER", "DIFFERENT"):
        raise ValidationError(
            "expected_direction must be HIGHER, LOWER or DIFFERENT",
            {"expected_direction": direction},
        )
    # the observation timestamp rule is mandatory: a hypothesis without a
    # time-availability rule cannot be evaluated causally (PD-2)
    rule = str(spec.get("observation_ts_rule", ""))
    if not rule:
        raise ValidationError(
            "observation_ts_rule is required (PD-2)",
            {"observation_ts_rule": rule},
        )
    # the rule must anchor on a CLOSED bar and must not reference the future
    # (a next-bar or forward timestamp is look-ahead, PD-2)
    leak_markers = ("next bar", "forward", "future", "subsequent", "after the bar")
    if any(marker in rule.lower() for marker in leak_markers):
        raise ValidationError(
            "observation_ts_rule references future information (PD-2)",
            {"observation_ts_rule": rule},
        )
    if "close_time" not in rule and "bar_close" not in rule and "closed" not in rule:
        raise ValidationError(
            "observation_ts_rule must anchor on a closed-bar timestamp (PD-2)",
            {"observation_ts_rule": rule},
        )


def create_hypothesis(
    conn: Connection,
    *,
    title: str,
    domain: str,
    origin: str,
    spec: Mapping[str, Any],
    original_question: str | None = None,
    created_by: str = "owner",
) -> str:
    """Create a hypothesis + its first version (spec §6)."""
    assert_valid_domain(domain)
    assert_valid_origin(origin)
    validate_definition(spec)
    hyp_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO research.hypothesis (hyp_id, title, statement, status,"
            " criteria, preregistered_at, hf_domain, hf_origin, hf_status,"
            " original_question)"
            " VALUES (:id, :title, :statement, 'TESTING', :criteria, now(),"
            " :domain, :origin, 'NOT_TESTED', :question)"
        ),
        {
            "id": hyp_id,
            "title": title,
            "statement": str(spec.get("alternative_hypothesis", "")),
            "criteria": json.dumps({"pass": [], "kill": []}),
            "domain": domain,
            "origin": origin,
            "question": original_question,
        },
    )
    _insert_version(conn, hypothesis_id=hyp_id, version=1, spec=spec, created_by=created_by)
    conn.execute(
        text(
            "INSERT INTO system.audit_log (actor, action, target, detail)"
            " VALUES (:actor, 'hypothesis.create', :target, :detail)"
        ),
        {
            "actor": created_by,
            "target": str(hyp_id),
            "detail": json.dumps({"domain": domain, "origin": origin, "version": 1}),
        },
    )
    return str(hyp_id)


def _insert_version(
    conn: Connection,
    *,
    hypothesis_id: UUID,
    version: int,
    spec: Mapping[str, Any],
    created_by: str,
) -> None:
    validate_definition(spec)
    conn.execute(
        text(
            "INSERT INTO research.hypothesis_version (hypothesis_id, version,"
            " description, mechanism, null_hypothesis, alternative_hypothesis,"
            " independent_vars, dependent_vars, features, population, timeframe,"
            " event_definition, observation_ts_rule, outcome_horizon,"
            " expected_direction, baseline, inclusion_criteria, exclusion_criteria,"
            " confounders, data_requirements, proposed_statistical_method,"
            " proposed_robustness_tests, falsification_criteria,"
            " economic_significance_threshold, known_limitations, created_by)"
            " VALUES (:id, :v, :description, :mechanism, :null_h, :alt_h,"
            " CAST(:ivars AS jsonb), CAST(:dvars AS jsonb), CAST(:features AS jsonb),"
            " :population, :tf, CAST(:event AS jsonb), :ts_rule, :horizon, :direction,"
            " :baseline, CAST(:incl AS jsonb), CAST(:excl AS jsonb),"
            " CAST(:conf AS jsonb), CAST(:data_req AS jsonb), :method,"
            " CAST(:robust AS jsonb), CAST(:falsify AS jsonb), :econ, :limits, :by)"
        ),
        {
            "id": hypothesis_id,
            "v": version,
            "description": spec.get("description"),
            "mechanism": spec.get("mechanism"),
            "null_h": str(spec["null_hypothesis"]),
            "alt_h": str(spec["alternative_hypothesis"]),
            "ivars": json.dumps(list(spec["independent_vars"]), default=str),
            "dvars": json.dumps(list(spec["dependent_vars"]), default=str),
            "features": json.dumps(list(spec["features"]), default=str),
            "population": str(spec["population"]),
            "tf": str(spec["timeframe"]),
            "event": json.dumps(spec.get("event_definition"), default=str),
            "ts_rule": str(spec["observation_ts_rule"]),
            "horizon": str(spec["outcome_horizon"]),
            "direction": str(spec["expected_direction"]),
            "baseline": spec.get("baseline"),
            "incl": json.dumps(spec.get("inclusion_criteria"), default=str),
            "excl": json.dumps(spec.get("exclusion_criteria"), default=str),
            "conf": json.dumps(spec.get("confounders"), default=str),
            "data_req": json.dumps(list(spec["data_requirements"]), default=str),
            "method": str(spec["proposed_statistical_method"]),
            "robust": json.dumps(spec.get("proposed_robustness_tests"), default=str),
            "falsify": json.dumps(list(spec["falsification_criteria"]), default=str),
            "econ": spec.get("economic_significance_threshold"),
            "limits": spec.get("known_limitations"),
            "by": created_by,
        },
    )


def add_version(
    conn: Connection,
    *,
    hypothesis_id: str,
    spec: Mapping[str, Any],
    created_by: str = "owner",
) -> int:
    """Append a new version (spec §18.1: a change produces a new version)."""
    row = conn.execute(
        text("SELECT hyp_id, hf_status FROM research.hypothesis WHERE hyp_id = :id"),
        {"id": hypothesis_id},
    ).first()
    if row is None:
        raise NotFoundError("hypothesis not found", {"hypothesis_id": hypothesis_id})
    next_version = (
        conn.execute(
            text(
                "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM research.hypothesis_version"
                " WHERE hypothesis_id = :id"
            ),
            {"id": hypothesis_id},
        ).scalar()
        or 1
    )
    _insert_version(
        conn,
        hypothesis_id=UUID(str(row.hyp_id)),
        version=int(next_version),
        spec=spec,
        created_by=created_by,
    )
    return int(next_version)


def get_hypothesis(conn: Connection, hypothesis_id: str) -> dict[str, Any]:
    """The hypothesis + its latest version (spec §20.4 detail view)."""
    row = conn.execute(
        text(
            "SELECT hyp_id, title, hf_domain, hf_origin, hf_status, original_question,"
            " preregistered_at AS created_at FROM research.hypothesis WHERE hyp_id = :id"
        ),
        {"id": hypothesis_id},
    ).first()
    if row is None:
        raise NotFoundError("hypothesis not found", {"hypothesis_id": hypothesis_id})
    version = conn.execute(
        text(
            "SELECT * FROM research.hypothesis_version WHERE hypothesis_id = :id"
            " ORDER BY version DESC LIMIT 1"
        ),
        {"id": hypothesis_id},
    ).first()
    return {
        "hypothesis_id": str(row.hyp_id),
        "title": row.title,
        "domain": row.hf_domain,
        "origin": row.hf_origin,
        "status": row.hf_status,
        "original_question": row.original_question,
        "version": version.version if version else None,
        "spec": _version_dict(version) if version else None,
    }


def _version_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "version": row.version,
        "description": row.description,
        "mechanism": row.mechanism,
        "null_hypothesis": row.null_hypothesis,
        "alternative_hypothesis": row.alternative_hypothesis,
        "independent_vars": row.independent_vars,
        "dependent_vars": row.dependent_vars,
        "features": row.features,
        "population": row.population,
        "timeframe": row.timeframe,
        "event_definition": row.event_definition,
        "observation_ts_rule": row.observation_ts_rule,
        "outcome_horizon": row.outcome_horizon,
        "expected_direction": row.expected_direction,
        "baseline": row.baseline,
        "falsification_criteria": row.falsification_criteria,
        "economic_significance_threshold": (
            float(row.economic_significance_threshold)
            if row.economic_significance_threshold is not None
            else None
        ),
        "created_by": row.created_by,
    }


def set_status(conn: Connection, hypothesis_id: str, target: str) -> None:
    """A status transition through the explicit table (spec §14.2)."""
    row = conn.execute(
        text("SELECT hf_status FROM research.hypothesis WHERE hyp_id = :id"),
        {"id": hypothesis_id},
    ).first()
    if row is None:
        raise NotFoundError("hypothesis not found", {"hypothesis_id": hypothesis_id})
    assert_valid_transition(str(row.hf_status), target)
    conn.execute(
        text("UPDATE research.hypothesis SET hf_status = :s WHERE hyp_id = :id"),
        {"s": target, "id": hypothesis_id},
    )
    conn.execute(
        text(
            "INSERT INTO system.audit_log (actor, action, target, detail)"
            " VALUES ('owner', 'hypothesis.status', :target, :detail)"
        ),
        {
            "target": hypothesis_id,
            "detail": json.dumps({"from": str(row.hf_status), "to": target}),
        },
    )


def add_relationship(
    conn: Connection,
    *,
    from_id: str,
    to_id: str,
    relationship: str,
    rationale: str | None = None,
) -> None:
    """An evidence-graph edge (spec §18.2)."""
    assert_valid_relationship(relationship)
    if from_id == to_id:
        raise ValidationError("a hypothesis cannot relate to itself", {})
    for label, hyp_id in (("from", from_id), ("to", to_id)):
        row = conn.execute(
            text("SELECT hyp_id FROM research.hypothesis WHERE hyp_id = :id"),
            {"id": hyp_id},
        ).first()
        if row is None:
            raise NotFoundError(
                "related hypothesis not found", {"hypothesis_id": hyp_id, "side": label}
            )
    conn.execute(
        text(
            "INSERT INTO research.hypothesis_relationship (from_hypothesis_id,"
            " to_hypothesis_id, relationship, rationale)"
            " VALUES (:f, :t, :r, :why)"
        ),
        {"f": from_id, "t": to_id, "r": relationship, "why": rationale},
    )


def declare_testing_family(
    conn: Connection,
    *,
    family_id: str,
    hypothesis_id: str,
    correction_plan: str,
) -> None:
    """The research ledger: the testing family + correction declared BEFORE
    the inferential analysis (HF-005, spec §12)."""
    if correction_plan not in (
        "BENJAMINI_HOCHBERG", "BONFERRONI", "WHITE_REALITY_CHECK",
        "DEFLATED_SHARPE", "NONE_DECLARED",
    ):
        raise ValidationError(
            "unknown multiple-testing correction plan",
            {"correction_plan": correction_plan},
        )
    row = conn.execute(
        text("SELECT hyp_id FROM research.hypothesis WHERE hyp_id = :id"),
        {"id": hypothesis_id},
    ).first()
    if row is None:
        raise NotFoundError("hypothesis not found", {"hypothesis_id": hypothesis_id})
    conn.execute(
        text(
            "INSERT INTO research.research_ledger (family_id, hypothesis_id,"
            " correction_plan) VALUES (:family, :hyp, :plan)"
        ),
        {"family": family_id, "hyp": hypothesis_id, "plan": correction_plan},
    )


def find_duplicates(conn: Connection, spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The deterministic exact-dup search (spec §8 level 1)."""
    key = dedup_key(spec)
    rows = conn.execute(
        text(
            "SELECT DISTINCT h.hyp_id, h.title, h.hf_status FROM research.hypothesis h"
            " JOIN research.hypothesis_version v ON v.hypothesis_id = h.hyp_id"
            " WHERE :key = '' OR TRUE"
        ),
        {"key": key},
    ).all()
    # the deterministic comparison is done in Python against the stored
    # versions' (features + target + horizon) tuples (the DB has no
    # expression index over the normalized JSON)
    matches: list[dict[str, Any]] = []
    for row in rows:
        version = conn.execute(
            text(
                "SELECT features, independent_vars, dependent_vars, outcome_horizon,"
                " expected_direction FROM research.hypothesis_version"
                " WHERE hypothesis_id = :id ORDER BY version DESC LIMIT 1"
            ),
            {"id": row.hyp_id},
        ).first()
        if version is None:
            continue
        if dedup_key(_version_dedup_payload(version)) == key:
            matches.append(
                {
                    "hypothesis_id": str(row.hyp_id),
                    "title": row.title,
                    "status": row.hf_status,
                    "match": "EXACT",
                }
            )
    return matches


def _version_dedup_payload(version: Any) -> dict[str, Any]:
    return {
        "features": version.features or [],
        "independent_vars": version.independent_vars or [],
        "dependent_vars": version.dependent_vars or [],
        "outcome_horizon": version.outcome_horizon,
        "expected_direction": version.expected_direction,
    }


def assert_status_valid(status: str) -> None:
    """Re-exported for the API layer."""
    assert_valid_status(status)
