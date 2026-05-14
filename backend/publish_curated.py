#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publish_curated.py — KTEO Curation Platform, Sprint 3.1
========================================================
Sprint: 3.1, version: 2
Generated: 2026-05-14

ARCHITECTURE CHANGE FROM SPRINT 2:
In Sprint 3.1, fetch_raw stores items with haiku_summary=NULL (no summary
generated at fetch time). publish_curated now runs Claude Haiku
summarization for each selected item BEFORE writing XML — but only for
items the user has approved. This is the human-in-the-loop principle:
Claude only summarizes content humans approved for screens.

Pipeline:
  [1] Fetch selected rows (status='selected')
  [2] Summarize items where haiku_summary is empty (Sprint 3.1 NEW)
  [3] Group by category, build Article objects with fresh summaries
  [4] Build & write XML for each category (atomic)
  [5] Mark rows status='published'
  [6] Insert publish_log row
  [7] subprocess: carry_over.py
  [8] subprocess: playlist_sync.py (unless --no-yodeck)

Exit codes:
  0 = success
  1 = nothing selected
  2 = playlist_sync failed (XMLs were written though)
  3 = summarization failed for too many items
  >3 = unexpected error
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic

# Re-use Phase 1 helpers
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

HAIKU_MODEL = os.environ.get("KTEO_HAIKU_MODEL", "claude-haiku-4-5-20251001")
SUMMARY_RATE_LIMIT_SEC = 5  # gentler than fetch_raw (we're already at publish time)

# Reverse map slug → Greek label (build_rss_xml expects the label)
SLUG_TO_CATEGORY = {v: k for k, v in SLUG_MAP.items()}


# -----------------------------------------------------------------------------
# Environment
# -----------------------------------------------------------------------------

def ensure_env_loaded() -> None:
    """Load /etc/news_aggregator.env into os.environ (idempotent)."""
    if not os.path.exists(ENV_FILE):
        return
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# -----------------------------------------------------------------------------
# DB
# -----------------------------------------------------------------------------

def fetch_selected_rows(
    conn: sqlite3.Connection, fetch_date: str
) -> dict[str, list[dict]]:
    """Group selected rows by category slug. Returns dicts (mutable for summary update)."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM pending_curation
         WHERE fetch_date = ? AND status = 'selected'
         ORDER BY classified_category, id
    """, (fetch_date,)).fetchall()

    grouped: dict[str, list[dict]] = {slug: [] for slug in SLUG_MAP.values()}
    for row in rows:
        slug = row["classified_category"]
        d = dict(row)
        if slug in grouped:
            grouped[slug].append(d)
        else:
            log.warning("Unknown category slug in row %s: %r", d["id"], slug)
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
# Sprint 3.1: on-demand summarization
# -----------------------------------------------------------------------------

SUMMARIZE_PROMPT = """Είσαι expert copywriter για digital signage σε δημόσιο χώρο (αίθουσα αναμονής KTEO).
Γράψε σύνοψη ΜΟΝΟ στα Ελληνικά για το άρθρο, ώστε να χωράει σε οθόνη και να διαβάζεται γρήγορα.

ΑΥΣΤΗΡΟΙ ΚΑΝΟΝΕΣ:
1. 120–160 χαρακτήρες ακριβώς (κρίσιμο για την οθόνη).
2. 3 πλήρεις, αυτόνομες προτάσεις.
3. Πληροφοριακό tone — όχι sensational, όχι clickbait.
4. ΜΗΝ αναφέρεις references σε video ή φωτογραφίες ("δείτε βίντεο", "(βίντεο)", "(φωτό)") — οι οθόνες δεν παίζουν multimedia.
5. ΜΗΝ αναφέρεις ονόματα Ελλήνων πολιτικών.
6. Φυσική, ρέουσα γλώσσα.

Τίτλος: %s

Σώμα άρθρου:
%s

Επιστρέφεις ΜΟΝΟ JSON: {"summary": "..."} — καμία λέξη πριν ή μετά.
"""


def summarize_for_screen(client: Anthropic, title: str, body: str) -> str | None:
    """Generate Greek screen-suitable summary (120-160 chars). Returns None on failure."""
    body_trim = (body or "")[:2500]
    prompt = SUMMARIZE_PROMPT % (title, body_trim)
    try:
        msg = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        log.error("Claude API error: %s", e)
        return None

    text = ""
    for block in msg.content:
        if hasattr(block, "text"):
            text += block.text
    text = text.strip()

    # Find the outermost {...} block and parse with json.loads (correct UTF-8 handling)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        log.debug("  no JSON in Claude response: %r", text[:200])
        return None

    json_str = text[start : end + 1]
    try:
        obj = json.loads(json_str)
    except json.JSONDecodeError as e:
        log.debug("  JSON parse failed: %s; raw: %r", e, json_str[:200])
        return None

    summary = obj.get("summary", "")
    if not isinstance(summary, str):
        return None
    summary = summary.strip()
    return summary or None


def summarize_missing(
    conn: sqlite3.Connection,
    grouped: dict[str, list[dict]],
    dry_run: bool,
) -> tuple[int, int]:
    """For each selected row without a summary, generate one. Persist back to DB
    and update the in-memory dict. Returns (summarized_count, failure_count).
    """
    needs = [r for rows in grouped.values() for r in rows
             if not (r.get("haiku_summary") or "").strip()]
    if not needs:
        log.info("All selected items already have summaries — skipping summarize step")
        return 0, 0

    log.info("Sprint 3.1 — summarizing %d items", len(needs))

    if dry_run:
        log.info("[dry-run] would call Claude Haiku %d times for summaries", len(needs))
        return 0, 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set — cannot summarize")
        return 0, len(needs)

    client = Anthropic(api_key=api_key)
    ok_count = 0
    fail_count = 0
    for i, row in enumerate(needs, 1):
        log.info("  [%d/%d] %s", i, len(needs), row["title"][:60])
        summary = summarize_for_screen(
            client,
            row["title"],
            row.get("body_full") or row.get("body_first_para") or "",
        )
        if summary:
            conn.execute(
                "UPDATE pending_curation SET haiku_summary=? WHERE id=?",
                (summary, row["id"]),
            )
            row["haiku_summary"] = summary  # update in-memory copy too
            ok_count += 1
            log.debug("    → %d chars: %s", len(summary), summary[:80])
        else:
            # Fallback: use first 160 chars of body so XML isn't empty
            fallback = (row.get("body_first_para") or row.get("title") or "")[:160]
            conn.execute(
                "UPDATE pending_curation SET haiku_summary=? WHERE id=?",
                (fallback, row["id"]),
            )
            row["haiku_summary"] = fallback
            fail_count += 1
            log.warning("    → fallback used (Claude failed)")

        time.sleep(SUMMARY_RATE_LIMIT_SEC)

    conn.commit()
    log.info("Summarization: %d ok, %d fallback", ok_count, fail_count)
    return ok_count, fail_count


# -----------------------------------------------------------------------------
# XML
# -----------------------------------------------------------------------------

def row_to_article(row: dict) -> Article:
    """Build an Article instance from a pending_curation row (dict)."""
    pub_dt_str = row["pub_date"]
    pub_dt = datetime.fromisoformat(pub_dt_str)
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
    tmp = path.with_suffix(".xml.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# -----------------------------------------------------------------------------
# Subprocess
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
    parser = argparse.ArgumentParser(description="publish_curated (Sprint 3.1)")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--fetch-date", default=None,
                        help="YYYY-MM-DD (defaults to today)")
    parser.add_argument("--triggered-by", default="cli",
                        help="Identifier for publish_log row")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate flow, no XML writes, no Claude calls")
    parser.add_argument("--no-carry-over", action="store_true")
    parser.add_argument("--no-yodeck", action="store_true",
                        help="Skip playlist_sync")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    ensure_env_loaded()

    if not args.fetch_date:
        args.fetch_date = datetime.now().strftime("%Y-%m-%d")

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
    log.info("publish_curated — Sprint 3.1")
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
        return 1

    # Sprint 3.1: summarize before XML write
    ok, failed = summarize_missing(conn, grouped, dry_run=args.dry_run)
    if failed and failed >= total // 2:
        log.error("Too many summarization failures (%d/%d) — aborting", failed, total)
        return 3

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

    # Mark published
    all_ids = [rid for ids in items_per_cat_ids.values() for rid in ids]
    mark_published(conn, all_ids)

    # publish_log
    log_id = insert_publish_log(
        conn,
        publish_date=publish_date_str,
        triggered_by=args.triggered_by,
        items_per_category=items_per_cat_ids,
    )
    log.info("publish_log row id=%d  (total %d items)", log_id, total)

    conn.close()

    # carry_over
    if args.no_carry_over:
        log.info("--no-carry-over: skipped")
    else:
        ok_co = run_subprocess(
            [PYTHON_BIN, CARRY_OVER_SCRIPT, str(output_dir)],
            "carry_over",
            timeout=60,
        )
        if not ok_co:
            log.warning("carry_over reported errors (non-fatal)")

    # playlist_sync
    if args.no_yodeck:
        log.info("--no-yodeck: skipped")
    else:
        ok_ps = run_subprocess(
            [PYTHON_BIN, PLAYLIST_SYNC],
            "playlist_sync",
            timeout=60,
        )
        if not ok_ps:
            log.error("playlist_sync failed — XMLs written but Yodeck not updated")
            return 2

    log.info("=" * 60)
    log.info("publish_curated done. items=%d, log_id=%d", total, log_id)
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("Interrupted\n")
        sys.exit(130)
