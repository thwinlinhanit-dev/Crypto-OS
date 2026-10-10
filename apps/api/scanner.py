"""M8 — watchlists + scanner + MTF API (spec 11, design note m8_scanner).

- CRUD ``/v1/watchlists`` (+ membership).
- ``GET /v1/scanner?watchlist&tf&event_types[]&min_grade&filters`` — the
  spec UI 560 rows, computed from stored data only (PD-5, D-056).
- ``GET /v1/mtf?instrument_id`` — per-timeframe regime summary reusing
  ``engine/features/regime.py`` verbatim (D-054).

Fail-closed (PD-4/PD-10/PD-12): unknown watchlist 404, unknown timeframe
422, unknown ``min_grade`` 422 (the A-D vocabulary, D-028).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, text

from apps.api.db import connection_scope
from engine.errors import NotFoundError, ValidationError
from engine.scanner.scan import GRADES, scan

router = APIRouter(prefix="/v1", tags=["scanner"])

#: Native timeframes the scanner serves (1W is derived, not in the seed).
TIMEFRAMES = ("1D", "4H", "1H")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WatchlistCreate(Model):
    name: str
    instrument_ids: list[UUID] = []


class WatchlistView(Model):
    watchlist_id: UUID
    name: str
    instrument_ids: list[UUID]
    created_at: str


class WatchlistItemsAdd(Model):
    """``POST /v1/watchlists/{id}/items`` body: membership only."""

    instrument_ids: list[UUID] = []


def _require_text(value: str, field: str) -> str:
    if not value.strip():
        raise ValidationError(f"{field} must be a non-empty string", {field: value})
    return value


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------
# watchlists
# --------------------------------------------------------------------------


@router.post("/watchlists", response_model=WatchlistView, status_code=201, tags=["scanner"])
async def create_watchlist(body: WatchlistCreate) -> WatchlistView:
    name = _require_text(body.name, "name")
    instrument_ids = sorted({str(i) for i in body.instrument_ids})
    watchlist_id = uuid.uuid4()
    with connection_scope() as conn:
        for instrument in instrument_ids:
            _require_instrument(conn, UUID(instrument))
        conn.execute(
            text("INSERT INTO market.watchlist (watchlist_id, name) VALUES (:id, :name)"),
            {"id": watchlist_id, "name": name},
        )
        for instrument in instrument_ids:
            conn.execute(
                text(
                    "INSERT INTO market.watchlist_item (watchlist_id, instrument_id)"
                    " VALUES (:w, :i)"
                ),
                {"w": watchlist_id, "i": instrument},
            )
        return WatchlistView(
            watchlist_id=watchlist_id,
            name=name,
            instrument_ids=[UUID(i) for i in instrument_ids],
            created_at=_iso(datetime.now(UTC)) or "",
        )


@router.get("/watchlists", response_model=list[WatchlistView], tags=["scanner"])
async def list_watchlists() -> list[WatchlistView]:
    with connection_scope() as conn:
        rows = conn.execute(
            text(
                "SELECT w.watchlist_id, w.name, w.created_at,"
                " COALESCE(array_agg(i.instrument_id) FILTER (WHERE i.instrument_id IS NOT NULL),"
                " '{}') AS items"
                " FROM market.watchlist w"
                " LEFT JOIN market.watchlist_item i ON i.watchlist_id = w.watchlist_id"
                " GROUP BY w.watchlist_id, w.name, w.created_at"
                " ORDER BY w.name"
            )
        ).all()
        return [_watchlist_view(row) for row in rows]


@router.get("/watchlists/{watchlist_id}", response_model=WatchlistView, tags=["scanner"])
async def get_watchlist(watchlist_id: UUID) -> WatchlistView:
    with connection_scope() as conn:
        row = conn.execute(
            text(
                "SELECT w.watchlist_id, w.name, w.created_at,"
                " COALESCE(array_agg(i.instrument_id) FILTER (WHERE i.instrument_id IS NOT NULL),"
                " '{}') AS items"
                " FROM market.watchlist w"
                " LEFT JOIN market.watchlist_item i ON i.watchlist_id = w.watchlist_id"
                " WHERE w.watchlist_id = :id GROUP BY w.watchlist_id, w.name, w.created_at"
            ),
            {"id": watchlist_id},
        ).first()
    if row is None:
        raise NotFoundError("watchlist not found", {"watchlist_id": str(watchlist_id)})
    return _watchlist_view(row)


@router.delete("/watchlists/{watchlist_id}", tags=["scanner"])
async def delete_watchlist(watchlist_id: UUID) -> dict[str, str]:
    with connection_scope() as conn:
        exists = conn.execute(
            text("SELECT watchlist_id FROM market.watchlist WHERE watchlist_id = :id"),
            {"id": watchlist_id},
        ).first()
        if exists is None:
            raise NotFoundError("watchlist not found", {"watchlist_id": str(watchlist_id)})
        conn.execute(
            text("DELETE FROM market.watchlist WHERE watchlist_id = :id"), {"id": watchlist_id}
        )
    return {"deleted": str(watchlist_id)}


@router.post(
    "/watchlists/{watchlist_id}/items", response_model=WatchlistView, tags=["scanner"]
)
async def add_watchlist_item(watchlist_id: UUID, body: WatchlistItemsAdd) -> WatchlistView:
    instrument_ids = sorted({str(i) for i in body.instrument_ids})
    with connection_scope() as conn:
        _require_watchlist(conn, watchlist_id)
        for instrument in instrument_ids:
            _require_instrument(conn, UUID(instrument))
            conn.execute(
                text(
                    "INSERT INTO market.watchlist_item (watchlist_id, instrument_id)"
                    " VALUES (:w, :i) ON CONFLICT DO NOTHING"
                ),
                {"w": watchlist_id, "i": instrument},
            )
        row = conn.execute(
            text(
                "SELECT w.watchlist_id, w.name, w.created_at,"
                " COALESCE(array_agg(i.instrument_id) FILTER (WHERE i.instrument_id IS NOT NULL),"
                " '{}') AS items"
                " FROM market.watchlist w"
                " LEFT JOIN market.watchlist_item i ON i.watchlist_id = w.watchlist_id"
                " WHERE w.watchlist_id = :id GROUP BY w.watchlist_id, w.name, w.created_at"
            ),
            {"id": watchlist_id},
        ).first()
        assert row is not None
        return _watchlist_view(row)


# --------------------------------------------------------------------------
# scanner + MTF
# --------------------------------------------------------------------------


@router.get("/scanner", tags=["scanner"])
async def get_scanner(
    watchlist: UUID | None = None,
    tf: str = "1D",
    event_types: list[str] | None = None,
    min_grade: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Spec 11/12: rows {instrument, price, chg_24h, rvol, last_event,
    regime, grade, n_eff}. One row per instrument, stored data only (PD-5)."""
    if tf not in TIMEFRAMES:
        raise ValidationError("unknown timeframe", {"timeframe": tf, "allowed": list(TIMEFRAMES)})
    if min_grade is not None and min_grade not in GRADES:
        raise ValidationError("unknown min_grade", {"min_grade": min_grade, "allowed": list(GRADES)})
    as_of_dt = None
    if as_of is not None:
        try:
            as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError("invalid as_of timestamp", {"as_of": as_of}) from exc
    with connection_scope() as conn:
        result = scan(
            conn,
            timeframe=tf,
            watchlist_id=str(watchlist) if watchlist is not None else None,
            event_types=event_types or [],
            min_grade=min_grade,
            as_of=as_of_dt,
        )
    return {
        "rows": [row.as_dict() for row in result.rows],
        "dataset_version": result.dataset_version,
        "timeframe": result.timeframe,
        "as_of": _iso(as_of_dt),
    }


@router.get("/mtf", tags=["scanner"])
async def get_mtf(instrument_id: UUID) -> dict[str, Any]:
    """Spec 11: per-timeframe regime summary for one instrument (D-054: the
    regime module is the single definition)."""
    with connection_scope() as conn:
        row = conn.execute(
            text("SELECT instrument_id, native_symbol FROM market.instrument"
                 " WHERE instrument_id = :id"),
            {"id": instrument_id},
        ).first()
        if row is None:
            raise NotFoundError("instrument not found", {"instrument_id": str(instrument_id)})
        from pathlib import Path

        import polars as pl

        from apps.api.settings import get_settings
        from engine.data.parquet import read_part
        from engine.features.regime import trend_state, vol_state, volume_state

        data_dir = Path(get_settings().data_dir)
        timeframes: dict[str, Any] = {}
        for tf in TIMEFRAMES:
            manifest = conn.execute(
                text(
                    "SELECT source_files FROM market.dataset_manifest"
                    " WHERE instruments @> CAST(:i AS jsonb) AND timeframes @> CAST(:tf AS jsonb)"
                    " ORDER BY created_at DESC LIMIT 1"
                ),
                {"i": f'["{instrument_id}"]', "tf": f'["{tf}"]'},
            ).first()
            if manifest is None:
                continue
            parts = []
            files = manifest.source_files
            if isinstance(files, str):
                files = json.loads(files)
            for entry in files or []:
                path = entry["path"] if isinstance(entry, dict) else entry
                if f"/symbol={row.native_symbol}/" not in f"/{path}" or f"/tf={tf}/" not in f"/{path}":
                    continue
                parts.append(read_part(data_dir, str(path)))
            if not parts:
                continue
            frame = pl.concat(parts).sort("open_time")
            trend = trend_state(frame)
            volume = volume_state(frame)
            vol = vol_state(frame)

            def latest(tags: Sequence[str | None]) -> str | None:
                for tag in reversed(tags):
                    if tag is not None:
                        return tag
                return None

            timeframes[tf] = {
                "bars": frame.height,
                "trend_state": latest(trend),
                "vol_state": latest(vol),
                "volume_state": latest(volume),
            }
    return {"instrument_id": str(instrument_id), "symbol": row.native_symbol,
            "timeframes": timeframes}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _watchlist_view(row: Any) -> WatchlistView:
    items = row.items
    if isinstance(items, str):
        items = json.loads(items)
    return WatchlistView(
        watchlist_id=row.watchlist_id,
        name=row.name,
        instrument_ids=[UUID(str(i)) for i in items or []],
        created_at=_iso(row.created_at) or "",
    )


def _require_watchlist(conn: Connection, watchlist_id: UUID) -> None:
    row = conn.execute(
        text("SELECT watchlist_id FROM market.watchlist WHERE watchlist_id = :id"),
        {"id": watchlist_id},
    ).first()
    if row is None:
        raise NotFoundError("watchlist not found", {"watchlist_id": str(watchlist_id)})


def _require_instrument(conn: Connection, instrument_id: UUID) -> None:
    row = conn.execute(
        text("SELECT instrument_id FROM market.instrument WHERE instrument_id = :id"),
        {"id": instrument_id},
    ).first()
    if row is None:
        raise NotFoundError("instrument not found", {"instrument_id": str(instrument_id)})
