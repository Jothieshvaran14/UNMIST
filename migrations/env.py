"""Alembic environment for Unmist.

This wires Alembic to the app's SQLAlchemy ``Base`` metadata and the
configured ``DATABASE_URL`` (SQLite for dev/tests, PostgreSQL for prod).
``render_as_batch`` is enabled so SQLite (which lacks ALTER support for many
operations) can still apply migrations.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure the backend package is importable when running `alembic` from the repo root.
BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db import Base  # noqa: E402
from app.config import get_settings  # noqa: E402

# Import every model module so that all tables are registered on Base.metadata.
from app.models import (  # noqa: E402,F401
    audit,
    documents,
    job,
    knowledge,
    learning,
    org,
    portfolio,
    retention,
    user,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    # Prefer an explicit DATABASE_URL (env or settings) over alembic.ini.
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DB connection)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (apply against a live connection)."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
