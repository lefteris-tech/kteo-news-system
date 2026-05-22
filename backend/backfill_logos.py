#!/usr/bin/env python3
"""Backfill logo_path for sources registered before Sprint 5.1.

For each source in the DB whose logo_path is NULL, derives a filesystem slug
from the URL, runs the multi-strategy fetch chain (Clearbit -> HTML parse ->
Google favicon), and stores the resulting on-disk path.

Idempotent: re-running only touches rows that still have logo_path IS NULL
(unless --force is passed, which refetches everything).

Usage:
    sudo -u pi /opt/news_aggregator/venv/bin/python3 backfill_logos.py
    sudo -u pi /opt/news_aggregator/venv/bin/python3 backfill_logos.py --dry-run
    sudo -u pi /opt/news_aggregator/venv/bin/python3 backfill_logos.py --force
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
import urllib.parse
from pathlib import Path

# Make source_logo importable whether installed or run from a checkout.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, "/opt/news_aggregator")

from source_logo import fetch_logo  # noqa: E402

DB_PATH = Path("/opt/news_aggregator/news_cache.db")
log = logging.getLogger("backfill_logos")


def derive_slug(url: str, source_id: int) -> str:
    """Stable filesystem slug. Prefers leftmost domain label, falls back to id."""
    try:
        host = urllib.parse.urlparse(url).netloc or url
    except Exception:
        host = url
    host = host.replace("www.", "", 1)
    label = host.split(".", 1)[0]
    slug = re.sub(r"[^a-z0-9_-]+", "_", label.lower()).strip("_")
    return slug or f"source_{source_id}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="Refetch even sources that already have logos")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show actions without writing")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not DB_PATH.exists():
        log.error("DB not found at %s", DB_PATH)
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    where = "" if args.force else " WHERE logo_path IS NULL"
    rows = list(conn.execute(f"SELECT * FROM sources{where} ORDER BY id").fetchall())
    if not rows:
        log.info("Nothing to backfill.")
        return 0

    log.info("Processing %d sources%s",
             len(rows), " (DRY RUN)" if args.dry_run else "")

    updated = 0
    failed = 0
    for row in rows:
        sid, url = row["id"], row["url"]
        if not url:
            log.warning("source id=%s has no URL — skipping", sid)
            failed += 1
            continue

        slug = derive_slug(url, sid)
        log.info("[%s] %-30s -> slug=%s", sid, url[:30], slug)

        if args.dry_run:
            continue

        result = fetch_logo(url, slug)
        if result:
            conn.execute("UPDATE sources SET logo_path=? WHERE id=?",
                         (str(result), sid))
            updated += 1
        else:
            log.warning("[%s] all logo strategies failed for %s", sid, url)
            failed += 1

    if not args.dry_run:
        conn.commit()

    log.info("Done. Updated=%d Failed=%d", updated, failed)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
