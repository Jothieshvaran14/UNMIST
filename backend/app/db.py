"""Database engine, session management, and schema utilities.

The system of record is PostgreSQL in production. SQLite is supported for
local development and tests so the project remains runnable without external
services. Foreign-key enforcement and CHECK constraints are applied in both.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import Settings, get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _sqlite_pragma(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def build_engine(settings: Settings, *, for_tests: bool = False) -> Engine:
    url = settings.test_database_url if for_tests else settings.database_url
    is_sqlite = url.startswith("sqlite")
    kwargs: dict[str, Any] = {"future": True}
    if is_sqlite:
        # Ensure the parent directory exists before SQLite tries to open the file.
        from pathlib import Path

        db_path = url.split("///", 1)[-1]
        if db_path and not db_path.startswith(":"):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # SQLite is single-writer; a small static pool avoids connection churn.
        kwargs["connect_args"] = {"check_same_thread": False}
        engine = create_engine(url, **kwargs)
        event.listen(engine, "connect", _sqlite_pragma)
    else:
        engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=10,
            pool_timeout=30,
            **kwargs,
        )
    return engine


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        _engine = build_engine(get_settings())
        _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped database session."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def reset_engine_for_tests() -> None:
    """Dispose the cached engine (used between test configurations)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
