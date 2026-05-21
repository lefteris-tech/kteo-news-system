# Troubleshooting

Common failure modes and their fixes, ordered roughly by frequency.

---

## Screens show stale content

### Diagnosis
1. Check whether today's fetch produced items:
   ```bash
   sqlite3 /opt/news_aggregator/news_cache.db \
     "SELECT count(*) FROM pending_curation WHERE fetch_date=date('now');"
   ```
2. Check whether anyone published today:
   ```bash
   sqlite3 /opt/news_aggregator/news_cache.db \
     "SELECT * FROM publish_log WHERE publish_date=date('now');"
   ```

### Resolution
- **No items fetched** → see "fetch_raw fails" below
- **Items fetched but no publish_log row** → curator did not publish; this is normal if it's still morning. By design, `carry_over.py` will supplement old archives onto today's XML if no new publishes happen.
- **publish_log row exists but screens stale** → Cloudflare cache. Purge Everything in the dashboard.

---

## fetch_raw fails

### Diagnosis
```bash
tail -100 /var/log/news_aggregator.log
```

Common error patterns:

| Log message | Cause | Fix |
|---|---|---|
| `No enabled sources found` | All sources disabled in DB | Streamlit → Πηγές → enable at least one |
| `RSS returned 0 articles` | Source feed temporarily down | Wait or switch source |
| `anthropic.APIError` | Bad/expired API key | Update `/etc/news_aggregator.env` |
| `sqlite3.OperationalError: no such column` | DB schema drift | Apply migrations from `db/migrations/` |

### Manual re-run
```bash
sudo -u pi /opt/news_aggregator/fetch_raw_cron.sh
```

---

## Publish fails

### Diagnosis
```bash
sudo journalctl -u kteo-curate.service --since "10 min ago"
# Or run from CLI for verbose output:
cd /opt/news_aggregator
./venv/bin/python curate_cli.py publish --verbose
```

### Common failures

**`anthropic.APIError` during summarization** → bad key or rate limit. Retry, or
edit the row to provide a manual summary and re-publish.

**`PermissionError` writing `/var/www/html/*.xml`** → file ownership drift. Fix:
```bash
sudo chown pi:www-data /var/www/html/*.xml
sudo chmod 644 /var/www/html/*.xml
```

**`playlist_sync.py` returns non-200 from Yodeck** → token expired or playlist
ID changed. Verify `/etc/news_aggregator.env` values match Yodeck dashboard.
Use `--dry-run` to test without side effects.

---

## Cannot access curate.dronepros.gr

### Diagnosis order

1. **DNS/Tunnel**: `dig curate.dronepros.gr` should return Cloudflare IPs
2. **Tunnel up**: `sudo systemctl status cloudflared` should be `active`
3. **nginx up**: `curl -fsS http://127.0.0.1/_stcore/health` from the Pi itself
4. **Streamlit up**: `curl -fsS http://127.0.0.1:8501/_stcore/health` from the Pi
5. **Cloudflare Access**: check the application policy includes your email

### Common fixes

```bash
# Streamlit service crashed
sudo systemctl restart kteo-curate.service

# nginx config error after edit
sudo nginx -t            # shows the error
sudo systemctl reload nginx

# Tunnel hung
sudo systemctl restart cloudflared
```

If you see a Cloudflare Access "Forbidden" page, the email isn't on the
allowlist — update the Access policy in Cloudflare Zero Trust.

---

## Widget shows "No items available"

### Cause
The XML feed for that category/position has fewer items than the position
requested (e.g. only 1 item but URL asks for `pos=2`).

### Resolution
This is normal on slow news days. `carry_over.py` will supplement from archives
on the next publish. If you need an immediate fix, manually inject an item via
the **Χειροκίνητη Προσθήκη** Streamlit page and republish.

---

## DB corruption / lost state

### Last-resort recovery

The DB is rebuildable. SQLite supports `.dump` and `.read` for export/import:

```bash
# Export from a working backup
sqlite3 news_cache.db.bak.YYYYMMDD ".dump" > /tmp/dump.sql

# Re-create
rm /opt/news_aggregator/news_cache.db
sqlite3 /opt/news_aggregator/news_cache.db < /tmp/dump.sql
```

If no backup exists, recreate the schema and accept the loss of dedup history:

```bash
sqlite3 /opt/news_aggregator/news_cache.db < /path/to/repo/db/schema.sql
# Re-seed sources and users via Streamlit or direct SQL
```

The system tolerates a fresh DB — within one day of operation it rebuilds the
dedup cache and curation queue.

---

## Yodeck screens show blank between transitions

### Cause
A layout is pointing to a category whose XML feed has zero items. `playlist_sync.py`
should auto-remove these layouts but may have failed silently if the API token
is invalid.

### Verification
```bash
sudo -u pi /opt/news_aggregator/venv/bin/python \
  /opt/news_aggregator/playlist_sync.py --dry-run
```

If the dry-run shows the API call would succeed, run for real (without
`--dry-run`). If it fails on auth, refresh the token.

---

## Greek characters render as mojibake

### Symptoms
Output looks like `Î Î¿Î»Î¹Ï„Î¹ÎºÎ®` instead of `Πολιτική`.

### Cause
This was a real bug fixed in Sprint 3.1 — initial summarization code used
`unicode_escape` decoding which corrupts Greek UTF-8. The fix is in
`publish_curated.py` (uses `json.loads`).

### If it reappears
Verify:
- Cron wrapper exports `LANG=C.UTF-8`, `LC_ALL=C.UTF-8`, `PYTHONIOENCODING=utf-8`
- The systemd unit has `Environment=PYTHONIOENCODING=utf-8`
- XML files have `<?xml version="1.0" encoding="UTF-8"?>` declaration

---

## Rollback to Phase 1 (fully automated, no human curation)

In an emergency where curation isn't happening and screens are stale:

```bash
# Re-enable the Phase 1 root cron
sudo crontab -e
# Uncomment: 40 7 * * 1-6 /opt/news_aggregator/run_cron.sh >> /var/log/news_aggregator.log 2>&1

# Optionally disable Phase 2
crontab -e -u pi
# Comment out the fetch_raw_cron.sh line

# Manually trigger Phase 1 once to refresh screens immediately
sudo /opt/news_aggregator/run_cron.sh
```

Phase 1 will keep screens fresh but won't filter for editorial sensitivity.
Restore Phase 2 as soon as the curation issue is resolved.
