"""M11 — the backup/export tests (spec M11 T11.4, design note m11_hardening
section 3, D-078/D-079).

The bundle shape, the import validation (fail-closed), the idempotency
(ON CONFLICT DO NOTHING) and the frozen contract (a frozen experiment
round-trips frozen, D-021).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Connection, create_engine, text

from apps.api.settings import get_settings
from engine.backup import (
    BACKUP_SCHEMA_VERSION,
    BackupValidationError,
    export_backup,
    import_backup,
    validate_bundle,
)


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


@pytest.fixture(autouse=True)
def clean(conn: Connection) -> Iterator[None]:
    # The M5b immutability trigger rejects DELETE/UPDATE on frozen
    # experiments (D-021); session_replication_role=replica disables the
    # triggers for the wipe only (the standard test-isolation pattern)
    conn.execute(text("SET session_replication_role = replica"))
    for table in (
        "research.note", "research.hypothesis", "research.experiment",
        "research.experiment_family",
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
            "research.experiment_family",
        ):
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("SET session_replication_role = DEFAULT"))
        conn.commit()


def _seed(conn: Connection) -> dict[str, str]:
    """A notebook note + a hypothesis + a family + a frozen experiment."""
    note_id = str(uuid4())
    hyp_id = str(uuid4())
    family_id = str(uuid4())
    exp_id = str(uuid4())
    conn.execute(
        text(
            "INSERT INTO research.note (note_id, title, body_md, created_at)"
            " VALUES (:id, :title, :body, now())"
        ),
        {"id": note_id, "title": "First note", "body": "BTC capitulated."},
    )
    conn.execute(
        text(
            "INSERT INTO research.hypothesis (hyp_id, title, statement, status,"
            " criteria, preregistered_at, locked)"
            " VALUES (:id, :title, :stmt, 'TESTING', :criteria, now(), false)"
        ),
        {
            "id": hyp_id,
            "title": "CONT events predict drift",
            "stmt": "CONT events precede +1R moves",
            "criteria": '{"pass": ["n_eff >= 10"], "kill": ["n_eff < 5"]}',
        },
    )
    conn.execute(
        text(
            "INSERT INTO research.experiment_family (family_id, hypothesis_ids,"
            " description, search_space, hypothesis_count, variant_count, created_at)"
            " VALUES (:id, :hyp, :desc, :space, 1, 1, now())"
        ),
        {
            "id": family_id,
            "hyp": json.dumps([hyp_id]),
            "desc": "cooldown sweep",
            "space": '{"cooldown": [0, 1, 2]}',
        },
    )
    conn.execute(
        text(
            "INSERT INTO research.experiment (experiment_id, hypothesis_id, family_id,"
            " title, status, conclusion, assessment, universe, timeframe, data_scope,"
            " dataset_version, dataset_schema_version, engine_version, code_version,"
            " detector_refs, resolved_params, selection_method, seed, refs, frozen,"
            " frozen_at, created_at)"
            " VALUES (:id, :hyp, :family, :title, 'VALIDATED', :conclusion, :assessment,"
            " :universe, '1D', 'DEVELOPMENT', 'dv', '1', '0.1.0', 'dev', '[]', '{}',"
            " 'PRE_SPECIFIED', 0, '{}', true, now(), now())"
        ),
        {
            "id": exp_id,
            "hyp": hyp_id,
            "family": family_id,
            "title": "cooldown sweep",
            "conclusion": "cooldown=1 wins",
            "assessment": '{"grade": "A", "n_eff": 23}',
            "universe": '["BTCUSDT"]',
        },
    )
    conn.commit()
    return {"note_id": note_id, "hypothesis_id": hyp_id, "experiment_id": exp_id}


def test_export_bundle_shape(conn: Connection) -> None:
    _seed(conn)
    bundle = export_backup(conn)
    assert bundle["schema_version"] == BACKUP_SCHEMA_VERSION
    assert "created_at" in bundle
    assert isinstance(bundle["notebook"], list)
    assert isinstance(bundle["registry"], dict)
    assert isinstance(bundle["lineage"], dict)
    experiment = bundle["registry"]["experiments"][0]
    assert experiment["frozen"] is True
    assert experiment["frozen_at"] is not None


def test_export_carries_the_notebook_and_hypotheses(conn: Connection) -> None:
    _seed(conn)
    bundle = export_backup(conn)
    assert bundle["notebook"][0]["title"] == "First note"
    assert bundle["registry"]["hypotheses"][0]["title"] == "CONT events predict drift"


def test_validate_rejects_a_bad_schema_version() -> None:
    with pytest.raises(BackupValidationError):
        validate_bundle({"schema_version": "999", "notebook": [],
                         "registry": {"hypotheses": [], "experiment_families": [],
                                      "experiments": []}, "lineage": {}})


def test_validate_rejects_a_missing_section() -> None:
    with pytest.raises(BackupValidationError):
        validate_bundle({"schema_version": BACKUP_SCHEMA_VERSION, "registry": {}})


def test_validate_rejects_a_non_object() -> None:
    with pytest.raises(BackupValidationError):
        validate_bundle("not a bundle")


def test_import_is_idempotent(conn: Connection) -> None:
    """D-079: re-importing the same bundle inserts nothing."""
    _seed(conn)
    bundle = export_backup(conn)
    first = import_backup(conn, bundle)
    second = import_backup(conn, bundle)
    assert first["notes"] == 0  # the rows already exist
    assert first["experiments"] == 0
    assert second["notes"] == 0
    assert second["experiments"] == 0


def test_import_into_a_clean_db_inserts(conn: Connection) -> None:
    seeded = _seed(conn)
    bundle = export_backup(conn)
    conn.execute(text("SET session_replication_role = replica"))
    conn.execute(text("DELETE FROM research.experiment"))
    conn.execute(text("DELETE FROM research.hypothesis"))
    conn.execute(text("DELETE FROM research.note"))
    conn.execute(text("SET session_replication_role = DEFAULT"))
    conn.commit()
    inserted = import_backup(conn, bundle)
    assert inserted["notes"] == 1
    assert inserted["hypotheses"] == 1
    assert inserted["experiments"] == 1
    row = conn.execute(
        text("SELECT frozen, frozen_at FROM research.experiment WHERE experiment_id = :id"),
        {"id": seeded["experiment_id"]},
    ).first()
    assert row is not None
    assert row.frozen is True  # the frozen contract survives (D-021/D-079)
    assert row.frozen_at is not None


def test_import_rejects_a_bad_bundle(conn: Connection) -> None:
    with pytest.raises(BackupValidationError):
        import_backup(conn, {"schema_version": "bad"})
