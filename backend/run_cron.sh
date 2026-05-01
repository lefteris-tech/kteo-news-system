#!/bin/bash
# ============================================================================
# Wrapper για cron job — KTEO News Aggregator v2
# ============================================================================
# Ορίζει το environment που λείπει στο minimal cron shell.
# Φορτώνει API key από /etc/news_aggregator.env και τρέχει το Python pipeline.
#
# Σημ.: Δεν κάνει log redirect εδώ — αν τρέξει από cron, βάλε το στο cron entry:
#   0 9 * * 1-6 /opt/news_aggregator/run_cron.sh >> /var/log/news_aggregator.log 2>&1
# Έτσι, όταν τρέχει interactively (π.χ. από τον deployer), βλέπεις output στο terminal.
# ============================================================================

set -e

# UTF-8 για ελληνικούς χαρακτήρες
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export PYTHONIOENCODING=utf-8

# Φόρτωση API key
set -a
source /etc/news_aggregator.env
set +a

# Run pipeline — per-category XML outputs στο /var/www/html
cd /opt/news_aggregator
/opt/news_aggregator/venv/bin/python news_aggregator.py \
    --output-dir /var/www/html \
    --db /opt/news_aggregator/news_cache.db

# Carry-over post-processor — supplement low-count categories from previous run
/opt/news_aggregator/venv/bin/python /opt/news_aggregator/carry_over.py /var/www/html
# Sync Yodeck playlists με τις διαθέσιμες ειδήσεις
/opt/news_aggregator/venv/bin/python /opt/news_aggregator/playlist_sync.py

