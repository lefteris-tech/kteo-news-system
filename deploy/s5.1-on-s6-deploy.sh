#!/bin/bash
# ============================================================================
# Sprint 5.1-on-S6 reconciliation deploy
# ============================================================================
# Run as: sudo bash /tmp/s51-on-s6.sh
# (or via SSH: ssh pi@kteo-news.local "sudo bash /tmp/s51-on-s6.sh")
#
# Fetches reconciled publish_curated.py + news_aggregator.py from the
# fix/sprint-6-reconciliation branch on GitHub, validates, backs up the
# current S6-only versions, installs as pi:pi, and dry-runs.
#
# No service restart needed — both files are invoked as subprocesses.
# ============================================================================
set -euo pipefail

# --- Configuration ----------------------------------------------------------
NA=/opt/news_aggregator
BRANCH=fix/sprint-6-reconciliation
REPO=lefteris-tech/kteo-news-system
TS=$(date +%Y%m%d_%H%M%S)
BDIR=$NA/backups/s5.1-on-s6
STAGE=/tmp/s51_on_s6

# --- PAT prompt -------------------------------------------------------------
# Either set GH_PAT in the environment, or paste it when prompted.
if [ -z "${GH_PAT:-}" ]; then
  read -srp "GitHub PAT (fine-grained, Contents:read on the repo): " GH_PAT
  echo
fi
if [ -z "$GH_PAT" ]; then
  echo "ERROR: PAT required to fetch from a private repo" >&2
  exit 1
fi

# --- Sanity: must be root (running via sudo) --------------------------------
if [ "$EUID" -ne 0 ]; then
  echo "ERROR: run with sudo (need to write to $NA and read /etc creds)" >&2
  exit 1
fi

mkdir -p "$STAGE" "$BDIR"
chown pi:pi "$BDIR"

echo "================================================================"
echo " Step 1/8: Backup current production files"
echo "================================================================"
cp -a "$NA/publish_curated.py" "$BDIR/publish_curated.py.bak.$TS"
cp -a "$NA/news_aggregator.py" "$BDIR/news_aggregator.py.bak.$TS"
ls -la "$BDIR/" | tail -5

echo
echo "================================================================"
echo " Step 2/8: Fetch reconciled files from GitHub"
echo "================================================================"
for f in publish_curated.py news_aggregator.py; do
  echo "  fetching backend/$f from branch $BRANCH..."
  http=$(curl -sw "%{http_code}" \
    -H "Authorization: Bearer $GH_PAT" \
    -H "Accept: application/vnd.github.raw" \
    "https://api.github.com/repos/$REPO/contents/backend/$f?ref=$BRANCH" \
    -o "$STAGE/$f")
  if [ "$http" != "200" ]; then
    echo "    ERROR: HTTP $http fetching $f" >&2
    exit 2
  fi
  ls -la "$STAGE/$f"
done

echo
echo "================================================================"
echo " Step 3/8: Sanity checks (S6 features + S5.1 features both present)"
echo "================================================================"
check() {
  if grep -q "$1" "$2"; then echo "  ✓ $3"; else echo "  ✗ MISSING: $3"; exit 3; fi
}
echo "--- publish_curated.py ---"
check "Sprint 6"            "$STAGE/publish_curated.py" "Sprint 6 banner"
check "uncategorised"       "$STAGE/publish_curated.py" "S6 validation block"
check "FAIL-FAST"           "$STAGE/publish_curated.py" "S6 fail-fast"
check "LEFT JOIN sources"   "$STAGE/publish_curated.py" "S5.1 JOIN"
check "source_logo_url"     "$STAGE/publish_curated.py" "S5.1 source_logo_url"
echo "--- news_aggregator.py ---"
check "xmlns:kteo"          "$STAGE/news_aggregator.py" "S5.1 xmlns:kteo"
check "source_name: str"    "$STAGE/news_aggregator.py" "S5.1 Article.source_name"
check "kteo:source_name"    "$STAGE/news_aggregator.py" "S5.1 emission"

echo
echo "================================================================"
echo " Step 4/8: Compile-check (no cache writes)"
echo "================================================================"
PYTHONDONTWRITEBYTECODE=1 "$NA/venv/bin/python3" -c "
import py_compile, sys
for f in ['$STAGE/publish_curated.py', '$STAGE/news_aggregator.py']:
    try:
        py_compile.compile(f, doraise=True, cfile='/dev/null')
        print(f'  ✓ {f}')
    except py_compile.PyCompileError as e:
        print(f'  ✗ {f}: {e}'); sys.exit(1)
"

echo
echo "================================================================"
echo " Step 5/8: Install (chown pi:pi 0644)"
echo "================================================================"
install -o pi -g pi -m 0644 "$STAGE/publish_curated.py" "$NA/publish_curated.py"
install -o pi -g pi -m 0644 "$STAGE/news_aggregator.py" "$NA/news_aggregator.py"
ls -la "$NA/publish_curated.py" "$NA/news_aggregator.py"

echo
echo "================================================================"
echo " Step 6/8: Wipe stale pycache + full-backend compile validation"
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
echo " Step 7/8: Dry-run publish (exits 1 if nothing selected — that's OK)"
echo "================================================================"
set +e
sudo -u pi "$NA/venv/bin/python3" "$NA/publish_curated.py" --dry-run \
  --triggered-by "deploy-verify@kteo-news.local" 2>&1 | tail -20
set -e

echo
echo "================================================================"
echo " Step 8/8: Service health"
echo "================================================================"
systemctl is-active kteo-curate.service
systemctl status kteo-curate.service --no-pager -l | head -5

# Don't leave the PAT in shell history
unset GH_PAT

echo
echo "================================================================"
echo " ✅ DEPLOYMENT COMPLETE"
echo "================================================================"
echo "Production state:"
echo "  kteo_curate.py     → S6 + S5.1 add_source(logo_path)"
echo "  pages/sources.py   → S5.1 (logo upload/auto-fetch UI)"
echo "  pages/curation.py  → S6 (manual category dropdown)"
echo "  fetch_raw.py       → S6 (zero API calls)"
echo "  publish_curated.py → S6 + S5.1 ← JUST DEPLOYED"
echo "  news_aggregator.py → pre-S5.1 + S5.1 patches ← JUST DEPLOYED"
echo "  widget v=6, source_logo.py, backfill_logos.py → S5.1"
echo
echo "Avatar pill appears in XML feeds on the NEXT publish."
echo
echo "REMAINING:"
echo "  1. Cloudflare → kteo-news.dronepros.gr → Caching → Purge Everything"
echo "  2. Trigger one publish at curate.dronepros.gr"
echo "  3. Visual check: https://kteo-news.dronepros.gr/news/news.html?cat=national&pos=1"
echo "  4. Merge PR:"
echo "     https://github.com/lefteris-tech/kteo-news-system/pull/new/fix/sprint-6-reconciliation"
echo "  5. Cleanup: rm -rf $STAGE /tmp/s6_prod"
