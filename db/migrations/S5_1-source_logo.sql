-- =============================================================================
-- Sprint 5.1 — Source logo path on sources table
-- =============================================================================
-- Adds one nullable column to `sources` for the on-disk path of the
-- normalized PNG avatar (served via nginx at /news/logos/<slug>.png).
--
-- Idempotent by detection: deploy runbook checks for the column before
-- running this file. SQLite does not support "ADD COLUMN IF NOT EXISTS",
-- but a second run will error fast on duplicate-column without harming
-- data (a backup is taken anyway by the runbook).
--
-- Rollback: file-level restore of news_cache.db from the backup taken
-- in step 1 of the runbook. SQLite cannot DROP COLUMN cleanly without
-- a table rebuild.
-- =============================================================================

ALTER TABLE sources
    ADD COLUMN logo_path TEXT;       -- absolute path on disk, e.g.
                                     --   /var/www/html/news/logos/newsbeast.png
                                     -- NULL until backfill or first auto-fetch.
