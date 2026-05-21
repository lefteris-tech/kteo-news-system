#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_raw.py — KTEO Curation Platform, Sprint 6
=================================================
Sprint: 6, version: 1
Generated: 2026-05-21

ARCHITECTURE CHANGE FROM SPRINT 3.2:
Classification at fetch time is REMOVED. The classifier (Claude Haiku) is
no longer invoked here. Articles are pulled from RSS, deduplicated, and
inserted into pending_curation with classified_category=NULL.

The human curator assigns the category during the curation step (see
pages/curation.py). The classifier is only invoked at publish time, and
only for items the human has both selected AND categorised.

Rationale: removes a fragile API dependency from fetch time. The Anthropic
API cap that triggered an incident on 2026-05-21 (40/40 fetch failures)
becomes operationally irrelevant — the curator queue keeps populating
even if the API is down. Cost also drops to "publish-only" usage.

Pipeline:
  [1] schedule check (skip Σαβ/Κυρ + αργίες)
  [2] schema sanity
  [3] load active sources from DB (fail-fast if empty)
  [4] fetch RSS from each source (round-robin interleave)
  [5] pre-filter + cap MAX_CANDIDATES
  [6] insert into pending_curation with classified_category=NULL

Exit codes:
  0 = success
  1 = skipped (weekend/holiday)
  3 = no enabled sources in DB
  >3 = error
"""

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

# Re-use Phase 1 helpers
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from news_aggregator import (  # type: ignore
    Article,
    GREEK_HOLIDAYS_FIXED,
    already_seen,
    fetch_article_content,
    fetch_rss,
    init_db,
    mark_seen,
    passes_prefilter,
    setup_logging,
)

log = logging.getLogger("fetch_raw")

MAX_CANDIDATES = 40


# -----------------------------------------------------------------------------
# Schedule check
# -----------------------------------------------------------------------------

def is_weekend_or_holiday(d: datetime) -> tuple[bool, str]:
    if d.weekday() >= 5:
        return True, "Σαββατοκύριακο"
    for month, day in GREEK_HOLIDAYS_FIXED:
        if d.month == month and d.day == day:
            return True, f"Αργία {day}/{month}"
    return False, ""


# -----------------------------------------------------------------------------
# Schema + sources
# -----------------------------------------------------------------------------

def ensure_pending_curation_schema(conn: sqlite3.Connection) -> None:
    cur = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='pending_curation'"
    )
    if cur.fetchone() is None:
        raise RuntimeError(
            "pending_curation table missing. "
            "Run schema_migration.sql first (Sprint 1)."
        )


def load_active_sources(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, name, url, type, category_hint
          FROM sources
         WHERE enabled = 1
         ORDER BY id
    """).fetchall()
    return [dict(r) for r in rows]


# -----------------------------------------------------------------------------
# Insert — Sprint 6: classified_category=NULL, haiku_confidence=NULL
# -----------------------------------------------------------------------------

def insert_pending_minimal(
    db: sqlite3.Connection,
    *,
    fetch_date: str,
    art: Article,
    source_id: int | None = None,
) -> bool:
    try:
        db.execute(
            """
            INSERT INTO pending_curation (
                fetch_date, source_id, guid, title, body_first_para, body_full,
                image_url, pub_date, classified_category,
                safety_passed, haiku_confidence, haiku_summary,
                status, source_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL,
                      'pending', 'auto', ?)
            """,
            (
                fetch_date,
                source_id,
                art.link,
                art.title,
                (art.body_text or "")[:500],
                art.body_text or "",
                art.image_url or "",
                art.published.isoformat() if art.published else datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        db.commit()
        return True
    except sqlite3.IntegrityError:
        log.debug("  → already in pending_curation (duplicate guid for today)")
        return False
    except Exception as e:
        log.error("  → insert failed: %s", e)
        return False


def expire_old_pendings(conn: sqlite3.Connection, today: str) -> int:
    cur = conn.execute(
        """
        UPDATE pending_curation
           SET status = 'expired'
         WHERE status = 'pending'
           AND fetch_date < ?
        """,
        (today,),
    )
    conn.commit()
    return cur.rowcount


# -----------------------------------------------------------------------------
# Multi-source fetch + round-robin interleave
# -----------------------------------------------------------------------------

def fetch_all_sources(sources: list[dict]) -> list[Article]:
    per_source: list[list[Article]] = []
    for src in sources:
        log.info("Fetching %s (%s)", src["name"], src["url"])
        try:
            articles = fetch_rss(src["url"])
            for a in articles:
                a._source_id = src["id"]
                a._source_name = src["name"]
            per_source.append(articles)
            log.info("  → %d articles", len(articles))
        except Exception as e:
            log.error("  → failed to fetch %s: %s", src["url"], e)
            continue

    interleaved: list[Article] = []
    if not per_source:
        return interleaved
    max_len = max(len(lst) for lst in per_source)
    for i in range(max_len):
        for lst in per_source:
            if i < len(lst):
                interleaved.append(lst[i])
    return interleaved


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="KTEO fetch_raw (Sprint 6)")
    parser.add_argument("--db", default="/opt/news_aggregator/news_cache.db")
    parser.add_argument("--ignore-holiday", action="store_true",
                        help="Run even on weekend/holiday")
    parser.add_argument("--limit-feed", type=int, default=None,
                        help="Only process first N items per source (testing)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)

    today_dt = datetime.now()
    today = today_dt.strftime("%Y-%m-%d")

    # [1] Schedule check
    skip, reason = is_weekend_or_holiday(today_dt)
    if skip and not args.ignore_holiday:
        log.info("Skip: %s (use --ignore-holiday to force)", reason)
        return 1

    # [2] Open DB + schema sanity
    db = init_db(args.db)
    ensure_pending_curation_schema(db)

    # [3] Load active sources (FAIL-FAST if empty)
    sources = load_active_sources(db)
    if not sources:
        log.error("=" * 60)
        log.error("ABORT: No enabled sources in DB.")
        log.error("Add at least one via Streamlit Πηγές page,")
        log.error("or seed via SQL into the `sources` table with enabled=1.")
        log.error("=" * 60)
        db.close()
        return 3

    log.info("Active sources: %d", len(sources))
    for s in sources:
        log.info("  [%d] %s — %s", s["id"], s["name"], s["url"])

    # [4] Fetch all sources, interleave round-robin
    raw_articles = fetch_all_sources(sources)
    log.info("Total raw articles (interleaved): %d", len(raw_articles))

    if args.limit_feed:
        raw_articles = raw_articles[: args.limit_feed * len(sources)]
        log.info("Limited to %d (--limit-feed × sources)", len(raw_articles))

    # [5] Pre-filter
    candidates = []
    for art in raw_articles:
        if already_seen(db, art.link):
            continue
        ok, why = passes_prefilter(art)
        if not ok:
            log.debug("Skip (prefilter %s): %s", why, art.title[:60])
            continue
        candidates.append(art)
    log.info("Pre-filter: %d / %d candidates", len(candidates), len(raw_articles))
    candidates = candidates[:MAX_CANDIDATES]

    # [6] Fetch content + insert (NO classification)
    inserted = 0
    skipped_no_content = 0
    per_source_inserts: dict[int, int] = {}
    for idx, art in enumerate(candidates, 1):
        src_id = getattr(art, "_source_id", None)
        src_name = getattr(art, "_source_name", "?")
        log.info("[%d/%d] [%s] %s", idx, len(candidates), src_name, art.title[:60])

        image_url, body_text = fetch_article_content(art.link)
        art.image_url = image_url
        art.body_text = body_text
        if not body_text or not image_url:
            log.debug("  → skip (missing image or body)")
            mark_seen(db, art)
            skipped_no_content += 1
            continue

        if insert_pending_minimal(
            db,
            fetch_date=today,
            art=art,
            source_id=src_id,
        ):
            inserted += 1
            per_source_inserts[src_id] = per_source_inserts.get(src_id, 0) + 1
        mark_seen(db, art)

    # [7] Expire prior days
    expired = expire_old_pendings(db, today)

    # [8] Summary
    log.info("=" * 60)
    log.info("fetch_raw run complete (Sprint 6 — no classification at fetch)")
    log.info("  fetch_date            : %s", today)
    log.info("  sources active        : %d", len(sources))
    log.info("  candidates            : %d", len(candidates))
    log.info("  inserted (pending)    : %d", inserted)
    log.info("  skipped (no content)  : %d", skipped_no_content)
    log.info("  expired (prior days)  : %d", expired)
    log.info("-" * 60)
    log.info("Inserts per source:")
    for s in sources:
        cnt = per_source_inserts.get(s["id"], 0)
        log.info("  [%d] %-25s : %d", s["id"], s["name"], cnt)
    log.info("-" * 60)
    total_pending = db.execute(
        "SELECT COUNT(*) FROM pending_curation "
        "WHERE fetch_date=? AND status='pending'",
        (today,),
    ).fetchone()[0]
    log.info("Today's pending (uncategorised): %d", total_pending)
    log.info("Categorisation happens at curation time via Streamlit.")
    log.info("=" * 60)

    db.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("Interrupted\n")
        sys.exit(130)
