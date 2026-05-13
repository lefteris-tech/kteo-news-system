-- =============================================================
-- S1-schema_migration.sql
-- KTEO Curation Platform — Sprint 1
-- Sprint: 1, version: 1, generated: 2026-05-13
-- =============================================================
-- Adds 5 new tables to /opt/news_aggregator/news_cache.db.
-- Existing 'seen_articles' table is NOT touched.
-- All statements use IF NOT EXISTS -> safe to re-run.
-- =============================================================

-- ---------- sources ----------
CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    url             TEXT    NOT NULL UNIQUE,
    type            TEXT    NOT NULL DEFAULT 'rss',     -- rss | atom
    enabled         INTEGER NOT NULL DEFAULT 1,
    category_hint   TEXT,                                -- optional pre-bias
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ---------- filters ----------
CREATE TABLE IF NOT EXISTS filters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope           TEXT    NOT NULL,                    -- global | category
    category        TEXT,                                -- NULL when scope=global
    mode            TEXT    NOT NULL,                    -- include | exclude
    keyword         TEXT    NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ---------- pending_curation ----------
CREATE TABLE IF NOT EXISTS pending_curation (
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
    safety_passed       INTEGER,                         -- 0 | 1
    haiku_confidence    REAL,
    haiku_summary       TEXT,                            -- the Greek 3-sentence summary
    status              TEXT    NOT NULL DEFAULT 'pending',  -- pending|selected|rejected|published|expired
    source_type         TEXT    NOT NULL DEFAULT 'auto',     -- auto | manual
    selected_by         TEXT,                            -- email from Cloudflare Access header
    selected_at         TEXT,
    published_at        TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_id) REFERENCES sources(id),
    UNIQUE (fetch_date, guid)
);

CREATE INDEX IF NOT EXISTS idx_pending_date_cat_status
    ON pending_curation(fetch_date, classified_category, status);

CREATE INDEX IF NOT EXISTS idx_pending_status
    ON pending_curation(status);

-- ---------- users ----------
CREATE TABLE IF NOT EXISTS users (
    email           TEXT PRIMARY KEY,                    -- from Cf-Access-Authenticated-User-Email
    role            TEXT NOT NULL DEFAULT 'curator',     -- admin | curator
    enabled         INTEGER NOT NULL DEFAULT 1,
    first_seen      TEXT NOT NULL DEFAULT (datetime('now')),
    last_login      TEXT
);

-- ---------- publish_log ----------
CREATE TABLE IF NOT EXISTS publish_log (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    publish_date            TEXT NOT NULL,               -- yyyy-mm-dd
    triggered_by            TEXT NOT NULL,               -- user email
    items_per_category_json TEXT,                        -- {"national":[12,45,67], ...}
    total_items             INTEGER,
    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_publog_date
    ON publish_log(publish_date);

-- =============================================================
-- End of S1 schema migration
-- =============================================================
