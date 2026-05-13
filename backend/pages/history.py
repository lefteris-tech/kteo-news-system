#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pages/history.py — Ιστορικό (History / Audit)
Sprint: 3, version: 1
Last 30 days of publishes, read-only.
"""
import sys, os, json
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from kteo_curate import (
    CATEGORIES, CATEGORY_SHORTS, current_user, get_db, get_publish_history,
)

user = st.session_state.get("user") or current_user()

st.markdown("""
<h2 style="margin-top:0;color:var(--av-ink);font-size:18px;">Ιστορικό Δημοσιεύσεων</h2>
<p style="color:var(--av-muted);font-size:12.5px;margin-top:0;">
  Τελευταίες 30 ημέρες · audit trail κάθε δημοσίευσης στις οθόνες.
</p>
""", unsafe_allow_html=True)

# Filters
col1, col2 = st.columns([2, 3])
with col1:
    limit = st.selectbox("Πλήθος εγγραφών",
                         options=[10, 30, 60, 90],
                         index=1)
with col2:
    # Pull distinct users for filter
    conn = get_db()
    distinct = conn.execute(
        "SELECT DISTINCT triggered_by FROM publish_log ORDER BY triggered_by"
    ).fetchall()
    user_options = ["(όλοι)"] + [r[0] for r in distinct]
    selected_user = st.selectbox("Φίλτρο χρήστη", user_options, index=0)

# Fetch history
rows = get_publish_history(limit)
if selected_user != "(όλοι)":
    rows = [r for r in rows if r["triggered_by"] == selected_user]

if not rows:
    st.info("Δεν υπάρχουν δημοσιεύσεις στο διάστημα.")
else:
    st.markdown(f'<div style="color:var(--av-muted);font-size:12px;margin-bottom:0.5rem;">'
                f'{len(rows)} εγγραφές</div>',
                unsafe_allow_html=True)

    for row in rows:
        # Parse JSON
        try:
            per_cat = json.loads(row["items_per_category_json"] or "{}")
        except Exception:
            per_cat = {}

        is_manual = row["total_items"] == 1 and len(per_cat) == 1

        # Build small per-category strip
        strip = " ".join(
            f'<span style="color:{"var(--av-accent)" if c["key"] in per_cat and len(per_cat[c["key"]])>0 else "var(--av-muted)"};">'
            f'{c["short"]} <span style="font-weight:600;color:var(--av-ink);">'
            f'{len(per_cat.get(c["key"], []))}</span></span>'
            for c in CATEGORIES
        )

        # Format date in Greek (best-effort)
        ts = row["created_at"][:16]  # yyyy-mm-dd HH:MM

        manual_badge = (
            '<span style="display:inline-block;padding:1px 6px;background:var(--av-accent);'
            'color:white;border-radius:3px;font-size:9px;font-weight:700;margin-left:0.5rem;">'
            'MANUAL</span>' if is_manual else ""
        )

        header_html = f"""
        <div style="display:flex;align-items:center;gap:1rem;padding:0.5rem 0;">
          <div style="color:var(--av-ink);font-size:13px;font-weight:600;font-family:ui-monospace,monospace;min-width:140px;">
            {ts}
          </div>
          <div style="color:var(--av-muted);font-size:12px;min-width:220px;">
            από <span style="color:var(--av-ink);">{row['triggered_by']}</span>{manual_badge}
          </div>
          <div style="color:var(--av-muted);font-size:12px;">
            <span style="color:var(--av-ink);font-weight:600;">{row['total_items']}</span> άρθρα
          </div>
          <div style="margin-left:auto;font-size:11.5px;font-family:ui-monospace,monospace;">
            {strip}
          </div>
        </div>
        """

        with st.expander("", expanded=False):
            st.markdown(header_html, unsafe_allow_html=True)

            # Show per-category breakdown of IDs
            if per_cat:
                st.markdown(
                    '<div style="margin-top:0.5rem;color:var(--av-muted);font-size:11.5px;">'
                    'Items per category (DB ids):</div>',
                    unsafe_allow_html=True,
                )
                for c in CATEGORIES:
                    ids = per_cat.get(c["key"], [])
                    if ids:
                        ids_str = ", ".join(str(i) for i in ids)
                        st.markdown(
                            f'<div style="font-size:12px;margin-left:1rem;">'
                            f'<span style="color:var(--av-accent);font-weight:600;">{c["label"]}:</span> '
                            f'<span style="font-family:ui-monospace,monospace;color:var(--av-muted);">{ids_str}</span></div>',
                            unsafe_allow_html=True,
                        )

        # Render the inline header above the expander (since expander label is empty)
        st.markdown(header_html, unsafe_allow_html=True)
        st.markdown(
            '<hr style="border-top:1px solid var(--av-border);margin:0;" />',
            unsafe_allow_html=True,
        )
