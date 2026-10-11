"""M11 — the backup/export bundle (spec M11 T11.4, design note m11_hardening
section 3, D-078/D-079).

A backup bundle is a JSON document with a schema version, the created-at
timestamp and three sections: ``notebook`` (notes), ``registry``
(hypotheses + experiment families + variants + experiments) and
``lineage`` (the backtest + validation refs). Export is read-only over the
live rows; import validates every section and is idempotent (ON CONFLICT
DO NOTHING). No secrets, no data files: the bundle is metadata + lineage
(PD-6).

The frozen-object contract survives the round trip: a frozen experiment
exports frozen and imports frozen (D-021/D-079).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import Connection, text

#: The bundle schema version (bump on any shape change).
BACKUP_SCHEMA_VERSION: Final = "1"


def _iso(value: Any) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def export_backup(conn: Connection) -> dict[str, Any]:
    """Build the backup bundle from the live rows (read-only)."""
    notes = conn.execute(
        text(
            "SELECT note_id, title, body_md, experiment_id, created_at"
            " FROM research.note ORDER BY created_at"
        )
    ).all()
    hypotheses = conn.execute(
        text(
            "SELECT hyp_id, title, statement, status, criteria, preregistered_at,"
            " holdout_opened_at, holdout_result, autopsy, locked"
            " FROM research.hypothesis ORDER BY preregistered_at"
        )
    ).all()
    families = conn.execute(
        text(
            "SELECT family_id, description, search_space, hypothesis_ids, created_at"
            " FROM research.experiment_family ORDER BY created_at"
        )
    ).all()
    experiments = conn.execute(
        text(
            "SELECT experiment_id, hypothesis_id, family_id, title, status,"
            " conclusion, assessment, universe, timeframe, data_scope,"
            " dataset_version, frozen, frozen_at, created_at"
            " FROM research.experiment ORDER BY created_at"
        )
    ).all()
    backtests = conn.execute(
        text(
            "SELECT backtest_id, experiment_id, strategy_id, version, status"
            " FROM strategy.backtest ORDER BY created_at"
        )
    ).all()

    # the HF tables (0011): the version history, the evidence graph and the
    # multiple-testing ledger - the research record (spec §18.2/§12)
    hf_versions = conn.execute(
        text(
            "SELECT hypothesis_id, version, description, mechanism, null_hypothesis,"
            " alternative_hypothesis, independent_vars, dependent_vars, features,"
            " population, timeframe, event_definition, observation_ts_rule,"
            " outcome_horizon, expected_direction, baseline, inclusion_criteria,"
            " exclusion_criteria, confounders, data_requirements,"
            " proposed_statistical_method, proposed_robustness_tests,"
            " falsification_criteria, economic_significance_threshold,"
            " known_limitations, created_by"
            " FROM research.hypothesis_version ORDER BY hypothesis_id, version"
        )
    ).all()
    hf_relationships = conn.execute(
        text(
            "SELECT from_hypothesis_id, to_hypothesis_id, relationship, rationale"
            " FROM research.hypothesis_relationship ORDER BY from_hypothesis_id,"
            " to_hypothesis_id, relationship"
        )
    ).all()
    hf_ledger = conn.execute(
        text(
            "SELECT family_id, hypothesis_id, correction_plan"
            " FROM research.research_ledger ORDER BY family_id, hypothesis_id"
        )
    ).all()
    hf_registry = conn.execute(
        text(
            "SELECT hyp_id, hf_domain, hf_origin, hf_status, original_question"
            " FROM research.hypothesis"
        )
    ).all()

    return {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "hypothesis_factory": {
            "registry": [
                {
                    "hypothesis_id": str(row.hyp_id),
                    "domain": row.hf_domain,
                    "origin": row.hf_origin,
                    "status": row.hf_status,
                    "original_question": row.original_question,
                }
                for row in hf_registry
            ],
            "versions": [
                {
                    "hypothesis_id": str(row.hypothesis_id),
                    "version": int(row.version),
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
                    "inclusion_criteria": row.inclusion_criteria,
                    "exclusion_criteria": row.exclusion_criteria,
                    "confounders": row.confounders,
                    "data_requirements": row.data_requirements,
                    "proposed_statistical_method": row.proposed_statistical_method,
                    "proposed_robustness_tests": row.proposed_robustness_tests,
                    "falsification_criteria": row.falsification_criteria,
                    "economic_significance_threshold": row.economic_significance_threshold,
                    "known_limitations": row.known_limitations,
                    "created_by": row.created_by,
                }
                for row in hf_versions
            ],
            "relationships": [
                {
                    "from": str(row.from_hypothesis_id),
                    "to": str(row.to_hypothesis_id),
                    "relationship": row.relationship,
                    "rationale": row.rationale,
                }
                for row in hf_relationships
            ],
            "ledger": [
                {
                    "family_id": row.family_id,
                    "hypothesis_id": str(row.hypothesis_id),
                    "correction_plan": row.correction_plan,
                }
                for row in hf_ledger
            ],
        },
        "notebook": [
            {
                "note_id": str(row.note_id),
                "title": row.title,
                "body": row.body_md,
                "experiment_id": str(row.experiment_id) if row.experiment_id else None,
                "created_at": _iso(row.created_at),
            }
            for row in notes
        ],
        "registry": {
            "hypotheses": [
                {
                    "hypothesis_id": str(row.hyp_id),
                    "title": row.title,
                    "statement": row.statement,
                    "status": row.status,
                    "criteria": row.criteria,
                    "preregistered_at": _iso(row.preregistered_at),
                    "holdout_opened_at": _iso(row.holdout_opened_at),
                    "holdout_result": row.holdout_result,
                    "autopsy": row.autopsy,
                    "locked": bool(row.locked),
                }
                for row in hypotheses
            ],
            "experiment_families": [
                {
                    "family_id": str(row.family_id),
                    "name": row.description,
                    "hypothesis_ids": row.hypothesis_ids,
                    "search_space": row.search_space,
                    "created_at": _iso(row.created_at),
                }
                for row in families
            ],
            "experiments": [
                {
                    "experiment_id": str(row.experiment_id),
                    "hypothesis_id": str(row.hypothesis_id) if row.hypothesis_id else None,
                    "family_id": str(row.family_id) if row.family_id else None,
                    "title": row.title,
                    "status": row.status,
                    "conclusion": row.conclusion,
                    "assessment": row.assessment,
                    "universe": row.universe,
                    "timeframe": row.timeframe,
                    "data_scope": row.data_scope,
                    "dataset_version": row.dataset_version,
                    "frozen": bool(row.frozen),
                    "frozen_at": _iso(row.frozen_at),
                    "created_at": _iso(row.created_at),
                }
                for row in experiments
            ],
        },
        "lineage": {
            "backtests": [
                {
                    "backtest_id": str(row.backtest_id),
                    "experiment_id": str(row.experiment_id),
                    "strategy_id": row.strategy_id,
                    "version": row.version,
                    "status": row.status,
                }
                for row in backtests
            ],
        },
    }


class BackupValidationError(ValueError):
    """A backup bundle failed validation (fail-closed)."""


def validate_bundle(bundle: Any) -> dict[str, Any]:
    """Validate the bundle shape (fail-closed): wrong type, missing
    sections or a mismatched schema version raise."""
    if not isinstance(bundle, dict):
        raise BackupValidationError("bundle must be a JSON object")
    if bundle.get("schema_version") != BACKUP_SCHEMA_VERSION:
        raise BackupValidationError(
            f"unsupported schema_version {bundle.get('schema_version')!r}"
        )
    for section in ("notebook", "registry", "lineage"):
        if not isinstance(bundle.get(section), dict if section != "notebook" else list):
            raise BackupValidationError(f"section {section!r} has the wrong shape")
    # the HF section is optional (a pre-0011 bundle imports without it)
    hf = bundle.get("hypothesis_factory")
    if hf is not None:
        if not isinstance(hf, dict):
            raise BackupValidationError("hypothesis_factory must be an object")
        for key in ("registry", "versions", "relationships", "ledger"):
            if not isinstance(hf.get(key), list):
                raise BackupValidationError(f"hypothesis_factory.{key} must be a list")
    registry = bundle["registry"]
    if not isinstance(registry, dict):
        raise BackupValidationError("registry must be an object")
    for key in ("hypotheses", "experiment_families", "experiments"):
        if not isinstance(registry.get(key), list):
            raise BackupValidationError(f"registry.{key} must be a list")
    return bundle


def import_backup(conn: Connection, bundle: Any) -> dict[str, int]:
    """Import a validated bundle idempotently (D-079).

    Every insert is ON CONFLICT DO NOTHING; re-importing the same bundle
    inserts nothing. The frozen contract is preserved: a frozen experiment
    imports with its ``frozen_at`` (D-021).
    """
    validated = validate_bundle(bundle)
    inserted = {"notes": 0, "hypotheses": 0, "families": 0, "experiments": 0}

    for note in validated["notebook"]:
        result = conn.execute(
            text(
                "INSERT INTO research.note (note_id, title, body_md, experiment_id,"
                " created_at)"
                " VALUES (:id, :title, :body, :experiment_id, :created_at)"
                " ON CONFLICT (note_id) DO NOTHING"
            ),
            {
                "id": note["note_id"],
                "title": note["title"],
                "body": note["body"],
                "experiment_id": note.get("experiment_id"),
                "created_at": note["created_at"],
            },
        )
        inserted["notes"] += int(getattr(result, "rowcount", 0) or 0)

    for hypothesis in validated["registry"]["hypotheses"]:
        result = conn.execute(
            text(
                "INSERT INTO research.hypothesis (hyp_id, title, statement, status,"
                " criteria, preregistered_at, holdout_opened_at, holdout_result,"
                " autopsy, locked)"
                " VALUES (:id, :title, :statement, :status, :criteria, :preregistered_at,"
                " :holdout_opened_at, :holdout_result, :autopsy, :locked)"
                " ON CONFLICT (hyp_id) DO NOTHING"
            ),
            {
                "id": hypothesis["hypothesis_id"],
                "title": hypothesis["title"],
                "statement": hypothesis["statement"],
                "status": hypothesis["status"],
                "criteria": json.dumps(hypothesis["criteria"]),
                "preregistered_at": hypothesis["preregistered_at"],
                "holdout_opened_at": hypothesis["holdout_opened_at"],
                "holdout_result": hypothesis["holdout_result"],
                "autopsy": hypothesis["autopsy"],
                "locked": hypothesis["locked"],
            },
        )
        inserted["hypotheses"] += int(getattr(result, "rowcount", 0) or 0)

    for family in validated["registry"]["experiment_families"]:
        result = conn.execute(
            text(
                "INSERT INTO research.experiment_family (family_id, description,"
                " hypothesis_ids, search_space, created_at)"
                " VALUES (:id, :name, :hypothesis_ids, :search_space, :created_at)"
                " ON CONFLICT (family_id) DO NOTHING"
            ),
            {
                "id": family["family_id"],
                "name": family["name"],
                "hypothesis_ids": json.dumps(family.get("hypothesis_ids", [])),
                "search_space": json.dumps(family["search_space"]),
                "created_at": family["created_at"],
            },
        )
        inserted["families"] += int(getattr(result, "rowcount", 0) or 0)

    for experiment in validated["registry"]["experiments"]:
        result = conn.execute(
            text(
                "INSERT INTO research.experiment (experiment_id, hypothesis_id, family_id,"
                " title, status, conclusion, assessment, universe, timeframe, data_scope,"
                " dataset_version, dataset_schema_version, engine_version, code_version,"
                " detector_refs, resolved_params, selection_method, seed, refs, frozen,"
                " frozen_at)"
                " VALUES (:id, :hypothesis_id, :family_id, :title, :status, :conclusion,"
                " :assessment, :universe, :timeframe, :data_scope, :dataset_version,"
                " '1', '0.1.0', 'dev', '[]', '{}', 'PRE_SPECIFIED', 0, '{}', :frozen,"
                " :frozen_at)"
                " ON CONFLICT (experiment_id) DO NOTHING"
            ),
            {
                "id": experiment["experiment_id"],
                "hypothesis_id": experiment["hypothesis_id"],
                "family_id": experiment["family_id"],
                "title": experiment["title"],
                "status": experiment["status"],
                "conclusion": experiment["conclusion"],
                "assessment": json.dumps(experiment["assessment"]),
                "universe": json.dumps(experiment["universe"]),
                "timeframe": experiment.get("timeframe") or "1D",
                "data_scope": experiment.get("data_scope") or "DEVELOPMENT",
                "dataset_version": experiment.get("dataset_version") or "imported",
                "frozen": experiment.get("frozen", False),
                "frozen_at": experiment.get("frozen_at"),
            },
        )
        inserted["experiments"] += int(getattr(result, "rowcount", 0) or 0)

    # the HF tables (0011): the version history, the evidence graph and the
    # multiple-testing ledger - imported idempotently (D-079). The registry
    # rows UPDATE the HF vocabulary columns so a re-import keeps them in
    # sync; versions/relationships/ledger insert-or-skip.
    hf = validated.get("hypothesis_factory") or {}
    inserted["hf_versions"] = 0
    inserted["hf_relationships"] = 0
    inserted["hf_ledger"] = 0

    for entry in hf.get("registry", []):
        conn.execute(
            text(
                "UPDATE research.hypothesis SET hf_domain = :domain,"
                " hf_origin = :origin, hf_status = :status,"
                " original_question = :question WHERE hyp_id = :id"
            ),
            {
                "id": entry["hypothesis_id"],
                "domain": entry.get("domain") or "DERIVATIVES",
                "origin": entry.get("origin") or "HUMAN",
                "status": entry.get("status") or "NOT_TESTED",
                "question": entry.get("original_question"),
            },
        )

    for version in hf.get("versions", []):
        result = conn.execute(
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
                " :population, :tf, CAST(:event AS jsonb), :ts_rule, :horizon,"
                " :direction, :baseline, CAST(:incl AS jsonb), CAST(:excl AS jsonb),"
                " CAST(:conf AS jsonb), CAST(:data_req AS jsonb), :method,"
                " CAST(:robust AS jsonb), CAST(:falsify AS jsonb), :econ, :limits, :by)"
                " ON CONFLICT (hypothesis_id, version) DO NOTHING"
            ),
            {
                "id": version["hypothesis_id"],
                "v": int(version["version"]),
                "description": version.get("description"),
                "mechanism": version.get("mechanism"),
                "null_h": version["null_hypothesis"],
                "alt_h": version["alternative_hypothesis"],
                "ivars": json.dumps(version.get("independent_vars") or []),
                "dvars": json.dumps(version.get("dependent_vars") or []),
                "features": json.dumps(version.get("features") or []),
                "population": version["population"],
                "tf": version["timeframe"],
                "event": json.dumps(version.get("event_definition")),
                "ts_rule": version["observation_ts_rule"],
                "horizon": version["outcome_horizon"],
                "direction": version["expected_direction"],
                "baseline": version.get("baseline"),
                "incl": json.dumps(version.get("inclusion_criteria")),
                "excl": json.dumps(version.get("exclusion_criteria")),
                "conf": json.dumps(version.get("confounders")),
                "data_req": json.dumps(version.get("data_requirements") or []),
                "method": version["proposed_statistical_method"],
                "robust": json.dumps(version.get("proposed_robustness_tests")),
                "falsify": json.dumps(version.get("falsification_criteria") or []),
                "econ": version.get("economic_significance_threshold"),
                "limits": version.get("known_limitations"),
                "by": version.get("created_by") or "owner",
            },
        )
        inserted["hf_versions"] += int(getattr(result, "rowcount", 0) or 0)

    for edge in hf.get("relationships", []):
        result = conn.execute(
            text(
                "INSERT INTO research.hypothesis_relationship (from_hypothesis_id,"
                " to_hypothesis_id, relationship, rationale)"
                " VALUES (:f, :t, :r, :why)"
                " ON CONFLICT ON CONSTRAINT hf_relationship_unique DO NOTHING"
            ),
            {
                "f": edge["from"],
                "t": edge["to"],
                "r": edge["relationship"],
                "why": edge.get("rationale"),
            },
        )
        inserted["hf_relationships"] += int(getattr(result, "rowcount", 0) or 0)

    for entry in hf.get("ledger", []):
        result = conn.execute(
            text(
                "INSERT INTO research.research_ledger (family_id, hypothesis_id,"
                " correction_plan) VALUES (:family, :hyp, :plan)"
                " ON CONFLICT ON CONSTRAINT research_ledger_unique DO NOTHING"
            ),
            {
                "family": entry["family_id"],
                "hyp": entry["hypothesis_id"],
                "plan": entry["correction_plan"],
            },
        )
        inserted["hf_ledger"] += int(getattr(result, "rowcount", 0) or 0)

    return inserted
