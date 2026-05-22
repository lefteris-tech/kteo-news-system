#!/bin/bash
# ============================================================================
# Sprint 5.2 full deploy
# ============================================================================
# Deploys EVERYTHING from fix/sprint-6-reconciliation that isn't yet live:
#
#   1. publish_curated.py   ← S6 + S5.1 source attribution    (was pending from yesterday)
#   2. news_aggregator.py   ← S5.1 namespace + per-item XML   (was pending from yesterday)
#   3. kteo_curate.py       ← S5.2 JOIN sources + bigger fonts (new today)
#   4. pages/curation.py    ← S5.2 source avatar pill          (new today)
#
# Restarts kteo-curate.service at the end (curator UI needs reload for CSS +
# the new JOIN). publish_curated.py and news_aggregator.py are invoked as
# subprocesses so they don't need a restart.
#
# Usage on the Pi:
#   curl -sf -H "Authorization: Bearer $GH_PAT" \
#     -H "Accept: application/vnd.github.raw" \
#     "https://api.github.com/repos/lefteris-tech/kteo-news-system/contents/deploy/s5.2-full-deploy.sh?ref=fix/sprint-6-reconciliation" \
#     -o /tmp/s5.2-deploy.sh
#   sudo -E bash /tmp/s5.2-deploy.sh
# ============================================================================
set -euo pipefail

# --- Configuration ----------------------------------------------------------
NA=/opt/news_aggregator
BRANCH=fix/sprint-6-reconciliation
REPO=lefteris-tech/kteo-news-system
TS=$(date +%Y%m%d_%H%M%S)
BDIR=$NA/backups/s5.2
STAGE=/tmp/s5.2_stage

# --- PAT --------------------------------------------------------------------
if [ -z "${GH_PAT:-}" ]; then
  read -srp "GitHub PAT (fine-grained, Contents:read on the repo): " GH_PAT
  echo
fi
if [ -z "$GH_PAT" ]; then
  echo "ERROR: PAT required" >&2
  exit 1
fi

# --- Must be root -----------------------------------------------------------
if [ "$EUID" -ne 0 ]; then
  echo "ERROR: run with sudo" >&2
  exit 1
fi

mkdir -p "$STAGE" "$BDIR"
chown pi:pi "$BDIR"

FILES=(
  "publish_curated.py"
  "news_aggregator.py"
  "kteo_curate.py"
  "pages/curation.py"
)

echo "================================================================"
echo " Step 1/9: Backup current production files"
echo "================================================================"
for f in "${FILES[@]}"; do
  src="$NA/$f"
  bn=$(basename "$f")
  if [ -f "$src" ]; then
    cp -a "$src" "$BDIR/${bn}.bak.$TS"
    echo "  ✓ backed up $f"
  else
    echo "  ⚠ $src not found (skipping)"
  fi
done
ls -la "$BDIR/" | grep ".bak.$TS"

echo
echo "================================================================"
echo " Step 2/9: Fetch reconciled files from GitHub branch $BRANCH"
echo "================================================================"
for f in "${FILES[@]}"; do
  out="$STAGE/$f"
  mkdir -p "$(dirname "$out")"
  echo "  fetching backend/$f..."
  http=$(curl -sw "%{http_code}" \
    -H "Authorization: Bearer $GH_PAT" \
    -H "Accept: application/vnd.github.raw" \
    "https://api.github.com/repos/$REPO/contents/backend/$f?ref=$BRANCH" \
    -o "$out")
  if [ "$http" != "200" ]; then
    echo "    ✗ HTTP $http fetching $f" >&2
    exit 2
  fi
  size=$(stat -c%s "$out")
  echo "    ✓ $f ($size bytes)"
done

echo
echo "================================================================"
echo " Step 3/9: Feature sanity checks"
echo "================================================================"
check() {
  if grep -q "$1" "$2"; then echo "  ✓ $3"; else echo "  ✗ MISSING: $3 in $2"; exit 3; fi
}

echo "--- publish_curated.py (S6 + S5.1) ---"
check "Sprint 6"             "$STAGE/publish_curated.py" "S6 banner"
check "uncategorised"        "$STAGE/publish_curated.py" "S6 category validation"
check "FAIL-FAST"            "$STAGE/publish_curated.py" "S6 fail-fast summarization"
check "LEFT JOIN sources"    "$STAGE/publish_curated.py" "S5.1 sources JOIN"
check "source_logo_url"      "$STAGE/publish_curated.py" "S5.1 logo URL build"

echo "--- news_aggregator.py (S5.1) ---"
check "xmlns:kteo"           "$STAGE/news_aggregator.py" "S5.1 namespace declaration"
check "source_name: str"     "$STAGE/news_aggregator.py" "S5.1 Article.source_name field"
check "kteo:source_name"     "$STAGE/news_aggregator.py" "S5.1 per-item XML emission"

echo "--- kteo_curate.py (S5.2) ---"
check "set_category"         "$STAGE/kteo_curate.py" "S6 set_category helper"
check "logo_path=None"       "$STAGE/kteo_curate.py" "S5.1 add_source logo_path param"
check "LEFT JOIN sources s"  "$STAGE/kteo_curate.py" "S5.2 get_pending_items JOIN"
check "av-source-pill"       "$STAGE/kteo_curate.py" "S5.2 source pill CSS"

echo "--- pages/curation.py (S5.2) ---"
check 'av-source-pill'       "$STAGE/pages/curation.py" "S5.2 source pill in card"
check 'source_logo_path'     "$STAGE/pages/curation.py" "S5.2 logo_path access"

echo
echo "================================================================"
echo " Step 4/9: Compile-check (syntax only, no bytecode write)"
echo "================================================================"
PYTHONDONTWRITEBYTECODE=1 "$NA/venv/bin/python3" -c "
import sys
for f in ['$STAGE/publish_curated.py', '$STAGE/news_aggregator.py',
          '$STAGE/kteo_curate.py',     '$STAGE/pages/curation.py']:
    try:
        with open(f) as fh: compile(fh.read(), f, 'exec')
        print(f'  ✓ {f}')
    except SyntaxError as e:
        print(f'  ✗ {f}: {e}'); sys.exit(1)
"

echo
echo "================================================================"
echo " Step 5/9: Install all 4 files (pi:pi 0644)"
echo "================================================================"
install -o pi -g pi -m 0644 "$STAGE/publish_curated.py" "$NA/publish_curated.py"
install -o pi -g pi -m 0644 "$STAGE/news_aggregator.py" "$NA/news_aggregator.py"
install -o pi -g pi -m 0644 "$STAGE/kteo_curate.py"     "$NA/kteo_curate.py"
install -o pi -g pi -m 0644 "$STAGE/pages/curation.py"  "$NA/pages/curation.py"
ls -la "$NA/publish_curated.py" "$NA/news_aggregator.py" "$NA/kteo_curate.py" "$NA/pages/curation.py"

echo
echo "================================================================"
echo " Step 6/9: Wipe stale pycache + full-backend compile validation"
echo "================================================================"
find "$NA" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
PYTHONDONTWRITEBYTECODE=1 "$NA/venv/bin/python3" -c "
import sys
files = ['kteo_curate.py','publish_curated.py','news_aggregator.py','fetch_raw.py',
        'pages/curation.py','pages/sources.py','source_logo.py','backfill_logos.py']
for f in files:
    p = f'/opt/news_aggregator/{f}'
    try:
        with open(p) as fh: compile(fh.read(), p, 'exec')
        print(f'  ✓ {f}')
    except Exception as e:
        print(f'  ✗ {f}: {e}'); sys.exit(1)
"

echo
echo "================================================================"
echo " Step 7/9: Restart kteo-curate.service (loads new CSS + JOIN)"
echo "================================================================"
systemctl restart kteo-curate.service
sleep 4
echo "Service active: $(systemctl is-active kteo-curate.service)"
echo
echo "Last 8 journal lines:"
journalctl -u kteo-curate.service --no-pager -n 8 | tail -8

echo
echo "================================================================"
echo " Step 8/9: Dry-run publish (validates new JOIN query parses)"
echo "================================================================"
set +e
sudo -u pi "$NA/venv/bin/python3" "$NA/publish_curated.py" --dry-run \
  --triggered-by "deploy-verify@kteo-news.local" 2>&1 | tail -15
set -e

echo
echo "================================================================"
echo " Step 9/9: Streamlit responding?"
echo "================================================================"
curl -sI -m 5 http://127.0.0.1:8501/ | head -3 || echo "  (check journalctl)"

# Sanitise
unset GH_PAT

echo
echo "================================================================"
echo " ✅ SPRINT 5.2 DEPLOYMENT COMPLETE"
echo "================================================================"
echo "What just changed in production:"
echo "  ✓ Σημερινή Επιμέλεια page now shows source logo on every card"
echo "  ✓ Fonts bumped 1-2px across titles, summaries, and meta rows"
echo "  ✓ Published RSS feeds will include <kteo:source_*> elements on next publish"
echo
echo "What you can do now:"
echo "  1. Reload curate.dronepros.gr → verify the avatar pills + larger fonts"
echo "  2. Trigger a publish → verify avatar appears on the TV screens"
echo "  3. Merge the PR:"
echo "     https://github.com/lefteris-tech/kteo-news-system/pull/new/fix/sprint-6-reconciliation"
echo "  4. Cleanup staging: rm -rf $STAGE"
echo
echo "Rollback (if anything looks wrong):"
echo "  sudo cp $BDIR/{kteo_curate,publish_curated,news_aggregator,curation}.py.bak.$TS \\"
echo "          $NA/ (then sudo systemctl restart kteo-curate.service)"
