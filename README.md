# KTEO News System

Digital signage news platform for **Autovision KTEO**, delivering daily curated Greek news across 53 inspection-centre screens.

The system aggregates RSS news, classifies each item with Claude (Anthropic), routes selected items through a human curator, and publishes per-category XML feeds that drive a self-hosted browser widget displayed via Yodeck.

---

## Architecture overview

```
┌──────────────────┐   07:50 cron    ┌──────────────────┐
│  RSS sources     │ ──────────────► │  fetch_raw.py    │
│  (DB-driven)     │                 │  classify-only   │
└──────────────────┘                 └────────┬─────────┘
                                              │
                                     pending_curation
                                              │
                                              ▼
                              ┌──────────────────────────────┐
                              │  Streamlit curation app      │
                              │  curate.dronepros.gr         │
                              │  (Cloudflare Access auth)    │
                              └──────────────┬───────────────┘
                                             │ human selects
                                             ▼
                              ┌──────────────────────────────┐
                              │  publish_curated.py          │
                              │  Haiku summarize → XML write │
                              │  → carry_over → playlist_sync│
                              └──────────────┬───────────────┘
                                             │
                                             ▼
                              ┌──────────────────────────────┐
                              │  /var/www/html/*.xml         │
                              │  served via nginx +          │
                              │  Cloudflare to widget        │
                              └──────────────┬───────────────┘
                                             │
                                             ▼
                              ┌──────────────────────────────┐
                              │  Yodeck Web Pages (12 URLs)  │
                              │  → 53 in-venue screens       │
                              └──────────────────────────────┘
```

See [`docs/architecture.md`](docs/architecture.md) for a detailed walk-through.

---

## Repository layout

| Path | Purpose |
|---|---|
| [`backend/`](backend/) | Python services: aggregator, fetcher, publisher, Streamlit app |
| [`backend/pages/`](backend/pages/) | Streamlit multi-page screens (curation, sources, filters, etc.) |
| [`widget/`](widget/) | Browser news widget (HTML/CSS/JS) served via nginx |
| [`infra/`](infra/) | nginx vhosts, systemd unit, cloudflared config, crontabs |
| [`db/`](db/) | SQLite schema + migration history |
| [`docs/`](docs/) | Architecture, deployment, operations, troubleshooting |
| [`deploy/`](deploy/) | Deployment helpers and snapshot/restore scripts |
| [`.env.example`](.env.example) | Environment variable template |

---

## Quick start (existing deployment)

The system is **already deployed and operational** on a Raspberry Pi at `kteo-news.local`. For day-to-day operations, see [`docs/operations.md`](docs/operations.md).

For a from-scratch deployment to a new host, see [`docs/deployment.md`](docs/deployment.md).

---

## Tech stack

- **Python 3.11+** with `feedparser`, `anthropic`, `streamlit`, `requests`, `beautifulsoup4`
- **SQLite** (`/opt/news_aggregator/news_cache.db`) for deduplication + curation state
- **Claude Haiku** (`claude-haiku-4-5`) for category classification and summarization
- **nginx** as web server + reverse proxy for Streamlit
- **systemd** for the curation service (`kteo-curate.service`)
- **Cloudflare Tunnel** + **Cloudflare Access** for public exposure and auth
- **Yodeck** (digital signage SaaS) for screen orchestration

---

## Sprint history

The system evolved through six sprints over May 2026. Summary:

| Sprint | Delivered | Date |
|---|---|---|
| **Phase 1** | Fully automated baseline (newsbeast → XML → Yodeck) | Apr–May 2026 |
| **S1** | Curation data layer (schema + raw fetch) | 2026-05-13 |
| **S2** | Publish layer (Haiku summarize + XML write + Yodeck sync) | 2026-05-13 |
| **S3** | Streamlit curation app (6 pages) | 2026-05-13 |
| **S3.1** | Architectural fix: classify-only fetch, on-demand summarize | 2026-05-14 |
| **S3.2** | DB-driven sources (multi-source round-robin) | 2026-05-14 |
| **S4** | Edge deployment (nginx + Cloudflare Tunnel + Access) | 2026-05-14 |

Full detail in [`docs/sprint-history.md`](docs/sprint-history.md). Each sprint corresponds to one tagged commit in `git log`.

---

## License

Proprietary © 2026 Amazing Projects ΙΚΕ. See [`LICENSE`](LICENSE).

## Maintainer

**Lefteris** &lt;lefteris@amazingprojects.gr&gt; — Amazing Projects ΙΚΕ
