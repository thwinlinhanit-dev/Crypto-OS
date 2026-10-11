"""HF Phase 1 — the Hypothesis Factory API (spec §19).

The spec's discovery/formalization/prioritization tool surface, as REST
endpoints. Phase 1 lands create/get/version/status/relationships/dedup/
ledger; the generation and experiment tools arrive with their phases
(PD-9: no endpoint claims an unbuilt capability).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from apps.api.db import connection_scope
from engine.errors import EngineError
from engine.hypothesis import registry as hf_registry

router = APIRouter(prefix="/v1/hypothesis-factory", tags=["hypothesis-factory"])


class CreateHypothesisRequest(BaseModel):
    """POST body: the formal hypothesis (spec §6)."""

    title: str
    domain: str
    origin: str
    spec: dict[str, Any]
    original_question: str | None = None
    created_by: str = "owner"


class StatusRequest(BaseModel):
    """PATCH body: the status transition (spec §14.2)."""

    status: str


class RelationshipRequest(BaseModel):
    """POST body: an evidence-graph edge (spec §18.2)."""

    to_hypothesis_id: str
    relationship: str
    rationale: str | None = None


class LedgerRequest(BaseModel):
    """POST body: the testing-family declaration (spec §12, HF-005)."""

    family_id: str
    correction_plan: str = Field(default="BENJAMINI_HOCHBERG")


def _engine_error(exc: EngineError) -> HTTPException:
    return HTTPException(exc.http_status, f"{exc.code}: {exc.message}")


@router.post("/hypotheses", status_code=201)
async def create_hypothesis(body: CreateHypothesisRequest) -> dict[str, Any]:
    """``hypothesis.create``: a formally defined, versioned hypothesis."""
    with connection_scope() as conn:
        try:
            hyp_id = hf_registry.create_hypothesis(
                conn,
                title=body.title,
                domain=body.domain,
                origin=body.origin,
                spec=body.spec,
                original_question=body.original_question,
                created_by=body.created_by,
            )
        except EngineError as exc:
            raise _engine_error(exc) from exc
    return {"hypothesis_id": hyp_id, "status": "NOT_TESTED", "version": 1}


@router.get("/hypotheses/{hypothesis_id}")
async def get_hypothesis(hypothesis_id: str) -> dict[str, Any]:
    """``hypothesis.get``: the hypothesis + its latest version."""
    with connection_scope() as conn:
        try:
            return hf_registry.get_hypothesis(conn, hypothesis_id)
        except EngineError as exc:
            raise _engine_error(exc) from exc


@router.post("/hypotheses/{hypothesis_id}/versions", status_code=201)
async def add_version(hypothesis_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Append a new version (spec §18.1: a change is a new version)."""
    with connection_scope() as conn:
        try:
            version = hf_registry.add_version(conn, hypothesis_id=hypothesis_id, spec=body)
        except EngineError as exc:
            raise _engine_error(exc) from exc
    return {"hypothesis_id": hypothesis_id, "version": version}


@router.patch("/hypotheses/{hypothesis_id}/status")
async def set_status(hypothesis_id: str, body: StatusRequest) -> dict[str, Any]:
    """A status transition through the explicit table (spec §14.2)."""
    with connection_scope() as conn:
        try:
            hf_registry.set_status(conn, hypothesis_id, body.status)
        except EngineError as exc:
            raise _engine_error(exc) from exc
    return {"hypothesis_id": hypothesis_id, "status": body.status}


@router.post("/hypotheses/{hypothesis_id}/relationships", status_code=201)
async def add_relationship(hypothesis_id: str, body: RelationshipRequest) -> dict[str, Any]:
    """An evidence-graph edge (spec §18.2)."""
    with connection_scope() as conn:
        try:
            hf_registry.add_relationship(
                conn,
                from_id=hypothesis_id,
                to_id=body.to_hypothesis_id,
                relationship=body.relationship,
                rationale=body.rationale,
            )
        except EngineError as exc:
            raise _engine_error(exc) from exc
    return {"from": hypothesis_id, "to": body.to_hypothesis_id, "relationship": body.relationship}


@router.post("/hypotheses/{hypothesis_id}/testing-family", status_code=201)
async def declare_testing_family(
    hypothesis_id: str, body: LedgerRequest
) -> dict[str, Any]:
    """The research-ledger declaration BEFORE the inferential analysis
    (spec §12, HF-005)."""
    with connection_scope() as conn:
        try:
            hf_registry.declare_testing_family(
                conn,
                family_id=body.family_id,
                hypothesis_id=hypothesis_id,
                correction_plan=body.correction_plan,
            )
        except EngineError as exc:
            raise _engine_error(exc) from exc
    return {"hypothesis_id": hypothesis_id, "family_id": body.family_id}


@router.post("/hypotheses/find-duplicates")
async def find_duplicates(body: dict[str, Any]) -> dict[str, Any]:
    """``hypothesis.find_duplicates``: the deterministic exact-dup search
    (spec §8 level 1)."""
    with connection_scope() as conn:
        try:
            hf_registry.validate_definition(body)
            matches = hf_registry.find_duplicates(conn, body)
        except EngineError as exc:
            raise _engine_error(exc) from exc
    return {"matches": matches}


@router.post("/validate-definition")
async def validate_definition(body: dict[str, Any]) -> dict[str, Any]:
    """``hypothesis.validate_definition``: reject an underspecified
    hypothesis (spec §6/§10)."""
    try:
        hf_registry.validate_definition(body)
    except EngineError as exc:
        raise _engine_error(exc) from exc
    return {"valid": True}


@router.get("/vocabularies")
async def vocabularies(
    domain: bool = Query(default=False),
) -> dict[str, Any]:
    """The status/domain/origin/relationship vocabularies (the UI's source)."""
    from engine.hypothesis import models as hf_models

    payload: dict[str, Any] = {
        "statuses": list(hf_models.STATUSES),
        "origins": list(hf_models.ORIGINS),
        "relationships": list(hf_models.RELATIONSHIPS),
    }
    if domain:
        payload["domains"] = list(hf_models.DOMAINS)
    return payload


__all__ = ["router"]
