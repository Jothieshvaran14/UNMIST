"""Verify the ocr_regions identity migration against a SQLite file.

Usage: python scripts/verify_region_uid.py <db-path>
"""

from __future__ import annotations

import sqlite3
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_region_uid.py <db-path>")
        return 2
    con = sqlite3.connect(sys.argv[1])
    cols = [row[1] for row in con.execute("PRAGMA table_info(ocr_regions)")]
    assert "region_uid" in cols, f"region_uid missing; cols={cols}"
    print("ocr_regions columns:", cols)

    # SQLite materializes a UNIQUE constraint as an unnamed sqlite_autoindex,
    # so identify the constraint by its columns rather than by name.
    found = False
    for row in con.execute("PRAGMA index_list(ocr_regions)"):
        # Column count varies across SQLite builds: (seq, name, unique, origin)
        # plus optional (partial). Pick by position-independent lookup.
        name = row[1]
        unique = row[2]
        info = [r[2] for r in con.execute(f"PRAGMA index_info({name})")]
        print(f"index {name} unique={unique} cols={info}")
        if unique == 1 and info == ["page_id", "region_uid"]:
            found = True
    assert found, "no unique index on (page_id, region_uid)"
    print("OK: region_uid present and (page_id, region_uid) unique")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
