#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_raw.py — KTEO Curation Platform, Sprint 1
================================================
Sprint: 1, version: 1
Generated: 2026-05-13

Δουλειά:
  Φέρνει τα raw άρθρα από το newsbeast RSS, τα κατηγοριοποιεί και
  συνοψίζει μέσω Claude Haiku, και τα γράφει στο pending_curation
  table για να τα διαλέξει ο marketing user στο Streamlit UI.

ΔΕΝ γράφει XML. ΔΕΝ ενημερώνει Yodeck. Καθαρά data-layer step.

Διαφορές από news_aggregator.py:
  - Skip Σαβ ΚΑΙ Κυρ (όχι μόνο Κυριακή)
  - Δεν εφαρμόζει top-N — κρατάει όλα τα classified items
  - Γράφει σε pending_curation αντί για XML
  - Πριν εισάγει σημερινά items, μαρκάρει expired τα παλιά pendings

Pipeline:
  [0] Weekend/holiday skip
  [1] RSS pull
  [2] Dedup μέσω υπάρχοντος seen_articles
  [3] Pre-filter (age, blocklist, title length)
  [4] Article fetch (og:image + body)
  [5] Claude Haiku classification + summarization
  [6] INSERT στο pending_curation
  [7] Mark prior-days pendings ως 'expired'

Trigger: /opt/news_aggregator/fetch_raw_cron.sh, pi cron Mon-Fri 07:50.
"""

import argparse
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

# -----------------------------------------------------------------------------
# Re-use Phase 1 code. Phase 1 stays installed during Sprint 1 validation;
# in Sprint 2 we'll consolidate the shared code into a config module.
# -----------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from news_aggregator import (  # type: ignore
    Article,
    CATEGORIES,
    GREEK_HOLIDAYS_FIXED,
    RATE_LIMIT_SECONDS,
    RSS_SOURCE,
    SLUG_MAP,
    already_seen,
    classify_with_claude,
    cleanup_old_entries,
    fetch_article_content,
    fetch_rss,
    init_db,
    mark_seen,
    mock_classify,
    passes_prefilter,
    setup_logging,
)

log = logging.getLogger("fetch_raw")

MAX_CANDIDATES = 40  # Same cap as Phase 1 για cost control


# -----------------------------------------------------------------------------
# Schedule check
# -----------------------------------------------------------------------------

def is_weekend_or_holiday(d: datetime) -> tuple[bool, str]:
    """Σαββατοκύριακα + ελληνικές αργίες.

    Διαφορά από news_aggregator.is_holiday(): εδώ skip και Σάββατο.
    """
    if d.weekday() >= 5:  # 5=Σάβ, 6=Κυρ
        return True, "Σαββατοκύριακο"
    for month, day in GREEK_HOLIDAYS_FIXED:
        if d.month == month and d.day == day:
            return True, f"Αργία {day}/{month}"
    return False, ""


# -----------------------------------------------------------------------------
# Schema sanity
# -----------------------------------------------------------------------------

def ensure_pending_curation_schema(conn: sqlite3.Connection) -> None:
    """Καθαρό error message αν δεν τρέξαμε ακόμα το migration."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='pending_curation'"
    )
    if cur.fetchone() is None:
        raise RuntimeError(
            "Table 'pending_curation' missing. "
            "Run S1-schema_migration.sql first."
        )


# -----------------------------------------------------------------------------
# DB writes
# -----------------------------------------------------------------------------

def expire_old_pendings(conn: sqlite3.Connection, today: str) -> int:
    """Mark prior days' 'pending' rows ως 'expired'. Returns count."""
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


def insert_pending(
    conn: sqlite3.Connection,
    *,
    fetch_date: str,
    art: Article,
    classified_slug: str,
    safety_passed: bool,
    confidence: float,
    summary: str,
) -> bool:
    """Insert classified article. Returns True αν inserted, False αν duplicate."""
    body_first_para = (art.body_text or "").split("\n", 1)[0][:500]
    try:
        conn.execute(
            """
            INSERT INTO pending_curation (
                fetch_date, source_id, guid, title, body_first_para, body_full,
                image_url, pub_date, classified_category,
                safety_passed, haiku_confidence, haiku_summary,
                status, source_type, created_at
            ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'auto', ?)
            """,
            (
                fetch_date,
                art.link,
                art.title,
                body_first_para,
                (art.body_text or "")[:4000],
                art.image_url or "",
                art.published.isoformat(),
                classified_slug,
                1 if safety_passed else 0,
                confidence,
                summary,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # UNIQUE(fetch_date, guid) — already inserted today, skip
        return False


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="KTEO fetch_raw — populate pending_curation (no XML output)",
    )
    parser.add_argument("--db", default="/opt/news_aggregator/news_cache.db",
                        help="Path to SQLite cache")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip Claude API; use mock classification")
    parser.add_argument("--limit-feed", action="store_true",
                        help="Only first 6 articles from RSS (test mode)")
    parser.add_argument("--ignore-holiday", action="store_true",
                        help="Bypass weekend/holiday skip")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="DEBUG logging")
    args = parser.parse_args()

    setup_logging(args.verbose)

    # [0] Weekend/holiday skip
    now = datetime.now()
    skip, reason = is_weekend_or_holiday(now)
    if skip and not args.ignore_holiday:
        log.info("Skip run: %s. (--ignore-holiday για manual override)", reason)
        return 0

    today = now.strftime("%Y-%m-%d")

    # [1] RSS
    raw_articles = fetch_rss(RSS_SOURCE)
    if not raw_articles:
        log.error("RSS returned 0 articles — abort")
        return 1
    if args.limit_feed:
        raw_articles = raw_articles[:6]
        log.info("--limit-feed → %d articles", len(raw_articles))

    # [2] DB + schema sanity + cleanup παλιού dedup cache
    db = init_db(args.db)
    ensure_pending_curation_schema(db)
    deleted = cleanup_old_entries(db)
    if deleted:
        log.info("Cleaned %d old dedup entries", deleted)

    # [3] Pre-filter
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

    # [4] Claude client (skip if dry-run)
    claude_client = None
    if not args.dry_run:
        try:
            from anthropic import Anthropic
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                log.error("ANTHROPIC_API_KEY missing. Use --dry-run για testing.")
                return 2
            claude_client = Anthropic(api_key=api_key)
        except ImportError:
            log.error("anthropic package not installed in venv")
            return 2

    # [5] fetch + classify + insert
    inserted = 0
    skipped_unsafe = 0
    for idx, art in enumerate(candidates, 1):
        log.info("[%d/%d] %s", idx, len(candidates), art.title[:70])

        image_url, body_text = fetch_article_content(art.link)
        art.image_url = image_url
        art.body_text = body_text
        if not body_text or not image_url:
            log.debug("  → skip (missing image or body)")
            continue

        if args.dry_run:
            result = mock_classify(art, idx)
        else:
            result = classify_with_claude(claude_client, art.title, body_text)
            time.sleep(RATE_LIMIT_SECONDS)

        category = result.get("category", "SKIP")
        if category == "SKIP" or not result.get("safe_for_kteo"):
            log.debug("  → SKIP (%s)", result.get("skip_reason"))
            mark_seen(db, art)  # μην ξανα-classify αύριο
            skipped_unsafe += 1
            continue
        if category not in CATEGORIES:
            log.debug("  → unknown category: %s", category)
            continue

        slug = SLUG_MAP[category]
        art.classified_category = category
        art.summary = result.get("summary", "")
        art.safe = bool(result.get("safe_for_kteo"))
        art.confidence = float(result.get("confidence", 0.0))

        if insert_pending(
            db,
            fetch_date=today,
            art=art,
            classified_slug=slug,
            safety_passed=art.safe,
            confidence=art.confidence,
            summary=art.summary,
        ):
            inserted += 1
        mark_seen(db, art)

    # [6] Expire prior days' pendings (AFTER inserts so we don't expire today's)
    expired = expire_old_pendings(db, today)

    # [7] Summary
    log.info("=" * 60)
    log.info("fetch_raw run complete")
    log.info("  fetch_date         : %s", today)
    log.info("  candidates         : %d", len(candidates))
    log.info("  inserted (pending) : %d", inserted)
    log.info("  rejected by Claude : %d", skipped_unsafe)
    log.info("  expired (prior)    : %d", expired)
    log.info("-" * 60)
    log.info("Today's pending by category:")
    for slug in ["national", "international", "economy",
                 "lifestyle", "auto", "sports"]:
        cnt = db.execute(
            "SELECT COUNT(*) FROM pending_curation "
            "WHERE fetch_date=? AND classified_category=? AND status='pending'",
            (today, slug),
        ).fetchone()[0]
        log.info("  %-14s : %d", slug, cnt)
    log.info("=" * 60)

    db.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(130)
    except Exception as e:
        log.exception("Unhandled error: %s", e)
        sys.exit(1)
