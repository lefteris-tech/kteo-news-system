#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kteo_curate.py — Shared module for the Streamlit curation app
==============================================================
Sprint: 3, version: 1
Generated: 2026-05-13

Provides: DB connection, identity (Cloudflare Access), custom CSS,
sources/filters/publish_log queries, manual injection helper (port of
infuse_news.py), and publish trigger.

Imported by streamlit_app.py and every page in pages/.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Optional

import streamlit as st

# -----------------------------------------------------------------------------
# Configuration (must match publish_curated.py / news_aggregator.py)
# -----------------------------------------------------------------------------

DB_PATH        = "/opt/news_aggregator/news_cache.db"
OUTPUT_DIR     = "/var/www/html"
PUBLISH_SCRIPT = "/opt/news_aggregator/publish_curated.py"
PLAYLIST_SYNC  = "/opt/news_aggregator/playlist_sync.py"
PYTHON_BIN     = "/opt/news_aggregator/venv/bin/python"
ENV_FILE       = "/etc/news_aggregator.env"

CATEGORIES = [
    {"key": "national",      "label": "Εθνικά",    "short": "Εθν"},
    {"key": "international", "label": "Διεθνή",    "short": "Δθν"},
    {"key": "economy",       "label": "Οικονομία", "short": "Οικ"},
    {"key": "lifestyle",     "label": "Lifestyle", "short": "Lif"},
    {"key": "auto",          "label": "Auto",      "short": "Auto"},
    {"key": "sports",        "label": "Σπορ",      "short": "Σπορ"},
]
CATEGORY_LABELS = {c["key"]: c["label"] for c in CATEGORIES}
CATEGORY_SHORTS = {c["key"]: c["short"] for c in CATEGORIES}
CATEGORY_KEYS   = [c["key"] for c in CATEGORIES]

DEV_USER_EMAIL_ENV = "DEV_USER_EMAIL"  # Fallback when Cloudflare Access not in front


# -----------------------------------------------------------------------------
# DB
# -----------------------------------------------------------------------------

@st.cache_resource
def get_db() -> sqlite3.Connection:
    """Cached DB connection (shared across Streamlit reruns)."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -----------------------------------------------------------------------------
# Identity (Cloudflare Access)
# -----------------------------------------------------------------------------

def current_user() -> dict:
    """Return {email, role, is_admin}. Read email from Cloudflare Access header,
    fall back to DEV_USER_EMAIL env var for local testing.
    Auto-creates a 'curator' row on first login.
    """
    headers = getattr(st.context, "headers", {}) if hasattr(st, "context") else {}
    email = (headers or {}).get("Cf-Access-Authenticated-User-Email")

    if not email:
        email = os.environ.get(DEV_USER_EMAIL_ENV) or "lefteris@amazingprojects.gr"

    email = email.strip().lower()

    conn = get_db()
    row = conn.execute("SELECT role, enabled FROM users WHERE email=?", (email,)).fetchone()

    if row:
        role = row["role"]
        conn.execute("UPDATE users SET last_login=? WHERE email=?", (now_iso(), email))
        conn.commit()
    else:
        # Auto-create. Bootstrap rule: lefteris@amazingprojects.gr starts as admin.
        role = "admin" if email == "lefteris@amazingprojects.gr" else "curator"
        conn.execute("""
            INSERT INTO users (email, role, last_login)
            VALUES (?, ?, ?)
        """, (email, role, now_iso()))
        conn.commit()

    return {"email": email, "role": role, "is_admin": role == "admin"}


def require_admin():
    """Stop page execution if user is not admin."""
    user = st.session_state.get("user") or current_user()
    if not user["is_admin"]:
        st.error("⛔ Πρόσβαση μόνο για διαχειριστές")
        st.stop()


# -----------------------------------------------------------------------------
# Pending curation queries
# -----------------------------------------------------------------------------

def get_pending_items(date: Optional[str] = None,
                      category: Optional[str] = None,
                      statuses: tuple = ("pending", "selected")) -> list[dict]:
    date = date or today_str()
    conn = get_db()
    where = ["fetch_date = ?", f"status IN ({','.join('?' * len(statuses))})"]
    params = [date, *statuses]
    if category:
        where.append("classified_category = ?")
        params.append(category)
    rows = conn.execute(f"""
        SELECT * FROM pending_curation
         WHERE {' AND '.join(where)}
         ORDER BY haiku_confidence DESC, id
    """, params).fetchall()
    return [dict(r) for r in rows]


def get_pending_counts(date: Optional[str] = None) -> dict:
    """{category_key: {status: count}} for the given date (default today)."""
    date = date or today_str()
    conn = get_db()
    rows = conn.execute("""
        SELECT classified_category, status, COUNT(*) AS n
          FROM pending_curation
         WHERE fetch_date = ?
         GROUP BY classified_category, status
    """, (date,)).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["classified_category"], {})[r["status"]] = r["n"]
    return out


def set_selection(item_id: int, selected: bool, by_email: str):
    conn = get_db()
    if selected:
        conn.execute("""
            UPDATE pending_curation
               SET status='selected', selected_by=?, selected_at=?
             WHERE id=? AND status IN ('pending','selected')
        """, (by_email, now_iso(), item_id))
    else:
        conn.execute("""
            UPDATE pending_curation
               SET status='pending', selected_by=NULL, selected_at=NULL
             WHERE id=? AND status='selected'
        """, (item_id,))
    conn.commit()


def update_summary(item_id: int, new_summary: str):
    conn = get_db()
    conn.execute("UPDATE pending_curation SET haiku_summary=? WHERE id=?",
                 (new_summary, item_id))
    conn.commit()


# -----------------------------------------------------------------------------
# Live screens — count items currently published per category
# -----------------------------------------------------------------------------

def get_live_screen_counts() -> dict:
    """Read /var/www/html/<slug>.xml and count <item> elements per category."""
    out = {}
    for c in CATEGORIES:
        slug = c["key"]
        path = Path(OUTPUT_DIR) / f"{slug}.xml"
        if not path.exists():
            out[slug] = 0
            continue
        try:
            tree = ET.parse(path)
            out[slug] = len(tree.getroot().findall(".//item"))
        except (ET.ParseError, OSError):
            out[slug] = 0
    return out


# -----------------------------------------------------------------------------
# Publish log
# -----------------------------------------------------------------------------

def get_last_publish() -> Optional[dict]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM publish_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def get_publish_history(limit: int = 30) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM publish_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# -----------------------------------------------------------------------------
# Sources / Filters / Users — CRUD
# -----------------------------------------------------------------------------

def get_sources() -> list[dict]:
    conn = get_db()
    return [dict(r) for r in conn.execute("SELECT * FROM sources ORDER BY id").fetchall()]


def add_source(name, url, source_type="rss", category_hint=None, enabled=True,
               logo_path=None) -> int:
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO sources (name, url, type, category_hint, enabled, logo_path)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (name, url, source_type, category_hint, 1 if enabled else 0, logo_path))
    conn.commit()
    return cur.lastrowid


def update_source(source_id: int, **fields):
    if not fields:
        return
    conn = get_db()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE sources SET {sets} WHERE id=?",
                 list(fields.values()) + [source_id])
    conn.commit()


def delete_source(source_id: int):
    conn = get_db()
    conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
    conn.commit()


def get_filters() -> list[dict]:
    conn = get_db()
    return [dict(r) for r in conn.execute("SELECT * FROM filters ORDER BY id").fetchall()]


def add_filter(scope, category, mode, keyword, enabled=True) -> int:
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO filters (scope, category, mode, keyword, enabled)
        VALUES (?, ?, ?, ?, ?)
    """, (scope, category, mode, keyword, 1 if enabled else 0))
    conn.commit()
    return cur.lastrowid


def update_filter(filter_id: int, **fields):
    if not fields:
        return
    conn = get_db()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE filters SET {sets} WHERE id=?",
                 list(fields.values()) + [filter_id])
    conn.commit()


def delete_filter(filter_id: int):
    conn = get_db()
    conn.execute("DELETE FROM filters WHERE id=?", (filter_id,))
    conn.commit()


def get_users() -> list[dict]:
    conn = get_db()
    return [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY email").fetchall()]


def update_user(email: str, **fields):
    if not fields:
        return
    conn = get_db()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE users SET {sets} WHERE email=?",
                 list(fields.values()) + [email])
    conn.commit()


# -----------------------------------------------------------------------------
# Publish (full curated batch via subprocess)
# -----------------------------------------------------------------------------

def _load_env(env: dict) -> dict:
    """Source /etc/news_aggregator.env into the env dict (no overwrites)."""
    if not os.path.exists(ENV_FILE):
        return env
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def trigger_full_publish(by_email: str, dry_run: bool = False) -> dict:
    """Invoke publish_curated.py. Returns {ok, stdout, stderr, code}."""
    cmd = [PYTHON_BIN, PUBLISH_SCRIPT,
           "--db", DB_PATH,
           "--triggered-by", by_email,
           "--verbose"]
    if dry_run:
        cmd.append("--dry-run")
    env = _load_env(os.environ.copy())
    try:
        result = subprocess.run(cmd, env=env, capture_output=True,
                                text=True, timeout=180)
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "timeout after 180s", "code": -1}


# -----------------------------------------------------------------------------
# Manual injection
# -----------------------------------------------------------------------------

def _make_manual_guid(title: str) -> str:
    h = hashlib.md5(title.encode("utf-8")).hexdigest()[:8]
    return f"manual-{int(datetime.now().timestamp())}-{h}"


def add_to_pool(by_email: str, category: str, title: str, summary: str,
                link: str = "", image_url: str = "") -> int:
    """Insert a manual item into pending_curation (status='pending').
    Marketing then selects it via normal curation flow.
    """
    guid = _make_manual_guid(title)
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO pending_curation (
            fetch_date, source_id, guid, title, body_first_para, body_full,
            image_url, pub_date, classified_category,
            safety_passed, haiku_confidence, haiku_summary,
            status, source_type, created_at
        ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, 1, 1.0, ?, 'pending', 'manual', ?)
    """, (today_str(), guid, title, summary[:500], summary,
          image_url or "", now_iso(), category, summary, now_iso()))
    conn.commit()
    return cur.lastrowid


def push_now(by_email: str, category: str, title: str, summary: str,
             link: str = "", image_url: str = "") -> dict:
    """Push a manual item directly to screens (port of infuse_news.py).
    Prepends to existing category XML, runs playlist_sync,
    records publish_log row.
    """
    guid = _make_manual_guid(title)
    now = now_iso()

    # 1) Record in pending_curation (status='published')
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO pending_curation (
            fetch_date, source_id, guid, title, body_first_para, body_full,
            image_url, pub_date, classified_category,
            safety_passed, haiku_confidence, haiku_summary,
            status, source_type, selected_by, selected_at,
            published_at, created_at
        ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, 1, 1.0, ?,
                  'published', 'manual', ?, ?, ?, ?)
    """, (today_str(), guid, title, summary[:500], summary,
          image_url or "", now, category, summary,
          by_email, now, now, now))
    item_id = cur.lastrowid

    # 2) Prepend to category XML
    xml_path = Path(OUTPUT_DIR) / f"{category}.xml"
    _inject_into_xml(xml_path, guid, title, summary, link, image_url)

    # 3) Publish log entry
    conn.execute("""
        INSERT INTO publish_log
            (publish_date, triggered_by, items_per_category_json, total_items)
        VALUES (?, ?, ?, ?)
    """, (today_str(), by_email,
          json.dumps({category: [item_id]}, sort_keys=True), 1))
    conn.commit()

    # 4) playlist_sync (so Yodeck picks up the new item)
    env = _load_env(os.environ.copy())
    try:
        result = subprocess.run(
            [PYTHON_BIN, PLAYLIST_SYNC],
            env=env, capture_output=True, text=True, timeout=60,
        )
        sync_ok = result.returncode == 0
        sync_output = (result.stdout or "") + (result.stderr or "")
    except Exception as e:
        sync_ok = False
        sync_output = str(e)

    return {"ok": True, "item_id": item_id,
            "sync_ok": sync_ok, "sync_output": sync_output}


def _inject_into_xml(path: Path, guid: str, title: str, summary: str,
                     link: str = "", image_url: str = ""):
    """Insert new <item> at position #1 in the category XML. Atomic write."""
    if path.exists():
        tree = ET.parse(path)
        rss = tree.getroot()
        channel = rss.find("channel")
        if channel is None:
            raise RuntimeError(f"Missing <channel> in {path}")
    else:
        # Bootstrap empty feed
        rss = ET.Element("rss", version="2.0")
        channel = ET.SubElement(rss, "channel")
        ET.SubElement(channel, "title").text = f"KTEO News — {path.stem}"
        ET.SubElement(channel, "link").text = "https://kteo-news.dronepros.gr"
        ET.SubElement(channel, "description").text = "AI-curated news"
        ET.SubElement(channel, "language").text = "el-GR"
        ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
        tree = ET.ElementTree(rss)

    item = ET.Element("item")
    ET.SubElement(item, "title").text = title
    if link:
        ET.SubElement(item, "link").text = link
    g = ET.SubElement(item, "guid"); g.set("isPermaLink", "false"); g.text = guid
    ET.SubElement(item, "description").text = summary
    ET.SubElement(item, "pubDate").text = format_datetime(datetime.now(timezone.utc))
    if image_url:
        enc = ET.SubElement(item, "enclosure")
        enc.set("url", image_url); enc.set("type", "image/jpeg"); enc.set("length", "0")

    first_idx = next((i for i, c in enumerate(channel) if c.tag == "item"), None)
    if first_idx is None:
        channel.append(item)
    else:
        channel.insert(first_idx, item)

    lbd = channel.find("lastBuildDate")
    if lbd is not None:
        lbd.text = format_datetime(datetime.now(timezone.utc))

    tmp = path.with_suffix(".xml.tmp")
    tree.write(tmp, encoding="utf-8", xml_declaration=True)
    tmp.replace(path)


# -----------------------------------------------------------------------------
# Custom CSS (dark theme + cards + tokens)
# -----------------------------------------------------------------------------

CUSTOM_CSS = """
<style>
:root {
    --av-bg:        #0d1117;
    --av-surface:   #161b22;
    --av-surface2:  #1c222b;
    --av-border:    #30363d;
    --av-ink:       #f0f6fc;
    --av-muted:     #8b949e;
    --av-accent:    #ff5722;
    --av-success:   #3fb950;
    --av-warning:   #d29922;
    --av-danger:    #f85149;
}
.stApp { background-color: var(--av-bg); }
#MainMenu, [data-testid="stMainMenu"], [data-testid="stToolbarActions"], footer { visibility: hidden; }
header[data-testid="stHeader"] { background: transparent; }
/* Sidebar always visible — hide the collapse button */
[data-testid="stSidebarCollapseButton"] { display: none !important; }
section[data-testid="stSidebar"] {
    background-color: var(--av-surface);
    border-right: 1px solid var(--av-border);
}
section[data-testid="stSidebar"] * { color: var(--av-ink); }
.main .block-container { padding-top: 1rem; padding-bottom: 5rem; max-width: 1400px; }

h1, h2, h3, h4, p, span, label { color: var(--av-ink); }

.stButton > button {
    border: 1px solid var(--av-border);
    background: var(--av-surface);
    color: var(--av-ink);
    font-size: 13px;
}
.stButton > button:hover { border-color: var(--av-muted); color: var(--av-ink); }
.stButton > button[kind="primary"] {
    background: var(--av-accent);
    border: 1px solid var(--av-accent);
    color: white;
    font-weight: 600;
}
.stButton > button[kind="primary"]:hover { background: #ff7d52; border-color: #ff7d52; }
.stButton > button:disabled { opacity: 0.4; }

.stTabs [data-baseweb="tab-list"] {
    gap: 0.25rem;
    border-bottom: 1px solid var(--av-border);
}
.stTabs [data-baseweb="tab"] {
    color: var(--av-muted);
    background: transparent;
    padding: 0.75rem 1rem;
    font-size: 13px;
}
.stTabs [aria-selected="true"] {
    color: var(--av-accent);
    box-shadow: inset 0 -2px 0 0 var(--av-accent);
}

.stTextInput input, .stTextArea textarea, .stSelectbox > div > div {
    background-color: var(--av-bg);
    border-color: var(--av-border);
    color: var(--av-ink);
}

[data-testid="stDataFrame"] { background: var(--av-surface); }

/* Status strip */
.av-status-strip {
    padding: 0.75rem 1rem;
    border: 1px solid var(--av-border);
    border-radius: 0.5rem;
    background-color: var(--av-surface);
    margin-bottom: 1rem;
    display: flex;
    align-items: center;
    gap: 1.5rem;
    flex-wrap: wrap;
}
.av-status-strip .pulse {
    width: 8px; height: 8px; border-radius: 50%;
    display: inline-block;
    margin-right: 0.5rem;
    animation: av-pulse 2s infinite;
}
.av-status-strip .pulse.success { background: var(--av-success); }
.av-status-strip .pulse.warning { background: var(--av-warning); }
.av-status-strip .pulse.danger  { background: var(--av-danger); }
@keyframes av-pulse { 0%, 100% {opacity:1;} 50% {opacity:0.4;} }
.av-status-strip .label { color: var(--av-muted); font-size: 12px; }
.av-status-strip .value { color: var(--av-ink); font-weight: 600; }
.av-status-strip .sep   { width:1px; height:1.25rem; background: var(--av-border); }

/* Article card */
.av-card {
    background-color: var(--av-surface);
    border: 1px solid var(--av-border);
    border-radius: 0.5rem;
    padding: 0.75rem;
    margin-bottom: 0.5rem;
}
.av-card.selected {
    box-shadow: inset 3px 0 0 0 var(--av-accent);
    background-color: var(--av-surface2);
}
.av-card .row { display: flex; gap: 1rem; align-items: flex-start; }
.av-thumb {
    width: 120px; height: 80px; border-radius: 0.375rem;
    background: linear-gradient(135deg, #1c222b, #161b22);
    flex-shrink: 0;
    background-size: cover; background-position: center;
}
.av-title {
    color: var(--av-ink); font-weight: 600; font-size: 14px;
    line-height: 1.3; margin: 0;
}
.av-snippet {
    color: var(--av-muted); font-size: 12.5px; line-height: 1.4;
    margin: 4px 0 0 0;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden;
}
.av-meta {
    display: flex; gap: 0.75rem; align-items: center;
    margin-top: 0.5rem; font-size: 11.5px; color: var(--av-muted);
    flex-wrap: wrap;
}
.av-pill {
    display: inline-flex; align-items: center;
    padding: 2px 8px; border-radius: 999px;
    font-size: 11px; border: 1px solid var(--av-border);
    color: var(--av-muted); background: var(--av-bg);
}
.av-pill.accent {
    border-color: var(--av-accent);
    background: rgba(255, 87, 34, 0.1);
    color: var(--av-accent);
}
.av-pill.manual {
    background: var(--av-accent); color: white; border: none;
    font-weight: 700; font-size: 10px;
}
.av-dot {
    display: inline-block; width: 8px; height: 8px;
    border-radius: 50%; margin-right: 4px;
}
.av-dot.high   { background: var(--av-success); }
.av-dot.medium { background: var(--av-warning); }
.av-dot.low    { background: var(--av-danger); }

.av-counter {
    padding: 0.5rem 0.75rem;
    border-radius: 0.375rem;
    border: 1px solid var(--av-border);
    background: var(--av-surface);
    font-size: 13px;
    display: inline-flex; gap: 0.5rem; align-items: center;
}
.av-counter.over {
    border-color: var(--av-warning);
    background: rgba(210, 153, 34, 0.1);
    color: var(--av-warning);
}
.av-counter .count { color: var(--av-ink); font-weight: 600; }
.av-counter.over .count { color: var(--av-warning); }

.av-live-strip {
    display: inline-flex; gap: 0.75rem;
    font-size: 12px; color: var(--av-muted);
    font-family: ui-monospace, Menlo, monospace;
}
.av-live-strip .cell { display: inline-flex; gap: 4px; }
.av-live-strip .cell.zero { color: var(--av-danger); }
.av-live-strip .cell .v { color: var(--av-ink); font-weight: 600; }
.av-live-strip .cell.zero .v { color: var(--av-danger); }
</style>
"""


def inject_css():
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# Rendering helpers
# -----------------------------------------------------------------------------

GREEK_DAYS = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή"]
GREEK_MONTHS = ["", "Ιανουαρίου", "Φεβρουαρίου", "Μαρτίου", "Απριλίου", "Μαΐου",
                "Ιουνίου", "Ιουλίου", "Αυγούστου", "Σεπτεμβρίου", "Οκτωβρίου",
                "Νοεμβρίου", "Δεκεμβρίου"]


def format_greek_date(d: datetime) -> str:
    return f"{GREEK_DAYS[d.weekday()]}, {d.day} {GREEK_MONTHS[d.month]} {d.year}"


def relative_age_greek(iso_ts: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return iso_ts[:16]
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return "μόλις τώρα"
    if secs < 3600:
        return f"πριν {secs // 60} λεπτά"
    if secs < 86400:
        return f"πριν {secs // 3600} ώρες"
    return f"πριν {secs // 86400} ημέρες"


def confidence_class(score: float) -> str:
    if score >= 0.90:
        return "high"
    if score >= 0.70:
        return "medium"
    return "low"


def render_status_strip(user: dict, available_count: int):
    """Render the status strip at the top of the Curation page."""
    today = format_greek_date(datetime.now())
    last = get_last_publish()
    live = get_live_screen_counts()

    if last:
        last_age = relative_age_greek(last["created_at"])
        pulse_class = "success"
        last_text = f"σήμερα {last['created_at'][11:16]}" if last['publish_date'] == today_str() else last_age
        last_html = (
            f'<span class="pulse {pulse_class}"></span>'
            f'<span class="label">Τελευταία δημοσίευση:</span> '
            f'<span class="value">{last_text}</span>'
            f' <span class="label">από {last["triggered_by"]}</span>'
        )
    else:
        last_html = (
            '<span class="pulse warning"></span>'
            '<span class="label">Καμία προηγούμενη δημοσίευση</span>'
        )

    live_cells = " ".join(
        f'<span class="cell {"zero" if live[c["key"]] == 0 else ""}">'
        f'{c["short"]} <span class="v">{live[c["key"]]}</span>'
        f'</span>'
        for c in CATEGORIES
    )

    st.markdown(f"""
    <div class="av-status-strip">
        <div><span class="label">📅</span> <span class="value">{today}</span></div>
        <div class="sep"></div>
        <div><span class="value">{available_count}</span> <span class="label">άρθρα διαθέσιμα</span></div>
        <div class="sep"></div>
        <div>{last_html}</div>
        <div class="sep" style="margin-left:auto"></div>
        <div>
            <span class="label" style="text-transform:uppercase;font-size:10px;letter-spacing:.05em">Στις οθόνες τώρα</span>
            <div class="av-live-strip" style="margin-top:2px">{live_cells}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_user_chip(user: dict):
    """Sidebar chip with email + role + sign-out link."""
    role_color = "#ff5722" if user["is_admin"] else "#3fb950"
    role_label = "Admin" if user["is_admin"] else "Curator"
    initials = "".join([p[0].upper() for p in user["email"].split("@")[0].split(".")[:2]])
    st.sidebar.markdown(f"""
    <div style="padding: 0.5rem 0 1rem 0; border-bottom: 1px solid #30363d;
                margin-bottom: 1rem; display: flex; gap: 0.625rem; align-items: center;">
        <div style="width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#ff5722,#b03d18);
                    color:white;display:flex;align-items:center;justify-content:center;
                    font-weight:700;font-size:12px;">{initials}</div>
        <div style="flex:1;min-width:0;">
            <div style="color:#f0f6fc;font-size:12px;overflow:hidden;text-overflow:ellipsis;
                        white-space:nowrap;">{user['email']}</div>
            <div style="margin-top:2px;">
                <span style="display:inline-block;padding:1px 6px;background:{role_color};
                             color:white;border-radius:999px;font-size:10px;font-weight:600;">
                    {role_label}
                </span>
                <a href="/cdn-cgi/access/logout" style="color:#8b949e;font-size:10.5px;margin-left:0.5rem;">
                    Sign out
                </a>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
