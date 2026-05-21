# Sprint 5.0 — Schema Migration Deploy

**Sprint:** 5.0 — Adaptive Pre-Filtering: Schema preparation
**Status:** Ready for deploy
**Risk:** Low — pure additive schema change, fully online, no service restart required
**Estimated time:** ~5 minutes

---

## What this sprint delivers

A minimal schema change that lets us start collecting structured data about
auto-filter decisions immediately, without blocking on later Sprint 5 sub-stories
(5.1 analysis, 5.2 admin UI, 5.3 runtime application).

**Concrete changes to `news_cache.db`:**

1. New nullable column: `pending_curation.auto_filter_rule_id INTEGER REFERENCES filters(id)`
2. New partial index: `idx_pending_auto_filter` on the column above, filtered to non-null rows
3. New status convention (no DDL): `pending_curation.status` may take the value `'auto_filtered'`

No application code changes in this sprint — the new column will be `NULL` for
all rows until Sprint 5.3 wires up the runtime filter. This sprint is pure
groundwork.

---

## Why deploy this now, ahead of 5.1–5.4

The Phase 2 system populates `pending_curation` every weekday morning. Each day
we delay this schema change, we lose another day of cleanly-structured data
that the Sprint 5.1 analysis script will eventually consume. Applying the
schema today means the data collection window starts immediately and is
already-formed by the time we build the analysis layer in ~30 days.

---

## Step 1 — Backup the database `[on PI]`

The migration is reversible only via backup restore (SQLite does not support
`ALTER TABLE DROP COLUMN` cleanly). Take the backup before anything else.

```bash
TS=$(date +%Y%m%d_%H%M%S)
BACKUP=/opt/news_aggregator/news_cache.db.bak.s5.0.$TS
sudo cp /opt/news_aggregator/news_cache.db "$BACKUP"
sudo chown pi:pi "$BACKUP"
ls -lh "$BACKUP"
echo "Backup OK: $BACKUP"
```

**Expected:** the file appears in the listing with a non-zero size. Keep the
exact `$BACKUP` path — Step 5 (rollback) and Step 4 (validation cross-check)
both reference it.

---

## Step 2 — Pre-flight check `[on PI]`

Confirm we're applying the migration against the expected schema:

```bash
sqlite3 /opt/news_aggregator/news_cache.db <<'SQL'
.headers on
.mode column
-- The column must NOT exist before migration
SELECT count(*) AS already_applied
  FROM pragma_table_info('pending_curation')
 WHERE name = 'auto_filter_rule_id';
SQL
```

**Expected:** `already_applied = 0`. If you see `1`, the migration is already
applied — skip to Step 4 (validation).

---

## Step 3 — Apply the migration `[on PI]`

Copy the migration SQL to the Pi (assumes the repo is checked out somewhere
reachable; adapt the path as needed):

```bash
# Option A — from a checkout on the Pi itself
sqlite3 /opt/news_aggregator/news_cache.db \
    < /path/to/repo/db/migrations/S5_0-prefiltering_schema.sql

# Option B — via scp from the desktop
# [on Desktop] scp db/migrations/S5_0-prefiltering_schema.sql pi@kteo-news.local:/tmp/
# [on PI]      sqlite3 /opt/news_aggregator/news_cache.db < /tmp/S5_0-prefiltering_schema.sql
```

**Expected:** silent exit with code 0. No output means success.

If you see `duplicate column name: auto_filter_rule_id`, the migration was
already applied (perhaps in a previous attempt). This is harmless; proceed to
validation.

---

## Step 4 — Validation `[on PI]`

Run the validation script that ships with this sprint:

```bash
/opt/news_aggregator/venv/bin/python /path/to/repo/db/migrations/S5_0-validate.py
```

**Expected output:**

```
✓ pending_curation.auto_filter_rule_id exists
✓ idx_pending_auto_filter exists
✓ Sprint 5.0 schema migration verified
```

Manual cross-check (optional but recommended once):

```bash
sqlite3 /opt/news_aggregator/news_cache.db <<'SQL'
.schema pending_curation
SELECT name, partial FROM sqlite_master
 WHERE type='index' AND tbl_name='pending_curation';
SQL
```

You should see:
- The new column `auto_filter_rule_id INTEGER REFERENCES filters(id)` in the table definition
- The index `idx_pending_auto_filter` with `partial = 1`

Also verify Phase 2 cron is unaffected — `fetch_raw.py` does not touch the new
column, so it should continue running normally:

```bash
sudo -u pi /opt/news_aggregator/fetch_raw_cron.sh
tail -50 /var/log/news_aggregator.log
```

**Expected:** the same output as before the migration. The new column will
populate as `NULL` for these fresh rows (intentional — Sprint 5.3 will start
writing non-NULL values).

---

## Step 5 — Rollback procedure (only if Step 4 fails)

```bash
# 1. Stop any writers that might be in flight
sudo systemctl stop kteo-curate.service

# 2. Confirm nothing is holding the DB open
sudo lsof /opt/news_aggregator/news_cache.db   # should be empty

# 3. Restore from backup taken in Step 1
sudo cp "$BACKUP" /opt/news_aggregator/news_cache.db
sudo chown pi:pi /opt/news_aggregator/news_cache.db

# 4. Restart curation service
sudo systemctl start kteo-curate.service
sudo systemctl status kteo-curate.service
```

The rollback is a file-level restore. Any curator activity between Step 1 and
rollback time is lost — keep that window short (minutes, not hours).

---

## Step 6 — Sign-off

Send back:

1. Step 1 — the `$BACKUP` path printed
2. Step 2 — `already_applied = 0`
3. Step 3 — clean exit (or the duplicate-column message if pre-applied)
4. Step 4 — three ✓ lines from `S5_0-validate.py`
5. Step 4 — output of the `.schema pending_curation` cross-check
6. Step 4 — confirmation that the next `fetch_raw_cron.sh` run completed normally

When all green, merge the PR into `main` and tag `v0.8-s5.0`.

---

## What comes after this sprint

Sprint 5.0 is groundwork only — no behavioural change. The follow-on sub-stories
need ~30 days of curator-decision data before they can produce useful results:

- **Sprint 5.1** — `analyze_decisions.py` offline analysis that surfaces
  filter suggestions (keyword, source, regex, classifier-mismatch)
- **Sprint 5.2** — Admin Streamlit page "AI Suggestions" for review/approve/edit
- **Sprint 5.3** — Apply approved filters at `fetch_raw.py` time (start writing
  to `auto_filter_rule_id` and using `status='auto_filtered'`)
- **Sprint 5.4** — Auto-filtered Inbox page (admin reversibility) and blind
  sampling safety net

Each is its own sprint; see `docs/sprint-history.md` for the running narrative.
