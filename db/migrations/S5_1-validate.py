#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S5_1-validate.py — Sprint 5.1 schema migration validator
=========================================================
Sprint: 5.1, generated: 2026-05-22

Confirms that the Sprint 5.1 schema migration has been applied successfully
to /opt/news_aggregator/news_cache.db (or to a path passed as argv[1]).

Checks:
  1. Column sources.logo_path exists
  2. Column type is TEXT (or empty type — SQLite ALTER ADD COLUMN preserves the spec)
  3. Existing sources rows are reachable through the new column (NULL-tolerant)

Exit codes:
  0 — all checks passed
  1 — one or more checks failed
  2 — DB file not found or unreadable
"""

import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "/opt/news_aggregator/news_cache.db"


def check(condition: bool, ok_msg: str, fail_msg: str) -> bool:
    """Print ✓/✗ line and return condition."""
    print(("✓ " + ok_msg) if condition else ("✗ " + fail_msg))
    return condition


def main() -> int:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(DEFAULT_DB)
    if not db_path.is_file():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 2

    print(f"Validating Sprint 5.1 schema in {db_path}\n")

    conn = sqlite3.connect(db_path)
    cols = list(conn.execute("PRAGMA table_info(sources)"))
    by_name = {row[1]: row for row in cols}

    ok = True

    # Check 1 — column exists
    ok &= check(
        "logo_path" in by_name,
        "sources.logo_path column exists",
        "sources.logo_path column missing — migration not applied",
    )

    # Check 2 — column type is TEXT
    if "logo_path" in by_name:
        col_type = (by_name["logo_path"][2] or "").upper()
        ok &= check(
            col_type in ("TEXT", ""),
            f"sources.logo_path declared as TEXT (or untyped — got '{col_type}')",
            f"sources.logo_path has unexpected type '{col_type}'",
        )

    # Check 3 — column queryable, NULL-tolerant
    try:
        n = conn.execute("SELECT COUNT(*) FROM sources WHERE logo_path IS NULL OR logo_path IS NOT NULL").fetchone()[0]
        ok &= check(True, f"sources.logo_path is queryable ({n} rows)", "")
    except sqlite3.Error as e:
        ok &= check(False, "", f"query against logo_path failed: {e}")

    conn.close()
    print()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
