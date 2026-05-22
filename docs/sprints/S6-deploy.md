# Sprint 6 — Human-Controlled Classification Deploy

**Sprint:** 6 — Human-controlled classification
**Status:** Ready for deploy
**Risk:** Medium — touches fetch, publish, and the main curator page. No schema change.
**Estimated time:** ~10 minutes including smoke test

---

## What this sprint changes

**Architectural pivot:** Claude Haiku is removed from the fetch path entirely.
Categorisation becomes a human decision in the Streamlit UI. The classifier
is only invoked at publish time, and only on items the curator has both
**selected** and **categorised**.

| Layer | Before (Sprint 3.2) | After (Sprint 6) |
|---|---|---|
| fetch_raw API calls | 40 Haiku calls/morning | **0** |
| Cost per day | classify + summarize | summarize only (publish-time) |
| API outage impact | curator queue empty (today's incident) | queue populates; publish blocked only if API still down |
| Classifier errors | possible (auto news misclassed as economy) | impossible — human picks the category |
| Curator workflow | check/uncheck items in 6 category tabs | pick category from dropdown, then check |

**Files changed:**

- `backend/fetch_raw.py` — drops the Haiku call entirely (Sprint 6 rewrite)
- `backend/publish_curated.py` — adds validation (refuse to publish uncategorised) + fail-fast on any Haiku error
- `backend/pages/curation.py` — unified list with per-row category dropdown, select disabled until category chosen
- `backend/kteo_curate.py` — new `set_category()` helper

No schema change. No service restart needed for fetch — only Streamlit needs to pick up the new page code.

---

## Step 1 — Pull the merged code on the Pi `[on PI]`

Assumes you have the repo checked out somewhere on the Pi. If not, clone first
(use the same auth as your normal git workflow):

```bash
cd /path/to/repo/kteo-news-system
git fetch origin
git checkout main
git pull origin main
git log --oneline -n 5    # confirm the Sprint 6 commits are there
```

---

## Step 2 — Backup the running scripts `[on PI]`

```bash
TS=$(date +%Y%m%d_%H%M%S)
sudo mkdir -p /opt/news_aggregator/backups
sudo cp /opt/news_aggregator/fetch_raw.py \
        /opt/news_aggregator/backups/fetch_raw.py.bak.$TS
sudo cp /opt/news_aggregator/publish_curated.py \
        /opt/news_aggregator/backups/publish_curated.py.bak.$TS
sudo cp /opt/news_aggregator/kteo_curate.py \
        /opt/news_aggregator/backups/kteo_curate.py.bak.$TS
sudo cp /opt/news_aggregator/pages/curation.py \
        /opt/news_aggregator/backups/pages_curation.py.bak.$TS
ls -la /opt/news_aggregator/backups/*.$TS
echo "Backup tag: $TS"
```

Keep `$TS` handy for Step 6 (rollback).

---

## Step 3 — Install new code `[on PI]`

```bash
cd /path/to/repo/kteo-news-system

sudo install -o pi -g pi -m 0644 backend/fetch_raw.py        /opt/news_aggregator/fetch_raw.py
sudo install -o pi -g pi -m 0644 backend/publish_curated.py  /opt/news_aggregator/publish_curated.py
sudo install -o pi -g pi -m 0644 backend/kteo_curate.py      /opt/news_aggregator/kteo_curate.py
sudo install -o pi -g pi -m 0644 backend/pages/curation.py   /opt/news_aggregator/pages/curation.py

# Syntax sanity
./venv/bin/python -c "import ast; [ast.parse(open(f).read()) for f in [
  '/opt/news_aggregator/fetch_raw.py',
  '/opt/news_aggregator/publish_curated.py',
  '/opt/news_aggregator/kteo_curate.py',
  '/opt/news_aggregator/pages/curation.py',
]]; print('all OK')"

# Import sanity (catches broken imports vs missing deps)
cd /opt/news_aggregator
./venv/bin/python -c "import fetch_raw; print('fetch_raw OK')"
./venv/bin/python -c "import publish_curated; print('publish_curated OK')"
./venv/bin/python -c "import kteo_curate; print('kteo_curate OK')"
```

---

## Step 4 — Restart Streamlit `[on PI]`

The Streamlit service must restart to pick up the page code change. The
fetch_raw and publish_curated changes are picked up on next invocation —
no restart needed for those.

```bash
sudo systemctl restart kteo-curate.service
sudo systemctl status  kteo-curate.service --no-pager | head -15
```

**Expected:** active (running), recent Uvicorn started line in the logs.

---

## Step 5 — End-to-end smoke test `[on PI]` then `[on browser]`

### 5a. Fix today's broken queue with a manual fetch

Today's queue is empty (API cap incident). The new fetch_raw doesn't call
the API, so it will populate immediately:

```bash
sudo -u pi /opt/news_aggregator/fetch_raw_cron.sh 2>&1 | tail -25
```

**Expected:** summary block with `inserted (pending): >0`, no `Claude API error`
lines, no `classification failed` counter.

```bash
sqlite3 -header -column /opt/news_aggregator/news_cache.db \
  "SELECT count(*) AS pending_today,
          count(classified_category) AS with_category
   FROM pending_curation
   WHERE fetch_date = date('now') AND status='pending';"
```

**Expected:** `pending_today > 0`, `with_category = 0` (all NULL — intentional).

### 5b. Open the curation UI

[https://curate.dronepros.gr](https://curate.dronepros.gr) → Σημερινή Επιμέλεια.

**Visual checks:**

- One unified list of items, no category tabs at the top
- Each row has a "— Διάλεξε Κατηγορία —" dropdown
- The Επιλογή checkbox is greyed out (disabled) on rows with no category
- Top strip: "N άρθρα στην ουρά · 0 επιλεγμένα"

**Interaction checks:**

1. Pick a category on one item → checkbox becomes enabled
2. Tick the checkbox → counter at the bottom updates to "1"
3. Change the category on the same item → still selected, new category sticks
4. Clear the category (back to placeholder) → checkbox auto-disables AND item auto-deselects
5. Re-categorise + select 2-3 items across different categories

### 5c. Publish

Click **🚀 Προεπισκόπηση & Δημοσίευση**:

- Preview modal groups items by chosen category
- Each summary panel says "Η σύνοψη θα παραχθεί από το Haiku κατά τη δημοσίευση"
- Click **Επιβεβαίωση και δημοσίευση**

**If the API cap is still active**, expect a clear error:

> ❌ Η σύνοψη Haiku απέτυχε. Τα άρθρα παραμένουν επιλεγμένα — δοκίμασε ξανά
> όταν επανέλθει η API.

Items stay `status='selected'` — visible in the UI for retry once the cap is
raised.

**If the API is OK**, expect green success message + screens update within
~15 minutes.

---

## Step 6 — Sign-off

Send back:

1. **Step 3** — the four `OK` lines from import sanity
2. **Step 4** — `Active: active (running)` line from systemctl status
3. **Step 5a** — last 25 lines of fetch_raw_cron.sh output + the pending_today/with_category result
4. **Step 5b** — brief description of what worked + screenshot if anything looks off
5. **Step 5c** — either green success OR the fail-fast error message (both are valid Sprint 6 outcomes)

When all green, tag `v0.9-s6` on GitHub.

---

## Rollback (if anything in Steps 3–5 fails)

```bash
sudo install -o pi -g pi -m 0644 /opt/news_aggregator/backups/fetch_raw.py.bak.$TS        /opt/news_aggregator/fetch_raw.py
sudo install -o pi -g pi -m 0644 /opt/news_aggregator/backups/publish_curated.py.bak.$TS  /opt/news_aggregator/publish_curated.py
sudo install -o pi -g pi -m 0644 /opt/news_aggregator/backups/kteo_curate.py.bak.$TS      /opt/news_aggregator/kteo_curate.py
sudo install -o pi -g pi -m 0644 /opt/news_aggregator/backups/pages_curation.py.bak.$TS   /opt/news_aggregator/pages/curation.py
sudo systemctl restart kteo-curate.service
```

Items already inserted by Sprint 6's fetch_raw will have `classified_category=NULL`
and will not be selectable in the Sprint 3.2 UI. Run the previous fetch_raw once
to re-classify today's batch:

```bash
sudo -u pi /opt/news_aggregator/fetch_raw_cron.sh
```

(Requires API cap to be raised first, since Sprint 3.2 uses Haiku at fetch.)
