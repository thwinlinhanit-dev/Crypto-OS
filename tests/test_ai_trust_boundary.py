"""M9 — the AI trust-boundary tests (review Phase 1: "a model must not be
able to claim an operation succeeded when it did not run").

The three confirmed P1 defects and their fixes:
1. placeholder-shaped success results for unbound tools -> explicit
   NOT_IMPLEMENTED (never ok:true for a computation that did not run);
2. process-randomized ``hash()`` ref_ids -> deterministic sha256, stable
   across processes;
3. stub schemas -> real JSON Schemas for every tool.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text

from apps.api.db import connection_scope
from apps.api.main import create_app
from apps.api.settings import get_settings
from engine.ai.handlers import canonical_ref_id


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
        "research.note", "research.hypothesis", "research.experiment",
        "market.instrument", "system.audit_log", "agents.tool_call",
        "agents.run",
    ):
        conn.execute(text(f"DELETE FROM {table}"))
    conn.execute(text("SET session_replication_role = DEFAULT"))
    conn.commit()
    try:
        yield
    finally:
        conn.execute(text("SET session_replication_role = replica"))
        for table in (
            "research.note", "research.hypothesis", "research.experiment",
            "market.instrument", "system.audit_log", "agents.tool_call",
            "agents.run",
        ):
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("SET session_replication_role = DEFAULT"))
        conn.commit()


# --------------------------------------------------------------------------
# The deterministic ref_id (P1 #2)
# --------------------------------------------------------------------------


def test_canonical_ref_id_is_stable_across_calls() -> None:
    payload = {"tool": "get_experiment", "experiment_id": "abc"}
    first = canonical_ref_id(payload)
    second = canonical_ref_id(dict(payload))
    assert first == second


def test_canonical_ref_id_is_not_python_hash() -> None:
    """The ref_id must not be Python's ``hash()`` (process-randomized); it is
    a hex sha256 prefix."""
    payload = {"tool": "list_hypotheses", "ok": True}
    ref = canonical_ref_id(payload)
    assert len(ref) == 24
    int(ref, 16)  # it parses as hex — a sha256 digest, not hash()
    # a different payload -> a different id
    assert canonical_ref_id({"tool": "other"}) != ref


def test_canonical_ref_id_is_key_order_independent() -> None:
    """Canonical JSON sorts keys, so the same payload in any order yields the
    same id (the reproducibility contract)."""
    a = canonical_ref_id({"b": 2, "a": 1})
    b = canonical_ref_id({"a": 1, "b": 2})
    assert a == b


# --------------------------------------------------------------------------
# The NOT_IMPLEMENTED boundary (P1 #1)
# --------------------------------------------------------------------------


def test_an_unbound_tool_returns_not_implemented(client: TestClient) -> None:
    """run_backtest has no data-backed handler: the executor MUST NOT return
    ok:true (the trust boundary)."""
    calls = '{"__tool_calls__": [{"name": "run_backtest", "arguments": {}}]}'
    response = client.post(
        "/v1/ai/chat",
        json={"mode": "Research", "messages": [{"role": "user", "content": calls}]},
    )
    assert response.status_code == 200, response.text
    body = response.text
    assert '"NOT_IMPLEMENTED"' in body


def test_a_bound_tool_still_succeeds(client: TestClient) -> None:
    """list_hypotheses has a real handler: it succeeds (the happy path)."""
    calls = '{"__tool_calls__": [{"name": "list_hypotheses", "arguments": {}}]}'
    response = client.post(
        "/v1/ai/chat",
        json={"mode": "Research", "messages": [{"role": "user", "content": calls}]},
    )
    assert response.status_code == 200, response.text
    assert '"NOT_IMPLEMENTED"' not in response.text


def test_search_instruments_returns_the_real_dataset_shape(client: TestClient) -> None:
    """A bound read tool runs its real query: the audit log records the call
    (the evidence the computation actually ran)."""
    from engine.data.instruments import upsert_instruments
    from tests.support import BTC

    with connection_scope() as conn:
        upsert_instruments(conn, [BTC])
        conn.commit()
    calls = (
        '{"__tool_calls__": [{"name": "search_instruments", '
        '"arguments": {"query": "BTC"}}]}'
    )
    response = client.post(
        "/v1/ai/chat",
        json={"mode": "Research", "messages": [{"role": "user", "content": calls}]},
    )
    assert response.status_code == 200, response.text
    # the tool executed (audited) and returned ok — no NOT_IMPLEMENTED
    assert '"NOT_IMPLEMENTED"' not in response.text
    with connection_scope() as conn:
        row = conn.execute(
            text(
                "SELECT detail FROM system.audit_log"
                " WHERE action = 'ai.tool_call' AND target = 'search_instruments'"
                " ORDER BY id DESC LIMIT 1"
            )
        ).first()
    assert row is not None, "the bound tool was audited (it really ran)"
    detail = row.detail  # JSONB: psycopg returns the dict directly
    assert detail["tool"] == "search_instruments"
    assert detail["ok"] is True


# --------------------------------------------------------------------------
# The real schemas (P1 #3)
# --------------------------------------------------------------------------


def test_every_tool_has_a_real_schema(client: TestClient) -> None:
    """Every advertised tool carries argument properties — not the stub
    ``{"type": "object", "title": name}``."""
    response = client.get("/v1/ai/tools")
    assert response.status_code == 200
    for tool in response.json()["tools"]:
        schema = tool["schema"]
        assert schema.get("type") == "object"
        assert "properties" in schema, f"{tool['name']} has no properties"
        assert schema["properties"], f"{tool['name']} has empty properties"


def test_the_schemas_cover_all_21_tools(client: TestClient) -> None:
    response = client.get("/v1/ai/tools")
    assert len(response.json()["tools"]) == 21


def test_no_order_related_tool_exists(client: TestClient) -> None:
    """The AC gate holds through the API surface."""
    response = client.get("/v1/ai/tools")
    names = [t["name"] for t in response.json()["tools"]]
    assert not any("order" in name for name in names)
