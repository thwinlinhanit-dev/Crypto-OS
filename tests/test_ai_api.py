"""M9 — the AI API tests (spec §11: POST /v1/ai/chat, GET /v1/ai/tools,
GET /v1/agents/runs).

Live-DB suite: the chat endpoint creates the agents.run row, executes
audited tool calls (D-062) and answers SSE; the tools endpoint exposes
the registry; the runs endpoints list and read back.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text

from apps.api.ai import _tool_executor
from apps.api.db import connection_scope
from apps.api.main import create_app
from apps.api.settings import get_settings


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
    for table in ("agents.tool_call", "agents.run", "system.audit_log"):
        conn.execute(text(f"DELETE FROM {table}"))
    conn.execute(text("SET session_replication_role = DEFAULT"))
    conn.commit()
    try:
        yield
    finally:
        conn.execute(text("SET session_replication_role = replica"))
        for table in ("agents.tool_call", "agents.run", "system.audit_log"):
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("SET session_replication_role = DEFAULT"))
        conn.commit()


def test_tools_endpoint_lists_the_registry(client: TestClient) -> None:
    response = client.get("/v1/ai/tools")
    assert response.status_code == 200, response.text
    names = [t["name"] for t in response.json()["tools"]]
    assert len(names) == 21
    assert "get_experiment" in names
    assert "place_order" not in names


def test_tools_endpoint_filters_by_tier(client: TestClient) -> None:
    response = client.get("/v1/ai/tools", params={"tier": "READ_ONLY"})
    assert response.status_code == 200, response.text
    assert {t["tier"] for t in response.json()["tools"]} == {"READ_ONLY"}


def test_tools_endpoint_rejects_an_unknown_tier(client: TestClient) -> None:
    response = client.get("/v1/ai/tools", params={"tier": "ORDER"})
    assert response.status_code == 422


def test_tools_endpoint_accepts_the_visualization_write_tier(client: TestClient) -> None:
    """D-103: the §26 tier vocabulary reaches the public tools endpoint."""
    response = client.get("/v1/ai/tools", params={"tier": "VISUALIZATION_WRITE"})
    assert response.status_code == 200, response.text
    assert {t["tier"] for t in response.json()["tools"]} <= {"VISUALIZATION_WRITE"}


def test_chat_streams_a_delta_and_a_done_event(client: TestClient) -> None:
    """The echo provider echoes the prompt; the done event carries the run
    id and the (empty) tool calls."""
    response = client.post(
        "/v1/ai/chat",
        json={"mode": "Research", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 200, response.text
    body = response.text
    assert "data: " in body
    assert '"delta"' in body
    assert '"type": "done"' in body or '"type":"done"' in body


def test_chat_rejects_an_unknown_mode(client: TestClient) -> None:
    response = client.post(
        "/v1/ai/chat",
        json={"mode": "Oracle", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 422


def test_chat_rejects_an_unknown_message_role(client: TestClient) -> None:
    response = client.post(
        "/v1/ai/chat",
        json={"mode": "Research", "messages": [{"role": "system32", "content": "hi"}]},
    )
    assert response.status_code == 422


def test_chat_creates_a_run_row(client: TestClient) -> None:
    client.post(
        "/v1/ai/chat",
        json={"mode": "Quant", "messages": [{"role": "user", "content": "q1"}]},
    )
    response = client.get("/v1/agents/runs")
    assert response.status_code == 200, response.text
    runs = response.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["mode"] == "Quant"
    assert runs[0]["user_prompt"] == "q1"


def test_chat_audits_executed_tool_calls(client: TestClient) -> None:
    """D-062: executed tool calls land in agents.tool_call; the executor's
    audit writes land in system.audit_log."""
    calls = '{"__tool_calls__": [{"name": "list_hypotheses", "arguments": {}}]}'
    client.post(
        "/v1/ai/chat",
        json={"mode": "Research", "messages": [{"role": "user", "content": calls}]},
    )
    runs = client.get("/v1/agents/runs").json()["runs"]
    assert runs[0]["tool_calls"] == 1
    detail = client.get(f"/v1/agents/runs/{runs[0]['run_id']}").json()
    assert detail["tool_calls"][0]["tool"] == "list_hypotheses"
    assert detail["tool_calls"][0]["result_ref"] != ""


def test_chat_refuses_a_tool_the_mode_may_not_use(client: TestClient) -> None:
    """Data mode has no create tools; the loop's mode gate fires (422)."""
    calls = '{"__tool_calls__": [{"name": "create_note", "arguments": {}}]}'
    response = client.post(
        "/v1/ai/chat",
        json={"mode": "Data", "messages": [{"role": "user", "content": calls}]},
    )
    assert response.status_code == 422


def test_get_run_404_for_an_unknown_id(client: TestClient) -> None:
    response = client.get("/v1/agents/runs/11111111-2222-3333-4444-555555555555")
    assert response.status_code == 404


def test_runs_endpoint_respects_the_limit(client: TestClient) -> None:
    for index in range(3):
        client.post(
            "/v1/ai/chat",
            json={
                "mode": "Research",
                "messages": [{"role": "user", "content": f"prompt {index}"}],
            },
        )
    response = client.get("/v1/agents/runs", params={"limit": 2})
    assert len(response.json()["runs"]) == 2


def test_a_tool_result_carries_a_ref(client: TestClient) -> None:
    """D-066: every tool result carries a deterministic ref_id; the
    dataset_version is the REAL one the result read (or None when the
    result is not dataset-scoped) — never a generic "1" (review P1 #2)."""
    with connection_scope() as conn:
        executor = _tool_executor(conn)
        result: dict[str, Any] = executor("list_hypotheses", {})
        assert result["ref_id"] != ""
        # a registry read is not dataset-scoped: dataset_version is None,
        # not a fabricated "1"
        assert result["dataset_version"] is None
