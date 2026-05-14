#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_raw.py — KTEO Curation Platform, Sprint 3.1
====================================================
Sprint: 3.1, version: 2
Generated: 2026-05-14

ARCHITECTURE CHANGE FROM SPRINT 1:
Sprint 1 used Phase 1's `classify_with_claude` which did classify + safety
filter + summarize in one call. The safety filter was designed for fully
automated Phase 1 (no human review) and aggressively rejected anything
"difficult" — including normal news (crimes, executions, politics).

In human-in-the-loop, the user is the filter. fetch_raw now ONLY classifies:
no safety filter, no summary. All articles passing basic prefilter end up
in pending_curation for the user to review.

Summarization happens later at publish time (publish_curated.py Sprint 3.1).

Pipeline:
  [1] schedule check (skip Σαβ/Κυρ + αργίες)
  [2] schema sanity
  [3] fetch RSS from newsbeast
  [4] pre-filter (seen-before, basic quality)
  [5] classify_only via Claude Haiku (category only)
  [6] insert into pending_curation (haiku_summary=NULL)
  [7] expire prior days' pendings

Exit codes:
  0 = success
  1 = skipped (weekend/holiday)
  >1 = error
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

from anthropic import Anthropic

# Re-use Phase 1 helpers (Article model, fetching, dedup)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from news_aggregator import (  # type: ignore
    Article,
    CATEGORIES,
    GREEK_HOLIDAYS_FIXED,
    RATE_LIMIT_SECONDS,
    RSS_SOURCE,
    SLUG_MAP,
    already_seen,
    cleanup_old_entries,
    fetch_article_content,
    fetch_rss,
    init_db,
    mark_seen,
    passes_prefilter,
    setup_logging,
)

log = logging.getLogger("fetch_raw")

MAX_CANDIDATES = 40
HAIKU_MODEL = os.environ.get("KTEO_HAIKU_MODEL", "claude-haiku-4-5-20251001")

# Allowed category slugs (matches SLUG_MAP values)
ALLOWED_SLUGS = {"national", "international", "economy", "lifestyle", "auto", "sports"}


# -----------------------------------------------------------------------------
# Schedule check
# -----------------------------------------------------------------------------

def is_weekend_or_holiday(d: datetime) -> tuple[bool, str]:
    """Σαββατοκύριακα + ελληνικές αργίες (skip και Σάββατο)."""
    if d.weekday() >= 5:
        return True, "Σαββατοκύριακο"
    for month, day in GREEK_HOLIDAYS_FIXED:
        if d.month == month and d.day == day:
            return True, f"Αργία {day}/{month}"
    return False, ""


# -----------------------------------------------------------------------------
# Schema sanity
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


# -----------------------------------------------------------------------------
# Sprint 3.1: classify-only (no safety filter, no summary)
# -----------------------------------------------------------------------------

CLASSIFY_PROMPT = """Είσαι ταξινομητής ειδήσεων. Διάβασε τίτλο και σώμα άρθρου και επέστρεψε ΜΟΝΟ JSON με την κατηγορία.

Κατηγορίες (επίλεξε ΜΙΑ):
- national      → εθνικές ειδήσεις (Ελλάδα, ελληνική κοινωνία, ελληνική πολιτική, ΕΛ.ΑΣ., δικαιοσύνη, ασφάλεια Ελλάδας)
- international → διεθνείς ειδήσεις (χώρες εκτός Ελλάδας, εξωτερική πολιτική, παγκόσμια γεγονότα)
- economy       → οικονομία, αγορές, επιχειρήσεις, χρηματιστήριο, banking, GDP, πρόστιμα
- lifestyle     → ταξίδια, μόδα, φαγητό, τέχνη, ψυχαγωγία, μουσική, εκδηλώσεις, μουσεία, διασκέδαση
- auto          → αυτοκίνητα, μηχανές, EVs, ράλι, μεταφορές
- sports        → αθλητικά (ποδόσφαιρο, μπάσκετ, αγώνες, παίκτες, αποτελέσματα)

ΣΗΜΑΝΤΙΚΟ:
- Δεν φιλτράρεις περιεχόμενο. ΟΛΑ τα άρθρα ταξινομούνται.
- Επιστρέφεις ΜΟΝΟ JSON: {"category": "..."} — καμία λέξη πριν ή μετά.

Τίτλος: %s

Σώμα:
%s
"""


def classify_only(client: Anthropic, title: str, body: str) -> dict | None:
    """Lightweight Claude call: category only. No safety judgment, no summary."""
    body_trim = (body or "")[:1500]
    prompt = CLASSIFY_PROMPT % (title, body_trim)
    try:
        msg = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=80,
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

    # Tolerant JSON extraction
    m = re.search(r'\{[^{}]*"category"\s*:\s*"([^"]+)"[^{}]*\}', text)
    if not m:
        log.debug("  → no JSON in Claude response: %r", text[:100])
        return None

    category = m.group(1).strip().lower()
    if category not in ALLOWED_SLUGS:
        log.debug("  → unknown category: %r", category)
        return None

    return {"category": category, "confidence": 1.0}


def mock_classify_only(art: Article, idx: int) -> dict:
    """Dry-run helper — round-robin categorization."""
    slugs = ["national", "international", "economy", "lifestyle", "auto", "sports"]
    return {"category": slugs[idx % len(slugs)], "confidence": 1.0}


# -----------------------------------------------------------------------------
# Insert (no summary at fetch time — that happens at publish time)
# -----------------------------------------------------------------------------

def insert_pending_minimal(
    db: sqlite3.Connection,
    *,
    fetch_date: str,
    art: Article,
    classified_slug: str,
    confidence: float,
) -> bool:
    """Insert with NULL haiku_summary; safety_passed=1 (deprecated flag in 3.1)."""
    try:
        db.execute(
            """
            INSERT INTO pending_curation (
                fetch_date, source_id, guid, title, body_first_para, body_full,
                image_url, pub_date, classified_category,
                safety_passed, haiku_confidence, haiku_summary,
                status, source_type, created_at
            ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, 'pending', 'auto', ?)
            """,
            (
                fetch_date,
                art.link,
                art.title,
                (art.body_text or "")[:500],
                art.body_text or "",
                art.image_url or "",
                art.published.isoformat() if art.published else datetime.now(timezone.utc).isoformat(),
                classified_slug,
                confidence,
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
# Main
# -----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="KTEO fetch_raw (Sprint 3.1)")
    parser.add_argument("--db", default="/opt/news_aggregator/news_cache.db")
    parser.add_argument("--dry-run", action="store_true",
                        help="Mock classify (no API calls), still inserts to DB")
    parser.add_argument("--ignore-holiday", action="store_true",
                        help="Run even on weekend/holiday")
    parser.add_argument("--limit-feed", type=int, default=None,
                        help="Only process first N RSS items (testing)")
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

    # [3] Anthropic client
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    claude_client = None
    if not args.dry_run:
        if not api_key:
            log.error("ANTHROPIC_API_KEY not set; aborting (use --dry-run for test)")
            return 2
        claude_client = Anthropic(api_key=api_key)

    # [4] Fetch RSS
    log.info("Fetching RSS feed: %s", RSS_SOURCE)
    raw_articles = fetch_rss(RSS_SOURCE)
    log.info("Got %d raw articles", len(raw_articles))
    if args.limit_feed:
        raw_articles = raw_articles[: args.limit_feed]
        log.info("Limited to %d (--limit-feed)", len(raw_articles))

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

    # [6] Fetch + classify + insert
    inserted = 0
    failed_classification = 0
    for idx, art in enumerate(candidates, 1):
        log.info("[%d/%d] %s", idx, len(candidates), art.title[:70])

        image_url, body_text = fetch_article_content(art.link)
        art.image_url = image_url
        art.body_text = body_text
        if not body_text or not image_url:
            log.debug("  → skip (missing image or body)")
            mark_seen(db, art)
            continue

        if args.dry_run:
            result = mock_classify_only(art, idx)
        else:
            result = classify_only(claude_client, art.title, body_text)
            time.sleep(RATE_LIMIT_SECONDS)

        if not result:
            log.debug("  → classification failed")
            mark_seen(db, art)
            failed_classification += 1
            continue

        slug = result["category"]
        art.classified_category = slug

        if insert_pending_minimal(
            db,
            fetch_date=today,
            art=art,
            classified_slug=slug,
            confidence=result["confidence"],
        ):
            inserted += 1
        mark_seen(db, art)

    # [7] Expire prior days' pendings
    expired = expire_old_pendings(db, today)

    # [8] Summary
    log.info("=" * 60)
    log.info("fetch_raw run complete (Sprint 3.1)")
    log.info("  fetch_date            : %s", today)
    log.info("  candidates            : %d", len(candidates))
    log.info("  inserted (pending)    : %d", inserted)
    log.info("  classification failed : %d", failed_classification)
    log.info("  expired (prior days)  : %d", expired)
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
        sys.stderr.write("Interrupted\n")
        sys.exit(130)
