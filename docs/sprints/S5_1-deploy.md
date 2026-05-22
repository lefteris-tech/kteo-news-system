# Sprint 5.1 — Source avatar pill — Deploy runbook

**Scope:** Adds a circular source-logo avatar to the left of the news widget's
timestamp on every item. Per-source registration captures the logo via a
multi-strategy fetch chain (Clearbit → HTML parse → Google favicon) and stores
the normalized 128×128 PNG under `/var/www/html/news/logos/`.

**Impact:** Schema gains one nullable column (`sources.logo_path`). No
behavioural change for items that have no associated source — they fall
back to the existing render path (no avatar). No new dependency on the
Anthropic API; the active spend-cap incident does **not** block this deploy.

**Tagged commit:** `v0.9-s5.1` (will be created after merge of
`feature/sprint-5.1-source-logos`).

---

## Step 1 — Backup the production DB

```bash
ssh pi@kteo-news.local
cd /opt/news_aggregator
BACKUP="news_cache.db.bak.s5.1.$(date +%Y%m%d_%H%M%S)"
cp -a news_cache.db "$BACKUP"
ls -lh "$BACKUP"
```

Note the printed `$BACKUP` path. SQLite cannot `DROP COLUMN` cleanly without
a table rebuild, so the rollback path is file-level restore from this backup.

---

## Step 2 — Pull the merged code

```bash
cd /opt/news_aggregator/repo     # or wherever your working checkout lives
git fetch origin
git checkout main
git pull origin main
git log --oneline -n 6           # confirm S5.1 commits are present
```

Confirm `backend/source_logo.py`, `backend/backfill_logos.py`, and
`db/migrations/S5_1-*` are now in place.

---

## Step 3 — Pre-flight idempotency check

```bash
already_applied=$(sqlite3 /opt/news_aggregator/news_cache.db \
  "SELECT COUNT(*) FROM pragma_table_info('sources') WHERE name='logo_path';")
echo "already_applied = $already_applied"
```

- `0` → run Step 4 (migration)
- `1` → migration already applied; skip to Step 5

---

## Step 4 — Apply migration

```bash
sqlite3 /opt/news_aggregator/news_cache.db \
  < /opt/news_aggregator/repo/db/migrations/S5_1-source_logo.sql
```

A duplicate-column error here is harmless — it means the migration was
already applied (re-confirm with Step 3).

---

## Step 5 — Install runtime deps + create logo dir

```bash
# Pillow is the only new Python dep (used by source_logo.py for normalization)
sudo /opt/news_aggregator/venv/bin/pip install --upgrade Pillow

# nginx serves /news/logos/ from this path; create with sane perms
sudo install -d -o pi -g www-data -m 0775 /var/www/html/news/logos
ls -la /var/www/html/news/logos
```

If `backend/source_logo.py` is not yet importable from the curation app's
search path, ensure the systemd unit's `WorkingDirectory` is the backend
directory (or that `PYTHONPATH` includes it). The Streamlit page imports
`source_logo` directly, so a quick test:

```bash
sudo -u pi /opt/news_aggregator/venv/bin/python3 -c \
  "import sys; sys.path.insert(0, '/opt/news_aggregator/repo/backend'); \
   from source_logo import fetch_logo; print('source_logo importable')"
```

---

## Step 6 — Validate the schema

```bash
sudo -u pi /opt/news_aggregator/venv/bin/python3 \
  /opt/news_aggregator/repo/db/migrations/S5_1-validate.py
```

Expected: three `✓` lines, exit code `0`.

---

## Step 7 — Backfill logos for existing sources

```bash
# Dry run first to see what will happen
sudo -u pi /opt/news_aggregator/venv/bin/python3 \
  /opt/news_aggregator/repo/backend/backfill_logos.py --dry-run

# Real run
sudo -u pi /opt/news_aggregator/venv/bin/python3 \
  /opt/news_aggregator/repo/backend/backfill_logos.py
```

Verify the PNGs landed and confirm at least one is non-empty:

```bash
ls -la /var/www/html/news/logos/
file /var/www/html/news/logos/*.png
```

---

## Step 8 — Deploy the new widget assets

The widget changes (`widget/css/style.css`, `widget/js/widget.js`,
`widget/news.html`) need to be installed at the nginx docroot.

```bash
sudo cp /opt/news_aggregator/repo/widget/css/style.css   /var/www/html/news/css/style.css
sudo cp /opt/news_aggregator/repo/widget/js/widget.js    /var/www/html/news/js/widget.js
sudo cp /opt/news_aggregator/repo/widget/news.html       /var/www/html/news/news.html
sudo chown -R www-data:www-data /var/www/html/news
```

Note that `news.html`'s cache-buster moves from `widget.js?v=5` → `widget.js?v=6`.
Without this bump, screens will continue to serve the old `widget.js` from
Cloudflare's edge cache and the avatar will not appear.

---

## Step 9 — Restart the curation app

The Streamlit Sources page was rewritten and imports `source_logo`. The
systemd unit must be restarted to pick up both:

```bash
sudo systemctl restart kteo-curate.service
sudo systemctl status kteo-curate.service --no-pager -l | head -12
```

---

## Step 10 — Cloudflare cache purge

Dashboard → `kteo-news.dronepros.gr` → **Caching → Configuration → Purge
Everything**.

XML feeds were already configured as no-cache so they propagate immediately.
The HTML and JS files are aggressively cached and need this manual purge to
deliver the new avatar code to all 53 screens.

---

## Step 11 — Smoke tests

```bash
# 1. Logo served by nginx?
curl -sI https://kteo-news.dronepros.gr/news/logos/newsbeast.png \
  | head -3
# Expect: HTTP/2 200, content-type: image/png

# 2. Custom elements present in the next published feed?
# Trigger a publish from the curation app (or wait for tomorrow's run),
# then:
curl -s https://kteo-news.dronepros.gr/national.xml | grep -c "kteo:source_name"
# Expect: >= 1 (one per item with a source_id)

# 3. Visual check — open in a browser:
#    https://kteo-news.dronepros.gr/news/news.html?cat=national&pos=1
# Expect: ~28px circular avatar to the left of the (hidden) timestamp slot,
#         hover tooltip = source name (e.g. "Newsbeast").
```

If items currently in `pending_curation` were classified **before** the
sources table had the matching source registered, the JOIN will return NULL
and those items will render with no avatar — that is the documented graceful
degradation. Future publishes will pick up the new source attribution.

---

## Step 12 — Sign-off

Send back:

1. Step 1 — the `$BACKUP` path printed
2. Step 3 — `already_applied` value (`0` before migration, `1` after)
3. Step 6 — three `✓` validator lines
4. Step 7 — `Updated=N Failed=0` from the backfill
5. Step 11 — HTTP 200 for the logo URL + ≥1 hit for the grep + screenshot
   confirming the circular avatar is visible on a real widget URL

When all green, tag `v0.9-s5.1` and publish the GitHub release.

---

## Rollback

| What broke | How to revert |
| --- | --- |
| Schema migration | `cp /opt/news_aggregator/news_cache.db.bak.s5.1.YYYYMMDD_HHMMSS /opt/news_aggregator/news_cache.db && sudo systemctl restart kteo-curate.service` |
| Streamlit page | `git checkout v0.8-s5.0 -- backend/pages/sources.py && sudo systemctl restart kteo-curate.service` |
| Widget | Revert `news.html` cache-buster to `?v=5` and Purge Everything on Cloudflare. The old `widget.js` (still in CF cache) takes over and the avatar disappears. |
| publish_curated emitting bad XML | `git checkout v0.8-s5.0 -- backend/publish_curated.py backend/news_aggregator.py` and re-trigger publish |
| Bad logo for a specific source | Delete `/var/www/html/news/logos/<slug>.png` and re-fetch from the Streamlit Πηγές page. Widget falls back to a colored-letter circle. |

---

## Operational notes

- **Logo refresh:** logos are fetched once at source registration and once
  per click of "🔄 Logo" in the admin page. There is no scheduled re-fetch.
  Greek news sites rarely change branding, and the curator can hit re-fetch
  anytime. The widget appends `?d=YYYY-MM-DD` to each logo URL, so an updated
  PNG on disk propagates to screens within 24h regardless of Cloudflare
  caching.
- **Missing logos:** widget falls back to a colored circle (Autovision orange)
  with the first letter of the source name. No empty space, no broken-image
  icon.
- **Manual injections** (`pending_curation.source_id IS NULL`): degrade
  cleanly — the kteo elements are simply omitted from the XML, and the
  widget renders no avatar for those items.
- **API cap incident:** Independent. `source_logo.py` uses Clearbit + HTML
  scraping + Google favicons — none touch Anthropic.
