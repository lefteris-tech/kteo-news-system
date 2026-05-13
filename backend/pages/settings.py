#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pages/settings.py — Ρυθμίσεις (Settings) — ADMIN ONLY
Sprint: 3, version: 1
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from kteo_curate import current_user, require_admin, get_users, update_user

user = st.session_state.get("user") or current_user()
require_admin()

st.markdown("""
<h2 style="margin-top:0;color:var(--av-ink);font-size:18px;">Ρυθμίσεις (Settings)</h2>
""", unsafe_allow_html=True)

# General
with st.expander("⚙️ General", expanded=True):
    col1, col2 = st.columns(2)
    with col1:
        st.markdown('<div style="color:var(--av-muted);font-size:12px;">App name</div>'
                    '<div style="color:var(--av-ink);">Autovision News Curator</div>',
                    unsafe_allow_html=True)
        st.markdown('<div style="color:var(--av-muted);font-size:12px;margin-top:0.75rem;">Timezone</div>'
                    '<div style="color:var(--av-ink);font-family:ui-monospace,monospace;">Europe/Athens</div>',
                    unsafe_allow_html=True)
    with col2:
        st.markdown('<div style="color:var(--av-muted);font-size:12px;">Support email</div>'
                    '<div style="color:var(--av-ink);">lefteris@dronepros.gr</div>',
                    unsafe_allow_html=True)

# Schedule
with st.expander("⏰ Schedule (read-only, edit via cron)", expanded=True):
    st.markdown("""
    <div style="font-family:ui-monospace,monospace;color:var(--av-ink);font-size:13px;line-height:1.7;">
    <span style="color:var(--av-accent);">07:50 Mon-Fri</span> · fetch_raw.py (classify + summarize via Claude Haiku)<br>
    <span style="color:var(--av-muted);">08:00–09:00</span> · curation window (informational, not enforced)<br>
    <span style="color:var(--av-muted);">Sat/Sun</span> · skip (no fetch, screens stay on Friday's content via carry_over)<br>
    <span style="color:var(--av-muted);">Greek public holidays</span> · auto-skipped
    </div>
    """, unsafe_allow_html=True)
    st.markdown(
        '<div style="margin-top:0.5rem;color:var(--av-warning);font-size:12px;">'
        '⚠ Phase 1 root cron (07:40 Mon-Sat) still runs side-by-side. '
        'Will be disabled in Sprint 4 cutover.'
        '</div>',
        unsafe_allow_html=True,
    )

# Users
with st.expander("👥 Users", expanded=True):
    users = get_users()
    st.markdown(f'<div style="color:var(--av-muted);font-size:12px;margin-bottom:0.5rem;">'
                f'{len(users)} εγγεγραμμένοι χρήστες</div>',
                unsafe_allow_html=True)

    for u in users:
        with st.container(border=True):
            col1, col2, col3, col4 = st.columns([3, 1.5, 2, 1])
            with col1:
                st.markdown(
                    f'<div style="color:var(--av-ink);font-weight:600;">{u["email"]}</div>'
                    f'<div style="font-size:11px;color:var(--av-muted);">'
                    f'First seen: {u["first_seen"][:16] if u.get("first_seen") else "—"}</div>',
                    unsafe_allow_html=True,
                )
            with col2:
                role_options = ["curator", "admin"]
                new_role = st.selectbox(
                    "Role", options=role_options,
                    index=role_options.index(u["role"]),
                    key=f"u_role_{u['email']}",
                    label_visibility="collapsed",
                )
                if new_role != u["role"]:
                    if u["email"] == user["email"] and new_role != "admin":
                        st.error("Δεν μπορείς να αφαιρέσεις τον δικό σου admin role.")
                    else:
                        update_user(u["email"], role=new_role)
                        st.rerun()
            with col3:
                ll = u.get("last_login")
                st.markdown(
                    f'<div style="color:var(--av-muted);font-size:11.5px;">'
                    f'Last login:<br>'
                    f'<span style="font-family:ui-monospace,monospace;">'
                    f'{ll[:16] if ll else "(never)"}</span></div>',
                    unsafe_allow_html=True,
                )
            with col4:
                en = st.toggle("On", value=bool(u["enabled"]),
                               key=f"u_enab_{u['email']}",
                               label_visibility="collapsed")
                if en != bool(u["enabled"]):
                    update_user(u["email"], enabled=1 if en else 0)
                    st.rerun()

# About
with st.expander("ℹ About", expanded=False):
    st.markdown("""
    <div style="color:var(--av-ink);font-size:12.5px;line-height:1.7;">
    <strong>Autovision News Curator</strong> v0.3 (Sprint 3)<br>
    Deploy date: 2026-05-13<br>
    Owner: Lefteris (Amazing Projects ΙΚΕ) · <a style="color:var(--av-accent);" href="mailto:lefteris@dronepros.gr">lefteris@dronepros.gr</a><br>
    <br>
    Logs: <code style="background:var(--av-bg);padding:2px 6px;border-radius:3px;">/var/log/news_aggregator.log</code><br>
    Systemd: <code style="background:var(--av-bg);padding:2px 6px;border-radius:3px;">kteo-curate.service</code><br>
    DB: <code style="background:var(--av-bg);padding:2px 6px;border-radius:3px;">/opt/news_aggregator/news_cache.db</code>
    </div>
    """, unsafe_allow_html=True)
