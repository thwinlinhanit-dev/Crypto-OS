"""M5b (T5b.1-T5b.2) - experiment families, variants, experiments (spec 8.4/8.5).

Acceptance evidence produced here (spec M5b AC):

- "frozen experiment and all referenced results reject UPDATE/DELETE" -
  ``test_freeze_locks_referenced_results``, ``test_frozen_experiment_rejects_mutation``;
- "verify detects tampering" - ``test_verify_intact`` / ``test_verify_detects_tampering``
  (the tamper is applied with the DB triggers bypassed: the hash layer is the
  backstop for what the trigger layer cannot stop);
- "every stored result resolves to its dataset manifest" - creation requires a
  registered manifest and lineage links to it
  (``test_create_experiment_requires_registered_dataset``, ``test_lineage_links``);
- "lineage nodes all link" - ``test_lineage_links``;
- "singleton family auto-created for ad-hoc runs" - every POST /v1/stats run
  records a family + variant (see tests/test_api_stats.py and
  ``test_ad_hoc_run_creates_singleton_family``).

Also covered: family CRUD incl. delete-while-referenced 409, variant
append-only (only ``selected`` may change), experiment 422s, PATCH rules and
the multiple-testing block growing with the family.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DBAPIError

from apps.api.main import create_app
from apps.api.settings import get_settings
from engine.data.bars import Timeframe
from engine.data.instruments import upsert_instruments
from exchanges.base import Kline
from tests.support import BTC, DAY, T0, WIDE_BUDGET, FixtureAdapter, reset_research
from tests.test_api_stats import SMALL_PARAMS

DEV_START = T0


def _bars() -> list[Kline]:
    rows: list[Kline] = []
    for i in range(60):
        open_ms = DEV_START + i * DAY
        open_ = 100.0
        if i in {8, 28, 48}:
            rows.append(
                Kline(
                    open_time=open_ms,
                    close_time=open_ms + DAY,
                    open=open_,
                    high=open_ + 1.0,
                    low=open_ - 12.0,
                    close=open_ - 11.0,
                    volume=10_000.0,
                    quote_volume=open_ * 10_000.0,
                    trades=5_000,
                )
            )
        else:
            rows.append(
                Kline(
                    open_time=open_ms,
                    close_time=open_ms + DAY,
                    open=open_,
                    high=open_ + 5.0,
                    low=open_ - 5.0,
                    close=open_ + 1.0,
                    volume=1_000.0,
                    quote_volume=open_ * 1_000.0,
                    trades=500,
                )
            )
    return rows


RESEARCH_TABLES = (
    "research.event, research.event_outcome, research.stats_result,"
    " research.hypothesis, research.note, research.variant, research.experiment,"
    " research.experiment_family"
)


def _wipe(conn: Connection) -> None:
    for statement in (
        "DELETE FROM system.job",
        "DELETE FROM system.gap",
        "DELETE FROM system.data_quality",
        "DELETE FROM market.dataset_manifest",
        "DELETE FROM market.instrument",
    ):
        conn.execute(text(statement))
    reset_research(conn)


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "data"
    directory.mkdir(parents=True)
    monkeypatch.setenv("DATA_DIR", str(directory))
    return directory


@pytest.fixture()
def venue(monkeypatch: pytest.MonkeyPatch) -> FixtureAdapter:
    adapter = FixtureAdapter({("BTCUSDT", Timeframe.D1): _bars()})
    monkeypatch.setattr("apps.api.jobs.build_adapter", lambda params: adapter)
    return adapter


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


@pytest.fixture()
def seeded(
    client: TestClient, conn: Connection, venue: FixtureAdapter, data_dir: Path
) -> str:
    upsert_instruments(conn, [BTC])
    conn.commit()
    response = client.post(
        "/v1/data/ingest",
        json={
            "venue": "BINANCE",
            "market_type": "SPOT",
            "instrument_ids": [str(BTC.instrument_id)],
            "timeframes": ["1D"],
            "from": "2024-01-01T00:00:00Z",
            "to": "2024-03-01T00:00:00Z",
            "storage_budget_gb": WIDE_BUDGET / 1024**3,
        },
    )
    assert response.status_code == 202, response.text
    view = client.get(f"/v1/jobs/{response.json()['job_id']}").json()
    assert view["status"] == "succeeded", view.get("error")
    return str(view["result_ref"])


def _hypothesis(client: TestClient) -> str:
    response = client.post(
        "/v1/hypotheses",
        json={
            "title": "t",
            "statement": "s",
            "criteria": {"pass": ["grade B or better"], "kill": ["grade D twice"]},
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["hyp_id"])


def _family(client: TestClient, hyp_id: str | None = None, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"description": "sweep over climax_mult"}
    if hyp_id is not None:
        body["hypothesis_ids"] = [hyp_id]
    body.update(over)
    response = client.post("/v1/families", json=body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def _experiment(client: TestClient, **over: Any) -> Any:
    body: dict[str, Any] = {
        "hypothesis_id": _hypothesis(client),
        "title": "e1",
        "universe": [],
        "timeframe": "1D",
        "data_scope": "DEVELOPMENT",
        "dataset_version": "dv",
        "detector_refs": {"detector_id": "RVOL_CLIMAX", "version": "5.0"},
        "resolved_params": {"climax_mult": 3.0},
        "selection_method": "PRE_SPECIFIED",
    }
    body.update(over)
    return client.post("/v1/experiments", json=body)


def _ingest_dataset_version(client: TestClient, seeded: str) -> str:
    return seeded


# --------------------------------------------------------------------------
# Families
# --------------------------------------------------------------------------


def test_family_crud(client: TestClient, conn: Connection) -> None:
    hyp_id = _hypothesis(client)
    created = _family(client, hyp_id, search_space={"climax_mult": [2.0, 3.0]})
    assert created["description"] == "sweep over climax_mult"
    assert [str(h) for h in created["hypothesis_ids"]] == [hyp_id]
    assert created["search_space"] == {"climax_mult": [2.0, 3.0]}
    assert created["variant_count"] == 0

    listed = client.get("/v1/families").json()
    assert [f["family_id"] for f in listed] == [created["family_id"]]
    assert client.get(f"/v1/families/{created['family_id']}").json() == created

    patched = client.patch(
        f"/v1/families/{created['family_id']}", json={"description": "wider sweep"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["description"] == "wider sweep"

    deleted = client.delete(f"/v1/families/{created['family_id']}")
    assert deleted.status_code == 204
    assert client.get(f"/v1/families/{created['family_id']}").status_code == 404
    assert client.delete(f"/v1/families/{created['family_id']}").status_code == 404


def test_family_delete_rejected_once_referenced(client: TestClient, conn: Connection) -> None:
    hyp_id = _hypothesis(client)
    family = _family(client, hyp_id)
    response = _experiment(
        client, universe=[], family_id=family["family_id"], dataset_version="dv"
    )
    assert response.status_code == 422  # dataset must be a registered manifest
    conn.execute(
        text(
            "INSERT INTO research.variant (variant_id, family_id, params_hash, result_ref)"
            " VALUES (gen_random_uuid(), :f, 'ph', gen_random_uuid())"
        ),
        {"f": family["family_id"]},
    )
    conn.commit()
    refused = client.delete(f"/v1/families/{family['family_id']}")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "IMMUTABLE_RESULT"


def test_family_unknown_hypothesis_404(client: TestClient) -> None:
    response = client.post(
        "/v1/families",
        json={"description": "d", "hypothesis_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------
# Variants
# --------------------------------------------------------------------------


def test_variant_is_append_only_except_selection(client: TestClient, conn: Connection) -> None:
    hyp_id = _hypothesis(client)
    family = _family(client, hyp_id)
    result_ref = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO research.variant (variant_id, family_id, params_hash,"
            " resolved_params, result_ref) VALUES (gen_random_uuid(), :f, 'ph',"
            " '{}'::jsonb, :ref)"
        ),
        {"f": family["family_id"], "ref": result_ref},
    )
    conn.commit()
    variants = client.get(f"/v1/families/{family['family_id']}/variants").json()
    assert len(variants) == 1
    variant_id = variants[0]["variant_id"]
    conn.execute(
        text("UPDATE research.variant SET selected = true WHERE variant_id = :v"),
        {"v": variant_id},
    )
    conn.commit()
    assert client.get(f"/v1/families/{family['family_id']}/variants").json()[0]["selected"] is True
    try:
        conn.execute(
            text("UPDATE research.variant SET result_ref = gen_random_uuid() WHERE variant_id = :v"),
            {"v": variant_id},
        )
        conn.commit()
        raise AssertionError("variant rewrite must be rejected")
    except DBAPIError as exc:
        conn.rollback()
        assert "IMMUTABLE_RESULT" in str(exc)


# --------------------------------------------------------------------------
# Experiments
# --------------------------------------------------------------------------


def test_create_experiment_requires_registered_dataset(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    hyp_id = _hypothesis(client)
    bad = _experiment(client, universe=[str(BTC.instrument_id)], dataset_version="not-a-manifest")
    assert bad.status_code == 422
    ok = _experiment(
        client,
        universe=[str(BTC.instrument_id)],
        dataset_version=seeded,
        hypothesis_id=hyp_id,
    )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["status"] == "RUNNING"
    assert body["frozen"] is False
    assert [str(i) for i in body["universe"]] == [str(BTC.instrument_id)]
    assert str(body["family_id"])  # a singleton family was auto-created
    family = client.get(f"/v1/families/{body['family_id']}").json()
    assert family["variant_count"] == 0
    listed = client.get("/v1/experiments").json()
    assert [e["experiment_id"] for e in listed] == [body["experiment_id"]]
    assert client.get(f"/v1/experiments/{body['experiment_id']}").json() == body


def test_create_experiment_422s(client: TestClient, conn: Connection, seeded: str) -> None:
    cases: dict[str, dict[str, Any]] = {
        "empty universe": {"universe": []},
        "unknown hypothesis": {"hypothesis_id": str(uuid.uuid4())},
        "unknown family": {"family_id": str(uuid.uuid4())},
        "unregistered dataset": {"dataset_version": "not-a-manifest"},
        "bad scope": {"data_scope": "SOMEWHERE"},
        "bad selection": {"selection_method": "VIBES"},
        "empty title": {"title": "  "},
    }
    for name, over in cases.items():
        response = _experiment(client, **over)
        assert response.status_code == 422, name
        assert response.json()["error"]["code"] == "VALIDATION_ERROR", name
    unknown_h = client.get(f"/v1/experiments/{uuid.uuid4()}")
    assert unknown_h.status_code == 404


def test_experiment_patch_rules(client: TestClient, conn: Connection, seeded: str) -> None:
    created = _experiment(client, universe=[str(BTC.instrument_id)], dataset_version=seeded).json()
    unknown_status = client.patch(
        f"/v1/experiments/{created['experiment_id']}", json={"status": "VIBES"}
    )
    assert unknown_status.status_code == 422
    rejected_no_conclusion = client.patch(
        f"/v1/experiments/{created['experiment_id']}", json={"status": "REJECTED"}
    )
    assert rejected_no_conclusion.status_code == 422
    patched = client.patch(
        f"/v1/experiments/{created['experiment_id']}",
        json={"status": "SUPPORTED", "conclusion": "OOS holds", "assessment": {"pf": 1.4}},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "SUPPORTED"


# --------------------------------------------------------------------------
# Freeze / verify / lineage (spec 8.5)
# --------------------------------------------------------------------------


def _stats_ref(client: TestClient, seeded: str) -> str:
    response = client.post(
        "/v1/stats",
        json={
            "filter": {
                "detector": "RVOL_CLIMAX",
                "timeframe": "1D",
                "params": json.dumps(SMALL_PARAMS),
            },
            "instrument_ids": [str(BTC.instrument_id)],
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["ref_id"])


def _frozen(client: TestClient, seeded: str, conn: Connection) -> tuple[str, str]:
    created = _experiment(
        client,
        universe=[str(BTC.instrument_id)],
        dataset_version=seeded,
        refs={"stats": [_stats_ref(client, seeded)]},
    ).json()
    frozen = client.post(f"/v1/experiments/{created['experiment_id']}/freeze")
    assert frozen.status_code == 200, frozen.text
    return str(created["experiment_id"]), frozen.json()["result_hashes"][
        str(_first_ref(frozen.json()["refs"]))
    ]


def _first_ref(refs: dict[str, Any]) -> str:
    return str(refs["stats"][0])


def test_freeze_locks_referenced_results(client: TestClient, conn: Connection, seeded: str) -> None:
    experiment_id, _hash = _frozen(client, seeded, conn)
    stored = conn.execute(
        text("SELECT refs FROM research.experiment WHERE experiment_id = :id"),
        {"id": experiment_id},
    ).scalar_one()
    target = uuid.UUID(str(stored["stats"][0]))
    try:
        conn.execute(
            text("UPDATE research.stats_result SET result = '{}'::jsonb WHERE ref_id = :ref"),
            {"ref": target},
        )
        conn.commit()
        raise AssertionError("locked stats_result must reject UPDATE")
    except DBAPIError as exc:
        conn.rollback()
        assert "IMMUTABLE_RESULT" in str(exc)
    hypothesis = conn.execute(
        text(
            "SELECT h.locked FROM research.experiment e JOIN research.hypothesis h"
            " ON h.hyp_id = e.hypothesis_id WHERE e.experiment_id = :id"
        ),
        {"id": experiment_id},
    ).scalar_one()
    assert hypothesis is True


def test_frozen_experiment_rejects_mutation(client: TestClient, conn: Connection, seeded: str) -> None:
    experiment_id, _hash = _frozen(client, seeded, conn)
    patched = client.patch(
        f"/v1/experiments/{experiment_id}", json={"status": "SUPPORTED"}
    )
    assert patched.status_code == 409
    assert patched.json()["error"]["code"] == "IMMUTABLE_RESULT"
    again = client.post(f"/v1/experiments/{experiment_id}/freeze")
    assert again.status_code == 409


def test_freeze_missing_result_422(client: TestClient, conn: Connection, seeded: str) -> None:
    created = _experiment(
        client,
        universe=[str(BTC.instrument_id)],
        dataset_version=seeded,
        refs={"stats": [str(uuid.uuid4())]},
    ).json()
    response = client.post(f"/v1/experiments/{created['experiment_id']}/freeze")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_verify_intact(client: TestClient, conn: Connection, seeded: str) -> None:
    experiment_id, _hash = _frozen(client, seeded, conn)
    verified = client.get(f"/v1/experiments/{experiment_id}/verify").json()
    assert verified["status"] == "INTACT"
    assert len(verified["results"]) == 1
    unfrozen = _experiment(
        client, universe=[str(BTC.instrument_id)], dataset_version=seeded
    ).json()
    early = client.get(f"/v1/experiments/{unfrozen['experiment_id']}/verify")
    assert early.status_code == 422


def test_verify_detects_tampering(client: TestClient, conn: Connection, seeded: str) -> None:
    """The DB triggers stop the normal path; this simulates a mutation that got
    past them (session_replication_role disables triggers) so the sha256 layer
    is what catches it - defense in depth, exactly what verify is for."""
    experiment_id, _hash = _frozen(client, seeded, conn)
    stored = conn.execute(
        text("SELECT refs FROM research.experiment WHERE experiment_id = :id"),
        {"id": experiment_id},
    ).scalar_one()
    target = uuid.UUID(str(stored["stats"][0]))
    bypass = create_engine(get_settings().database_url, future=True)
    with bypass.connect() as rogue:
        rogue.execute(text("SET session_replication_role = replica"))
        rogue.execute(
            text("UPDATE research.stats_result SET result = '{\"tampered\": true}'::jsonb"
                 " WHERE ref_id = :ref"),
            {"ref": target},
        )
        rogue.commit()
    bypass.dispose()
    verified = client.get(f"/v1/experiments/{experiment_id}/verify").json()
    assert verified["status"] == "TAMPERED"
    entry = verified["results"][str(target)]
    assert entry["status"] == "TAMPERED"
    assert entry["expected"] != entry["actual"]


def test_lineage_links(client: TestClient, conn: Connection, seeded: str) -> None:
    created = _experiment(
        client,
        universe=[str(BTC.instrument_id)],
        dataset_version=seeded,
        refs={"stats": [_stats_ref(client, seeded)]},
    ).json()
    lineage = client.get(f"/v1/experiments/{created['experiment_id']}/lineage").json()
    kinds = [node["kind"] for node in lineage["nodes"]]
    assert kinds == [
        "dataset",
        "features",
        "detector",
        "stats",
        "hypothesis",
        "experiment",
        "stats_result",
    ]
    dataset_node = lineage["nodes"][0]
    assert dataset_node["ref"] == f"/v1/datasets/{seeded}"
    for node in lineage["nodes"]:
        assert node["status"] == "OK", node
    assert lineage["not_implemented"] == ["strategy", "backtest", "validation", "conclusion"]
    unknown = client.get(f"/v1/experiments/{uuid.uuid4()}/lineage")
    assert unknown.status_code == 404


# --------------------------------------------------------------------------
# Multiple testing
# --------------------------------------------------------------------------


def test_family_variant_count_grows_across_runs(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    hyp_id = _hypothesis(client)
    family = _family(client, hyp_id)
    body: dict[str, Any] = {
        "filter": {
            "detector": "RVOL_CLIMAX",
            "timeframe": "1D",
            "params": json.dumps(SMALL_PARAMS),
        },
        "instrument_ids": [str(BTC.instrument_id)],
        "family_id": family["family_id"],
    }
    first = client.post("/v1/stats", json=body).json()
    second = client.post("/v1/stats", json=body).json()
    assert first["result"]["multiple_testing"]["variant_count"] == 1
    assert second["result"]["multiple_testing"]["variant_count"] == 2
    assert second["result"]["multiple_testing"]["family_id"] == family["family_id"]
    stored = client.get(f"/v1/families/{family['family_id']}").json()
    assert stored["variant_count"] == 2
    variants = client.get(f"/v1/families/{family['family_id']}/variants").json()
    assert [v["result_ref"] for v in variants] == [first["ref_id"], second["ref_id"]]
    unknown_family = client.post(
        "/v1/stats", json={**body, "family_id": str(uuid.uuid4())}
    )
    assert unknown_family.status_code == 404


def test_ad_hoc_run_creates_singleton_family(
    client: TestClient, conn: Connection, seeded: str
) -> None:
    ref_id = _stats_ref(client, seeded)
    recorded = conn.execute(
        text(
            "SELECT f.description, f.variant_count FROM research.variant v"
            " JOIN research.experiment_family f ON f.family_id = v.family_id"
            " WHERE v.result_ref = :ref"
        ),
        {"ref": ref_id},
    ).first()
    assert recorded is not None
    assert recorded.variant_count == 1
    assert "singleton" in recorded.description
