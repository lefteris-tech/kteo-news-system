#!/bin/bash
# =============================================================
# fetch_raw_cron.sh — KTEO Curation Platform, Sprint 1
# Sprint: 1, version: 1, generated: 2026-05-13
# =============================================================
# Wrapper για το pi cron entry. Source-άρει το env file και
# τρέχει το fetch_raw.py μέσω του Phase 1 venv.
#
# Pi crontab: 50 7 * * 1-5 /opt/news_aggregator/fetch_raw_cron.sh \
#               >> /var/log/news_aggregator.log 2>&1
# =============================================================

set -e

export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export PYTHONIOENCODING=utf-8

# Source env vars (ANTHROPIC_API_KEY κλπ). Env file is mode 640 pi:pi
# after Sprint 1 permission change.
set -a
source /etc/news_aggregator.env
set +a

cd /opt/news_aggregator
exec /opt/news_aggregator/venv/bin/python /opt/news_aggregator/fetch_raw.py \
    --db /opt/news_aggregator/news_cache.db
