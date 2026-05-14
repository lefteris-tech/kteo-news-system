#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_raw.py — KTEO Curation Platform, Sprint 3.2
====================================================
Sprint: 3.2, version: 3
Generated: 2026-05-14

CHANGES FROM SPRINT 3.1:
- Sources are now read from the `sources` DB table (managed via Streamlit
  Πηγές page) instead of the hardcoded RSS_SOURCE constant.
- Each pending_curation row records its source_id (was always NULL before).
- Multiple sources are fetched and interleaved round-robin so MAX_CANDIDATES
  budget is shared fairly across sources.
- Fail-fast: if no enabled source exists, fetch_raw exits with code 3 and
  a clear error message. No silent failure, no fallback.

CHANGES FROM SPRINT 3.1 KEPT:
- Classify-only (no safety filter, no summary at fetch).
- haiku_summary stored NULL until publish-time summarization.

Pipeline:
  [1] schedule check (skip Σαβ/Κυρ + αργίες)
  [2] schema sanity
  [3] load active sources from DB (fail-fast if empty)
  [4] fetch RSS from each source
  [5] pre-filter + interleave + cap MAX_CANDIDATES
  [6] classify_only via Claude Haiku
  [7] insert into pending_curation with source_id
  [8] expire prior days' pendings

Exit codes:
  0 = success
  1 = skipped (weekend/holiday)
  2 = API key missing
  3 = no enabled sources in DB
  >3 = error
"""

import argparse
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

from anthropic import Anthropic

# Re-use Phase 1 helpers
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from news_aggregator import (  # type: ignore
    Article,
    CATEGORIES,
    GREEK_HOLIDAYS_FIXED,
    RATE_LIMIT_SECONDS,
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
    """Read enabled sources from DB. Returns list of dicts."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, name, url, type, category_hint
          FROM sources
         WHERE enabled = 1
         ORDER BY id
    """).fetchall()
    return [dict(r) for r in rows]


# -----------------------------------------------------------------------------
# Classify-only (Sprint 3.1, unchanged in 3.2)
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
    slugs = ["national", "international", "economy", "lifestyle", "auto", "sports"]
    return {"category": slugs[idx % len(slugs)], "confidence": 1.0}


# -----------------------------------------------------------------------------
# Insert (Sprint 3.2: source_id is now a parameter)
# -----------------------------------------------------------------------------

def insert_pending_minimal(
    db: sqlite3.Connection,
    *,
    fetch_date: str,
    art: Article,
    classified_slug: str,
    confidence: float,
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, 'pending', 'auto', ?)
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
# Multi-source fetch + round-robin interleave
# -----------------------------------------------------------------------------

def fetch_all_sources(sources: list[dict]) -> list[Article]:
    """Fetch each source's RSS, tag articles with _source_id, return interleaved list."""
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

    # Round-robin interleave so each source shares the MAX_CANDIDATES budget fairly
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
    parser = argparse.ArgumentParser(description="KTEO fetch_raw (Sprint 3.2)")
    parser.add_argument("--db", default="/opt/news_aggregator/news_cache.db")
    parser.add_argument("--dry-run", action="store_true",
                        help="Mock classify (no API calls), still inserts to DB")
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

    # [4] Anthropic client
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    claude_client = None
    if not args.dry_run:
        if not api_key:
            log.error("ANTHROPIC_API_KEY not set; aborting (use --dry-run for test)")
            db.close()
            return 2
        claude_client = Anthropic(api_key=api_key)

    # [5] Fetch all sources, interleave round-robin
    raw_articles = fetch_all_sources(sources)
    log.info("Total raw articles (interleaved): %d", len(raw_articles))

    if args.limit_feed:
        raw_articles = raw_articles[: args.limit_feed * len(sources)]
        log.info("Limited to %d (--limit-feed × sources)", len(raw_articles))

    # [6] Pre-filter
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

    # [7] Fetch + classify + insert
    inserted = 0
    failed_classification = 0
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
            source_id=src_id,
        ):
            inserted += 1
            per_source_inserts[src_id] = per_source_inserts.get(src_id, 0) + 1
        mark_seen(db, art)

    # [8] Expire prior days
    expired = expire_old_pendings(db, today)

    # [9] Summary
    log.info("=" * 60)
    log.info("fetch_raw run complete (Sprint 3.2)")
    log.info("  fetch_date            : %s", today)
    log.info("  sources active        : %d", len(sources))
    log.info("  candidates            : %d", len(candidates))
    log.info("  inserted (pending)    : %d", inserted)
    log.info("  classification failed : %d", failed_classification)
    log.info("  expired (prior days)  : %d", expired)
    log.info("-" * 60)
    log.info("Inserts per source:")
    for s in sources:
        cnt = per_source_inserts.get(s["id"], 0)
        log.info("  [%d] %-25s : %d", s["id"], s["name"], cnt)
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
