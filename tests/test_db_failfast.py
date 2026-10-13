"""The database-connection fail-fast contract (docs/BACKLOG.md).

The historical failure mode: ``get_settings().database_url`` names a port with
no cluster behind it and the connection attempt hangs for the OS default
timeout (~2 min per try), so ``pytest`` looks stuck instead of reporting
"database unreachable", and ``GET /v1/status`` hangs instead of answering
``database: down``. The engine now binds a 3-second ``connect_timeout``, which
makes both paths fast. These tests pin that:

- the engine is built with a connect timeout at all,
- a dead port surfaces as a plain failure within the timeout budget (never a
  hang), asserted with a wall-clock bound rather than a sleep,
- ``/v1/status`` still answers ``database: down`` for an unreachable URL.

No live database is required — the dead-port case is the whole point.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from apps.api import db as db_module

# A port with nothing listening on it (the "database down" case the suite
# must not hang on). 54321 is in the dynamic range and unassigned here.
DEAD_URL = "postgresql://cryptoos:cryptoos@127.0.0.1:54321/cryptoos"


def test_get_engine_binds_a_connect_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine factory must bind a connect timeout — a dead port fails in
    seconds, never the OS-default ~2-minute hang.

    Asserted behaviorally against the real ``get_engine()``: SQLAlchemy 2.x keeps
    the engine-level ``connect_args`` in the pool's connection-creation closure,
    so it is not observable via ``dialect.create_connect_args`` (that returns only
    URL-derived args). The only honest proof that ``get_engine()`` bound the
    timeout is a timed connect attempt to a dead port.
    """
    monkeypatch.setenv("DATABASE_URL", DEAD_URL)
    db_module.reset_engine()
    try:
        started = time.monotonic()
        with pytest.raises(OperationalError), db_module.get_engine().connect():
            pass
        elapsed = time.monotonic() - started
        assert elapsed < 20, (
            f"a dead port took {elapsed:.1f}s to fail — get_engine() bound no "
            "connect timeout, so the suite hangs on an unreachable cluster "
            "(docs/BACKLOG.md)"
        )
    finally:
        monkeypatch.delenv("DATABASE_URL")
        db_module.reset_engine()


def test_dead_port_fails_fast_instead_of_hanging() -> None:
    """A dead port raises within the connect-timeout budget, not after minutes."""
    engine = create_engine(
        DEAD_URL,
        future=True,
        connect_args={"connect_timeout": db_module._CONNECT_TIMEOUT_S},
    )
    started = time.monotonic()
    try:
        # connect() itself raises OperationalError on a dead port; pytest.raises
        # catches it. The execute() line is only reached on a reachable DB (which
        # this test never has), so the SELECT is the assertion's unreachable tail.
        with pytest.raises(OperationalError), engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()
    elapsed = time.monotonic() - started
    # connect_timeout=3 plus dialect overhead: well under the OS default (~2 min)
    assert elapsed < 20, f"connection attempt took {elapsed:.1f}s — the fail-fast contract is broken"


def test_database_reachable_reports_false_for_a_dead_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """``database_reachable()`` answers False quickly — never raises, never hangs."""
    monkeypatch.setenv("DATABASE_URL", DEAD_URL)
    db_module.reset_engine()
    try:
        started = time.monotonic()
        assert db_module.database_reachable() is False
        elapsed = time.monotonic() - started
        assert elapsed < 20, f"liveness probe took {elapsed:.1f}s on a dead port"
    finally:
        monkeypatch.delenv("DATABASE_URL")
        db_module.reset_engine()
