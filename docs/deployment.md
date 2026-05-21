# Deployment

This guide covers a from-scratch deployment of the KTEO News System to a new
Raspberry Pi (or compatible Debian/Ubuntu host).

For migrating an existing deployment to a new host, see `deploy/MIGRATION.md`
(separate document delivered with snapshot/restore scripts).

---

## Prerequisites

- **Host:** Raspberry Pi 4/5 with Pi OS, or any Debian 12 / Ubuntu 22.04+ host
- **Network:** Public hostname (we use `kteo-news.dronepros.gr`) routable via
  Cloudflare Tunnel
- **External services:**
  - Anthropic API account with an API key for `claude-haiku-4-5`
  - Yodeck account with API token and pre-configured playlists/layouts
  - Cloudflare account with a Tunnel and Access policies configured
- **Local tools:** `git`, `ssh`, `scp` on the deployer's workstation

---

## Step 1 — System packages

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip \
  nginx sqlite3 \
  cloudflared
```

---

## Step 2 — Clone the repository

```bash
sudo mkdir -p /opt/news_aggregator
sudo chown pi:pi /opt/news_aggregator
git clone git@github.com:Lefterisst/kteo-news-system.git /tmp/kteo-news-system

# Backend code
cp /tmp/kteo-news-system/backend/*.py /opt/news_aggregator/
cp -r /tmp/kteo-news-system/backend/pages /opt/news_aggregator/
cp /tmp/kteo-news-system/backend/*.sh /opt/news_aggregator/
cp /tmp/kteo-news-system/backend/requirements.txt /opt/news_aggregator/
chmod +x /opt/news_aggregator/*.sh

# Widget
sudo mkdir -p /var/www/html/news
sudo cp -r /tmp/kteo-news-system/widget/* /var/www/html/news/
sudo chown -R pi:www-data /var/www/html/news
```

---

## Step 3 — Python venv

```bash
cd /opt/news_aggregator
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
./venv/bin/pip install streamlit  # not in requirements.txt by default
```

---

## Step 4 — Environment file

```bash
sudo cp /tmp/kteo-news-system/.env.example /etc/news_aggregator.env
sudo chown pi:pi /etc/news_aggregator.env
sudo chmod 640 /etc/news_aggregator.env
sudo nano /etc/news_aggregator.env   # fill in real values
```

Required variables:
- `ANTHROPIC_API_KEY` — from console.anthropic.com
- `YODECK_API_TOKEN` — from Yodeck account settings → API
- `YODECK_PLAYLIST_A_ID` — ID of the "Set 1" playlist
- `YODECK_PLAYLIST_B_ID` — ID of the "Set 2" playlist

---

## Step 5 — Database initialization

```bash
sqlite3 /opt/news_aggregator/news_cache.db < /tmp/kteo-news-system/db/schema.sql
sudo chown pi:pi /opt/news_aggregator/news_cache.db

# Seed at least one source
sqlite3 /opt/news_aggregator/news_cache.db \
  "INSERT INTO sources (name, url, type, enabled) VALUES \
   ('Newsbeast', 'https://www.newsbeast.gr/feed', 'rss', 1);"

# Bootstrap your admin user
sqlite3 /opt/news_aggregator/news_cache.db \
  "INSERT INTO users (email, role) VALUES \
   ('lefteris@amazingprojects.gr', 'admin');"
```

---

## Step 6 — nginx

```bash
sudo cp /tmp/kteo-news-system/infra/nginx/kteo-news.conf /etc/nginx/sites-available/default
sudo cp /tmp/kteo-news-system/infra/nginx/curate.conf /etc/nginx/sites-available/curate
sudo ln -s /etc/nginx/sites-available/curate /etc/nginx/sites-enabled/curate
sudo nginx -t && sudo systemctl reload nginx
```

---

## Step 7 — systemd unit for Streamlit

```bash
sudo cp /tmp/kteo-news-system/infra/systemd/kteo-curate.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kteo-curate.service
sudo systemctl status kteo-curate.service
```

---

## Step 8 — Cloudflare Tunnel

Authenticate cloudflared and create the tunnel (one-time, on the new host):

```bash
sudo cloudflared tunnel login
sudo cloudflared tunnel create kteo-news
sudo cloudflared tunnel route dns <tunnel-id> kteo-news.dronepros.gr
sudo cloudflared tunnel route dns <tunnel-id> curate.dronepros.gr
```

Then install the config and run as a service:

```bash
sudo mkdir -p /etc/cloudflared
sudo cp /tmp/kteo-news-system/infra/cloudflared/config.yml /etc/cloudflared/config.yml
# Edit /etc/cloudflared/config.yml to point credentials-file at your new tunnel UUID's JSON
sudo cloudflared service install
sudo systemctl status cloudflared
```

Configure Cloudflare Access via the Zero Trust dashboard:
1. Create an Application of type "Self-hosted" for `curate.dronepros.gr`
2. Attach a policy with the email allowlist (curator + admin emails)
3. Set session duration appropriate to your security posture (24h works well)

---

## Step 9 — Crontab entries

```bash
# Pi user — Phase 2 fetch (active)
crontab -e
# Add: 50 7 * * 1-5 /opt/news_aggregator/fetch_raw_cron.sh >> /var/log/news_aggregator.log 2>&1

# Root — Phase 1 (commented out per Sprint 4; uncomment only for emergency rollback)
sudo crontab -e
# Reference: infra/cron/root-crontab.txt
```

---

## Step 10 — Verification

```bash
# Manually trigger fetch_raw to populate the curation queue
sudo -u pi /opt/news_aggregator/fetch_raw_cron.sh

# Verify items landed
sqlite3 /opt/news_aggregator/news_cache.db \
  "SELECT classified_category, count(*) FROM pending_curation \
   WHERE fetch_date=date('now') GROUP BY classified_category;"

# Open the curation app from an allowlisted browser
# → https://curate.dronepros.gr
```

If items appear in the UI and the publish round-trip works (item shows on a
Yodeck-connected screen within ~15 min of clicking Δημοσίευση), the deployment
is operational.

---

## Yodeck setup (one-time, in the Yodeck web console)

Create 12 Web Page media items pointing to:

```
https://kteo-news.dronepros.gr/news/news.html?cat=national&pos=1
https://kteo-news.dronepros.gr/news/news.html?cat=national&pos=2
https://kteo-news.dronepros.gr/news/news.html?cat=international&pos=1
https://kteo-news.dronepros.gr/news/news.html?cat=international&pos=2
... (6 categories × 2 positions = 12 URLs)
```

Set each Web Page with **Zoom Factor 75%** and **Auto-Adjust Zoom enabled**
(Yodeck's WebKit engine does not run the widget's auto-shrink JS reliably).

Build two playlists ("Set 1" with all pos=1, "Set 2" with all pos=2), 15 sec
per layout. Note the playlist IDs and add them to `/etc/news_aggregator.env`.

---

## Cache strategy (Cloudflare)

In the Cloudflare dashboard, configure two page rules for the
`kteo-news.dronepros.gr` zone:

| Pattern | Cache level | Reason |
|---|---|---|
| `*kteo-news.dronepros.gr/*.xml` | Bypass / no cache | Feeds must reflect publish events immediately |
| `*kteo-news.dronepros.gr/news/*` | Standard cache | HTML/JS/CSS rarely change |

After any widget code update, run **Purge Everything** in the dashboard and
bump the `?v=N` query string on the `widget.js` `<script>` tag in `news.html`.
