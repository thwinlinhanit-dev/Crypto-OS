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

    return {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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

    return inserted
