#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
infuse_news.py — Ad-hoc manual injection of a news item.
Inserts at position #1 of /var/www/html/{slug}.xml. Calls playlist_sync.
"""
import argparse, os, sys, hashlib, subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

XML_DIR     = "/var/www/html"
SYNC_SCRIPT = "/opt/news_aggregator/playlist_sync.py"
PYTHON_BIN  = "/opt/news_aggregator/venv/bin/python"

CATEGORIES = {
    "national":      "Εθνικά",
    "international": "Διεθνή",
    "economy":       "Οικονομία",
    "lifestyle":     "Lifestyle",
    "auto":          "Auto",
    "sports":        "Σπορ",
}

def make_guid(title):
    h = hashlib.md5(title.encode("utf-8")).hexdigest()[:12]
    return f"infusion-{int(datetime.now().timestamp())}-{h}"

def ask(prompt, multiline=False):
    print(prompt + (": " if not multiline else " (empty line = τέλος):"), flush=True)
    if not multiline:
        return input().strip()
    lines = []
    while True:
        try: line = input()
        except EOFError: break
        if not line: break
        lines.append(line)
    return " ".join(lines)

def inject(slug, title, summary, link, image):
    path = Path(XML_DIR) / f"{slug}.xml"
    if path.exists():
        tree = ET.parse(path)
        rss = tree.getroot()
        channel = rss.find("channel")
        if channel is None:
            sys.exit(f"ERROR: <channel> missing in {path}")
    else:
        # Bootstrap empty feed
        rss = ET.Element("rss", version="2.0")
        channel = ET.SubElement(rss, "channel")
        ET.SubElement(channel, "title").text = f"KTEO News — {slug}"
        ET.SubElement(channel, "link").text = "https://kteo-news.dronepros.gr"
        ET.SubElement(channel, "description").text = f"AI-curated {slug} news"
        ET.SubElement(channel, "language").text = "el-GR"
        ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
        tree = ET.ElementTree(rss)

    item = ET.Element("item")
    ET.SubElement(item, "title").text = title
    if link:
        ET.SubElement(item, "link").text = link
    g = ET.SubElement(item, "guid"); g.set("isPermaLink", "false"); g.text = make_guid(title)
    ET.SubElement(item, "description").text = summary
    ET.SubElement(item, "pubDate").text = format_datetime(datetime.now(timezone.utc))
    if image:
        enc = ET.SubElement(item, "enclosure")
        enc.set("url", image); enc.set("type", "image/jpeg"); enc.set("length", "0")

    # Βρες πρώτο <item> και βάλε το νέο πριν από αυτό
    first_idx = next((i for i, c in enumerate(channel) if c.tag == "item"), None)
    if first_idx is None:
        channel.append(item)
    else:
        channel.insert(first_idx, item)

    # Update lastBuildDate
    lbd = channel.find("lastBuildDate")
    if lbd is not None:
        lbd.text = format_datetime(datetime.now(timezone.utc))

    # Atomic write
    tmp = path.with_suffix(".xml.tmp")
    tree.write(tmp, encoding="utf-8", xml_declaration=True)
    tmp.replace(path)
    return len(channel.findall("item"))

def call_sync():
    result = subprocess.run([PYTHON_BIN, SYNC_SCRIPT], capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"⚠ playlist_sync exit={result.returncode}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)

def main():
    ap = argparse.ArgumentParser(description="Ad-hoc injection of news to a KTEO category feed.")
    ap.add_argument("-c", "--category", choices=list(CATEGORIES.keys()))
    ap.add_argument("-t", "--title")
    ap.add_argument("-s", "--summary")
    ap.add_argument("-l", "--link", default="")
    ap.add_argument("-i", "--image", default="")
    ap.add_argument("--no-sync", action="store_true", help="Skip playlist_sync call")
    a = ap.parse_args()

    cat = a.category
    if not cat:
        print("Κατηγορίες:")
        for k, v in CATEGORIES.items():
            print(f"  {k:15s} ({v})")
        cat = ask("Slug")
        if cat not in CATEGORIES:
            sys.exit(f"Άκυρη κατηγορία: {cat}")

    title = a.title or ask("Τίτλος")
    if len(title) < 5: sys.exit("Τίτλος πολύ μικρός")

    summary = a.summary or ask("Σύνοψη (3 φράσεις)", multiline=True)
    if len(summary) < 20: sys.exit("Σύνοψη πολύ μικρή")

    link  = a.link  or (ask("Link URL (Enter για παράλειψη)") if not a.title else "")
    image = a.image or (ask("Image URL (Enter για παράλειψη)") if not a.title else "")

    print(f"\n→ Inserting into /var/www/html/{cat}.xml ...")
    count = inject(cat, title, summary, link or None, image or None)
    print(f"✓ Inserted at position 1. Total items: {count}")

    if not a.no_sync:
        print("\n→ Triggering playlist_sync ...")
        call_sync()

    print(f"\n✓ Done. Εμφάνιση στις οθόνες σε ~10-15 min (Yodeck sync interval).")
    print("ℹ Το item θα αντικατασταθεί στον επόμενο aggregator run (αύριο 07:40).")
    print("  Για επιμονή: ξανα-τρέξε το script.")

if __name__ == "__main__":
    main()
