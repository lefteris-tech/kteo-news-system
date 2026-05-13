#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
streamlit_app.py — Autovision News Curator entry point
=======================================================
Sprint: 3, version: 1
Generated: 2026-05-13

Streamlit multi-page app. Uses st.navigation() to filter admin-only pages
based on the user's role (read from Cloudflare Access JWT header at the edge).

Launch:
  streamlit run /opt/news_aggregator/streamlit_app.py \
      --server.port 8501 --server.address 127.0.0.1 --server.headless true

The systemd unit (kteo-curate.service) does this for you.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
from kteo_curate import inject_css, current_user, render_user_chip

# Must be the first Streamlit call
st.set_page_config(
    page_title="Autovision News Curator",
    page_icon="📰",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"Get help": None, "Report a bug": None, "About": None},
)

inject_css()

# Identity (from Cloudflare Access header, with dev fallback)
user = current_user()
st.session_state["user"] = user

# Build the page list — admin pages only visible to admins
pages = [
    st.Page("pages/curation.py", title="Σημερινή Επιμέλεια", icon="📋", default=True),
    st.Page("pages/manual.py",   title="Χειροκίνητη Προσθήκη", icon="✍️"),
    st.Page("pages/history.py",  title="Ιστορικό", icon="📜"),
]
if user["is_admin"]:
    pages += [
        st.Page("pages/sources.py",  title="Πηγές",      icon="🔗"),
        st.Page("pages/filters.py",  title="Φίλτρα",     icon="🔍"),
        st.Page("pages/settings.py", title="Ρυθμίσεις",  icon="⚙️"),
    ]

# Sidebar — user chip first, then auto-rendered nav from st.navigation
render_user_chip(user)

pg = st.navigation(pages, position="sidebar")
pg.run()
