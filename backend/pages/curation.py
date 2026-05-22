#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pages/curation.py — Σημερινή Επιμέλεια (Today's Curation)
Sprint: 6, version: 1

Sprint 6 redesign: items arrive WITHOUT a category (fetch_raw no longer
classifies). The curator assigns a category from a dropdown before being
allowed to select an item for publication.

Layout:
- One unified queue of all today's pending+selected items (no category tabs)
- Each row: thumb / title+snippet / category dropdown / select checkbox
- Select checkbox is DISABLED while category placeholder is shown
- Clearing the category on a selected item auto-deselects (DB-side guard)
- Bottom bar: count of selected items + Δημοσίευση button (preview)
- Preview groups selected items by user-assigned category
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from kteo_curate import (
    CATEGORIES, CATEGORY_KEYS, CATEGORY_LABELS,
    current_user,
    get_pending_items,
    relative_age_greek, render_status_strip,
    set_category, set_selection, today_str,
    trigger_full_publish, update_summary,
)

PLACEHOLDER = "— Διάλεξε Κατηγορία —"

user = st.session_state.get("user") or current_user()

# -----------------------------------------------------------------------------
# Top status strip — total today, not per-category (categories are NULL until
# the curator assigns them in this very page).
# -----------------------------------------------------------------------------
items_today = get_pending_items()  # all today, pending+selected, no category filter
total_available = len(items_today)
selected_total = sum(1 for it in items_today if it["status"] == "selected")
uncategorised_selected = sum(
    1 for it in items_today
    if it["status"] == "selected" and not (it.get("classified_category") or "").strip()
)

render_status_strip(user, total_available)

st.markdown(
    f'<div style="color:var(--av-muted);font-size:12.5px;margin:0.5rem 0 1rem 0;">'
    f'<span style="color:var(--av-ink);font-weight:600;">{total_available}</span> '
    f'άρθρα στην ουρά · <span style="color:var(--av-accent);font-weight:600;">'
    f'{selected_total}</span> επιλεγμένα'
    f'</div>',
    unsafe_allow_html=True,
)

if total_available == 0:
    st.markdown("""
    <div style="text-align:center;padding:3rem 1rem;color:var(--av-muted);">
      <div style="font-size:48px;margin-bottom:0.5rem;">📰</div>
      <div style="color:var(--av-ink);font-size:14px;font-weight:500;">
        Δεν υπάρχουν άρθρα στην ουρά για σήμερα.
      </div>
      <div style="font-size:12.5px;margin-top:0.25rem;">
        Περίμενε το επόμενο fetch (07:50 Δευ–Παρ) ή τρέξε χειροκίνητα το
        <code>fetch_raw_cron.sh</code> στο Pi.
      </div>
    </div>
    """, unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# Render one row per item
# -----------------------------------------------------------------------------

def render_row(item: dict):
    is_selected = (item["status"] == "selected")
    is_manual = (item["source_type"] == "manual")
    current_cat = item.get("classified_category") or None
    has_category = current_cat in CATEGORY_KEYS

    age = relative_age_greek(item.get("pub_date") or item.get("created_at"))
    thumb_bg = (
        f"background-image:url('{item['image_url']}');"
        if item.get("image_url") else ""
    )
    snippet = (item.get("body_first_para") or "")[:180]

    src_pill = (
        '<span class="av-pill manual">M</span> '
        '<span style="color:var(--av-accent)">Χειροκίνητο</span>'
        if is_manual
        else ''
    )

    card_cls = "av-card selected" if is_selected else "av-card"

    # Card content + dropdown + checkbox
    col_content, col_cat, col_sel = st.columns([8, 3, 1])

    with col_content:
        st.markdown(f"""
<div class="{card_cls}"><div class="row"><div class="av-thumb" style="{thumb_bg}"></div><div style="flex:1;min-width:0;"><div class="av-title">{item['title']}</div><div class="av-snippet">{snippet}</div><div class="av-meta">{src_pill}<span>{age}</span></div></div></div></div>
        """, unsafe_allow_html=True)

    with col_cat:
        options = [PLACEHOLDER] + [CATEGORY_LABELS[k] for k in CATEGORY_KEYS]
        current_label = CATEGORY_LABELS[current_cat] if has_category else PLACEHOLDER
        idx = options.index(current_label)

        new_label = st.selectbox(
            "Κατηγορία",
            options=options,
            index=idx,
            key=f"cat_{item['id']}",
            label_visibility="collapsed",
        )
        # Map label back to slug
        new_slug = None
        if new_label != PLACEHOLDER:
            for k, lbl in CATEGORY_LABELS.items():
                if lbl == new_label:
                    new_slug = k
                    break

        # Persist if changed
        if new_slug != current_cat:
            set_category(item["id"], new_slug)
            st.rerun()

    with col_sel:
        cb_key = f"sel_{item['id']}"
        cb_disabled = not has_category
        new_value = st.checkbox(
            "Επιλογή",
            value=is_selected,
            key=cb_key,
            disabled=cb_disabled,
            label_visibility="collapsed",
            help=("Πρώτα διάλεξε κατηγορία" if cb_disabled
                  else "Δείκτης επιλογής για δημοσίευση"),
        )
        if new_value != is_selected and has_category:
            set_selection(item["id"], new_value, user["email"])
            st.rerun()


for item in items_today:
    render_row(item)


# -----------------------------------------------------------------------------
# Preview modal — items grouped by user-assigned category
# -----------------------------------------------------------------------------
@st.dialog("Προεπισκόπηση δημοσίευσης", width="large")
def preview_dialog():
    # Group SELECTED items by their now-assigned category
    selected_items = [it for it in get_pending_items(statuses=("selected",))]
    by_cat: dict[str, list[dict]] = {}
    for it in selected_items:
        slug = it.get("classified_category")
        if slug:
            by_cat.setdefault(slug, []).append(it)

    total = sum(len(v) for v in by_cat.values())
    st.markdown(
        f'<div style="color:var(--av-muted);font-size:12.5px;margin-bottom:1rem;">'
        f'{total} άρθρα σε {len(by_cat)} κατηγορίες · '
        f'Το Haiku θα παράξει συνόψεις κατά τη δημοσίευση'
        f'</div>',
        unsafe_allow_html=True,
    )

    for slug in CATEGORY_KEYS:
        items = by_cat.get(slug, [])
        if not items:
            continue
        st.markdown(
            f'<div style="color:var(--av-accent);font-weight:600;font-size:13px;'
            f'margin:0.75rem 0 0.5rem 0;">{CATEGORY_LABELS[slug]} · {len(items)} άρθρα</div>',
            unsafe_allow_html=True,
        )
        for it in items:
            with st.container(border=True):
                st.markdown(
                    f'<div style="font-size:13px;line-height:1.3;">{it["title"]}</div>',
                    unsafe_allow_html=True,
                )
                existing_summary = it.get("haiku_summary") or ""
                if existing_summary.strip():
                    new_sum = st.text_area(
                        "Σύνοψη",
                        value=existing_summary,
                        height=80,
                        label_visibility="collapsed",
                        key=f"prev_summary_{it['id']}",
                    )
                    n = len(new_sum)
                    ok = 120 <= n <= 160
                    st.caption(f"{'✓' if ok else '⚠'} {n} χαρ. (στόχος 120–160)")
                    if new_sum != existing_summary:
                        update_summary(it["id"], new_sum)
                else:
                    st.caption(
                        "Η σύνοψη θα παραχθεί από το Haiku κατά τη δημοσίευση "
                        "(120–160 χαρακτήρες)."
                    )

    st.divider()
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("Ακύρωση", use_container_width=True):
            st.rerun()
    with c2:
        if st.button("Επιβεβαίωση και δημοσίευση",
                     type="primary", use_container_width=True):
            with st.spinner("Δημοσίευση σε εξέλιξη… (το Haiku γράφει συνόψεις)"):
                result = trigger_full_publish(user["email"], dry_run=False)
            if result["ok"]:
                st.success(
                    f"✅ Δημοσιεύτηκαν {total} άρθρα. "
                    f"Θα εμφανιστούν στις οθόνες σε 10–15 λεπτά."
                )
                st.session_state["last_publish_result"] = result
                st.balloons()
            else:
                # Sprint 6: distinguish the publish-time failure modes
                code = result.get("code", -1)
                if code == 4:
                    st.error(
                        "❌ Σφάλμα: ένα ή περισσότερα επιλεγμένα άρθρα δεν "
                        "έχουν κατηγορία. Δες λεπτομέρειες παρακάτω."
                    )
                elif code == 3:
                    st.error(
                        "❌ Η σύνοψη Haiku απέτυχε. Τα άρθρα παραμένουν "
                        "επιλεγμένα — δοκίμασε ξανά όταν επανέλθει η API."
                    )
                else:
                    st.error(f"❌ Σφάλμα δημοσίευσης (exit {code})")
                with st.expander("Λεπτομέρειες"):
                    st.code(result.get("stderr") or result.get("stdout") or "")


# -----------------------------------------------------------------------------
# Bottom action bar
# -----------------------------------------------------------------------------
st.markdown("---")
bcol1, bcol2 = st.columns([3, 2])

with bcol1:
    if selected_total > 0:
        if uncategorised_selected:
            msg = (
                f'<span style="color:var(--av-warning);">⚠ '
                f'{uncategorised_selected} επιλεγμένα χωρίς κατηγορία — '
                f'πρέπει να ανατεθούν πριν τη δημοσίευση</span>'
            )
        else:
            msg = (
                f'<span style="color:var(--av-ink);font-weight:600;">'
                f'{selected_total}</span> '
                f'<span style="color:var(--av-muted);">άρθρα έτοιμα για δημοσίευση</span>'
            )
        st.markdown(
            f'<div style="font-size:12.5px;">{msg}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="color:var(--av-muted);font-size:12.5px;">'
            'Διάλεξε κατηγορία και τσέκαρε για να επιλέξεις άρθρα.'
            '</div>',
            unsafe_allow_html=True,
        )

with bcol2:
    can_publish = (selected_total > 0 and uncategorised_selected == 0)
    if st.button("🚀  Προεπισκόπηση & Δημοσίευση",
                 type="primary",
                 disabled=not can_publish,
                 use_container_width=True):
        preview_dialog()
