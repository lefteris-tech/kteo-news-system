-- Phase 1 schema — single dedup table
CREATE TABLE seen_articles (
    link            TEXT PRIMARY KEY,
    title           TEXT,
    category        TEXT,
    published_iso   TEXT,
    seen_at_iso     TEXT
);
