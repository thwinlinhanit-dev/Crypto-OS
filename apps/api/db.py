"""SQLAlchemy engine/session wiring for PostgreSQL (spec section 3, R3)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from apps.api.settings import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
    return _engine


def _factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session; commits on success, rolls back on error."""
    session = _factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def connection_scope() -> Iterator[Connection]:
    """Raw connection for engine-level calls; commits on success, rolls back on error.

    ``session_scope`` is for ORM sessions; the M1 data endpoints and the job
    runner drive ``engine.*`` functions that take a ``Connection`` directly.
    """
    with get_engine().connect() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def database_reachable() -> bool:
    """Cheap liveness probe for GET /v1/status; never raises."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def reset_engine() -> None:
    """Drop cached connections (tests that change DATABASE_URL)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
