"""M11 — the backup/export API (spec M11 T11.4, design note m11_hardening
section 3).

POST /v1/backups returns the bundle JSON (the UI offers it as a download,
OQ-27); GET /v1/backups/{id} is reserved for stored bundles (the default
deployment returns the export directly, no server-side retention).
Fail-closed: an unknown schema version on import is 422.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from apps.api.db import connection_scope
from engine.backup import (
    BackupValidationError,
    export_backup,
    import_backup,
)

router = APIRouter(prefix="/v1/backups", tags=["backup"])


@router.post("")
async def create_backup() -> dict[str, Any]:
    """POST /v1/backups: export the notebook + registry + lineage (spec
    M11 T11.4). The response is the bundle; the UI offers it as a
    download (OQ-27)."""
    with connection_scope() as conn:
        return export_backup(conn)


@router.get("/{backup_id}")
async def get_backup(backup_id: str) -> dict[str, Any]:
    """GET /v1/backups/{id}: a stored bundle by id. The default deployment
    keeps no server-side bundles (export is a direct download), so this
    answers 404 unless the id names a stored export."""
    raise HTTPException(404, f"backup {backup_id} not found; exports are direct downloads")


@router.post("/import")
async def import_backup_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """POST /v1/backups/import: validate + import a bundle (D-079:
    idempotent; the frozen contract preserved)."""
    with connection_scope() as conn:
        try:
            inserted = import_backup(conn, bundle)
        except BackupValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
    return {"inserted": inserted}
