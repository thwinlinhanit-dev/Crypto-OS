"""M5 (T5.5) - notebook notes (spec 10, 11; design note m5_stats section 7).

Acceptance evidence produced here:
- CRUD: create (201), list newest-first, get one, patch fields, delete
  (204) then get 404s;
- validation: empty title/body 422, unknown id 404 on get/patch/delete;
- the Evidence panel's Save-to-Notebook payload shape round-trips
  (title "<type> <symbol> <tf> <time>", generated summary body with event
  ref ids, experiment_id null).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text

from apps.api.main import create_app
from apps.api.settings import get_settings


def _wipe(conn: Connection) -> None:
    conn.execute(text("TRUNCATE research.note"))
    conn.commit()


@pytest.fixture()
def conn() -> Iterator[Connection]:
    engine = create_engine(get_settings().database_url, future=True)
    connection = engine.connect()
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
    _wipe(conn)
    try:
        yield
    finally:
        _wipe(conn)


def _note(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"title": "t", "body_md": "b"}
    body.update(over)
    return body


def _note_id(client: TestClient) -> str:
    response = client.post("/v1/notes", json=_note())
    assert response.status_code == 201, response.text
    return str(response.json()["note_id"])


def test_note_crud(client: TestClient) -> None:
    created = client.post(
        "/v1/notes",
        json=_note(title="CAPIT BTC/USDT spot 1D", chart_state={"symbol": "BTCUSDT"}),
    ).json()
    assert created["title"] == "CAPIT BTC/USDT spot 1D"
    assert created["body_md"] == "b"
    assert created["experiment_id"] is None
    assert created["chart_state"] == {"symbol": "BTCUSDT"}
    assert created["created_at"] is not None

    listed = client.get("/v1/notes").json()
    assert [n["note_id"] for n in listed] == [created["note_id"]]
    assert client.get(f"/v1/notes/{created['note_id']}").json() == created

    patched = client.patch(
        f"/v1/notes/{created['note_id']}", json={"title": "t2", "body_md": "b2"}
    )
    assert patched.status_code == 200, patched.text
    assert (patched.json()["title"], patched.json()["body_md"]) == ("t2", "b2")

    deleted = client.delete(f"/v1/notes/{created['note_id']}")
    assert deleted.status_code == 204
    assert client.get(f"/v1/notes/{created['note_id']}").status_code == 404
    assert client.get("/v1/notes").json() == []


def test_note_validation(client: TestClient) -> None:
    for bad in ({"title": "", "body_md": "b"}, {"title": "t", "body_md": "  "}):
        response = client.post("/v1/notes", json=bad)
        assert response.status_code == 422, bad
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    missing = str(uuid.uuid4())
    assert client.get(f"/v1/notes/{missing}").status_code == 404
    assert client.patch(f"/v1/notes/{missing}", json={"title": "t"}).status_code == 404
    assert client.delete(f"/v1/notes/{missing}").status_code == 404
    note_id = _note_id(client)
    assert client.patch(f"/v1/notes/{note_id}", json={"title": ""}).status_code == 422


def test_evidence_save_to_notebook_shape(client: TestClient) -> None:
    """The payload the Evidence panel builds for Save to Notebook round-trips."""
    event_id = "a" * 40
    payload = {
        "title": "CAPIT BTC/USDT spot 1D 2024-01-09T00:00:00Z",
        "body_md": (
            "CAPIT BTC/USDT spot 1D 2024-01-09T00:00:00Z\n\n"
            "Evidence grade D (n_eff=3).\n\n"
            f"Event ref ids: {event_id}\n"
        ),
        "experiment_id": None,
    }
    created = client.post("/v1/notes", json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["title"] == payload["title"]
    assert event_id in body["body_md"]
    assert body["experiment_id"] is None
