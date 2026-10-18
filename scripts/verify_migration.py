"""One-off verification: compare migrated SQLite schema to model metadata.

Usage: PYTHONPATH=backend python scripts/verify_migration.py <db_path>
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db import Base  # noqa: E402
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


def main() -> int:
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/migration_check.db"
    engine = create_engine(f"sqlite:///{db_path}")
    migrated = set(inspect(engine).get_table_names())
    expected = set(Base.metadata.tables.keys())
    missing = sorted(expected - migrated)
    extra = sorted(migrated - expected - {"alembic_version"})
    print(f"expected tables: {len(expected)}")
    print(f"migrated tables: {len(migrated)}")
    if missing:
        print("MISSING from migration:", missing)
    if extra:
        print("EXTRA in migration:", extra)
    if not missing and not extra:
        print("OK: migration schema matches model metadata")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
