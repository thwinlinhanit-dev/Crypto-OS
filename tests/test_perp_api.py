"""M10 — the perp API tests (spec §11: GET /v1/funding|oi|liquidations,
WebSocket /v1/ws; design note m10_perp_live section 5).

Live-DB suite: seeds a PERPETUAL instrument with funding/OI/liquidation
rows, serves them, and asserts the fail-closed vocabulary (PD-10: a spot
instrument is 422, an unknown instrument 404, an unknown timeframe 422).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text

from apps.api.main import create_app
from apps.api.settings import get_settings

T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture()
def conn() -> Iterator[Connection]:
    engine = create_engine(
        get_settings().database_url, future=True, connect_args={"connect_timeout": 3}
    )
    try:
        connection = engine.connect()
    except Exception as exc:
        engine.dispose()
        pytest.skip(f"database unreachable: {exc}")
    try:
        yield connection
    finally:
        connection.close()
        engine.dispose()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def clean(conn: Connection) -> Iterator[None]:
    conn.execute(text("SET session_replication_role = replica"))
    for table in (
        "market.liquidation", "market.open_interest", "market.funding_rate",
        "market.instrument", "market.dataset_manifest",
    ):
        conn.execute(text(f"DELETE FROM {table}"))
    conn.execute(text("SET session_replication_role = DEFAULT"))
    conn.commit()
    try:
        yield
    finally:
        conn.execute(text("SET session_replication_role = replica"))
        for table in (
            "market.liquidation", "market.open_interest", "market.funding_rate",
            "market.instrument", "market.dataset_manifest",
        ):
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("SET session_replication_role = DEFAULT"))
        conn.commit()


def _seed_instrument(
    conn: Connection,
    *,
    market_type: str,
    symbol: str,
    with_rows: bool = True,
) -> str:
    """Insert an instrument (spot or perp) and (optionally) its perp rows."""
    instrument_id = str(uuid4())
    conn.execute(
        text(
            "INSERT INTO market.instrument (instrument_id, venue, market_type,"
            " asset, quote, native_symbol, tick_size, lot_step, listed_at)"
            " VALUES (:id, 'BINANCE', :mt, 'BTC', 'USDT', :sym, 0.1, 0.001, :at)"
        ),
        {"id": instrument_id, "mt": market_type, "sym": symbol, "at": T0},
    )
    if with_rows and market_type == "PERPETUAL":
        for offset in (8, 16, 24):
            conn.execute(
                text(
                    "INSERT INTO market.funding_rate (instrument_id, ts, rate)"
                    " VALUES (:id, :ts, :rate)"
                ),
                {
                    "id": instrument_id,
                    "ts": T0.replace(hour=offset % 24),
                    "rate": 0.0001 * offset,
                },
            )
        conn.execute(
            text(
                "INSERT INTO market.open_interest (instrument_id, timeframe, bar_time, oi)"
                " VALUES (:id, '1D', :ts, :oi)"
            ),
            {"id": instrument_id, "ts": T0, "oi": 1234.5},
        )
        conn.execute(
            text(
                "INSERT INTO market.liquidation (instrument_id, ts, side, qty, price)"
                " VALUES (:id, :ts, 'LONG', 2.0, 60000.0)"
            ),
            {"id": instrument_id, "ts": T0},
        )
    conn.commit()
    return instrument_id


def test_funding_endpoint_serves_the_rows(
    client: TestClient, conn: Connection
) -> None:
    instrument_id = _seed_instrument(conn, market_type="PERPETUAL", symbol="BTCUSDT")
    response = client.get(f"/v1/funding/{instrument_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "BTCUSDT"
    assert len(body["funding"]) == 3
    assert body["funding"][0]["rate"] > 0


def test_oi_endpoint_serves_the_rows(
    client: TestClient, conn: Connection
) -> None:
    instrument_id = _seed_instrument(conn, market_type="PERPETUAL", symbol="BTCUSDT")
    response = client.get(f"/v1/oi/{instrument_id}", params={"tf": "1D"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["timeframe"] == "1D"
    assert body["open_interest"][0]["oi"] == 1234.5


def test_liquidations_endpoint_serves_the_rows(
    client: TestClient, conn: Connection
) -> None:
    instrument_id = _seed_instrument(conn, market_type="PERPETUAL", symbol="BTCUSDT")
    response = client.get(f"/v1/liquidations/{instrument_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["liquidations"][0]["side"] == "LONG"
    assert body["liquidations"][0]["qty"] == 2.0


def test_funding_on_a_spot_instrument_is_422(
    client: TestClient, conn: Connection
) -> None:
    """PD-10: perp data for perp only; a spot instrument is 422, never
    substituted."""
    instrument_id = _seed_instrument(
        conn, market_type="SPOT", symbol="BTCUSDT", with_rows=False
    )
    response = client.get(f"/v1/funding/{instrument_id}")
    assert response.status_code == 422
    assert "PD-10" in response.text


def test_oi_on_a_spot_instrument_is_422(
    client: TestClient, conn: Connection
) -> None:
    instrument_id = _seed_instrument(
        conn, market_type="SPOT", symbol="BTCUSDT", with_rows=False
    )
    response = client.get(f"/v1/oi/{instrument_id}")
    assert response.status_code == 422


def test_an_unknown_instrument_is_404(
    client: TestClient, conn: Connection
) -> None:
    response = client.get(f"/v1/funding/{uuid4()}")
    assert response.status_code == 404


def test_an_unknown_timeframe_is_422(
    client: TestClient, conn: Connection
) -> None:
    instrument_id = _seed_instrument(conn, market_type="PERPETUAL", symbol="BTCUSDT")
    response = client.get(f"/v1/oi/{instrument_id}", params={"tf": "7D"})
    assert response.status_code == 422


def test_ws_subscribes_to_the_candle_channel(
    client: TestClient, conn: Connection
) -> None:
    """Spec §11 / D-073: /v1/ws multiplexes candle.{instrument}.{tf}."""
    instrument_id = _seed_instrument(conn, market_type="PERPETUAL", symbol="BTCUSDT")
    with client.websocket_connect("/v1/ws") as socket:
        socket.send_json(
            {"op": "subscribe", "channel": "candle", "instrument": instrument_id, "tf": "1D"}
        )
        response = socket.receive_json()
        assert response["type"] == "subscribed"
        assert response["channel"] == "candle"


def test_ws_rejects_an_unknown_channel(
    client: TestClient, conn: Connection
) -> None:
    with client.websocket_connect("/v1/ws") as socket:
        socket.send_json({"op": "subscribe", "channel": "orderbook"})
        response = socket.receive_json()
        assert response["type"] == "error"


def test_ws_ping_pong(client: TestClient, conn: Connection) -> None:
    with client.websocket_connect("/v1/ws") as socket:
        socket.send_json({"op": "ping"})
        response = socket.receive_json()
        assert response["type"] == "pong"
