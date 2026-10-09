"""M5 (T5.5) - notebook notes (spec 10, 11; design note m5_stats section 7).

Notes link to ``experiment_id`` (NULL until M5b) and never to live queries.
Notes are not results: DELETE is allowed (PD-13 covers result tables only).
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text

from apps.api.db import connection_scope
from engine.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/v1", tags=["notes"])


class NoteCreate(BaseModel):
    """``POST /v1/notes`` body (``experiment_id`` stays NULL until M5b)."""

    title: str
    body_md: str
    experiment_id: UUID | None = None
    chart_state: dict[str, Any] | None = None


class NotePatch(BaseModel):
    """``PATCH /v1/notes/{id}`` body: every field optional."""

    title: str | None = None
    body_md: str | None = None
    experiment_id: UUID | None = None
    chart_state: dict[str, Any] | None = None


class NoteView(BaseModel):
    """One note row as served (spec 10 columns)."""

    note_id: UUID
    title: str
    body_md: str
    experiment_id: UUID | None
    chart_state: dict[str, Any] | None
    created_at: str


def _view(row: Any) -> NoteView:
    return NoteView(
        note_id=row.note_id,
        title=row.title,
        body_md=row.body_md,
        experiment_id=row.experiment_id,
        chart_state=dict(row.chart_state) if row.chart_state is not None else None,
        created_at=row.created_at.isoformat(),
    )


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty string", {field: value})
    return value


def _assignment(key: str) -> str:
    if key == "chart_state":
        return "chart_state = CAST(:chart_state AS jsonb)"
    return f"{key} = :{key}"


@router.post("/notes", response_model=NoteView, status_code=201, tags=["notes"])
async def create_note(body: NoteCreate) -> NoteView:
    """Create a notebook note (the Evidence panel's Save to Notebook lands here)."""
    title = _require_text(body.title, "title")
    body_md = _require_text(body.body_md, "body_md")
    note_id = uuid.uuid4()
    with connection_scope() as conn:
        row = conn.execute(
            text(
                "INSERT INTO research.note (note_id, title, body_md, experiment_id,"
                " chart_state) VALUES (:id, :title, :body_md, :experiment_id,"
                " CAST(:chart_state AS jsonb)) RETURNING *"
            ),
            {
                "id": note_id,
                "title": title,
                "body_md": body_md,
                "experiment_id": body.experiment_id,
                "chart_state": json.dumps(body.chart_state)
                if body.chart_state is not None
                else None,
            },
        ).first()
        assert row is not None
        return _view(row)


@router.get("/notes", response_model=list[NoteView], tags=["notes"])
async def list_notes() -> list[NoteView]:
    """List notes, newest first."""
    with connection_scope() as conn:
        rows = conn.execute(
            text("SELECT * FROM research.note ORDER BY created_at DESC, note_id ASC")
        ).all()
        return [_view(row) for row in rows]


@router.get("/notes/{note_id}", response_model=NoteView, tags=["notes"])
async def get_note(note_id: UUID) -> NoteView:
    """One note by id (404 unknown)."""
    with connection_scope() as conn:
        row = conn.execute(
            text("SELECT * FROM research.note WHERE note_id = :id"), {"id": note_id}
        ).first()
    if row is None:
        raise NotFoundError("note not found", {"note_id": str(note_id)})
    return _view(row)


@router.patch("/notes/{note_id}", response_model=NoteView, tags=["notes"])
async def patch_note(note_id: UUID, body: NotePatch) -> NoteView:
    """Edit a note's title, body, chart state or experiment link."""
    updates: dict[str, Any] = {}
    if body.title is not None:
        updates["title"] = _require_text(body.title, "title")
    if body.body_md is not None:
        updates["body_md"] = _require_text(body.body_md, "body_md")
    if body.experiment_id is not None:
        updates["experiment_id"] = body.experiment_id
    if body.chart_state is not None:
        updates["chart_state"] = json.dumps(body.chart_state)
    with connection_scope() as conn:
        if updates:
            assignments = ", ".join(_assignment(key) for key in updates)
            row = conn.execute(
                text(f"UPDATE research.note SET {assignments} WHERE note_id = :id RETURNING *"),
                {**updates, "id": note_id},
            ).first()
        else:
            row = conn.execute(
                text("SELECT * FROM research.note WHERE note_id = :id"), {"id": note_id}
            ).first()
    if row is None:
        raise NotFoundError("note not found", {"note_id": str(note_id)})
    return _view(row)


@router.delete("/notes/{note_id}", tags=["notes"])
async def delete_note(note_id: UUID) -> Response:
    """Delete a note (notes are not results, PD-13 does not apply)."""
    with connection_scope() as conn:
        deleted = conn.execute(
            text("DELETE FROM research.note WHERE note_id = :id"), {"id": note_id}
        ).rowcount
    if deleted != 1:
        raise NotFoundError("note not found", {"note_id": str(note_id)})
    return Response(status_code=204)
