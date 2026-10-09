"""M5 (T5.4) - hypothesis registry (spec 8.4, 8.1, 10, 11).

Pre-registered research questions with frozen criteria and a one-time
holdout opening. Reads only plus queue-free sync writes; no order surface
(PD-1). Fail-closed (PD-4/PD-10): criteria are immutable after creation
(422, edits create a new hypothesis), status enum enforced on every write
(422), REJECTED requires an autopsy (422), VALIDATED needs frozen
experiments (422, M5b), locked rows reject UPDATE/DELETE (409 via the DB
trigger mapped here), second open-holdout is 409 HOLDOUT_LOCKED.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from apps.api.db import connection_scope
from apps.api.errors import ErrorCode
from engine.errors import HoldoutLockedError, ImmutableResultError, NotFoundError, ValidationError

router = APIRouter(prefix="/v1", tags=["hypotheses"])

#: Spec 8.4 status enum (mirrored by migration 0004's ck_hypothesis_status).
HYPOTHESIS_STATUSES = (
    "IDEA",
    "TESTING",
    "PROMISING",
    "SUPPORTED",
    "WEAK",
    "REJECTED",
    "VALIDATED",
)


class HypothesisCreate(BaseModel):
    """``POST /v1/hypotheses`` body: criteria frozen at creation (8.4)."""

    title: str
    statement: str
    criteria: dict[str, Any]


class HypothesisPatch(BaseModel):
    """``PATCH /v1/hypotheses/{id}`` body: criteria may never appear here."""

    title: str | None = None
    statement: str | None = None
    status: str | None = None
    autopsy: str | None = None
    criteria: dict[str, Any] | None = None


class HypothesisView(BaseModel):
    """One hypothesis row as served (spec 10 columns)."""

    hyp_id: UUID
    title: str
    statement: str
    status: str
    criteria: dict[str, Any]
    preregistered_at: str
    holdout_opened_at: str | None
    holdout_result: dict[str, Any] | None
    autopsy: str | None
    locked: bool


def _view(row: Any) -> HypothesisView:
    return HypothesisView(
        hyp_id=row.hyp_id,
        title=row.title,
        statement=row.statement,
        status=row.status,
        criteria=dict(row.criteria),
        preregistered_at=row.preregistered_at.isoformat(),
        holdout_opened_at=(
            row.holdout_opened_at.isoformat() if row.holdout_opened_at is not None else None
        ),
        holdout_result=dict(row.holdout_result) if row.holdout_result is not None else None,
        autopsy=row.autopsy,
        locked=bool(row.locked),
    )


def _require_criteria(criteria: Any) -> dict[str, Any]:
    """Pre-registered pass/kill criteria (spec 8.4 ``criteria{pass[], kill[]}``)."""
    if not isinstance(criteria, dict):
        raise ValidationError("criteria must be a mapping", {"criteria": criteria})
    for key in ("pass", "kill"):
        entries = criteria.get(key)
        if not isinstance(entries, list) or any(not isinstance(e, str) for e in entries):
            raise ValidationError(
                "criteria needs 'pass' and 'kill' lists of strings",
                {"criteria": criteria},
            )
    return dict(criteria)


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty string", {field: value})
    return value


def _require_status(value: Any) -> str:
    if value not in HYPOTHESIS_STATUSES:
        raise ValidationError(
            "unknown hypothesis status",
            {"status": value, "allowed": list(HYPOTHESIS_STATUSES)},
        )
    return str(value)


def _locked_guard(conn: Connection, hyp_id: UUID) -> Any:
    """Fetch the row, 404 when absent, 409 when the DB froze it (PD-13)."""
    row = conn.execute(
        text("SELECT * FROM research.hypothesis WHERE hyp_id = :id"), {"id": hyp_id}
    ).first()
    if row is None:
        raise NotFoundError("hypothesis not found", {"hypothesis_id": str(hyp_id)})
    if bool(row.locked):
        raise ImmutableResultError(
            "hypothesis is locked; follow-ups create a new hypothesis",
            {"hypothesis_id": str(hyp_id)},
        )
    return row


def _write(conn: Connection, sql: str, params: dict[str, Any]) -> Any:
    """Execute a hypothesis write, mapping the locked-row trigger to 409."""
    try:
        return conn.execute(text(sql), params)
    except DBAPIError as exc:
        if ErrorCode.IMMUTABLE_RESULT.value in str(exc):
            raise ImmutableResultError(
                "hypothesis is locked; follow-ups create a new hypothesis",
                {},
            ) from exc
        raise


@router.post("/hypotheses", response_model=HypothesisView, status_code=201, tags=["hypotheses"])
async def create_hypothesis(body: HypothesisCreate) -> HypothesisView:
    """Pre-register a hypothesis: criteria frozen with timestamp (spec 8.4)."""
    title = _require_text(body.title, "title")
    statement = _require_text(body.statement, "statement")
    criteria = _require_criteria(body.criteria)
    hyp_id = uuid.uuid4()
    with connection_scope() as conn:
        row = _write(
            conn,
            "INSERT INTO research.hypothesis (hyp_id, title, statement, status,"
            " criteria, preregistered_at) VALUES (:id, :title, :statement, 'IDEA',"
            " CAST(:criteria AS jsonb), now()) RETURNING *",
            {"id": hyp_id, "title": title, "statement": statement,
             "criteria": json.dumps(criteria)},
        ).first()
        assert row is not None
        return _view(row)


@router.get("/hypotheses", response_model=list[HypothesisView], tags=["hypotheses"])
async def list_hypotheses(status: str | None = Query(default=None)) -> list[HypothesisView]:
    """List hypotheses; ``?status=REJECTED`` is the Graveyard view (spec 8.4)."""
    if status is not None:
        _require_status(status)
    with connection_scope() as conn:
        if status is None:
            rows = conn.execute(
                text(
                    "SELECT * FROM research.hypothesis"
                    " ORDER BY preregistered_at DESC, hyp_id ASC"
                )
            ).all()
        else:
            rows = conn.execute(
                text(
                    "SELECT * FROM research.hypothesis WHERE status = :status"
                    " ORDER BY preregistered_at DESC, hyp_id ASC"
                ),
                {"status": status},
            ).all()
        return [_view(row) for row in rows]


@router.get("/hypotheses/{hyp_id}", response_model=HypothesisView, tags=["hypotheses"])
async def get_hypothesis(hyp_id: UUID) -> HypothesisView:
    """One hypothesis by id (404 unknown)."""
    with connection_scope() as conn:
        row = conn.execute(
            text("SELECT * FROM research.hypothesis WHERE hyp_id = :id"), {"id": hyp_id}
        ).first()
    if row is None:
        raise NotFoundError("hypothesis not found", {"hypothesis_id": str(hyp_id)})
    return _view(row)


@router.patch("/hypotheses/{hyp_id}", response_model=HypothesisView, tags=["hypotheses"])
async def patch_hypothesis(hyp_id: UUID, body: HypothesisPatch) -> HypothesisView:
    """Edit title/statement/status/autopsy. Criteria are pre-registered (422)."""
    if body.criteria is not None:
        raise ValidationError(
            "criteria are pre-registered; edits create a new hypothesis",
            {"hypothesis_id": str(hyp_id)},
        )
    updates: dict[str, Any] = {}
    if body.title is not None:
        updates["title"] = _require_text(body.title, "title")
    if body.statement is not None:
        updates["statement"] = _require_text(body.statement, "statement")
    status = _require_status(body.status) if body.status is not None else None
    if status == "VALIDATED":
        raise ValidationError(
            "VALIDATED requires frozen experiment(s) per 9.4 (M5b)",
            {"requires_frozen_experiment": "M5b"},
        )
    if body.autopsy is not None and not isinstance(body.autopsy, str):
        raise ValidationError("autopsy must be a string", {})
    with connection_scope() as conn:
        current = _locked_guard(conn, hyp_id)
        new_status = status if status is not None else current.status
        new_autopsy = body.autopsy if body.autopsy is not None else current.autopsy
        if new_status == "REJECTED" and not (isinstance(new_autopsy, str) and new_autopsy.strip()):
            raise ValidationError(
                "REJECTED requires a non-empty autopsy (spec 8.4)",
                {"hypothesis_id": str(hyp_id)},
            )
        if status is not None:
            updates["status"] = status
        if body.autopsy is not None:
            updates["autopsy"] = body.autopsy
        if updates:
            assignments = ", ".join(f"{key} = :{key}" for key in updates)
            params = {**updates, "id": hyp_id}
            updated = _write(
                conn,
                f"UPDATE research.hypothesis SET {assignments} WHERE hyp_id = :id"
                " RETURNING *",
                params,
            ).first()
            assert updated is not None
            return _view(updated)
        return _view(current)


@router.post(
    "/hypotheses/{hyp_id}/open-holdout", response_model=HypothesisView, tags=["hypotheses"]
)
async def open_holdout(hyp_id: UUID) -> HypothesisView:
    """Open the holdout exactly once (atomic; second call is 409, spec 8.1)."""
    with connection_scope() as conn:
        row = conn.execute(
            text("SELECT * FROM research.hypothesis WHERE hyp_id = :id"), {"id": hyp_id}
        ).first()
        if row is None:
            raise NotFoundError("hypothesis not found", {"hypothesis_id": str(hyp_id)})
        if bool(row.locked):
            raise ImmutableResultError(
                "hypothesis is locked; follow-ups create a new hypothesis",
                {"hypothesis_id": str(hyp_id)},
            )
        opened = _write(
            conn,
            "UPDATE research.hypothesis SET holdout_opened_at = now()"
            " WHERE hyp_id = :id AND holdout_opened_at IS NULL RETURNING *",
            {"id": hyp_id},
        ).first()
        if opened is None:
            raise HoldoutLockedError(
                "holdout already opened for this hypothesis (once per hypothesis)",
                {"hypothesis_id": str(hyp_id)},
            )
        conn.execute(
            text(
                "INSERT INTO system.audit_log (actor, action, target, detail)"
                " VALUES (:actor, :action, :target, CAST(:detail AS jsonb))"
            ),
            {
                "actor": "api",
                "action": "hypothesis.open_holdout",
                "target": str(hyp_id),
                "detail": json.dumps({"opened_at": opened.holdout_opened_at.isoformat()}),
            },
        )
        return _view(opened)
