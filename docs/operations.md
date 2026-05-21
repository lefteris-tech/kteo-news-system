# Operations

Day-to-day operational reference for an already-deployed KTEO News System.

---

## Daily curator workflow

**Mon–Fri 08:00 onward:**

1. Open `https://curate.dronepros.gr` (sign in via email-OTP)
2. Page **Σημερινή Επιμέλεια** shows category tabs with new items from the 07:50 fetch
3. Click items to preview, select 1–3 per category
4. Optional: use **Χειροκίνητη Προσθήκη** for items not in the RSS feed
5. Click **Δημοσίευση** when satisfied — items appear on Yodeck screens within ~15 min

If a curator forgets to publish, the system simply doesn't update for the day —
the previous day's content remains on screens, supplemented by `carry_over.py`
from the rolling archive.

---

## Common admin tasks

### Add a new RSS source

Streamlit → **Πηγές** → "Προσθήκη πηγής" → enter name + URL → Save.
Active immediately for the next 07:50 fetch.

### Add a keyword filter

Streamlit → **Φίλτρα** → "Προσθήκη φίλτρου" → choose scope, mode (include/exclude),
keyword → Save. Filters apply at the next publish cycle.

### Add a new curator

Streamlit → **Ρυθμίσεις** → Users table. First login auto-provisions as curator;
to promote to admin, manually update the DB:

```bash
sqlite3 /opt/news_aggregator/news_cache.db \
  "UPDATE users SET role='admin' WHERE email='newperson@example.com';"
```

Also: ensure the email is on the Cloudflare Access allowlist for `curate.dronepros.gr`.

---

## Service management

```bash
# Streamlit service
sudo systemctl status   kteo-curate.service
sudo systemctl restart  kteo-curate.service
sudo journalctl -u kteo-curate.service -f

# Cloudflare Tunnel
sudo systemctl status   cloudflared
sudo journalctl -u cloudflared -f

# nginx
sudo systemctl status nginx
sudo nginx -t && sudo systemctl reload nginx
```

---

## Manual triggers

### Run fetch manually (e.g. on a holiday that wasn't in the skip list)

```bash
sudo -u pi /opt/news_aggregator/fetch_raw_cron.sh
tail -100 /var/log/news_aggregator.log
```

### Publish from CLI (bypassing the Streamlit UI)

```bash
cd /opt/news_aggregator
./venv/bin/python curate_cli.py list                     # show today's pending
./venv/bin/python curate_cli.py select 12 34 56          # select by ID
./venv/bin/python curate_cli.py publish                  # run the full publish chain
./venv/bin/python curate_cli.py status                   # category × status grid
```

### Run carry-over only (rare; usually called automatically by publish)

```bash
sudo -u pi /opt/news_aggregator/venv/bin/python \
  /opt/news_aggregator/carry_over.py /var/www/html
```

### Run playlist sync only (rare; usually called automatically by publish)

```bash
sudo -u pi /opt/news_aggregator/venv/bin/python \
  /opt/news_aggregator/playlist_sync.py
# Add --dry-run to preview without PATCHing Yodeck
```

---

## Database inspection

```bash
# Today's curation state
sqlite3 /opt/news_aggregator/news_cache.db <<'SQL'
SELECT classified_category, status, count(*)
FROM pending_curation
WHERE fetch_date = date('now')
GROUP BY classified_category, status
ORDER BY classified_category;
SQL

# Last 7 publish events
sqlite3 /opt/news_aggregator/news_cache.db \
  "SELECT publish_date, triggered_by, total_items, created_at \
   FROM publish_log ORDER BY created_at DESC LIMIT 7;"

# Backup the DB before any risky operation
cp /opt/news_aggregator/news_cache.db \
   /opt/news_aggregator/news_cache.db.bak.$(date +%Y%m%d_%H%M%S)
```

---

## Updating widget code

1. Edit files in `widget/` locally and commit to Git
2. `scp` updated files to `pi@kteo-news.local:/var/www/html/news/`
3. Bump `?v=N` to `?v=N+1` in `news.html` `<script>` tag
4. **Cloudflare dashboard → Caching → Configuration → Purge Everything**
5. Verify in a clean browser (or Yodeck preview) within ~5 min

---

## Updating backend code

1. Edit files in `backend/` locally and commit
2. `scp` changed files to `pi@kteo-news.local:/tmp/`
3. `sudo install -o pi -g pi -m 0755 /tmp/<file>.py /opt/news_aggregator/`
4. Syntax-check: `./venv/bin/python -c "import ast; ast.parse(open('/opt/news_aggregator/<file>.py').read())"`
5. If touching the Streamlit app: `sudo systemctl restart kteo-curate.service`
6. Verify with a test fetch or via the UI

For systemic changes, prefer working through a sprint-style deploy doc with a
documented rollback path — that's been the discipline that has kept the system
stable through six sprints.

---

## Monitoring & health

The system has no formal monitoring stack. Lightweight health probes:

```bash
# Curation app is up?
curl -fsS http://127.0.0.1:8501/_stcore/health   # should print "ok"

# Public widget endpoint serving fresh XML?
curl -sI https://kteo-news.dronepros.gr/national.xml | head -5

# Curation app reachable through Cloudflare?
curl -sI https://curate.dronepros.gr   # expect 302 to Access login page

# Today's fetch happened?
ls -la /var/log/news_aggregator.log
grep "$(date +%Y-%m-%d)" /var/log/news_aggregator.log | tail -20
```

If you need formal monitoring, the natural next step is a small probe script
in cron that reports to a status page or sends an email on failure.
