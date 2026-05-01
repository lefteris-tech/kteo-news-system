#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
playlist_sync.py — Sync Yodeck playlists με τις διαθέσιμες ειδήσεις
====================================================================

Σκοπός
------
Μετά τον aggregator + carry_over, αυτό το script ελέγχει τα 6 per-category
XMLs στο /var/www/html και ενημερώνει τα 2 Yodeck News playlists ώστε:

  - Playlist A («Set 1», #1 news) να περιέχει layouts μόνο για κατηγορίες με
    >= 1 διαθέσιμη είδηση.
  - Playlist B («Set 2», #2 news) να περιέχει layouts μόνο για κατηγορίες με
    >= 2 διαθέσιμες ειδήσεις.

Παράδειγμα: αν το auto.xml σήμερα έχει 0 items, αφαιρούνται και τα δύο auto
layouts από τα δύο playlists. Όταν το auto γεμίσει ξανά (μέσω carry_over ή
από φρέσκο aggregator run), επιστρέφουν αυτόματα στη σωστή τους θέση.

Env vars (από /etc/news_aggregator.env)
---------------------------------------
  YODECK_API_TOKEN     - API token από Yodeck portal → API Tokens
  YODECK_PLAYLIST_A_ID - π.χ. 33489289 (Set 1)
  YODECK_PLAYLIST_B_ID - π.χ. 33489385 (Set 2)

Χρήση
-----
    python3 playlist_sync.py            # κανονική εκτέλεση
    python3 playlist_sync.py --dry-run  # δείχνει τι θα έστελνε χωρίς PATCH
"""

import os
import sys
import json
import argparse
import xml.etree.ElementTree as ET
from urllib import request, error

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE         = "https://app.yodeck.com/api/v2"
XML_DIR          = "/var/www/html"
DEFAULT_DURATION = 15        # δευτερόλεπτα ανά layout (όπως το έχεις στήσει)
HTTP_TIMEOUT     = 30        # seconds

# Σειρά κατηγοριών για κάθε playlist, όπως είναι ήδη στημένα στο portal.
# Αν αλλάξεις τη σειρά στο Yodeck, ενημέρωσε εδώ — ή μπορούμε να γράψουμε
# έκδοση που διαβάζει τη σειρά από το ίδιο το playlist.
#
# Format: (slug, position, yodeck_layout_id)

PLAYLIST_A = {
    "id":    None,   # γεμίζει από env
    "label": "Set 1 (#1 news)",
    "order": [
        ("national",      1, 8563681),
        ("international", 1, 8566384),
        ("sports",        1, 8566408),
        ("economy",       1, 8566601),
        ("lifestyle",     1, 8566561),
        ("auto",          1, 8566663),
    ],
}

PLAYLIST_B = {
    "id":    None,
    "label": "Set 2 (#2 news)",
    "order": [
        ("national",      2, 8564557),
        ("international", 2, 8566394),
        ("economy",       2, 8566614),
        ("sports",        2, 8566522),
        ("lifestyle",     2, 8566569),
        ("auto",          2, 8566671),
    ],
}

CATEGORIES = ["national", "international", "economy",
              "lifestyle", "auto", "sports"]


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def count_items(slug):
    """Μέτρα <item> elements στο per-category XML. Επιστρέφει 0 αν λείπει/άκυρο."""
    path = os.path.join(XML_DIR, f"{slug}.xml")
    if not os.path.exists(path):
        return 0
    try:
        tree = ET.parse(path)
        channel = tree.getroot().find("channel")
        return len(channel.findall("item")) if channel is not None else 0
    except Exception as e:
        print(f"  ⚠ parse error for {path}: {e}", file=sys.stderr)
        return 0


# ---------------------------------------------------------------------------
# Yodeck API helpers
# ---------------------------------------------------------------------------

def yodeck_request(method, path, token, body=None):
    """Minimal HTTP wrapper — στέλνει request, επιστρέφει (status, parsed_json)."""
    url = f"{API_BASE}{path}"
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = resp.read().decode("utf-8")
            return resp.status, (json.loads(payload) if payload else None)
    except error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:500]
        print(f"  ✗ HTTP {e.code} on {method} {path}: {body_text}", file=sys.stderr)
        return e.code, None
    except Exception as e:
        print(f"  ✗ Request failed: {e}", file=sys.stderr)
        return None, None


def build_items(playlist_def, counts):
    """Φτιάχνει τη λίστα items που πρέπει να βρίσκεται στο playlist αυτή τη στιγμή."""
    items = []
    priority = 1
    for slug, pos, layout_id in playlist_def["order"]:
        if counts.get(slug, 0) >= pos:
            items.append({
                "id":       layout_id,
                "type":     "layout",
                "priority": priority,
                "duration": DEFAULT_DURATION,
            })
            priority += 1
    return items


def sync_playlist(playlist_def, counts, token, dry_run):
    """Στέλνει PATCH στο playlist με filtered items list."""
    pid = playlist_def["id"]
    label = playlist_def["label"]
    items = build_items(playlist_def, counts)

    print(f"\n=== {label} (id={pid}) ===")
    if not items:
        print("  ⚠ Όλες οι κατηγορίες κενές — playlist θα μείνει άδειο")
    for it in items:
        # Βρες την κατηγορία για ωραίο logging
        for slug, pos, lid in playlist_def["order"]:
            if lid == it["id"]:
                print(f"  ✓ #{it['priority']:>2}  {slug:<14s} pos={pos}  layout_id={it['id']}")
                break

    skipped = []
    for slug, pos, lid in playlist_def["order"]:
        if counts.get(slug, 0) < pos:
            skipped.append(f"{slug}/pos{pos}")
    if skipped:
        print(f"  ✗ Disabled: {', '.join(skipped)}")

    if dry_run:
        print("  (dry-run — δεν στάλθηκε PATCH)")
        return True

    status, _ = yodeck_request("PATCH", f"/playlists/{pid}/", token,
                               body={"items": items})
    if status and 200 <= status < 300:
        print(f"  → PATCH OK (HTTP {status})")
        return True
    print(f"  → PATCH ΑΠΕΤΥΧΕ (HTTP {status})")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync Yodeck news playlists")
    parser.add_argument("--dry-run", action="store_true",
                        help="μόνο εμφάνιση τι θα γίνει, χωρίς PATCH")
    args = parser.parse_args()

    token  = os.environ.get("YODECK_API_TOKEN")
    pl_a   = os.environ.get("YODECK_PLAYLIST_A_ID")
    pl_b   = os.environ.get("YODECK_PLAYLIST_B_ID")

    missing = [v for v, n in [(token,"YODECK_API_TOKEN"),
                              (pl_a,"YODECK_PLAYLIST_A_ID"),
                              (pl_b,"YODECK_PLAYLIST_B_ID")] if not v]
    if missing:
        print(f"Λείπουν env vars. Έλεγξε /etc/news_aggregator.env", file=sys.stderr)
        return 2

    PLAYLIST_A["id"] = pl_a
    PLAYLIST_B["id"] = pl_b

    print("Yodeck Playlist Sync")
    print("-" * 60)

    counts = {cat: count_items(cat) for cat in CATEGORIES}
    print("XML item counts ανά κατηγορία:")
    for cat in CATEGORIES:
        print(f"  {cat:<14s} {counts[cat]} items")

    ok_a = sync_playlist(PLAYLIST_A, counts, token, args.dry_run)
    ok_b = sync_playlist(PLAYLIST_B, counts, token, args.dry_run)

    print("\n" + "-" * 60)
    print(f"Result: A={'✓' if ok_a else '✗'}  B={'✓' if ok_b else '✗'}")
    return 0 if (ok_a and ok_b) else 1


if __name__ == "__main__":
    sys.exit(main())
