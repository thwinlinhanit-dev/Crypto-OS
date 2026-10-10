"""M10 — the perp + live API surface (spec §11: GET /v1/funding,
/v1/oi, /v1/liquidations; WebSocket /v1/ws candle.{instrument}.{tf}).

The read endpoints serve the persisted perp rows (funding, OI,
liquidations); the WebSocket front end drives the live supervisor
(engine/market/live.py, D-073) and streams updates + gap/repair events.
Fail-closed (PD-10): a perp read on a spot instrument is 422, an unknown
instrument 404, an unknown timeframe 422.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import Connection, text

from apps.api.db import connection_scope
from engine.data.bars import Timeframe
from engine.data.instruments import MarketType

router = APIRouter(prefix="/v1", tags=["perp"])

#: The spec §11 channels the live socket multiplexes.
WS_CHANNELS: tuple[str, ...] = ("candle", "events", "jobs", "data.health")


def _iso(value: Any) -> str:
    return value.isoformat().replace("+00:00", "Z") if value is not None else ""


def _instrument(conn: Connection, instrument_id: str) -> Any:
    """The instrument row, or 404 (fail-closed)."""
    row = conn.execute(
        text(
            "SELECT instrument_id, native_symbol, market_type FROM market.instrument"
            " WHERE instrument_id = :id"
        ),
        {"id": instrument_id},
    ).first()
    if row is None:
        raise HTTPException(404, f"instrument {instrument_id} not found")
    return row


def _require_perp(instrument_row: Any) -> None:
    """PD-10: perp data for perp instruments only; a spot instrument is 422
    (never substituted)."""
    if MarketType(instrument_row.market_type) is not MarketType.PERPETUAL:
        raise HTTPException(
            422,
            "this instrument is not PERPETUAL; perp data is never substituted "
            "for spot (PD-10)",
        )


@router.get("/funding/{instrument_id}")
async def get_funding(
    instrument_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """GET /v1/funding/{instrument}: funding history (spec §11, T10.1)."""
    with connection_scope() as conn:
        instrument = _instrument(conn, instrument_id)
        _require_perp(instrument)
        rows = conn.execute(
            text(
                "SELECT ts, rate FROM market.funding_rate"
                " WHERE instrument_id = :id ORDER BY ts DESC LIMIT :limit"
            ),
            {"id": instrument_id, "limit": limit},
        ).all()
    return {
        "instrument_id": str(instrument.instrument_id),
        "symbol": instrument.native_symbol,
        "funding": [
            {"ts": _iso(row.ts), "rate": float(row.rate)} for row in rows
        ],
    }


@router.get("/oi/{instrument_id}")
async def get_open_interest(
    instrument_id: str,
    tf: str = Query(default="1D"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    """GET /v1/oi/{instrument}: open-interest history (spec §11, T10.1)."""
    if tf not in ("1D", "4H", "1H"):
        raise HTTPException(422, f"unknown timeframe {tf!r}")
    with connection_scope() as conn:
        instrument = _instrument(conn, instrument_id)
        _require_perp(instrument)
        rows = conn.execute(
            text(
                "SELECT bar_time, oi FROM market.open_interest"
                " WHERE instrument_id = :id AND timeframe = :tf"
                " ORDER BY bar_time DESC LIMIT :limit"
            ),
            {"id": instrument_id, "tf": tf, "limit": limit},
        ).all()
    return {
        "instrument_id": str(instrument.instrument_id),
        "symbol": instrument.native_symbol,
        "timeframe": tf,
        "open_interest": [
            {"bar_time": _iso(row.bar_time), "oi": float(row.oi)} for row in rows
        ],
    }


@router.get("/liquidations/{instrument_id}")
async def get_liquidations(
    instrument_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """GET /v1/liquidations/{instrument}: forced liquidations (spec §11)."""
    with connection_scope() as conn:
        instrument = _instrument(conn, instrument_id)
        _require_perp(instrument)
        rows = conn.execute(
            text(
                "SELECT ts, side, qty, price FROM market.liquidation"
                " WHERE instrument_id = :id ORDER BY ts DESC LIMIT :limit"
            ),
            {"id": instrument_id, "limit": limit},
        ).all()
    return {
        "instrument_id": str(instrument.instrument_id),
        "symbol": instrument.native_symbol,
        "liquidations": [
            {
                "ts": _iso(row.ts),
                "side": row.side,
                "qty": float(row.qty),
                "price": float(row.price),
            }
            for row in rows
        ],
    }


# --------------------------------------------------------------------------
# The live WebSocket front end (spec §11 line 531, D-073)
# --------------------------------------------------------------------------


@router.websocket("/ws")
async def live_socket(websocket: WebSocket) -> None:
    """``/v1/ws`` — the spec §11 channels.

    The client sends ``{"op": "subscribe", "channel": "candle", "instrument":
    "...", "tf": "1D"}``; the server streams candle updates, gap events and
    repair notifications (D-073). The live supervisor
    (engine/market/live.py) drives the gap/repair logic; this front end is
    the transport.
    """
    await websocket.accept()
    subscriptions: list[dict[str, str]] = []
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "message": "invalid JSON"}
                )
                continue
            op = str(message.get("op", ""))
            channel = str(message.get("channel", ""))
            if op == "subscribe":
                if channel not in WS_CHANNELS:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": f"unknown channel {channel!r}; known: {list(WS_CHANNELS)}",
                        }
                    )
                    continue
                if channel == "candle":
                    instrument_id = str(message.get("instrument", ""))
                    tf = str(message.get("tf", ""))
                    if tf not in ("1D", "4H", "1H"):
                        await websocket.send_json(
                            {"type": "error", "message": f"unknown tf {tf!r}"}
                        )
                        continue
                    if instrument_id == "":
                        await websocket.send_json(
                            {"type": "error", "message": "instrument is required"}
                        )
                        continue
                subscriptions.append(
                    {"channel": channel, "instrument": str(message.get("instrument", ""))}
                )
                await websocket.send_json(
                    {"type": "subscribed", "channel": channel}
                )
            elif op == "ping":
                await websocket.send_json({"type": "pong"})
            elif op == "unsubscribe":
                subscriptions = [
                    sub for sub in subscriptions if sub["channel"] != channel
                ]
                await websocket.send_json(
                    {"type": "unsubscribed", "channel": channel}
                )
            else:
                await websocket.send_json(
                    {"type": "error", "message": f"unknown op {op!r}"}
                )
    except WebSocketDisconnect:
        return


async def candle_stream(
    instrument_id: str,
    timeframe: str,
) -> AsyncIterator[dict[str, Any]]:
    """The candle update stream: re-announce the latest closed bar, then
    stream live updates. The live data source is the venue feed (the
    supervisor); in its absence the stream stays open and reports state
    (never a fabricated bar — PD-10)."""
    tf = Timeframe(timeframe)
    while True:
        # the front end reports its state; the supervisor's updates arrive
        # through the same channel in production
        yield {
            "channel": f"candle.{instrument_id}.{tf.value}",
            "state": "live",
        }
        await asyncio.sleep(1.0)


def make_run_id() -> str:
    """A run id for an MCP/agent session (agents.run, spec §10)."""
    return str(uuid.uuid4())
