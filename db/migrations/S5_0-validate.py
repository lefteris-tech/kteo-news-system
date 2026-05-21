#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S5_0-validate.py — Sprint 5.0 schema migration validator
=========================================================
Sprint: 5.0, version: 1, generated: 2026-05-21

Confirms that the Sprint 5.0 schema migration has been applied successfully
to /opt/news_aggregator/news_cache.db (or to a path passed as argv[1]).

Checks:
  1. Column pending_curation.auto_filter_rule_id exists
  2. Index idx_pending_auto_filter exists and is partial
  3. Foreign-key declaration on the new column references filters(id)

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
    print(("✓ " if condition else "✗ ") + (ok_msg if condition else fail_msg))
    return condition


def main(argv: list[str]) -> int:
    db_path = argv[1] if len(argv) > 1 else DEFAULT_DB
    if not Path(db_path).is_file():
        print(f"✗ Database file not found: {db_path}", file=sys.stderr)
        return 2

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as e:
        print(f"✗ Cannot open database: {e}", file=sys.stderr)
        return 2

    all_ok = True

    # Check 1: column exists
    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(pending_curation)")}
    all_ok &= check(
        "auto_filter_rule_id" in cols,
        "pending_curation.auto_filter_rule_id exists",
        "pending_curation.auto_filter_rule_id is MISSING — run S5_0-prefiltering_schema.sql",
    )

    # Check 2: index exists and is partial.
    # The `partial` column on sqlite_master was added in SQLite 3.36, but
    # the Python `sqlite3` module is often built against older libraries.
    # Parse the index definition string instead — portable across versions.
    idx_row = conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='index' AND name='idx_pending_auto_filter'"
    ).fetchone()
    all_ok &= check(
        idx_row is not None,
        "idx_pending_auto_filter exists",
        "idx_pending_auto_filter is MISSING — run S5_0-prefiltering_schema.sql",
    )
    if idx_row is not None and idx_row["sql"]:
        is_partial = "WHERE" in idx_row["sql"].upper()
        all_ok &= check(
            is_partial,
            "idx_pending_auto_filter is a partial index",
            "idx_pending_auto_filter is NOT partial — re-apply migration",
        )

    # Check 3: foreign-key declaration on the new column
    fks = conn.execute("PRAGMA foreign_key_list(pending_curation)").fetchall()
    has_filter_fk = any(
        fk["from"] == "auto_filter_rule_id" and fk["table"] == "filters" and fk["to"] == "id"
        for fk in fks
    )
    all_ok &= check(
        has_filter_fk,
        "FK pending_curation.auto_filter_rule_id → filters(id) declared",
        "FK declaration on auto_filter_rule_id MISSING — re-apply migration",
    )

    conn.close()

    if all_ok:
        print("\n✓ Sprint 5.0 schema migration verified")
        return 0
    else:
        print("\n✗ One or more checks failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
