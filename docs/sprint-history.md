# Sprint History

The KTEO News System was built incrementally over six sprints, each addressing
a specific capability gap or architectural mistake from the previous iteration.
This document captures the *why* behind each sprint.

For machine-readable changelog format, see [`../CHANGELOG.md`](../CHANGELOG.md).

---

## Phase 1 — Fully automated baseline (April–May 2026)

**Goal:** Replace static signage content with daily news cycling across 53 sites.

**Approach:** Single-stage pipeline. `news_aggregator.py` fetched RSS, asked
Claude Haiku to classify + safety-filter + summarize in one call, and wrote
per-category XML feeds that Yodeck Web Pages consumed via a self-hosted widget.

**What worked:**
- Zero-touch operation: cron fires at 07:40, screens updated by ~07:55
- Architecture pivot from Yodeck Custom App to Yodeck Web Page solved S3
  permission and schema-parser issues
- Carry-over mechanism kept screens populated even on slow news days

**What didn't:**
- No editorial control. Crime, accidents, political flare-ups all reached
  screens at customer-facing inspection centres. The safety filter Claude
  applied was crude and inconsistent.
- The classifier sometimes mis-categorized items (auto news classed as economy,
  etc.) with no way to correct.

**Lesson:** Full automation is wrong for editorial content. The marketing team
needs final control.

---

## Sprint 1 — Curation data layer (2026-05-13)

**Goal:** Add the schema and ingestion path for a curator-in-the-loop workflow,
without disrupting Phase 1.

**Delivered:**
- 5 new SQLite tables: `sources`, `filters`, `pending_curation`, `users`, `publish_log`
- New script `fetch_raw.py` running at 07:50 (10 min after Phase 1) — wrote to
  `pending_curation` instead of producing XML

**Architecture decision:** Phase 1 and S1 run **side by side**. Phase 1 keeps
producing the live XML feeds; S1 is observation-only until later sprints wire
up publishing. This let us iterate without risk.

**Bug carried forward (fixed in S3.1):** `fetch_raw.py` reused Phase 1's
`classify_with_claude` function which did classify + safety + summarize in one
call. Designed for fully-automated Phase 1, the safety filter aggressively
rejected normal news.

---

## Sprint 2 — Publish layer (2026-05-13)

**Goal:** Close the loop. Read curator selections from `pending_curation` and
produce XML feeds that screens actually consume.

**Delivered:**
- `publish_curated.py` — reads `status='selected'` rows, writes per-category
  XML to `/var/www/html/`, triggers `carry_over.py` + `playlist_sync.py`
- `curate_cli.py` — headless CLI for selecting and publishing (used during
  development; remains available for ops)
- `publish_log` table writes for audit trail

**Validation:** End-to-end test with 11 manually-selected items published to
live screens. HTTP 200 from Yodeck API, items visible on screens within ~10 min.

---

## Sprint 3 — Streamlit curation app (2026-05-13)

**Goal:** Give the marketing team a UI. CLI was fine for development but not
for daily use by non-technical curators.

**Delivered:**
- Multi-page Streamlit app with role-aware navigation via `st.navigation()`
- 6 pages: Σημερινή Επιμέλεια, Χειροκίνητη Προσθήκη, Ιστορικό, Πηγές, Φίλτρα, Ρυθμίσεις
- `kteo-curate.service` systemd unit listening on `127.0.0.1:8501`
- Custom dark CSS matching the widget palette (`#ff5722` accent, `#0d1117` bg)
- Identity from Cloudflare Access header with dev-mode env fallback

**Deferred to Sprint 4:** Public exposure of the app. Sprint 3 ran behind an
SSH tunnel for testing.

**Bugs fixed during deploy:**
- Malformed f-string in `pages/manual.py`
- Sidebar collapse button needed to be hidden via custom CSS

---

## Sprint 3.1 — Architectural fix (2026-05-14)

**Goal:** Fix the safety-filter problem inherited from Sprint 1.

**Trigger:** First production-like run on 2026-05-13 rejected 39 of 40 fetched
articles. Reviewing the rejected items showed Claude was acting as a strict
editorial filter — appropriate for unsupervised Phase 1, wrong for
human-in-the-loop.

**Architecture change:** Separation of concerns.

| Stage | Old (S1/S2) | New (S3.1) |
|---|---|---|
| `fetch_raw` Claude call | classify + safety filter + summarize | classify only |
| Summary timing | At fetch (wasted on rejected items) | At publish (only on approved items) |
| Effective rejection rate | 70–100% | ~0% (only invalid categorization) |

**Delivered:**
- New `fetch_raw.py` with `classify_only()` function (lightweight Haiku prompt)
- New `publish_curated.py` with on-demand summarization step before XML write
- No schema changes — `haiku_summary` simply populates at a different stage

**Second bug fixed during deploy:** Initial summarization used `unicode_escape`
decoding which corrupted Greek UTF-8 to mojibake. Replaced with `json.loads`.

**Cost impact:** Net reduction — Phase 2 spends fewer Haiku tokens overall
because no summarization happens on rejected items.

---

## Sprint 3.2 — DB-driven sources (2026-05-14)

**Goal:** Remove the hardcoded `RSS_SOURCE = "https://newsbeast.gr/feed"`
constant. Make the source list curatable via the Streamlit Πηγές page.

**Delivered:**
- `fetch_raw.py` reads from the `sources` table at runtime
- Multiple sources are interleaved round-robin so `MAX_CANDIDATES` budget is
  shared fairly
- Each `pending_curation` row records its `source_id` (was always `NULL` before)
- **Fail-fast** philosophy: if zero sources are enabled, exit code 3 with a
  clear error message. No silent fallback.

**Design choice:** Refused to add a "default newsbeast" fallback. Curator
discipline > technical safety net.

---

## Sprint 4 — Edge deployment (2026-05-14)

**Goal:** Expose the curation app to the marketing team without VPN/SSH tunnel.

**Delivered:**
- nginx `curate.dronepros.gr` vhost with WebSocket-aware reverse proxy to
  Streamlit on port 8501
- Cloudflare Tunnel ingress rule routing `curate.dronepros.gr` through the
  existing tunnel (no new tunnel needed)
- Cloudflare Access policy: email-OTP allowlist for `lefteris@amazingprojects.gr`
  (admin) + Autovision marketing addresses (curators)
- **Phase 1 cron at 07:40 commented out** — system now operates Phase 2 only

**Result:** System is end-to-end Phase 2. Marketing team has secure browser
access, no VPN required. Phase 1 remains in the codebase and crontab (commented)
for emergency rollback.

---

## What's next

**Phase 2.5 — Adaptive pre-filtering** (planned, sprint 5):

After ~30 days of curator decisions accumulated in the database, run a periodic
analysis to surface suggested filter rules — keywords curators consistently
reject, sources with low approval rates, classifier mismatches. Admin reviews
and approves suggestions; approved rules get applied at fetch time so obviously
unwanted items don't reach the curator's queue at all.

This is a closing of the human-in-the-loop loop: human → data → suggestions →
human approval → automated filter, with a blind-sampling safety net to prevent
silent bias amplification.

---

## What we'd do differently

In hindsight, the Sprint 1 safety-filter issue was foreseeable. Reusing the
Phase 1 function carried Phase 1 semantics that didn't fit Phase 2 architecture.
Going forward: when adding a new architectural layer, audit the contracts of
borrowed functions rather than borrowing them wholesale.

Beyond that, the sprint cadence (one functional layer per day, side-by-side
deployment with Phase 1, deploy/test/rollback docs for every sprint) was
disciplined enough that the only fix-up sprints (3.1, 3.2) addressed real
issues rather than design churn.
