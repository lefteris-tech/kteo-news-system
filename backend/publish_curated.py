#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publish_curated.py — KTEO Curation Platform, Sprint 2
======================================================
Sprint: 2, version: 1
Generated: 2026-05-13

Διαβάζει τα 'selected' items από pending_curation, παράγει τα 6
per-category XML files, μετά τρέχει carry_over και playlist_sync.

Triggered ONLY by:
  - Streamlit "Publish to screens" button (Sprint 3+)
  - curate_cli.py publish command

ΠΟΤΕ από cron. Κανένα automated fallback.

Pipeline:
  [1] Φέρε τα selected rows (status='selected') για το fetch_date
  [2] Group by category, build Article objects
  [3] Build & write XML για κάθε κατηγορία (atomic write)
  [4] Mark pending_curation rows: status='published', published_at
  [5] Insert publish_log row
  [6] subprocess: carry_over.py
  [7] subprocess: playlist_sync.py (skipped if --no-yodeck)

Exit codes:
  0 = success
  1 = nothing selected
  2 = playlist_sync failed (XMLs were written though)
  >2 = unexpected error
"""

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Re-use Phase 1 helpers (constants + XML builder)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from news_aggregator import (  # type: ignore
    Article,
    SLUG_MAP,
    build_rss_xml,
    setup_logging,
)

log = logging.getLogger("publish_curated")

DEFAULT_DB         = "/opt/news_aggregator/news_cache.db"
DEFAULT_OUTPUT_DIR = "/var/www/html"
CARRY_OVER_SCRIPT  = "/opt/news_aggregator/carry_over.py"
PLAYLIST_SYNC      = "/opt/news_aggregator/playlist_sync.py"
PYTHON_BIN         = "/opt/news_aggregator/venv/bin/python"
ENV_FILE           = "/etc/news_aggregator.env"

# Reverse map slug → Greek label (build_rss_xml expects the label)
SLUG_TO_CATEGORY = {v: k for k, v in SLUG_MAP.items()}


# -----------------------------------------------------------------------------
# Env loader (in case caller didn't source it)
# -----------------------------------------------------------------------------

def ensure_env_loaded() -> None:
    """Source /etc/news_aggregator.env if YODECK_* are missing."""
    needed = ("ANTHROPIC_API_KEY", "YODECK_API_TOKEN",
              "YODECK_PLAYLIST_A_ID", "YODECK_PLAYLIST_B_ID")
    if all(os.environ.get(k) for k in needed):
        return
    if not os.path.exists(ENV_FILE):
        log.warning("Env file %s missing — playlist_sync may fail", ENV_FILE)
        return
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(),
                                  v.strip().strip('"').strip("'"))


# -----------------------------------------------------------------------------
# DB I/O
# -----------------------------------------------------------------------------

def fetch_selected_rows(
    conn: sqlite3.Connection, fetch_date: str
) -> dict[str, list[sqlite3.Row]]:
    """Group selected rows by category slug. Empty list per category."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM pending_curation
         WHERE fetch_date = ? AND status = 'selected'
         ORDER BY classified_category, id
    """, (fetch_date,)).fetchall()

    grouped: dict[str, list[sqlite3.Row]] = {slug: [] for slug in SLUG_MAP.values()}
    for row in rows:
        slug = row["classified_category"]
        if slug in grouped:
            grouped[slug].append(row)
        else:
            log.warning("Unknown category slug in row %s: %r", row["id"], slug)
    return grouped


def mark_published(conn: sqlite3.Connection, row_ids: list[int]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        "UPDATE pending_curation SET status='published', published_at=? WHERE id=?",
        [(now, rid) for rid in row_ids],
    )
    conn.commit()


def insert_publish_log(
    conn: sqlite3.Connection,
    *,
    publish_date: str,
    triggered_by: str,
    items_per_category: dict[str, list[int]],
) -> int:
    total = sum(len(v) for v in items_per_category.values())
    cur = conn.execute("""
        INSERT INTO publish_log
            (publish_date, triggered_by, items_per_category_json, total_items)
        VALUES (?, ?, ?, ?)
    """, (publish_date, triggered_by,
          json.dumps(items_per_category, sort_keys=True), total))
    conn.commit()
    return cur.lastrowid


# -----------------------------------------------------------------------------
# XML
# -----------------------------------------------------------------------------

def row_to_article(row: sqlite3.Row) -> Article:
    """Build an Article instance from a pending_curation row."""
    pub_dt = datetime.fromisoformat(row["pub_date"])
    if pub_dt.tzinfo is None:
        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
    art = Article(
        title=row["title"],
        link=row["guid"],
        published=pub_dt,
        image_url=row["image_url"] or "",
        summary=row["haiku_summary"] or "",
    )
    art.classified_category = SLUG_TO_CATEGORY[row["classified_category"]]
    return art


def write_xml_atomic(path: Path, content: str) -> None:
    """Atomic write: tmp file + rename."""
    tmp = path.with_suffix(".xml.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# -----------------------------------------------------------------------------
# Subprocess helpers
# -----------------------------------------------------------------------------

def run_subprocess(cmd: list, description: str, timeout: int = 120) -> bool:
    log.info("→ %s", description)
    log.debug("  cmd: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        if result.stdout:
            for line in result.stdout.rstrip().splitlines():
                log.info("    %s", line)
        if result.returncode != 0:
            log.error("%s FAILED (exit %d)", description, result.returncode)
            if result.stderr:
                for line in result.stderr.rstrip().splitlines():
                    log.error("    %s", line)
            return False
        return True
    except subprocess.TimeoutExpired:
        log.error("%s timed out after %d sec", description, timeout)
        return False
    except Exception as e:
        log.error("%s error: %s", description, e)
        return False


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="KTEO publish_curated — selected items → XMLs → Yodeck",
    )
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help="Where to write the per-category XML files")
    parser.add_argument("--triggered-by", default="cli@local",
                        help="Identifier for publish_log row")
    parser.add_argument("--fetch-date",
                        default=datetime.now().strftime("%Y-%m-%d"),
                        help="yyyy-mm-dd (default: today)")
    parser.add_argument("--no-yodeck", action="store_true",
                        help="Skip playlist_sync invocation")
    parser.add_argument("--no-carry-over", action="store_true",
                        help="Skip carry_over invocation")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen — write nothing, mutate nothing")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    ensure_env_loaded()

    output_dir = Path(args.output_dir)
    publish_date_str = datetime.now().strftime("%Y-%m-%d")

    if not output_dir.exists():
        if args.dry_run:
            log.warning("output_dir %s missing (dry-run, continuing)", output_dir)
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
            log.info("Created %s", output_dir)

    conn = sqlite3.connect(args.db)
    grouped = fetch_selected_rows(conn, args.fetch_date)
    total = sum(len(v) for v in grouped.values())

    log.info("=" * 60)
    log.info("publish_curated — Sprint 2")
    log.info("  fetch_date     : %s", args.fetch_date)
    log.info("  output_dir     : %s", output_dir)
    log.info("  triggered_by   : %s", args.triggered_by)
    log.info("  dry_run        : %s", args.dry_run)
    log.info("  selected items : %d", total)
    for slug, rows in grouped.items():
        log.info("    %-14s %d", slug, len(rows))
    log.info("=" * 60)

    if total == 0:
        log.warning("No items selected for %s — nothing to publish.", args.fetch_date)
        log.warning("Use: curate_cli.py select <id> <id> ...")
        return 1

    # Build XMLs
    items_per_cat_ids: dict[str, list[int]] = {}
    for slug, rows in grouped.items():
        category_label = SLUG_TO_CATEGORY[slug]
        articles = [row_to_article(r) for r in rows]
        xml = build_rss_xml(category_label, articles)
        out_path = output_dir / f"{slug}.xml"

        items_per_cat_ids[slug] = [r["id"] for r in rows]

        if args.dry_run:
            log.info("[dry-run] would write %s (%d items, %d bytes)",
                     out_path, len(articles), len(xml))
        else:
            write_xml_atomic(out_path, xml)
            size = out_path.stat().st_size
            log.info("Wrote %s — %d items, %d bytes",
                     out_path, len(articles), size)

    if args.dry_run:
        log.info("[dry-run] would mark %d items published", total)
        log.info("[dry-run] would insert publish_log row")
        log.info("[dry-run] would run carry_over and playlist_sync")
        conn.close()
        return 0

    # Mark items as published + audit log
    all_ids = [rid for ids in items_per_cat_ids.values() for rid in ids]
    mark_published(conn, all_ids)
    log_id = insert_publish_log(
        conn,
        publish_date=publish_date_str,
        triggered_by=args.triggered_by,
        items_per_category=items_per_cat_ids,
    )
    log.info("publish_log row id=%d  (total %d items)", log_id, total)
    conn.close()

    # carry_over (still useful even in curated mode — tops up thin categories
    # from archive within a single human-curated publish)
    if not args.no_carry_over:
        run_subprocess(
            [PYTHON_BIN, CARRY_OVER_SCRIPT, str(output_dir)],
            "carry_over",
        )
    else:
        log.info("--no-carry-over: skipped")

    # playlist_sync
    if not args.no_yodeck:
        ok = run_subprocess(
            [PYTHON_BIN, PLAYLIST_SYNC],
            "playlist_sync",
            timeout=60,
        )
        if not ok:
            log.warning("playlist_sync failed — XMLs written, Yodeck NOT updated")
            return 2
    else:
        log.info("--no-yodeck: skipped")

    log.info("=" * 60)
    log.info("publish_curated done. items=%d, log_id=%d", total, log_id)
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(130)
    except Exception as e:
        log.exception("Unhandled: %s", e)
        sys.exit(3)
