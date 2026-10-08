"""PD-13: ``research.stats_result`` is append-only — the database, not the app, enforces it.

Every case runs inside a transaction that is rolled back, so the suite leaves no rows
behind (DELETE is rejected by design). Each rejected statement is wrapped in a
savepoint so one rejection does not poison the rest of the transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from apps.api.errors import ErrorCode
from apps.api.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
IMMUTABLE = ErrorCode.IMMUTABLE_RESULT.value

INSERT_SQL = """
INSERT INTO research.stats_result (
    ref_id, "filter", data_scope, dataset_version, dataset_schema_version,
    engine_version, code_version, resolved_params, result, seed, supersedes, locked
) VALUES (
    :ref, '{}'::jsonb, 'DEVELOPMENT', 'dv-m0', '1',
    '0.1.0', 'code-m0', '{}'::jsonb, '{"n": 1}'::jsonb, 7, :supersedes, false
)
"""


def _upgrade() -> None:
    cfg = AlembicConfig(str(REPO_ROOT / "infra" / "migrations" / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


def _head() -> str:
    """Current head revision from the scripts directory (never a pinned literal)."""
    cfg = AlembicConfig(str(REPO_ROOT / "infra" / "migrations" / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    return str(ScriptDirectory.from_config(cfg).get_current_head())


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    """Apply migrations, then hand out one engine for the module."""
    _upgrade()
    eng = create_engine(get_settings().database_url, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def tx(engine: Engine) -> Iterator[Connection]:
    """A transaction every test rolls back: the suite writes nothing that stays."""
    conn = engine.connect()
    conn.begin()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _insert(conn: Connection, ref: uuid.UUID, supersedes: uuid.UUID | None = None) -> None:
    conn.execute(text(INSERT_SQL), {"ref": ref, "supersedes": supersedes})


def _expect_rejected(conn: Connection, sql: str, params: dict[str, object]) -> str:
    conn.execute(text("SAVEPOINT imm_case"))
    with pytest.raises(DBAPIError) as exc:
        conn.execute(text(sql), params)
    conn.execute(text("ROLLBACK TO SAVEPOINT imm_case"))
    message = str(exc.value)
    assert IMMUTABLE in message, f"expected {IMMUTABLE}, got: {message}"
    return message


def test_migration_created_the_table(tx: Connection) -> None:
    revision = tx.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == _head()
    table = tx.execute(
        text(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema = 'research' AND table_name = 'stats_result'"
        )
    ).first()
    assert table is not None


def test_a_result_can_be_inserted(tx: Connection) -> None:
    ref = uuid.uuid4()
    _insert(tx, ref)
    stored = tx.execute(
        text("SELECT ref_id, locked, data_scope FROM research.stats_result WHERE ref_id = :ref"),
        {"ref": ref},
    ).one()
    assert stored.ref_id == ref
    assert stored.locked is False
    assert stored.data_scope == "DEVELOPMENT"


def test_identity_columns_cannot_change(tx: Connection) -> None:
    ref = uuid.uuid4()
    _insert(tx, ref)
    for column, value in (
        ("dataset_version", "'dv-other'"),
        ("engine_version", "'9.9.9'"),
        ("code_version", "'other'"),
        # jsonb literals with a colon would be read as bind parameters; both
        # values must also differ from what the INSERT stored.
        ("resolved_params", "jsonb_build_object('changed', true)"),
        ("result", "jsonb_build_object('n', 2)"),
        ("seed", "99"),
    ):
        message = _expect_rejected(
            tx,
            f"UPDATE research.stats_result SET {column} = {value} WHERE ref_id = :ref",
            {"ref": ref},
        )
        assert IMMUTABLE in message


def test_only_the_locked_flag_may_change(tx: Connection) -> None:
    ref = uuid.uuid4()
    _insert(tx, ref)
    tx.execute(
        text("UPDATE research.stats_result SET locked = true WHERE ref_id = :ref"),
        {"ref": ref},
    )
    row = tx.execute(
        text("SELECT locked FROM research.stats_result WHERE ref_id = :ref"), {"ref": ref}
    ).one()
    assert row.locked is True


def test_a_locked_row_rejects_every_update(tx: Connection) -> None:
    ref = uuid.uuid4()
    _insert(tx, ref)
    tx.execute(
        text("UPDATE research.stats_result SET locked = true WHERE ref_id = :ref"),
        {"ref": ref},
    )
    _expect_rejected(
        tx,
        "UPDATE research.stats_result SET locked = false WHERE ref_id = :ref",
        {"ref": ref},
    )


def test_delete_is_always_rejected(tx: Connection) -> None:
    ref = uuid.uuid4()
    _insert(tx, ref)
    message = _expect_rejected(
        tx,
        "DELETE FROM research.stats_result WHERE ref_id = :ref",
        {"ref": ref},
    )
    assert IMMUTABLE in message
    still_there = tx.execute(
        text("SELECT count(*) FROM research.stats_result WHERE ref_id = :ref"), {"ref": ref}
    ).scalar_one()
    assert still_there == 1


def test_recalculation_appends_with_supersedes(tx: Connection) -> None:
    first = uuid.uuid4()
    second = uuid.uuid4()
    _insert(tx, first)
    _insert(tx, second, supersedes=first)
    rows = tx.execute(
        text(
            "SELECT ref_id, supersedes FROM research.stats_result "
            "WHERE ref_id IN (:first, :second)"
        ),
        {"first": first, "second": second},
    ).all()
    assert len(rows) == 2
    by_ref = {row.ref_id: row.supersedes for row in rows}
    assert by_ref[second] == first
    assert by_ref[first] is None
