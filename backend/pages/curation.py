#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pages/curation.py — Σημερινή Επιμέλεια (Today's Curation)
Sprint: 3, version: 1
The main 90%-use-case page.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from kteo_curate import (
    CATEGORIES, CATEGORY_KEYS, CATEGORY_LABELS,
    confidence_class, current_user,
    get_pending_counts, get_pending_items,
    relative_age_greek, render_status_strip,
    set_selection, today_str, trigger_full_publish,
    update_summary,
)

user = st.session_state.get("user") or current_user()

# -----------------------------------------------------------------------------
# Top status strip
# -----------------------------------------------------------------------------
counts = get_pending_counts(today_str())
available = sum(c.get("pending", 0) + c.get("selected", 0)
                for c in counts.values())
selected_total = sum(c.get("selected", 0) for c in counts.values())

render_status_strip(user, available)

# -----------------------------------------------------------------------------
# Category tabs
# -----------------------------------------------------------------------------
tab_labels = [
    f"{c['label']} ({counts.get(c['key'], {}).get('pending', 0) + counts.get(c['key'], {}).get('selected', 0)})"
    for c in CATEGORIES
]
tabs = st.tabs(tab_labels)

# Helper to render one article row
def render_article(item: dict, key_prefix: str):
    is_selected = (item["status"] == "selected")
    is_manual = (item["source_type"] == "manual")
    conf = float(item.get("haiku_confidence") or 0)
    conf_pct = int(conf * 100) if conf <= 1 else int(conf)
    conf_cls = confidence_class(conf if conf <= 1 else conf / 100)
    age = relative_age_greek(item.get("pub_date") or item.get("created_at"))

    # Image: use real image_url if available, else CSS-only thumb
    thumb_bg = ""
    if item.get("image_url"):
        thumb_bg = f"background-image:url('{item['image_url']}');"

    snippet = (item.get("body_first_para") or item.get("haiku_summary") or "")[:200]

    src_pill = (
        '<span class="av-pill manual">M</span> <span style="color:var(--av-accent)">Χειροκίνητο</span>'
        if is_manual
        else f'<span class="av-pill">newsbeast.gr</span>'  # TODO: lookup source name when sources table populated
    )

    card_cls = "av-card selected" if is_selected else "av-card"

    # Layout: card content + checkbox column
    col_a, col_b = st.columns([12, 1])
    with col_a:
        st.markdown(f"""
        <div class="{card_cls}">
          <div class="row">
            <div class="av-thumb" style="{thumb_bg}"></div>
            <div style="flex:1;min-width:0;">
              <div class="av-title">{item['title']}</div>
              <div class="av-snippet">{snippet}</div>
              <div class="av-meta">
                {src_pill}
                <span>{age}</span>
                <span><span class="av-dot {conf_cls}"></span>{conf_pct}%</span>
              </div>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)
    with col_b:
        cb_key = f"{key_prefix}_cb_{item['id']}"
        new_value = st.checkbox(
            "Επιλογή",
            value=is_selected,
            key=cb_key,
            label_visibility="collapsed",
        )
        if new_value != is_selected:
            set_selection(item["id"], new_value, user["email"])
            st.rerun()

# Render each tab
for i, c in enumerate(CATEGORIES):
    with tabs[i]:
        cat_key = c["key"]
        items = get_pending_items(category=cat_key)
        cat_selected = sum(1 for it in items if it["status"] == "selected")

        # Counter
        max_per_cat = 3
        counter_cls = "av-counter over" if cat_selected > max_per_cat else "av-counter"
        counter_html = f"""
        <div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:1rem;">
          <div style="flex:1;">
            <h3 style="margin:0;color:var(--av-ink);font-size:15px;">
              {c['label']} — διαθέσιμα άρθρα
            </h3>
          </div>
          <div class="{counter_cls}">
            <span style="color:var(--av-muted);">Επιλεγμένα:</span>
            <span class="count">{cat_selected}</span>
            <span style="color:var(--av-muted);">/ {max_per_cat}</span>
            {'<span style="color:var(--av-warning);font-size:11.5px;">⚠ πάνω από το όριο</span>'
             if cat_selected > max_per_cat else ''}
          </div>
        </div>
        """
        st.markdown(counter_html, unsafe_allow_html=True)

        if not items:
            st.markdown(f"""
            <div style="text-align:center;padding:3rem 1rem;color:var(--av-muted);">
              <div style="font-size:48px;margin-bottom:0.5rem;">📰</div>
              <div style="color:var(--av-ink);font-size:14px;font-weight:500;">
                Δεν υπάρχουν διαθέσιμα άρθρα για αυτήν την κατηγορία σήμερα.
              </div>
              <div style="font-size:12.5px;margin-top:0.25rem;max-width:30rem;margin-left:auto;margin-right:auto;">
                Όλα τα άρθρα φιλτραρίστηκαν ή έχουν ήδη δημοσιευτεί.
                Μπορείς να εισάγεις χειροκίνητα από την καρτέλα "Χειροκίνητη Προσθήκη".
              </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            for item in items:
                render_article(item, key_prefix=f"tab_{cat_key}")

# -----------------------------------------------------------------------------
# Preview modal (Streamlit @st.dialog)
# -----------------------------------------------------------------------------
@st.dialog("Προεπισκόπηση δημοσίευσης", width="large")
def preview_dialog():
    selected_by_cat: dict[str, list[dict]] = {}
    for c in CATEGORIES:
        items = get_pending_items(category=c["key"], statuses=("selected",))
        if items:
            selected_by_cat[c["key"]] = items

    total = sum(len(v) for v in selected_by_cat.values())
    st.markdown(
        f'<div style="color:var(--av-muted);font-size:12.5px;margin-bottom:1rem;">'
        f'{total} άρθρα σε {len(selected_by_cat)} κατηγορίες · '
        f'Έλεγξε τις συνόψεις πριν τη δημοσίευση'
        f'</div>',
        unsafe_allow_html=True,
    )

    for slug, items in selected_by_cat.items():
        st.markdown(
            f'<div style="color:var(--av-accent);font-weight:600;font-size:13px;'
            f'margin:0.75rem 0 0.5rem 0;">{CATEGORY_LABELS[slug]} · {len(items)} άρθρα</div>',
            unsafe_allow_html=True,
        )
        for it in items:
            with st.container(border=True):
                col1, col2 = st.columns([1, 2])
                with col1:
                    st.markdown(
                        f'<div style="color:var(--av-muted);font-size:12px;line-height:1.3;">{it["title"]}</div>',
                        unsafe_allow_html=True,
                    )
                with col2:
                    new_sum = st.text_area(
                        "Σύνοψη",
                        value=it.get("haiku_summary") or "",
                        height=80,
                        label_visibility="collapsed",
                        key=f"prev_summary_{it['id']}",
                    )
                    n = len(new_sum)
                    ok = 120 <= n <= 160
                    st.caption(
                        f"{'✓' if ok else '⚠'} {n} χαρ. (στόχος 120–160)"
                    )
                    if new_sum != it.get("haiku_summary"):
                        update_summary(it["id"], new_sum)

    st.divider()
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("Ακύρωση", use_container_width=True):
            st.rerun()
    with c2:
        if st.button("Επιβεβαίωση και δημοσίευση",
                     type="primary", use_container_width=True):
            with st.spinner("Δημοσίευση σε εξέλιξη…"):
                result = trigger_full_publish(user["email"], dry_run=False)
            if result["ok"]:
                st.success(
                    f"✅ Δημοσιεύτηκαν {total} άρθρα. "
                    f"Θα εμφανιστούν στις οθόνες σε 10–15 λεπτά."
                )
                st.session_state["last_publish_result"] = result
                st.balloons()
            else:
                st.error(f"❌ Σφάλμα δημοσίευσης (exit {result['code']})")
                with st.expander("Λεπτομέρειες"):
                    st.code(result["stderr"] or result["stdout"])

# -----------------------------------------------------------------------------
# Bottom action bar
# -----------------------------------------------------------------------------
st.markdown("---")
bcol1, bcol2, bcol3 = st.columns([3, 1, 2])
with bcol1:
    if selected_total > 0:
        live_warning = ""
        # warn if any category will have 0 items
        warn_cats = [c["label"] for c in CATEGORIES
                     if counts.get(c["key"], {}).get("selected", 0) == 0]
        if warn_cats:
            live_warning = (
                f'<span style="color:var(--av-warning);font-size:11.5px;">'
                f'⚠ Κενές κατηγορίες: {", ".join(warn_cats)}'
                f'</span>'
            )
        st.markdown(
            f'<div style="color:var(--av-muted);font-size:12.5px;">'
            f'<span style="color:var(--av-ink);font-weight:600;">{selected_total}</span> '
            f'άρθρα επιλεγμένα · {live_warning}'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="color:var(--av-muted);font-size:12.5px;">'
            'Επίλεξε άρθρα από τις καρτέλες πάνω για να δημοσιεύσεις.'
            '</div>',
            unsafe_allow_html=True,
        )

with bcol2:
    if st.button("👁  Προεπισκόπηση",
                 disabled=(selected_total == 0),
                 use_container_width=True):
        preview_dialog()

with bcol3:
    if st.button("🚀  Δημοσίευση στις οθόνες",
                 type="primary",
                 disabled=(selected_total == 0),
                 use_container_width=True):
        preview_dialog()
