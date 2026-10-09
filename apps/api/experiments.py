"""M5b (T5b.1-T5b.2) - experiment families, variants, experiments (spec 8.4/8.5, 11).

The research-integrity surface:

- CRUD ``/v1/families`` plus ``GET /v1/families/{id}/variants``.
- CRUD ``/v1/experiments`` (PATCH only while unfrozen; frozen rows reject every
  UPDATE/DELETE at the DB level too), ``POST /v1/experiments/{id}/freeze``
  (one transaction: lock referenced results + hypothesis, record
  ``result_hashes``, set ``frozen``), ``GET .../verify`` (recompute hashes ->
  INTACT/TAMPERED) and ``GET .../lineage`` (read-only node list).
- Ad-hoc runs never bypass recording: ``POST /v1/stats`` without ``family_id``
  creates a singleton family and records a variant under it (spec 8.4).

Fail-closed (PD-4/PD-10/PD-11): unknown hypothesis/family 404, unknown dataset
manifest 422, bad scope/selection-method/status 422, PATCH on a frozen
experiment 409, double freeze 409, DELETE of a referenced family 409.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from apps.api.db import connection_scope
from engine.errors import ImmutableResultError, NotFoundError, ValidationError
from engine.versions import DATASET_SCHEMA_VERSION, ENGINE_VERSION, code_version

router = APIRouter(prefix="/v1", tags=["experiments"])

#: Spec 8.4/11 selection methods (mirrored by ck_experiment_selection_method).
SELECTION_METHODS = ("PRE_SPECIFIED", "SWEEP_BEST", "MANUAL_CHOICE")

#: Experiment status vocabulary (spec 8.4/11; mirrored by ck_experiment_status).
EXPERIMENT_STATUSES = (
    "IDEA",
    "RUNNING",
    "PROMISING",
    "SUPPORTED",
    "WEAK",
    "REJECTED",
    "VALIDATED",
)


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class FamilyCreate(BaseModel):
    """``POST /v1/families`` body (spec 10 columns)."""

    description: str
    hypothesis_ids: list[UUID] = []
    search_space: dict[str, Any] | None = None


class FamilyPatch(BaseModel):
    """``PATCH /v1/families/{id}`` body."""

    description: str | None = None
    hypothesis_ids: list[UUID] | None = None
    search_space: dict[str, Any] | None = None


class FamilyView(BaseModel):
    family_id: UUID
    description: str
    hypothesis_ids: list[UUID]
    search_space: dict[str, Any]
    hypothesis_count: int
    variant_count: int
    created_at: str


class VariantView(BaseModel):
    variant_id: UUID
    family_id: UUID
    experiment_id: UUID | None
    params_hash: str
    resolved_params: dict[str, Any]
    result_ref: UUID
    selected: bool
    created_at: str


class ExperimentCreate(BaseModel):
    """``POST /v1/experiments`` body (PD-12: universe + dataset are explicit)."""

    hypothesis_id: UUID
    family_id: UUID | None = None
    title: str
    universe: list[UUID]
    timeframe: str
    data_scope: str = "DEVELOPMENT"
    dataset_version: str
    detector_refs: dict[str, Any]
    resolved_params: dict[str, Any]
    selection_method: str = "PRE_SPECIFIED"
    assessment: dict[str, Any] | None = None
    conclusion: str | None = None
    refs: dict[str, Any] | None = None
    seed: int | None = None


class ExperimentPatch(BaseModel):
    """``PATCH /v1/experiments/{id}`` body (only while unfrozen, spec 8.5)."""

    title: str | None = None
    status: str | None = None
    conclusion: str | None = None
    assessment: dict[str, Any] | None = None
    refs: dict[str, Any] | None = None


class ExperimentView(BaseModel):
    experiment_id: UUID
    hypothesis_id: UUID
    family_id: UUID
    title: str
    status: str
    conclusion: str | None
    assessment: dict[str, Any]
    universe: list[UUID]
    timeframe: str
    data_scope: str
    dataset_version: str
    dataset_schema_version: str
    engine_version: str
    code_version: str
    detector_refs: dict[str, Any]
    resolved_params: dict[str, Any]
    selection_method: str
    seed: int | None
    refs: dict[str, Any]
    result_hashes: dict[str, Any]
    frozen: bool
    frozen_at: str | None
    supersedes: UUID | None
    created_at: str


class LineageView(BaseModel):
    """Read-only lineage (spec 8.5): nodes that exist, and what is not built yet."""

    experiment_id: UUID
    nodes: list[dict[str, Any]]
    not_implemented: list[str]


class VerifyView(BaseModel):
    """``GET /v1/experiments/{id}/verify`` body (spec 8.5)."""

    experiment_id: UUID
    status: str  # INTACT | TAMPERED
    results: dict[str, Any]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _family_view(row: Any) -> FamilyView:
    return FamilyView(
        family_id=row.family_id,
        description=row.description,
        hypothesis_ids=[UUID(str(h)) for h in row.hypothesis_ids],
        search_space=dict(row.search_space or {}),
        hypothesis_count=row.hypothesis_count,
        variant_count=row.variant_count,
        created_at=row.created_at.isoformat(),
    )


def _variant_view(row: Any) -> VariantView:
    return VariantView(
        variant_id=row.variant_id,
        family_id=row.family_id,
        experiment_id=row.experiment_id,
        params_hash=row.params_hash,
        resolved_params=dict(row.resolved_params or {}),
        result_ref=row.result_ref,
        selected=bool(row.selected),
        created_at=row.created_at.isoformat(),
    )


def _experiment_view(row: Any) -> ExperimentView:
    return ExperimentView(
        experiment_id=row.experiment_id,
        hypothesis_id=row.hypothesis_id,
        family_id=row.family_id,
        title=row.title,
        status=row.status,
        conclusion=row.conclusion,
        assessment=dict(row.assessment or {}),
        universe=[UUID(str(i)) for i in row.universe],
        timeframe=row.timeframe,
        data_scope=row.data_scope,
        dataset_version=row.dataset_version,
        dataset_schema_version=row.dataset_schema_version,
        engine_version=row.engine_version,
        code_version=row.code_version,
        detector_refs=dict(row.detector_refs or {}),
        resolved_params=dict(row.resolved_params or {}),
        selection_method=row.selection_method,
        seed=row.seed,
        refs=dict(row.refs or {}),
        result_hashes=dict(row.result_hashes or {}),
        frozen=bool(row.frozen),
        frozen_at=_iso(row.frozen_at),
        supersedes=row.supersedes,
        created_at=row.created_at.isoformat(),
    )


# --------------------------------------------------------------------------
# Families
# --------------------------------------------------------------------------


@router.post("/families", response_model=FamilyView, status_code=201, tags=["experiments"])
async def create_family(body: FamilyCreate) -> FamilyView:
    """Create an experiment family (the search record, spec 8.4)."""
    description = _require_text(body.description, "description")
    hypothesis_ids = sorted({str(h) for h in body.hypothesis_ids})
    family_id = uuid.uuid4()
    with connection_scope() as conn:
        for hyp in hypothesis_ids:
            _require_hypothesis(conn, UUID(hyp))
        row = _write(
            conn,
            "INSERT INTO research.experiment_family (family_id, hypothesis_ids, description,"
            " search_space, hypothesis_count) VALUES (:id, CAST(:hyps AS jsonb), :description,"
            " CAST(:space AS jsonb), :count) RETURNING *",
            {
                "id": family_id,
                "hyps": json.dumps(hypothesis_ids),
                "description": description,
                "space": json.dumps(body.search_space if body.search_space is not None else {}),
                "count": len(hypothesis_ids),
            },
        ).first()
        assert row is not None
        return _family_view(row)


@router.get("/families", response_model=list[FamilyView], tags=["experiments"])
async def list_families() -> list[FamilyView]:
    """List families, newest first."""
    with connection_scope() as conn:
        rows = conn.execute(
            text(
                "SELECT * FROM research.experiment_family"
                " ORDER BY created_at DESC, family_id ASC"
            )
        ).all()
        return [_family_view(row) for row in rows]


@router.get("/families/{family_id}", response_model=FamilyView, tags=["experiments"])
async def get_family(family_id: UUID) -> FamilyView:
    with connection_scope() as conn:
        row = conn.execute(
            text("SELECT * FROM research.experiment_family WHERE family_id = :id"),
            {"id": family_id},
        ).first()
    if row is None:
        raise NotFoundError("family not found", {"family_id": str(family_id)})
    return _family_view(row)


@router.get("/families/{family_id}/variants", response_model=list[VariantView], tags=["experiments"])
async def list_family_variants(family_id: UUID) -> list[VariantView]:
    """Every variant recorded under one family, in insertion order (spec 8.4)."""
    with connection_scope() as conn:
        row = conn.execute(
            text("SELECT family_id FROM research.experiment_family WHERE family_id = :id"),
            {"id": family_id},
        ).first()
        if row is None:
            raise NotFoundError("family not found", {"family_id": str(family_id)})
        rows = conn.execute(
            text(
                "SELECT * FROM research.variant WHERE family_id = :id"
                " ORDER BY created_at ASC, variant_id ASC"
            ),
            {"id": family_id},
        ).all()
        return [_variant_view(row) for row in rows]


@router.patch("/families/{family_id}", response_model=FamilyView, tags=["experiments"])
async def patch_family(family_id: UUID, body: FamilyPatch) -> FamilyView:
    """Edit a family's description / search space / hypothesis links."""
    updates: dict[str, Any] = {}
    if body.description is not None:
        updates["description"] = _require_text(body.description, "description")
    if body.search_space is not None:
        updates["search_space"] = json.dumps(body.search_space)
    if body.hypothesis_ids is not None:
        hypothesis_ids = sorted({str(h) for h in body.hypothesis_ids})
        with connection_scope() as conn:
            for hyp in hypothesis_ids:
                _require_hypothesis(conn, UUID(hyp))
        updates["hypothesis_ids"] = json.dumps(hypothesis_ids)
        updates["hypothesis_count"] = len(hypothesis_ids)
    with connection_scope() as conn:
        if updates:
            assignments = ", ".join(
                f"{key} = CAST(:{key} AS jsonb)" if key in {"search_space", "hypothesis_ids"} else f"{key} = :{key}"
                for key in updates
            )
            row = _write(
                conn,
                f"UPDATE research.experiment_family SET {assignments} WHERE family_id = :id"
                " RETURNING *",
                {**updates, "id": family_id},
            ).first()
        else:
            row = conn.execute(
                text("SELECT * FROM research.experiment_family WHERE family_id = :id"),
                {"id": family_id},
            ).first()
    if row is None:
        raise NotFoundError("family not found", {"family_id": str(family_id)})
    return _family_view(row)


@router.delete("/families/{family_id}", tags=["experiments"])
async def delete_family(family_id: UUID) -> Response:
    """Delete an unreferenced family (409 once variants/experiments exist)."""
    with connection_scope() as conn:
        existing = conn.execute(
            text("SELECT family_id FROM research.experiment_family WHERE family_id = :id"),
            {"id": family_id},
        ).first()
        if existing is None:
            raise NotFoundError("family not found", {"family_id": str(family_id)})
        _write(
            conn,
            "DELETE FROM research.experiment_family WHERE family_id = :id",
            {"id": family_id},
        )
    return Response(status_code=204)


# --------------------------------------------------------------------------
# Experiments
# --------------------------------------------------------------------------


@router.post("/experiments", response_model=ExperimentView, status_code=201, tags=["experiments"])
async def create_experiment(body: ExperimentCreate) -> ExperimentView:
    """Create one reproducible attempt; ``family_id`` omitted -> singleton family."""
    title = _require_text(body.title, "title")
    if not body.universe:
        raise ValidationError(
            "universe is required and must not be empty (PD-12)",
            {"universe": body.universe},
        )
    if body.data_scope not in {"DEVELOPMENT", "HOLDOUT"}:
        raise ValidationError(
            "data_scope must be DEVELOPMENT or HOLDOUT",
            {"data_scope": body.data_scope},
        )
    if body.selection_method not in SELECTION_METHODS:
        raise ValidationError(
            "unknown selection_method",
            {"selection_method": body.selection_method, "allowed": list(SELECTION_METHODS)},
        )
    universe = [str(i) for i in dict.fromkeys(str(i) for i in body.universe)]
    refs = dict(body.refs) if body.refs is not None else {}
    experiment_id = uuid.uuid4()
    with connection_scope() as conn:
        _require_hypothesis(conn, body.hypothesis_id)
        if body.family_id is not None:
            family = conn.execute(
                text("SELECT * FROM research.experiment_family WHERE family_id = :id"),
                {"id": body.family_id},
            ).first()
            if family is None:
                raise NotFoundError("family not found", {"family_id": str(body.family_id)})
            family_id = body.family_id
        else:
            family_id = create_singleton_family(
                conn,
                family_id=uuid.uuid4(),
                hypothesis_ids=[str(body.hypothesis_id)],
            )
        _require_manifest(conn, body.dataset_version)
        row = _write(
            conn,
            "INSERT INTO research.experiment (experiment_id, hypothesis_id, family_id, title,"
            " status, conclusion, assessment, universe, timeframe, data_scope,"
            " dataset_version, dataset_schema_version, engine_version, code_version,"
            " detector_refs, resolved_params, selection_method, seed, refs) VALUES"
            " (:id, :hypothesis_id, :family_id, :title, 'RUNNING', :conclusion,"
            " CAST(:assessment AS jsonb), CAST(:universe AS jsonb), :timeframe, :data_scope,"
            " :dataset_version, :dataset_schema_version, :engine_version, :code_version,"
            " CAST(:detector_refs AS jsonb), CAST(:resolved_params AS jsonb),"
            " :selection_method, :seed, CAST(:refs AS jsonb)) RETURNING *",
            {
                "id": experiment_id,
                "hypothesis_id": body.hypothesis_id,
                "family_id": family_id,
                "title": title,
                "conclusion": body.conclusion,
                "assessment": json.dumps(body.assessment if body.assessment is not None else {}),
                "universe": json.dumps(universe),
                "timeframe": body.timeframe,
                "data_scope": body.data_scope,
                "dataset_version": body.dataset_version,
                "dataset_schema_version": DATASET_SCHEMA_VERSION,
                "engine_version": ENGINE_VERSION,
                "code_version": code_version(),
                "detector_refs": json.dumps(body.detector_refs),
                "resolved_params": json.dumps(body.resolved_params),
                "selection_method": body.selection_method,
                "seed": body.seed,
                "refs": json.dumps(refs),
            },
        ).first()
        assert row is not None
        return _experiment_view(row)


@router.get("/experiments", response_model=list[ExperimentView], tags=["experiments"])
async def list_experiments() -> list[ExperimentView]:
    with connection_scope() as conn:
        rows = conn.execute(
            text(
                "SELECT * FROM research.experiment"
                " ORDER BY created_at DESC, experiment_id ASC"
            )
        ).all()
        return [_experiment_view(row) for row in rows]


@router.get("/experiments/{experiment_id}", response_model=ExperimentView, tags=["experiments"])
async def get_experiment(experiment_id: UUID) -> ExperimentView:
    with connection_scope() as conn:
        row = conn.execute(
            text("SELECT * FROM research.experiment WHERE experiment_id = :id"),
            {"id": experiment_id},
        ).first()
    if row is None:
        raise NotFoundError("experiment not found", {"experiment_id": str(experiment_id)})
    return _experiment_view(row)


@router.patch("/experiments/{experiment_id}", response_model=ExperimentView, tags=["experiments"])
async def patch_experiment(experiment_id: UUID, body: ExperimentPatch) -> ExperimentView:
    """PATCH only while unfrozen (spec 8.5: frozen experiments cannot be updated)."""
    updates: dict[str, Any] = {}
    if body.title is not None:
        updates["title"] = _require_text(body.title, "title")
    if body.conclusion is not None:
        updates["conclusion"] = body.conclusion
    if body.assessment is not None:
        updates["assessment"] = json.dumps(body.assessment)
    if body.refs is not None:
        updates["refs"] = json.dumps(body.refs)
    if body.status is not None:
        if body.status not in EXPERIMENT_STATUSES:
            raise ValidationError(
                "unknown experiment status",
                {"status": body.status, "allowed": list(EXPERIMENT_STATUSES)},
            )
        if body.status == "REJECTED" and not _patch_has_conclusion(body):
            raise ValidationError(
                "REJECTED requires a conclusion (pre-registered kill criterion)",
                {"experiment_id": str(experiment_id)},
            )
        updates["status"] = body.status
    with connection_scope() as conn:
        current = conn.execute(
            text("SELECT frozen FROM research.experiment WHERE experiment_id = :id"),
            {"id": experiment_id},
        ).first()
        if current is None:
            raise NotFoundError("experiment not found", {"experiment_id": str(experiment_id)})
        if bool(current.frozen):
            raise ImmutableResultError(
                "experiment is frozen; follow-ups create a new experiment with supersedes",
                {"experiment_id": str(experiment_id)},
            )
        if updates:
            assignments = ", ".join(
                f"{key} = CAST(:{key} AS jsonb)" if key in {"assessment", "refs"} else f"{key} = :{key}"
                for key in updates
            )
            row = _write(
                conn,
                f"UPDATE research.experiment SET {assignments} WHERE experiment_id = :id"
                " RETURNING *",
                {**updates, "id": experiment_id},
            ).first()
        else:
            row = conn.execute(
                text("SELECT * FROM research.experiment WHERE experiment_id = :id"),
                {"id": experiment_id},
            ).first()
        assert row is not None
        return _experiment_view(row)


@router.post(
    "/experiments/{experiment_id}/freeze", response_model=ExperimentView, tags=["experiments"]
)
async def freeze_experiment(experiment_id: UUID) -> ExperimentView:
    """Freeze in one transaction: lock referenced results + hypothesis, store hashes."""
    with connection_scope() as conn:
        row = conn.execute(
            text("SELECT * FROM research.experiment WHERE experiment_id = :id"),
            {"id": experiment_id},
        ).first()
        if row is None:
            raise NotFoundError("experiment not found", {"experiment_id": str(experiment_id)})
        if bool(row.frozen):
            raise ImmutableResultError(
                "experiment is already frozen",
                {"experiment_id": str(experiment_id)},
            )
        stats_refs = _stats_refs(row.refs)
        frozen_at = datetime.now(UTC)
        hashes: dict[str, str] = {}
        for ref_id in stats_refs:
            result = conn.execute(
                text("SELECT result FROM research.stats_result WHERE ref_id = :ref"),
                {"ref": ref_id},
            ).first()
            if result is None:
                raise ValidationError(
                    "referenced stats result does not exist",
                    {"experiment_id": str(experiment_id), "ref_id": ref_id},
                )
            hashes[ref_id] = _canonical_hash(result.result)
            _write(
                conn,
                "UPDATE research.stats_result SET locked = true WHERE ref_id = :ref",
                {"ref": ref_id},
            )
        # 8.5 (a): the referenced hypothesis criteria freeze with the experiment.
        _write(
            conn,
            "UPDATE research.hypothesis SET locked = true WHERE hyp_id = :id",
            {"id": row.hypothesis_id},
        )
        frozen = _write(
            conn,
            "UPDATE research.experiment SET frozen = true, frozen_at = :at,"
            " result_hashes = CAST(:hashes AS jsonb) WHERE experiment_id = :id RETURNING *",
            {
                "at": frozen_at,
                "hashes": json.dumps(hashes),
                "id": experiment_id,
            },
        ).first()
        assert frozen is not None
        conn.execute(
            text(
                "INSERT INTO system.audit_log (actor, action, target, detail)"
                " VALUES (:actor, :action, :target, CAST(:detail AS jsonb))"
            ),
            {
                "actor": "api",
                "action": "experiment.freeze",
                "target": str(experiment_id),
                "detail": json.dumps(
                    {"frozen_at": frozen_at.isoformat(), "result_hashes": hashes}
                ),
            },
        )
        return _experiment_view(frozen)


@router.get("/experiments/{experiment_id}/verify", response_model=VerifyView, tags=["experiments"])
async def verify_experiment(experiment_id: UUID) -> VerifyView:
    """Recompute stored result hashes -> INTACT or TAMPERED (spec 8.5)."""
    with connection_scope() as conn:
        row = conn.execute(
            text("SELECT frozen, result_hashes FROM research.experiment WHERE experiment_id = :id"),
            {"id": experiment_id},
        ).first()
        if row is None:
            raise NotFoundError("experiment not found", {"experiment_id": str(experiment_id)})
        if not bool(row.frozen):
            raise ValidationError(
                "experiment is not frozen; freeze it before verifying",
                {"experiment_id": str(experiment_id)},
            )
        stored = dict(row.result_hashes or {})
        results: dict[str, Any] = {}
        intact = True
        for ref_id, expected in sorted(stored.items()):
            current = conn.execute(
                text("SELECT result FROM research.stats_result WHERE ref_id = :ref"),
                {"ref": UUID(ref_id)},
            ).first()
            if current is None:
                results[ref_id] = {"status": "MISSING", "expected": expected, "actual": None}
                intact = False
                continue
            actual = _canonical_hash(current.result)
            ok = actual == expected
            intact = intact and ok
            results[ref_id] = {
                "status": "INTACT" if ok else "TAMPERED",
                "expected": expected,
                "actual": actual,
            }
        return VerifyView(
            experiment_id=experiment_id,
            status="INTACT" if intact else "TAMPERED",
            results=results,
        )


@router.get("/experiments/{experiment_id}/lineage", response_model=LineageView, tags=["experiments"])
async def experiment_lineage(experiment_id: UUID) -> LineageView:
    """Read-only lineage from stored ids (spec 8.5: every node links to its object)."""
    with connection_scope() as conn:
        row = conn.execute(
            text("SELECT * FROM research.experiment WHERE experiment_id = :id"),
            {"id": experiment_id},
        ).first()
        if row is None:
            raise NotFoundError("experiment not found", {"experiment_id": str(experiment_id)})
        manifest = conn.execute(
            text("SELECT dataset_version FROM market.dataset_manifest WHERE dataset_version = :dv"),
            {"dv": row.dataset_version},
        ).first()
        stats_refs = _stats_refs(row.refs)
        hypothesis = conn.execute(
            text("SELECT hyp_id FROM research.hypothesis WHERE hyp_id = :id"),
            {"id": row.hypothesis_id},
        ).first()
    nodes: list[dict[str, Any]] = [
        {
            "kind": "dataset",
            "label": row.dataset_version,
            "ref": ("/v1/datasets/" + row.dataset_version) if manifest else None,
            "status": "OK" if manifest else "NOT_IMPLEMENTED",
        },
        {"kind": "features", "label": "engine/features", "ref": None, "status": "OK"},
        {
            "kind": "detector",
            "label": json.dumps(row.detector_refs, sort_keys=True),
            "ref": None,
            "status": "OK",
        },
        {"kind": "stats", "label": f"{len(stats_refs)} result(s)", "ref": None, "status": "OK"},
        {
            "kind": "hypothesis",
            "label": str(row.hypothesis_id),
            "ref": ("/v1/hypotheses/" + str(row.hypothesis_id)) if hypothesis else None,
            "status": "OK" if hypothesis else "MISSING",
        },
        {
            "kind": "experiment",
            "label": str(experiment_id),
            "ref": "/v1/experiments/" + str(experiment_id),
            "status": "OK",
        },
    ]
    for ref_id in stats_refs:
        nodes.append(
            {
                "kind": "stats_result",
                "label": str(ref_id),
                "ref": "/v1/stats/" + str(ref_id),
                "status": "OK",
            }
        )
    return LineageView(
        experiment_id=experiment_id,
        nodes=nodes,
        not_implemented=["strategy", "backtest", "validation", "conclusion"],
    )


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty string", {field: value})
    return value


def _require_hypothesis(conn: Connection, hyp_id: UUID) -> None:
    row = conn.execute(
        text("SELECT hyp_id FROM research.hypothesis WHERE hyp_id = :id"), {"id": hyp_id}
    ).first()
    if row is None:
        raise NotFoundError("hypothesis not found", {"hypothesis_id": str(hyp_id)})


def _require_manifest(conn: Connection, dataset_version: str) -> None:
    row = conn.execute(
        text("SELECT dataset_version FROM market.dataset_manifest WHERE dataset_version = :dv"),
        {"dv": dataset_version},
    ).first()
    if row is None:
        raise ValidationError(
            "dataset_version is not a registered manifest",
            {"dataset_version": dataset_version},
        )


def _write(conn: Connection, sql: str, params: dict[str, Any]) -> Any:
    """Execute a research write, mapping trigger rejections to 409 (PD-13)."""
    try:
        return conn.execute(text(sql), params)
    except DBAPIError as exc:
        if "IMMUTABLE_RESULT" in str(exc):
            raise ImmutableResultError(
                "research row is immutable (frozen or append-only)",
                {"sql": sql.split("\n", 1)[0][:80]},
            ) from exc
        raise


def _stats_refs(refs: Mapping[str, Any]) -> list[str]:
    """Referenced stats ref ids as strings (JSONB keys must be strings)."""
    raw = refs.get("stats")
    if not isinstance(raw, list):
        return []
    return [str(r) for r in raw]


def _patch_has_conclusion(body: ExperimentPatch) -> bool:
    return body.conclusion is not None and str(body.conclusion).strip() != ""


def _canonical_hash(payload: Any) -> str:
    """sha256 of the canonical JSON of a stored result (spec 8.5 (b), D-006 style)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def create_singleton_family(
    conn: Connection,
    *,
    family_id: uuid.UUID,
    hypothesis_ids: Sequence[str] = (),
) -> uuid.UUID:
    """Ad-hoc runs never bypass recording: create the singleton family (8.4).

    The caller mints ``family_id`` up front so the stats result can reference
    it; the row is written only once the run has succeeded.
    """
    linked = sorted({str(h) for h in hypothesis_ids})
    _write(
        conn,
        "INSERT INTO research.experiment_family (family_id, hypothesis_ids, description,"
        " search_space, hypothesis_count) VALUES (:id, CAST(:hyps AS jsonb),"
        " 'ad-hoc run (singleton family)', '{}'::jsonb, :count)",
        {"id": family_id, "hyps": json.dumps(linked), "count": len(linked)},
    )
    return family_id


def record_variant(
    conn: Connection,
    *,
    family_id: uuid.UUID,
    stats_ref_id: uuid.UUID,
    params_hash_value: str,
    resolved_params: Mapping[str, Any],
    experiment_id: uuid.UUID | None = None,
) -> int:
    """Append a variant row and bump the family's variant_count (one transaction)."""
    conn.execute(
        text(
            "INSERT INTO research.variant (variant_id, family_id, experiment_id,"
            " params_hash, resolved_params, result_ref) VALUES (:id, :family_id,"
            " :experiment_id, :params_hash, CAST(:resolved AS jsonb), :result_ref)"
        ),
        {
            "id": uuid.uuid4(),
            "family_id": family_id,
            "experiment_id": experiment_id,
            "params_hash": params_hash_value,
            "resolved": json.dumps(dict(resolved_params)),
            "result_ref": stats_ref_id,
        },
    )
    return int(
        conn.execute(
            text(
                "UPDATE research.experiment_family"
                " SET variant_count = variant_count + 1 WHERE family_id = :id"
                " RETURNING variant_count"
            ),
            {"id": family_id},
        ).scalar_one()
    )
