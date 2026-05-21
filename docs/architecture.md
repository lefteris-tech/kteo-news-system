# Architecture

This document describes the runtime architecture of the KTEO News System as it
exists today (post-Sprint 4). For the evolution from Phase 1 to the current
state, see [`sprint-history.md`](sprint-history.md).

---

## Components

### 1. Data sources (RSS feeds)

Active sources are stored in the `sources` table (managed via the Streamlit
**Πηγές** admin page). Each row holds a name, URL, and `enabled` flag. At fetch
time, `fetch_raw.py` iterates over all enabled sources and interleaves their
items round-robin, so a single noisy source cannot dominate the curator's queue.

`fetch_raw.py` fails fast (exit code 3) if zero sources are enabled — there is
no fallback to a hardcoded URL.

### 2. Classifier (Claude Haiku, classify-only)

For each candidate article, `fetch_raw.py` calls Claude Haiku (`claude-haiku-4-5`)
with a minimal prompt that returns only one of six category slugs:
`national`, `international`, `economy`, `lifestyle`, `auto`, `sports`.

No safety filtering happens at this stage — the human curator is the filter.
No summarization happens either, to avoid spending tokens on items that will be
rejected.

### 3. Curation queue (`pending_curation` table)

Each classified item lands here with `status='pending'`. The schema captures:

- Source provenance (`source_id`)
- Original metadata (title, body excerpt, full body, image URL, pub date, guid)
- Classifier output (`classified_category`, `haiku_confidence`)
- Curation state (`status`, `selected_by`, `selected_at`, `published_at`)
- Provenance type (`source_type` ∈ {`auto`, `manual`}) — manual rows come from the **Χειροκίνητη Προσθήκη** page

The `(fetch_date, guid)` unique constraint plus the `seen_articles` dedup cache
guarantee no duplicate ingestion.

### 4. Curation app (Streamlit)

Served by `kteo-curate.service` (systemd) on `127.0.0.1:8501`. Identity resolves
from the `Cf-Access-Authenticated-User-Email` header injected by Cloudflare
Access at the edge. First-time users auto-provision as `curator` unless the
bootstrap rule (`lefteris@amazingprojects.gr`) elevates them to `admin`.

Page-level role gating happens in `streamlit_app.py` via `st.navigation()` —
admin-only pages are filtered out of the sidebar for non-admins.

### 5. Publisher (`publish_curated.py`)

Triggered manually by a curator clicking **Δημοσίευση** in the Streamlit app
(which shells out to `publish_curated.py`). Pipeline:

1. Read all `status='selected'` rows for today
2. Run Haiku summarization for each row where `haiku_summary IS NULL`
3. Group by category, build atomic per-category XML files in `/var/www/html/`
4. Mark items `status='published'` and write a `publish_log` row
5. Subprocess: `carry_over.py` — supplements low-count categories from `*.archive.xml`
6. Subprocess: `playlist_sync.py` — PATCHes Yodeck playlists to remove layouts for empty categories

### 6. Carry-over (`carry_over.py`)

For each category, if today's XML has fewer than 3 fresh items, the script reads
the corresponding `*.archive.xml` and appends non-duplicate items younger than
7 days. Maintains rolling archive XML files. Critical for keeping screens
populated on slow news days.

### 7. Playlist sync (`playlist_sync.py`)

Yodeck has two playlists (Set 1 for `pos=1` items, Set 2 for `pos=2` items) of
six layouts each (one per category). This script PATCHes both playlists via the
Yodeck REST API, removing layouts for categories whose XML feed has zero items.
Prevents blank screens.

### 8. Widget (`widget/`)

Vanilla-JS browser widget (~12 KB total). Each Yodeck Web Page URL has the form:

```
https://kteo-news.dronepros.gr/news/news.html?cat=<slug>&pos=<1|2>
```

The widget fetches the corresponding `<slug>.xml` feed, picks item N (1 or 2),
and renders a horizontal layout (image 60% / text 40%) with auto-shrink-to-fit
typography. Cache-busting is via `?v=N` query param on the script tag.

### 9. Edge (nginx + Cloudflare)

Two public hostnames, both terminating at the Pi via a single Cloudflare Tunnel:

| Hostname | nginx vhost | Purpose | Auth |
|---|---|---|---|
| `kteo-news.dronepros.gr` | `nginx/kteo-news.conf` | Static widget + XML feeds for Yodeck | None (public read) |
| `curate.dronepros.gr` | `nginx/curate.conf` | Reverse proxy to Streamlit `:8501` | Cloudflare Access (email-OTP) |

XML feeds are served as dynamic (no Cloudflare cache); HTML/JS/CSS are
aggressively cached and require Cloudflare **Purge Everything** after widget
updates.

---

## Data flow timing

```
07:40  Mon–Sat   (DISABLED in Sprint 4 — kept commented in crontab_root.txt for rollback)
07:50  Mon–Fri   fetch_raw_cron.sh → fetch_raw.py → pending_curation
07:55 → 09:30    Curators review and select items via curate.dronepros.gr
09:30 → 10:00    Curator clicks Δημοσίευση → publish_curated.py runs end-to-end
10:00–24:00      Yodeck widgets pull fresh XML feeds on next 60-min refresh
```

Weekend / Greek-holiday handling is in `fetch_raw.py` (skips `weekday() >= 5`
and dates in the embedded holiday list).

---

## Key file responsibilities

| File | Owns |
|---|---|
| `backend/news_aggregator.py` | Phase 1 fully-automated pipeline (now disabled; kept for rollback) |
| `backend/carry_over.py` | Archive-supplement logic |
| `backend/playlist_sync.py` | Yodeck playlist orchestration |
| `backend/fetch_raw.py` | Phase 2 classify-only ingestion (Sprint 3.2: DB-driven sources) |
| `backend/publish_curated.py` | Phase 2 summarize + XML write + downstream triggers |
| `backend/curate_cli.py` | Headless CLI for ops (used during development; remains for ad-hoc use) |
| `backend/infuse_news.py` | Manual-injection helper, ported into `kteo_curate.py` |
| `backend/kteo_curate.py` | Shared module: DB connection, identity, CSS, publish trigger |
| `backend/streamlit_app.py` | Streamlit entry point + role-aware navigation |
| `backend/pages/*.py` | One file per Streamlit page (curation, manual, history, sources, filters, settings) |
| `backend/run_cron.sh` | Phase 1 wrapper (aggregator → carry_over → playlist_sync) — disabled in S4 |
| `backend/fetch_raw_cron.sh` | Phase 2 wrapper (fetch_raw only) — active |

---

## Security model

- **Public widget endpoint** has no auth — only static XML + HTML/CSS/JS, no PII
- **Curation app endpoint** is behind Cloudflare Access with an email allowlist
- **No API keys in code** — secrets live in `/etc/news_aggregator.env` (mode `640 pi:pi`), loaded by both cron wrappers and the systemd unit
- **Database has no internet exposure** — SQLite file accessible only on the Pi filesystem
- **Yodeck REST API token** scoped to playlist PATCH operations (no admin scope)
