# Changelog

All notable changes to this project are documented in this file. Each sprint
corresponds to a tagged release.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.7.0] — 2026-05-14 — Sprint 4: Edge deployment

### Added
- nginx server block for `curate.dronepros.gr` with WebSocket proxy to Streamlit
- Cloudflare Tunnel ingress rule for the curation app
- Cloudflare Access policy (email-OTP allowlist for the marketing team)

### Changed
- Disabled the Phase 1 root cron at 07:40 (commented out, not deleted)

### Result
- System operates Phase 2 only: `fetch_raw` 07:50 Mon–Fri + human curation via the public URL

---

## [0.6.0] — 2026-05-14 — Sprint 3.2: DB-driven sources

### Changed
- `fetch_raw.py` now reads RSS sources from the `sources` table instead of a hardcoded constant
- Multiple sources are interleaved round-robin to share the `MAX_CANDIDATES` budget fairly
- Each `pending_curation` row now records its `source_id`
- Fail-fast: exits with code 3 if no enabled source exists (no silent fallback)

---

## [0.5.0] — 2026-05-14 — Sprint 3.1: Architectural fix

### Changed
- `fetch_raw.py` rewritten to **classify-only** (no safety filter, no summary)
- `publish_curated.py` now runs Claude Haiku summarization on-demand for selected items only

### Fixed
- Sprint 1's safety filter was rejecting ~100% of normal news (heavy-content bias for fully-automated Phase 1)
- Initial `unicode_escape` decode in summary path was corrupting Greek UTF-8 to mojibake; replaced with `json.loads`

---

## [0.4.0] — 2026-05-13 — Sprint 3: Streamlit curation app

### Added
- Multi-page Streamlit app using `st.navigation()`
  - Σημερινή Επιμέλεια (curator)
  - Χειροκίνητη Προσθήκη (curator)
  - Ιστορικό (curator + admin)
  - Πηγές (admin)
  - Φίλτρα (admin)
  - Ρυθμίσεις (admin)
- `systemd` unit `kteo-curate.service` running on `127.0.0.1:8501`
- Identity resolution from Cloudflare Access JWT header with dev-mode fallback
- Custom dark theme CSS matching the widget palette (`#ff5722` accent, `#0d1117` background)

---

## [0.3.0] — 2026-05-13 — Sprint 2: Publish layer

### Added
- `publish_curated.py` — reads `status='selected'` items, summarizes with Haiku, writes per-category XML, triggers `carry_over.py` + `playlist_sync.py`
- `curate_cli.py` — CLI for `list`, `select`, `publish`, `status` operations (used during development; remains available for ops)
- `publish_log` table writes for audit trail

---

## [0.2.0] — 2026-05-13 — Sprint 1: Data layer

### Added
- 5 new SQLite tables: `sources`, `filters`, `pending_curation`, `users`, `publish_log`
- `fetch_raw.py` (v1: classify + safety + summarize in one Haiku call)
- `fetch_raw_cron.sh` wrapper for the `pi` user crontab (07:50 Mon–Fri)
- Migration script `db/migrations/S1-schema_migration.sql` (idempotent)

### Note
- Phase 1 root cron at 07:40 remained active in parallel during S1–S3 for safety

---

## [0.1.0] — 2026-05-01 — Phase 1: Fully automated baseline

### Added
- `news_aggregator.py` — RSS fetch from `newsbeast.gr/feed`, classify with Haiku, dedupe via SQLite, write 6 per-category XML feeds
- `carry_over.py` — post-processor that supplements low-count categories from `*.archive.xml`
- `playlist_sync.py` — Yodeck REST API integration, manages 2 playlists × 6 layouts
- `run_cron.sh` — chains aggregator → carry_over → playlist_sync
- Browser widget (`widget/`) — vanilla JS, ~12KB, auto-shrink, served as Yodeck Web Pages
- nginx default vhost serving `kteo-news.dronepros.gr`
- Cloudflare Tunnel ingress for `kteo-news.dronepros.gr`

### Operational outcome
- 53 sites in active deployment, automated 6-category news cycle, ~4 min per loop
