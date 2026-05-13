#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pages/sources.py — Πηγές (Sources management) — ADMIN ONLY
Sprint: 3, version: 1
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import feedparser

from kteo_curate import (
    CATEGORIES, current_user, require_admin,
    get_sources, add_source, update_source, delete_source,
)

user = st.session_state.get("user") or current_user()
require_admin()

st.markdown("""
<h2 style="margin-top:0;color:var(--av-ink);font-size:18px;">Πηγές (Sources)</h2>
<p style="color:var(--av-muted);font-size:12.5px;margin-top:0;">
  RSS / Atom feeds που τροφοδοτούν το morning fetch.
</p>
""", unsafe_allow_html=True)

# Add new source
with st.expander("➕  Προσθήκη πηγής", expanded=False):
    with st.form("add_source", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Όνομα *", placeholder="π.χ. Καθημερινή")
            stype = st.selectbox("Τύπος", ["rss", "atom"])
        with col2:
            url = st.text_input("URL *", placeholder="https://...")
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
            )
            st.success(f"Προστέθηκε (id={new_id})")
            st.rerun()
        except Exception as e:
            st.error(f"Σφάλμα: {e}")

# List
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
    # Header
    st.markdown(f'<div style="color:var(--av-muted);font-size:12px;margin-bottom:0.5rem;">'
                f'{len(sources)} πηγές</div>', unsafe_allow_html=True)

    for s in sources:
        with st.container(border=True):
            col1, col2, col3, col4 = st.columns([3, 4, 2, 2])
            with col1:
                st.markdown(
                    f'<div style="font-weight:600;color:var(--av-ink);">{s["name"]}</div>'
                    f'<div style="font-size:11px;color:var(--av-muted);font-family:ui-monospace,monospace;">'
                    f'{s["type"].upper()}</div>',
                    unsafe_allow_html=True,
                )
            with col2:
                st.markdown(
                    f'<div style="font-family:ui-monospace,monospace;font-size:11.5px;color:var(--av-muted);'
                    f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{s["url"]}</div>'
                    f'<div style="font-size:11px;color:var(--av-muted);">'
                    f'hint: {s["category_hint"] or "—"}</div>',
                    unsafe_allow_html=True,
                )
            with col3:
                new_enabled = st.toggle("Ενεργή", value=bool(s["enabled"]),
                                        key=f"src_enab_{s['id']}")
                if new_enabled != bool(s["enabled"]):
                    update_source(s["id"], enabled=1 if new_enabled else 0)
                    st.rerun()
            with col4:
                if st.button("🗑 Διαγραφή", key=f"src_del_{s['id']}",
                             use_container_width=True):
                    delete_source(s["id"])
                    st.rerun()
