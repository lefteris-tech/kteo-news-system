#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pages/filters.py — Φίλτρα (Filters management) — ADMIN ONLY
Sprint: 3, version: 1
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from kteo_curate import (
    CATEGORIES, current_user, require_admin,
    get_filters, add_filter, update_filter, delete_filter,
)

user = st.session_state.get("user") or current_user()
require_admin()

st.markdown("""
<h2 style="margin-top:0;color:var(--av-ink);font-size:18px;">Φίλτρα (Filters)</h2>
<p style="color:var(--av-muted);font-size:12.5px;margin-top:0;">
  Keyword include / exclude rules. Εφαρμόζονται κατά το morning fetch (07:50 Mon-Fri).
  <span style="color:var(--av-warning);">Σημείωση:</span> για την παρούσα έκδοση, το <code>fetch_raw.py</code>
  χρησιμοποιεί ακόμα το hardcoded blocklist. Sprint 4 cleanup will switch to this table.
</p>
""", unsafe_allow_html=True)

# Add new filter
with st.expander("➕  Προσθήκη φίλτρου", expanded=False):
    with st.form("add_filter", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            scope = st.selectbox("Scope", ["global", "category"])
        with col2:
            if scope == "category":
                cat = st.selectbox(
                    "Κατηγορία",
                    options=[c["key"] for c in CATEGORIES],
                    format_func=lambda k: next(c["label"] for c in CATEGORIES if c["key"] == k),
                )
            else:
                cat = None
                st.markdown('<div style="color:var(--av-muted);font-size:12px;'
                            'margin-top:1.75rem;">(σε όλες τις κατηγορίες)</div>',
                            unsafe_allow_html=True)
        with col3:
            mode = st.selectbox("Mode", ["exclude", "include"])

        keyword = st.text_input("Keyword *",
                                placeholder="π.χ. έγκλημα, Tesla, ΑΕΠ")
        enabled = st.checkbox("Ενεργό", value=True)

        submit = st.form_submit_button("Προσθήκη", type="primary",
                                       use_container_width=True)

    if submit and keyword:
        new_id = add_filter(
            scope=scope, category=cat, mode=mode,
            keyword=keyword.strip(), enabled=enabled,
        )
        st.success(f"Προστέθηκε (id={new_id})")
        st.rerun()

# List
st.markdown("---")
filters = get_filters()

if not filters:
    st.markdown(
        '<div style="text-align:center;padding:3rem;color:var(--av-muted);">'
        'Δεν υπάρχουν προσαρμοσμένα φίλτρα ακόμα. Το <code>fetch_raw.py</code> '
        'χρησιμοποιεί το hardcoded blocklist του Phase 1.'
        '</div>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(f'<div style="color:var(--av-muted);font-size:12px;margin-bottom:0.5rem;">'
                f'{len(filters)} φίλτρα</div>', unsafe_allow_html=True)

    for f in filters:
        with st.container(border=True):
            col1, col2, col3, col4, col5 = st.columns([1.5, 2, 1, 3, 1.5])
            with col1:
                badge_color = "var(--av-danger)" if f["mode"] == "exclude" else "var(--av-success)"
                st.markdown(
                    f'<div style="padding:2px 8px;display:inline-block;'
                    f'border:1px solid {badge_color};color:{badge_color};'
                    f'border-radius:999px;font-size:11px;font-weight:600;">'
                    f'{f["mode"].upper()}</div>',
                    unsafe_allow_html=True,
                )
            with col2:
                scope_label = "Global" if f["scope"] == "global" else f"Cat: {f['category']}"
                st.markdown(
                    f'<div style="color:var(--av-muted);font-size:12px;">{scope_label}</div>',
                    unsafe_allow_html=True,
                )
            with col3:
                pass  # spacer
            with col4:
                st.markdown(
                    f'<div style="font-family:ui-monospace,monospace;color:var(--av-ink);'
                    f'font-size:13px;">{f["keyword"]}</div>',
                    unsafe_allow_html=True,
                )
            with col5:
                en = st.toggle("On", value=bool(f["enabled"]),
                               key=f"fil_enab_{f['id']}", label_visibility="collapsed")
                if en != bool(f["enabled"]):
                    update_filter(f["id"], enabled=1 if en else 0)
                    st.rerun()
                if st.button("🗑", key=f"fil_del_{f['id']}",
                             help="Διαγραφή"):
                    delete_filter(f["id"])
                    st.rerun()
