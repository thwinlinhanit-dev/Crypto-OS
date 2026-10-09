"""M5 (T5.4) - hypothesis registry (spec 8.4, 8.1; design note m5_stats section 6).

Acceptance evidence produced here:
- CRUD: create (201, frozen criteria, IDEA default), list, status filter
  (REJECTED is the Graveyard query), get one, patch fields;
- pre-registration freeze: PATCH touching criteria -> 422;
- enum enforced on writes, REJECTED needs an autopsy, VALIDATED is M5b (422);
- locked rows reject updates (409, DB trigger backstop);
- open-holdout: first call 200 + audit row, second call 409, unknown 404.
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
from tests.support import reset_research

CRITERIA: dict[str, Any] = {"pass": ["grade B or better"], "kill": ["grade D twice"]}


_MISSING: Any = object()


def _create(
    client: TestClient,
    title: str = "t",
    statement: str = "s",
    criteria: Any = _MISSING,
) -> Any:
    payload: dict[str, Any] = {"title": title, "statement": statement}
    payload["criteria"] = CRITERIA if criteria is _MISSING else criteria
    return client.post("/v1/hypotheses", json=payload)


def _hyp_id(client: TestClient) -> str:
    return str(_create(client).json()["hyp_id"])


def _wipe(conn: Connection) -> None:
    reset_research(conn)


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


# --------------------------------------------------------------------------
# CRUD + freeze rules
# --------------------------------------------------------------------------


def test_create_freezes_criteria(client: TestClient) -> None:
    response = _create(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "t"
    assert body["status"] == "IDEA"
    assert body["criteria"] == CRITERIA
    assert body["preregistered_at"] is not None
    assert body["holdout_opened_at"] is None
    assert body["holdout_result"] is None
    assert body["locked"] is False


def test_create_rejects_bad_criteria(client: TestClient) -> None:
    bad: Any
    for bad in (None, [], "x", {"pass": ["a"]}, {"pass": ["a"], "kill": "x"}):
        response = _create(client, criteria=bad)
        assert response.status_code == 422, bad
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_list_get_and_status_filter(client: TestClient) -> None:
    first = _hyp_id(client)
    second = _hyp_id(client)
    listed = client.get("/v1/hypotheses").json()
    assert {h["hyp_id"] for h in listed} == {first, second}
    assert client.get("/v1/hypotheses", params={"status": "IDEA"}).json() == listed
    assert client.get("/v1/hypotheses", params={"status": "REJECTED"}).json() == []
    bad = client.get("/v1/hypotheses", params={"status": "BOGUS"})
    assert bad.status_code == 422
    one = client.get(f"/v1/hypotheses/{first}").json()
    assert one["hyp_id"] == first
    missing = client.get(f"/v1/hypotheses/{uuid.uuid4()}")
    assert missing.status_code == 404


def test_patch_fields(client: TestClient) -> None:
    hyp_id = _hyp_id(client)
    response = client.patch(
        f"/v1/hypotheses/{hyp_id}",
        json={"title": "t2", "statement": "s2", "status": "TESTING"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["title"], body["statement"], body["status"]) == ("t2", "s2", "TESTING")


def test_patch_criteria_frozen(client: TestClient) -> None:
    hyp_id = _hyp_id(client)
    response = client.patch(f"/v1/hypotheses/{hyp_id}", json={"criteria": CRITERIA})
    assert response.status_code == 422
    assert "pre-registered" in response.json()["error"]["message"]


def test_patch_status_rules(client: TestClient) -> None:
    hyp_id = _hyp_id(client)
    bad = client.patch(f"/v1/hypotheses/{hyp_id}", json={"status": "BOGUS"})
    assert bad.status_code == 422
    no_autopsy = client.patch(f"/v1/hypotheses/{hyp_id}", json={"status": "REJECTED"})
    assert no_autopsy.status_code == 422
    validated = client.patch(f"/v1/hypotheses/{hyp_id}", json={"status": "VALIDATED"})
    assert validated.status_code == 422
    assert validated.json()["error"]["detail"] == {"requires_frozen_experiment": "M5b"}
    rejected = client.patch(
        f"/v1/hypotheses/{hyp_id}", json={"status": "REJECTED", "autopsy": "dead"}
    )
    assert rejected.status_code == 200, rejected.text
    graveyard = client.get("/v1/hypotheses", params={"status": "REJECTED"}).json()
    assert [h["hyp_id"] for h in graveyard] == [hyp_id]


def test_patch_locked_row_rejected(client: TestClient, conn: Connection) -> None:
    hyp_id = _hyp_id(client)
    conn.execute(
        text("UPDATE research.hypothesis SET locked = true WHERE hyp_id = :id"),
        {"id": hyp_id},
    )
    conn.commit()
    response = client.patch(f"/v1/hypotheses/{hyp_id}", json={"title": "t2"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IMMUTABLE_RESULT"


# --------------------------------------------------------------------------
# Open-holdout once
# --------------------------------------------------------------------------


def test_open_holdout_once(client: TestClient, conn: Connection) -> None:
    hyp_id = _hyp_id(client)
    first = client.post(f"/v1/hypotheses/{hyp_id}/open-holdout")
    assert first.status_code == 200, first.text
    assert first.json()["holdout_opened_at"] is not None
    audit = conn.execute(
        text(
            "SELECT COUNT(*) FROM system.audit_log"
            " WHERE action = 'hypothesis.open_holdout' AND target = :id"
        ),
        {"id": hyp_id},
    ).scalar_one()
    assert audit == 1
    second = client.post(f"/v1/hypotheses/{hyp_id}/open-holdout")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "HOLDOUT_LOCKED"
    missing = client.post(f"/v1/hypotheses/{uuid.uuid4()}/open-holdout")
    assert missing.status_code == 404
