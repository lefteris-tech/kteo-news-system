-- =============================================================================
-- S5_0-prefiltering_schema.sql
-- Sprint 5.0 — Adaptive Pre-Filtering: Schema preparation
-- Sprint: 5.0, version: 1, generated: 2026-05-21
-- =============================================================================
--
-- PURPOSE
-- -------
-- Prepare the schema to support adaptive pre-filtering at fetch time (full
-- design in docs/sprints/S5_0-deploy.md and docs/sprint-history.md).
--
-- This is the minimum-viable schema change that lets us start collecting
-- structured data about auto-filter decisions immediately, without blocking
-- on the analysis layer (5.1) or the runtime filter application (5.3).
--
-- CHANGES
-- -------
-- 1. New column: pending_curation.auto_filter_rule_id (nullable, FK→filters.id)
--    Identifies which `filters` row caused an item to be auto-filtered.
--    NULL means the item was not auto-filtered.
--
-- 2. New partial index: idx_pending_auto_filter
--    For fast lookups of "what did this rule kill" queries in Sprint 5.1
--    analysis. Partial (WHERE NOT NULL) keeps it tiny.
--
-- 3. New convention (no DDL): pending_curation.status may now take the value
--    'auto_filtered' in addition to the existing values:
--      pending | selected | rejected | published | expired
--    Items with status='auto_filtered' are visible to admins only, do not
--    appear in the curator queue, and are kept in the DB for audit purposes.
--
-- IDEMPOTENCY
-- -----------
-- SQLite does NOT support ALTER TABLE ADD COLUMN IF NOT EXISTS, so the
-- ALTER below is non-idempotent: re-running this script after success will
-- error with "duplicate column name: auto_filter_rule_id". This is harmless
-- and serves as a clear "already applied" signal.
--
-- The CREATE INDEX uses IF NOT EXISTS and is fully idempotent.
--
-- ROLLBACK
-- --------
-- See docs/sprints/S5_0-deploy.md for the recommended rollback procedure
-- (restore the pre-migration backup). SQLite does not support dropping
-- columns directly until version 3.35, and even then it requires a
-- table-rebuild. The cleanest rollback is a backup restore.
-- =============================================================================

BEGIN TRANSACTION;

ALTER TABLE pending_curation
    ADD COLUMN auto_filter_rule_id INTEGER REFERENCES filters(id);

CREATE INDEX IF NOT EXISTS idx_pending_auto_filter
    ON pending_curation(auto_filter_rule_id)
    WHERE auto_filter_rule_id IS NOT NULL;

COMMIT;

-- =============================================================================
-- End of S5_0 schema migration
-- =============================================================================
