-- =============================================================================
-- schema.sql — Consolidated current schema for KTEO News System
-- Generated: 2026-05-22 (post Sprint 5.1)
-- =============================================================================
--
-- Apply this to a fresh news_cache.db to get the current production schema.
-- For incremental migrations on an existing DB, see db/migrations/.
--
-- Schema evolution:
--   Phase 1  — seen_articles
--   S1        — sources, filters, pending_curation, users, publish_log
--   S5.0      — pending_curation.auto_filter_rule_id + index
--   S5.1      — sources.logo_path (avatar pill in widget)
-- =============================================================================

-- Phase 1: deduplication cache (newsbeast.gr/feed dedup)
CREATE TABLE seen_articles (
    link            TEXT PRIMARY KEY,
    title           TEXT,
    category        TEXT,
    published_iso   TEXT,
    seen_at_iso     TEXT
);

-- S1: RSS source registry (managed via Streamlit Πηγές page)
-- S5.1: added logo_path
CREATE TABLE sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    url             TEXT    NOT NULL UNIQUE,
    type            TEXT    NOT NULL DEFAULT 'rss',     -- rss | atom
    enabled         INTEGER NOT NULL DEFAULT 1,
    category_hint   TEXT,                                -- optional pre-bias
    logo_path       TEXT,                                -- /var/www/html/news/logos/<slug>.png
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- S1: Filter rules (managed via Streamlit Φίλτρα page)
CREATE TABLE filters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope           TEXT    NOT NULL,                    -- global | category
    category        TEXT,                                -- NULL when scope=global
    mode            TEXT    NOT NULL,                    -- include | exclude
    keyword         TEXT    NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- S1: Curation queue (the heart of the human-in-the-loop workflow)
-- S5.0: added auto_filter_rule_id for tracing auto-filtered items
CREATE TABLE pending_curation (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_date          TEXT    NOT NULL,                -- yyyy-mm-dd
    source_id           INTEGER,                         -- NULL for manual injections
    guid                TEXT    NOT NULL,                -- article URL, or manual GUID
    title               TEXT    NOT NULL,
    body_first_para     TEXT,                            -- ~500 chars for the picker UI
    body_full           TEXT,                            -- up to 4000 chars
    image_url           TEXT,
    pub_date            TEXT,
    classified_category TEXT,                            -- national/international/economy/lifestyle/auto/sports
    safety_passed       INTEGER,                         -- 0 | 1 (legacy from S1, unused after S3.1)
    haiku_confidence    REAL,
    haiku_summary       TEXT,                            -- the Greek 3-sentence summary
    -- Status values: pending | selected | rejected | published | expired | auto_filtered
    -- auto_filtered (S5.0): the item was blocked by an active filter at fetch time
    --                      and never reached the curator queue. Visible only to admins.
    status              TEXT    NOT NULL DEFAULT 'pending',
    source_type         TEXT    NOT NULL DEFAULT 'auto',     -- auto | manual
    selected_by         TEXT,                            -- email from Cloudflare Access header
    selected_at         TEXT,
    published_at        TEXT,
    -- S5.0: which filter (if any) caused this item to be auto_filtered. NULL otherwise.
    auto_filter_rule_id INTEGER REFERENCES filters(id),
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_id) REFERENCES sources(id),
    UNIQUE (fetch_date, guid)
);

CREATE INDEX idx_pending_date_cat_status
    ON pending_curation(fetch_date, classified_category, status);

CREATE INDEX idx_pending_status
    ON pending_curation(status);

-- S5.0: partial index for analytics queries ("what did rule X kill?")
CREATE INDEX idx_pending_auto_filter
    ON pending_curation(auto_filter_rule_id)
    WHERE auto_filter_rule_id IS NOT NULL;

-- S1: User registry (auto-provisioned on first Cloudflare Access login)
CREATE TABLE users (
    email           TEXT PRIMARY KEY,                    -- from Cf-Access-Authenticated-User-Email
    role            TEXT NOT NULL DEFAULT 'curator',     -- admin | curator
    enabled         INTEGER NOT NULL DEFAULT 1,
    first_seen      TEXT NOT NULL DEFAULT (datetime('now')),
    last_login      TEXT
);

-- S1: Audit log of publish events
CREATE TABLE publish_log (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    publish_date            TEXT NOT NULL,               -- yyyy-mm-dd
    triggered_by            TEXT NOT NULL,               -- user email
    items_per_category_json TEXT,                        -- {"national":[12,45,67], ...}
    total_items             INTEGER,
    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_publog_date
    ON publish_log(publish_date);

-- =============================================================================
-- End of consolidated schema
-- =============================================================================
