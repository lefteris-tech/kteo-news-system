#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pages/manual.py — Χειροκίνητη Προσθήκη (Manual Injection)
Sprint: 3, version: 1
Two modes: Push now (immediate XML inject + sync) or Add to pool.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from kteo_curate import (
    CATEGORIES, current_user, add_to_pool, push_now,
)

user = st.session_state.get("user") or current_user()

st.markdown("""
<h2 style="margin-top:0;color:var(--av-ink);font-size:18px;">
  Χειροκίνητη προσθήκη άρθρου
</h2>
<p style="color:var(--av-muted);font-size:12.5px;margin-top:0;">
  Για επείγουσες ανακοινώσεις, εσωτερικά νέα ή ό,τι ο αυτοματισμός δεν έπιασε.
</p>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# Mode toggle
# -----------------------------------------------------------------------------
mode = st.radio(
    "Λειτουργία",
    options=["push_now", "pool"],
    index=0,
    format_func=lambda m: (
        "Δημοσίευση τώρα — άμεση εισαγωγή στις οθόνες (θέση #1)"
        if m == "push_now"
        else "Προσθήκη στη λίστα — προστίθεται στις σημερινές επιλογές"
    ),
    horizontal=False,
    label_visibility="collapsed",
)

st.divider()

# -----------------------------------------------------------------------------
# Form
# -----------------------------------------------------------------------------
with st.form("manual_form", clear_on_submit=False):
    col1, col2 = st.columns(2)
    with col1:
        category = st.selectbox(
            "Κατηγορία *",
            options=[c["key"] for c in CATEGORIES],
            format_func=lambda k: next(c["label"] for c in CATEGORIES if c["key"] == k),
        )
    with col2:
        link = st.text_input(
            "Σύνδεσμος (URL)",
            help="Προαιρετικό · χρησιμοποιείται μόνο για αρχειακούς λόγους.",
        )

    title = st.text_input(
        "Τίτλος *",
        help="Ελάχιστο 5 χαρακτήρες",
    )
    if title:
        n = len(title)
        if n < 5:
            st.caption(f"⚠ {n} χαρ. (ελάχιστο 5)")
        else:
            st.caption(f"✓ {n} χαρ.")

    col3, col4 = st.columns([3, 1])
    with col3:
        image_url = st.text_input(
            "Εικόνα (URL)",
            help="Προαιρετικό · 16:10 ή 3:2 ιδανικά",
        )
    with col4:
        if image_url:
            st.markdown(
                f'<img src="{image_url}" style="width:100%;max-width:120px;'
                f'height:80px;border-radius:0.375rem;object-fit:cover;border:1px solid var(--av-border);" '
                f'onerror="this.style.opacity=0.3"/>',
                unsafe_allow_html=True,
            )

    summary = st.text_area(
        "Σύνοψη *",
        height=120,
        help="120–160 χαρακτήρες ιδανικά. Ελάχιστο 20.",
    )
    if summary:
        n = len(summary)
        target_ok = 120 <= n <= 160
        if n < 20:
            st.caption(f"⚠ {n} χαρ. (ελάχιστο 20)")
        else:
            label = "✓" if target_ok else "ⓘ"
            st.caption(f"{label} {n} χαρ. (στόχος 120–160)")

    mode_msg = (
        "Mode: Push now — θα εμφανιστεί στις 53 οθόνες σε 10–15 λεπτά"
        if mode == "push_now"
        else "Mode: Add to pool — θα εμφανιστεί στην καρτέλα Επιμέλεια για επιλογή"
    )
    st.markdown(
        f'<div style="margin:0.5rem 0;color:var(--av-muted);font-size:12px;">'
        f'<span style="color:var(--av-accent);font-weight:600;">{mode_msg}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    submitted = st.form_submit_button(
        ("🚀  Δημοσίευση τώρα στις οθόνες" if mode == "push_now"
         else "➕  Προσθήκη στη λίστα σήμερα"),
        type="primary",
        use_container_width=True,
    )

# -----------------------------------------------------------------------------
# Handle submission
# -----------------------------------------------------------------------------
if submitted:
    errs = []
    if not title or len(title) < 5:
        errs.append("Ο τίτλος πρέπει να είναι τουλάχιστον 5 χαρακτήρες.")
    if not summary or len(summary) < 20:
        errs.append("Η σύνοψη πρέπει να είναι τουλάχιστον 20 χαρακτήρες.")

    if errs:
        for e in errs:
            st.error(e)
    else:
        if mode == "push_now":
            # Confirmation step — second click required
            confirmed = st.session_state.get("manual_push_confirmed", False)
            if not confirmed:
                cat_label = next(c["label"] for c in CATEGORIES if c["key"] == category)
                st.warning(
                    f"⚠ Σίγουρα; Το άρθρο θα εμφανιστεί στις **53 οθόνες** "
                    f"σε 10–15 λεπτά, στη θέση #1 της κατηγορίας **{cat_label}**.",
                    icon="⚠",
                )
                cc1, cc2 = st.columns(2)
                with cc1:
                    if st.button("❌ Ακύρωση", use_container_width=True):
                        st.rerun()
                with cc2:
                    if st.button("✓ Ναι, δημοσίευση",
                                 type="primary", use_container_width=True):
                        st.session_state["manual_push_confirmed"] = True
                        st.session_state["manual_pending"] = {
                            "category": category, "title": title,
                            "summary": summary, "link": link, "image_url": image_url,
                        }
                        st.rerun()
            else:
                payload = st.session_state.pop("manual_pending", {})
                st.session_state["manual_push_confirmed"] = False
                with st.spinner("Δημοσίευση σε εξέλιξη…"):
                    result = push_now(
                        by_email=user["email"],
                        **payload,
                    )
                if result["ok"]:
                    st.success(
                        f"✅ Δημοσιεύτηκε. Θα εμφανιστεί στις οθόνες σε ~10–15 λεπτά. "
                        f"(item id={result['item_id']})"
                    )
                    if not result["sync_ok"]:
                        st.warning(
                            "Το XML γράφτηκε αλλά το playlist_sync απέτυχε. "
                            "Το Yodeck θα συγχρονιστεί στον επόμενο κύκλο."
                        )
                    with st.expander("Λεπτομέρειες playlist_sync"):
                        st.code(result["sync_output"][:2000])
                    st.balloons()
                else:
                    st.error("❌ Κάτι πήγε στραβά. Δες τα logs.")
        else:
            # Add to pool — silent insert, no confirmation
            new_id = add_to_pool(
                by_email=user["email"],
                category=category,
                title=title,
                summary=summary,
                link=link,
                image_url=image_url,
            )
            st.success(
                f"✓ Προστέθηκε στη λίστα σήμερα (id={new_id}). "
                f"Δες το στην καρτέλα **Σημερινή Επιμέλεια** στην κατηγορία "
                f"**{next(c['label'] for c in CATEGORIES if c['key'] == category)}**."
            )
