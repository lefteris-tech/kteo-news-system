#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Carry-over post-processor — KTEO Autovision News Aggregator
============================================================

Σκοπός
------
Όταν ο aggregator παράγει per-category XMLs (national.xml,
international.xml, …) και κάποια κατηγορία έχει ΛΙΓΟΤΕΡΕΣ ειδήσεις
από το TARGET (π.χ. 0 ή 1), συμπληρώνουμε από το προηγούμενο
"archive" αρχείο που κρατάμε μετά από κάθε successful run.

Έτσι αποφεύγουμε κενά Yodeck zones όταν ο aggregator δεν παρήγαγε
αρκετές ειδήσεις σε μία κατηγορία αυτή τη μέρα.

Λογική
------
Για κάθε κατηγορία:
  1. Διάβασε το σημερινό {slug}.xml.
  2. Αν έχει >= TARGET items → απλά ενημέρωσε το archive και προχώρα.
  3. Αν έχει < TARGET items:
        α. Διάβασε το {slug}.archive.xml (αν υπάρχει).
        β. Πέρνα μέσα όσα archive items χρειάζονται για να φτάσουμε
           στο TARGET, παραλείποντας:
             - duplicates (ίδιο GUID ή link με σημερινά)
             - articles παλαιότερα από MAX_CARRY_OVER_DAYS
        γ. Γράψε το συγχωνευμένο XML πίσω στο {slug}.xml.
  4. Αντίγραψε το (πιθανώς συγχωνευμένο) {slug}.xml σε
     {slug}.archive.xml για χρήση στην επόμενη εκτέλεση.

Τα carried-over items διατηρούν τα αρχικά τους pubDates. Επειδή το
front-end (news.html) έχει show_date: false, ο τελικός θεατής δεν
βλέπει τι ώρα είναι κάθε άρθρο — μόνο τίτλο/εικόνα/περιγραφή.

Χρήση
-----
    python3 carry_over.py /var/www/html

Εκτελείται από το run_cron.sh αμέσως μετά τον main aggregator.
"""

import os
import sys
import shutil
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TARGET                 = 3      # στόχος items ανά κατηγορία
MAX_CARRY_OVER_DAYS    = 7      # μην κουβαλάς άρθρα παλαιότερα από αυτό

SLUGS = ['national', 'international', 'economy',
         'lifestyle', 'auto', 'sports']

# Διατήρηση των κοινών RSS prefixes για τα namespace elements
# (αλλιώς το ElementTree γράφει ns0:, ns1:, …)
for prefix, uri in [
    ('media',   'http://search.yahoo.com/mrss/'),
    ('content', 'http://purl.org/rss/1.0/modules/content/'),
    ('itunes',  'http://www.itunes.com/dtds/podcast-1.0.dtd'),
    ('atom',    'http://www.w3.org/2005/Atom'),
    ('dc',      'http://purl.org/dc/elements/1.1/'),
]:
    ET.register_namespace(prefix, uri)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_xml(path):
    """Επιστρέφει (tree, list_of_items) ή (None, []) αν λείπει το αρχείο."""
    if not os.path.exists(path):
        return None, []
    try:
        tree = ET.parse(path)
        channel = tree.getroot().find('channel')
        items = channel.findall('item') if channel is not None else []
        return tree, items
    except ET.ParseError as e:
        print(f'  ⚠ parse error στο {path}: {e}', file=sys.stderr)
        return None, []


def get_item_id(item):
    """Unique identifier ανά item: guid > link > title."""
    for tag in ('guid', 'link', 'title'):
        el = item.find(tag)
        if el is not None and el.text:
            return el.text.strip()
    return None


def get_item_date(item):
    """pubDate ως aware datetime, ή None αν λείπει/άκυρο."""
    pub = item.find('pubDate')
    if pub is None or not pub.text:
        return None
    try:
        dt = parsedate_to_datetime(pub.text.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_category(slug, output_dir, cutoff):
    today_path   = os.path.join(output_dir, f'{slug}.xml')
    archive_path = os.path.join(output_dir, f'{slug}.archive.xml')

    today_tree, today_items = parse_xml(today_path)
    if today_tree is None:
        print(f'  {slug:14s}  σημερινό λείπει — skip')
        return

    today_count = len(today_items)

    # Περίπτωση A: ήδη γεμάτο
    if today_count >= TARGET:
        shutil.copy2(today_path, archive_path)
        print(f'  {slug:14s}  {today_count} fresh — αρκούν, αρχειοθετήθηκε')
        return

    # Περίπτωση B: χρειάζεται supplement από archive
    needed = TARGET - today_count
    arch_tree, arch_items = parse_xml(archive_path)

    if arch_tree is None or not arch_items:
        # Δεν έχουμε archive ακόμα (πρώτη εκτέλεση) ή είναι κενό
        if today_count > 0:
            shutil.copy2(today_path, archive_path)
        print(f'  {slug:14s}  {today_count} fresh — δεν υπάρχει archive')
        return

    today_ids = {get_item_id(it) for it in today_items if get_item_id(it)}
    carried = []
    for it in arch_items:
        if len(carried) >= needed:
            break
        iid = get_item_id(it)
        if iid and iid in today_ids:
            continue                      # ήδη στα σημερινά
        d = get_item_date(it)
        if d is not None and d < cutoff:
            continue                      # πολύ παλιό
        carried.append(it)

    if not carried:
        if today_count > 0:
            shutil.copy2(today_path, archive_path)
        print(f'  {slug:14s}  {today_count} fresh — archive δεν είχε χρήσιμα items')
        return

    # Append τα carried-over στο σημερινό channel
    channel = today_tree.getroot().find('channel')
    for it in carried:
        channel.append(it)

    today_tree.write(today_path, encoding='utf-8', xml_declaration=True)

    # Update archive με την νέα συγχωνευμένη έκδοση
    shutil.copy2(today_path, archive_path)

    final_count = today_count + len(carried)
    print(f'  {slug:14s}  {today_count} fresh + {len(carried)} carried-over = {final_count}')


def main():
    output_dir = sys.argv[1] if len(sys.argv) > 1 else '/var/www/html'
    if not os.path.isdir(output_dir):
        print(f'output_dir δεν υπάρχει: {output_dir}', file=sys.stderr)
        return 2

    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_CARRY_OVER_DAYS)
    print(f'Carry-over — output={output_dir}, target={TARGET}, '
          f'max_age={MAX_CARRY_OVER_DAYS}d')
    print('-' * 60)
    for slug in SLUGS:
        process_category(slug, output_dir, cutoff)
    print('-' * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())
