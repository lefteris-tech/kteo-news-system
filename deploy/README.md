# Deploy

This directory holds deployment helpers. For the canonical from-scratch
deployment procedure, see [`../docs/deployment.md`](../docs/deployment.md).

## Future contents

- `snapshot.sh` — bundles the current Pi state into a tarball for migration
- `restore.sh` — restores from a snapshot on a new host
- `MIGRATION.md` — Pi-to-Pi (or Pi-to-Ubuntu Server) migration runbook

These files were delivered as a separate package during May 2026 (file-level
migration, not SD card clone). They should be folded into this directory in a
future commit.

## Related infrastructure files

- [`../infra/systemd/kteo-curate.service`](../infra/systemd/kteo-curate.service) — systemd unit for the Streamlit curation app
- [`../infra/nginx/`](../infra/nginx/) — nginx server blocks for both public hostnames
- [`../infra/cloudflared/config.yml`](../infra/cloudflared/config.yml) — Cloudflare Tunnel ingress
- [`../infra/cron/`](../infra/cron/) — captured crontab content for `pi` and `root` users
- [`../db/schema.sql`](../db/schema.sql) — current SQLite schema (apply on fresh deploy)
- [`../db/migrations/`](../db/migrations/) — historical migrations
