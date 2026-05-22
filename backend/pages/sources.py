#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pages/sources.py — Πηγές (Sources management) — ADMIN ONLY
Sprint: 3, version: 2 (S5.1 — added logo auto-fetch + manual upload)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import urllib.parse
from pathlib import Path

import streamlit as st
import feedparser

from kteo_curate import (
    CATEGORIES, current_user, require_admin,
    get_sources, add_source, update_source, delete_source, get_db,
)
from source_logo import fetch_logo, save_uploaded_logo, LOGO_DIR

LOGO_PUBLIC_PREFIX = "https://kteo-news.dronepros.gr/news/logos/"

user = st.session_state.get("user") or current_user()
require_admin()

st.markdown("""
<h2 style="margin-top:0;color:var(--av-ink);font-size:18px;">Πηγές (Sources)</h2>
<p style="color:var(--av-muted);font-size:12.5px;margin-top:0;">
  RSS / Atom feeds που τροφοδοτούν το morning fetch. Το logo κάθε πηγής
  εμφανίζεται ως circular avatar στο widget δίπλα στο timestamp.
</p>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _derive_slug_from_url(url: str) -> str:
    """Stable filesystem slug from feed URL (e.g. newsbeast.gr/feed -> newsbeast)."""
    try:
        host = urllib.parse.urlparse(url).netloc or url
    except Exception:
        host = url
    host = host.replace("www.", "", 1)
    label = host.split(".", 1)[0]
    slug = re.sub(r"[^a-z0-9_-]+", "_", label.lower()).strip("_")
    return slug or "source"


def _logo_public_url(logo_path) -> str:
    if not logo_path:
        return ""
    filename = str(logo_path).rsplit("/", 1)[-1]
    return LOGO_PUBLIC_PREFIX + filename


def _set_source_logo(source_id: int, logo_path: str) -> None:
    """Direct UPDATE — explicit single-field set for clarity."""
    conn = get_db()
    conn.execute("UPDATE sources SET logo_path = ? WHERE id = ?",
                 (logo_path, source_id))
    conn.commit()


# ---------------------------------------------------------------------------
# Add new source — form + inline auto-fetch / upload
# ---------------------------------------------------------------------------
with st.expander("➕  Προσθήκη πηγής", expanded=False):
    # Logo state lives outside the form so async fetch survives reruns.
    if "new_src_logo_path" not in st.session_state:
        st.session_state.new_src_logo_path = None
    if "new_src_url_cache" not in st.session_state:
        st.session_state.new_src_url_cache = ""

    # --- Form: text fields + submit/test buttons ---
    with st.form("add_source", clear_on_submit=False):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Όνομα *", placeholder="π.χ. Καθημερινή")
            stype = st.selectbox("Τύπος", ["rss", "atom"])
        with col2:
            url = st.text_input("URL *", placeholder="https://...",
                                value=st.session_state.new_src_url_cache)
            hint_opts = ["(καμία)"] + [c["key"] for c in CATEGORIES]
            hint = st.selectbox(
                "Πρόταση κατηγορίας",
                options=hint_opts,
                format_func=lambda k: ("(καμία)" if k == "(καμία)"
                                       else next(c["label"] for c in CATEGORIES if c["key"] == k)),
            )
        enabled = st.checkbox("Ενεργή", value=True)

        c_add, c_test = st.columns(2)
        with c_add:
            submit = st.form_submit_button("Προσθήκη", type="primary",
                                           use_container_width=True)
        with c_test:
            test = st.form_submit_button("🔍 Δοκιμή feed",
                                         use_container_width=True)

    # Cache URL outside the form so the fetch buttons below can use it.
    if url and url != st.session_state.new_src_url_cache:
        st.session_state.new_src_url_cache = url

    # --- Logo fetch / upload (outside the form) ---
    st.markdown('<div style="color:var(--av-muted);font-size:12px;margin:0.5rem 0 0.25rem 0;">'
                'Logo (avatar στο widget)</div>', unsafe_allow_html=True)
    lcol1, lcol2, lcol3 = st.columns([1, 1.2, 1])
    with lcol1:
        if st.button("🌐 Auto-fetch",
                     disabled=not st.session_state.new_src_url_cache,
                     use_container_width=True, key="new_src_autofetch"):
            with st.spinner("Clearbit → HTML parse → Google favicon…"):
                slug = _derive_slug_from_url(st.session_state.new_src_url_cache)
                result = fetch_logo(st.session_state.new_src_url_cache, slug)
            if result:
                st.session_state.new_src_logo_path = str(result)
                st.toast(f"Logo: {result.name}", icon="✅")
            else:
                st.toast("Όλες οι στρατηγικές απέτυχαν — χρησιμοποίησε upload.",
                         icon="⚠️")
    with lcol2:
        uploaded = st.file_uploader("ή upload",
                                    type=["png", "jpg", "jpeg", "webp"],
                                    label_visibility="collapsed",
                                    key="new_src_upload")
        if uploaded is not None and st.session_state.new_src_url_cache:
            slug = _derive_slug_from_url(st.session_state.new_src_url_cache)
            saved = save_uploaded_logo(uploaded.read(), slug)
            if saved:
                st.session_state.new_src_logo_path = str(saved)
                st.toast(f"Uploaded: {saved.name}", icon="✅")
    with lcol3:
        if st.session_state.new_src_logo_path:
            if st.button("✖ Καθάρισμα", use_container_width=True,
                         key="new_src_clear"):
                st.session_state.new_src_logo_path = None
                st.rerun()

    # Preview thumbnail
    if st.session_state.new_src_logo_path:
        preview = _logo_public_url(st.session_state.new_src_logo_path)
        st.markdown(
            f'<div style="margin:0.5rem 0;display:flex;align-items:center;gap:0.5rem;">'
            f'<img src="{preview}" '
            f'style="width:48px;height:48px;border-radius:50%;background:#222;'
            f'border:1px solid #444;object-fit:cover;" />'
            f'<span style="font-size:11.5px;color:var(--av-muted);font-family:ui-monospace,monospace;">'
            f'{Path(st.session_state.new_src_logo_path).name}</span></div>',
            unsafe_allow_html=True,
        )

    # --- Form actions ---
    if test and url:
        with st.spinner("Φέρνω τα τελευταία 5 items..."):
            try:
                feed = feedparser.parse(url)
                if feed.entries:
                    st.success(f"✓ Feed valid — {len(feed.entries)} items")
                    for e in feed.entries[:5]:
                        title = e.get("title", "(no title)")
                        published = e.get("published", "")
                        st.markdown(
                            f'<div style="padding:0.5rem;margin-bottom:0.25rem;'
                            f'background:var(--av-surface);border:1px solid var(--av-border);'
                            f'border-radius:0.375rem;font-size:12px;">'
                            f'<div style="color:var(--av-ink);">{title[:120]}</div>'
                            f'<div style="color:var(--av-muted);font-size:11px;">{published}</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                else:
                    st.warning("Feed parsed but returned 0 items.")
            except Exception as e:
                st.error(f"Δεν μπορώ να διαβάσω το feed: {e}")

    if submit and name and url:
        try:
            new_id = add_source(
                name=name, url=url, source_type=stype,
                category_hint=(None if hint == "(καμία)" else hint),
                enabled=enabled,
                logo_path=st.session_state.new_src_logo_path,
            )
            st.success(f"Προστέθηκε (id={new_id})")
            st.session_state.new_src_logo_path = None
            st.session_state.new_src_url_cache = ""
            st.rerun()
        except Exception as e:
            st.error(f"Σφάλμα: {e}")


# ---------------------------------------------------------------------------
# List existing sources
# ---------------------------------------------------------------------------
st.markdown("---")
sources = get_sources()

if not sources:
    st.markdown(
        '<div style="text-align:center;padding:3rem;color:var(--av-muted);">'
        'Δεν υπάρχουν ακόμα προσαρμοσμένες πηγές. '
        'Το <code>newsbeast.gr</code> είναι hardcoded στο fetch_raw.py.'
        '</div>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(f'<div style="color:var(--av-muted);font-size:12px;margin-bottom:0.5rem;">'
                f'{len(sources)} πηγές</div>', unsafe_allow_html=True)

    for s in sources:
        with st.container(border=True):
            col_logo, col_name, col_url, col_en, col_act = st.columns(
                [0.6, 2.4, 3, 1.4, 1.6]
            )

            with col_logo:
                logo_url = _logo_public_url(s.get("logo_path"))
                if logo_url:
                    st.markdown(
                        f'<img src="{logo_url}" '
                        f'style="width:44px;height:44px;border-radius:50%;'
                        f'background:#222;border:1px solid #444;object-fit:cover;" '
                        f'title="{s["name"]}" />',
                        unsafe_allow_html=True,
                    )
                else:
                    initial = (s["name"] or "?")[:1].upper()
                    st.markdown(
                        f'<div style="width:44px;height:44px;border-radius:50%;'
                        f'background:#ff5722;color:#fff;display:flex;'
                        f'align-items:center;justify-content:center;'
                        f'font-weight:700;font-size:20px;">{initial}</div>',
                        unsafe_allow_html=True,
                    )

            with col_name:
                st.markdown(
                    f'<div style="font-weight:600;color:var(--av-ink);">{s["name"]}</div>'
                    f'<div style="font-size:11px;color:var(--av-muted);font-family:ui-monospace,monospace;">'
                    f'{s["type"].upper()}</div>',
                    unsafe_allow_html=True,
                )

            with col_url:
                st.markdown(
                    f'<div style="font-family:ui-monospace,monospace;font-size:11.5px;color:var(--av-muted);'
                    f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{s["url"]}</div>'
                    f'<div style="font-size:11px;color:var(--av-muted);">'
                    f'hint: {s["category_hint"] or "—"}</div>',
                    unsafe_allow_html=True,
                )

            with col_en:
                new_enabled = st.toggle("Ενεργή", value=bool(s["enabled"]),
                                        key=f"src_enab_{s['id']}")
                if new_enabled != bool(s["enabled"]):
                    update_source(s["id"], enabled=1 if new_enabled else 0)
                    st.rerun()

            with col_act:
                if st.button("🔄 Logo", key=f"src_refetch_{s['id']}",
                             use_container_width=True, help="Auto-fetch logo"):
                    slug = _derive_slug_from_url(s["url"])
                    with st.spinner("Fetching…"):
                        result = fetch_logo(s["url"], slug)
                    if result:
                        _set_source_logo(s["id"], str(result))
                        st.toast(f"Updated: {result.name}", icon="✅")
                        st.rerun()
                    else:
                        st.toast("Fetch failed — try manual upload", icon="⚠️")
                if st.button("🗑", key=f"src_del_{s['id']}",
                             use_container_width=True, help="Διαγραφή"):
                    delete_source(s["id"])
                    st.rerun()
