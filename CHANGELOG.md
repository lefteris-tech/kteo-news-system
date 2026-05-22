# Changelog

All notable changes to this project are documented in this file. Each sprint
corresponds to a tagged release.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.9.0] — 2026-05-22 — v0.9-s5.2: Sprints 5.1 + 6 + 5.2 reconciliation

This release brings four sprints into main in one merge (`d3b585b`) after a brief reconciliation: Sprint 6 had been deployed directly to the Pi but never pushed to GitHub. The reconciliation restored S6 in the repo and re-applied S5.1 + S5.2 on top of it cleanly.

### Sprint 5.1 — Source avatar pill in news widget (Added)
- `backend/source_logo.py` — multi-strategy logo fetcher (Clearbit → HTML parse → Google favicon) with Pillow-based 128×128 RGBA PNG normalization
- `backend/backfill_logos.py` — one-off backfill for sources registered before S5.1
- `db/migrations/S5_1-source_logo.sql` — adds `sources.logo_path TEXT` (NULL-able)
- `db/migrations/S5_1-validate.py` — schema validator for the migration
- `docs/sprints/S5_1-deploy.md` — 12-step runbook with rollback per component
- `<kteo:source_name>` and `<kteo:source_logo>` custom-namespace elements in the per-category RSS XML (`xmlns:kteo="https://kteo-news.dronepros.gr/ns/1.0"` declared on `<rss>`)

### Sprint 5.1 — Changed
- `backend/pages/sources.py` — full rewrite: avatar column in the list, "Auto-fetch" + upload + clear + re-fetch buttons on the form and per row
- `backend/publish_curated.py` — `fetch_selected_rows` now LEFT-JOINs `sources`; `row_to_article` populates `Article.source_name` and `Article.source_logo_url`
- `backend/news_aggregator.py` — `Article` gains `source_name` and `source_logo_url`; `build_rss_xml` emits the new namespace + per-item source elements (only when populated)
- `backend/kteo_curate.py` — `add_source()` accepts `logo_path=None`
- `widget/css/style.css` — new `.source-avatar` (circle), `.source-avatar.no-logo` (colored-letter fallback)
- `widget/js/widget.js` — parses `kteo:source_*` from each item (namespace-agnostic via `getElementsByTagNameNS`), renders a circular avatar; rendering decoupled from `show_date` so the source pill shows even when timestamps are hidden
- `widget/news.html` — cache-buster bumped `widget.js?v=5` → `widget.js?v=6`
- `db/schema.sql` — consolidated to reflect the post-S5.1 state

### Sprint 6 — Human-controlled classification (Changed)
- **`backend/fetch_raw.py`** — removed the Claude Haiku classification step entirely. Articles are pulled from RSS, deduplicated, and inserted into `pending_curation` with `classified_category=NULL`. The classifier is no longer invoked at fetch time.
- **`backend/publish_curated.py`** — added pre-publish validation that refuses to write XML if any selected row has `classified_category=NULL` (exit code 4). Summarisation is now **fail-fast**: the first Haiku failure aborts the entire run (exit code 3), no fallback to truncated body text. Items already summarised in the failing run are persisted and skipped on retry.
- **`backend/pages/curation.py`** — major UI redesign. Removed the 6 category tabs. Single unified queue with per-row category dropdown ("— Διάλεξε Κατηγορία —" placeholder). The Επιλογή checkbox is disabled until a category is chosen. Clearing the category on a selected row auto-deselects.
- **`backend/kteo_curate.py`** — added `set_category()` helper.
- `docs/sprints/S6-deploy.md` — Sprint 6 deploy runbook with API-cost comparison table

### Sprint 5.2 — Curator UI source pill + typography (Changed)
- `backend/kteo_curate.py` — `get_pending_items()` now LEFT-JOINs `sources` to surface source name + logo_path on every pending row
- `backend/pages/curation.py` — source avatar pill rendered on each article card with 4 fallback paths (manual → ✋, registered+logo → 24px circle, registered no-logo → colored-letter circle, orphan → no pill)
- `backend/kteo_curate.py` — CSS bumps across the curator UI: title 17→19px, snippet 14.5→16px, meta 13→14.5px, buttons/tabs +1px; new `.av-source-pill` styles

### Deploy infrastructure
- `deploy/s5.1-on-s6-deploy.sh` — superseded by s5.2
- `deploy/s5.2-full-deploy.sh` — self-contained deploy script that fetches all 4 reconciled files from a tagged ref, backs up, validates 13 features, compiles, installs, restarts service, dry-runs publish, verifies HTTP 200

### Operational impact
- Anthropic API outages no longer break the curator queue — `fetch_raw` makes zero API calls
- Per-day API spend dropped from `(fetch + publish)` ~40–55 calls to `publish only` ~5–15 calls
- Curator picks the category explicitly — no classifier errors reach production
- Source attribution visible both on TV screens (widget) and in the curator queue

### Notes
- Sprint 6 work was originally on `feature/sprint-6-human-classification`; reconciliation captured it into the main line history via `d3b585b`
- Avatar logos for currently registered sources: `newsbeast.png` (id=1), `news.png` (id=15)
- Widget v=6 is backward-compatible: items without `<kteo:source_*>` elements (e.g. carry-over from before S5.1) render with no avatar, no error
- No Anthropic API calls anywhere in S5.1 or S5.2; deploys remained unaffected by the 2026-05-21 spend-cap incident

---

## [0.8.0] — 2026-05-21 — Sprint 5.0: Adaptive pre-filtering schema preparation

### Added
- `db/migrations/S5_0-prefiltering_schema.sql` — adds `auto_filter_rule_id` to `pending_curation` (FK → `filters.id`) and a partial index for analytics queries
- `db/migrations/S5_0-validate.py` — standalone validator for the migration
- `docs/sprints/S5_0-deploy.md` — runbook with backup, apply, validate, rollback steps
- `'auto_filtered'` is now a recognized value of `pending_curation.status` (convention; no DDL)

### Changed
- `db/schema.sql` — consolidated to reflect the post-S5.0 state, with section comments per sprint origin

### Notes
- Pure groundwork sprint. No application-code behaviour change in this release — the new column will remain `NULL` until Sprint 5.3 wires up the runtime filter.
- Migration deployed manually on the Pi per the deploy runbook; no service restart required.

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
