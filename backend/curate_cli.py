#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
curate_cli.py — KTEO Curation Platform, Sprint 2
=================================================
Sprint: 2, version: 1
Generated: 2026-05-13

CLI test harness για το curation pipeline. Mark items as selected,
preview, and trigger publish_curated — χωρίς το Streamlit UI που
έρχεται στο Sprint 3.

Subcommands:
  list       Δείξε pending/selected items
  show       Πλήρες περιεχόμενο ενός item
  select     Mark items as selected
  unselect   Revert selected → pending
  status     Σύνοψη τρέχοντος state + last publishes
  publish    Trigger publish_curated.py

Examples:
  ./venv/bin/python curate_cli.py list
  ./venv/bin/python curate_cli.py list --category sports
  ./venv/bin/python curate_cli.py show 42
  ./venv/bin/python curate_cli.py select 42 47 51
  ./venv/bin/python curate_cli.py status
  ./venv/bin/python curate_cli.py publish --dry-run
  ./venv/bin/python curate_cli.py publish --no-yodeck
  ./venv/bin/python curate_cli.py publish
"""

import argparse
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

DEFAULT_DB     = "/opt/news_aggregator/news_cache.db"
PUBLISH_SCRIPT = "/opt/news_aggregator/publish_curated.py"
PYTHON_BIN     = "/opt/news_aggregator/venv/bin/python"
ENV_FILE       = "/etc/news_aggregator.env"

CATEGORIES = ["national", "international", "economy",
              "lifestyle", "auto", "sports"]


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def truncate(s: str, n: int) -> str:
    s = s or ""
    if len(s) <= n:
        return s
    return s[:n - 1] + "…"


def load_env_into(env: dict) -> dict:
    """Merge /etc/news_aggregator.env keys into the env dict (don't overwrite)."""
    if not os.path.exists(ENV_FILE):
        return env
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------

def cmd_list(args, conn) -> int:
    where = ["fetch_date = ?"]
    params = [args.date]
    if args.category:
        where.append("classified_category = ?")
        params.append(args.category)
    if args.status:
        where.append("status = ?")
        params.append(args.status)
    else:
        where.append("status IN ('pending','selected')")

    rows = conn.execute(f"""
        SELECT id, classified_category, status, title,
               COALESCE(image_url,'') AS image_url, source_type
          FROM pending_curation
         WHERE {' AND '.join(where)}
         ORDER BY classified_category, id
    """, params).fetchall()

    if not rows:
        suffix = (f" category={args.category}" if args.category else "") \
               + (f" status={args.status}" if args.status else "")
        print(f"No items for date={args.date}{suffix}.")
        return 0

    print(f"  ID    CAT             STATUS    IMG SRC  TITLE")
    print(f"  " + "-" * 75)
    for r in rows:
        img = "✓" if r["image_url"] else " "
        src = "M" if r["source_type"] == "manual" else "A"
        print(f"  {r['id']:>4}  {r['classified_category']:<14s}  "
              f"{r['status']:<9s} {img}   {src}    {truncate(r['title'], 55)}")
    print()
    print(f"  Total: {len(rows)} item(s)")
    return 0


def cmd_show(args, conn) -> int:
    row = conn.execute(
        "SELECT * FROM pending_curation WHERE id = ?", (args.id,)
    ).fetchone()
    if not row:
        print(f"Item {args.id} not found.")
        return 1

    print(f"ID:         {row['id']}")
    print(f"Status:     {row['status']}")
    print(f"Category:   {row['classified_category']}")
    print(f"Source:     {row['source_type']}")
    print(f"Fetched:    {row['fetch_date']}")
    print(f"Title:      {row['title']}")
    print(f"Link:       {row['guid']}")
    print(f"Image:      {row['image_url'] or '(none)'}")
    print(f"Pub date:   {row['pub_date']}")
    print(f"Safety:     {'PASS' if row['safety_passed'] else 'FAIL'}  "
          f"conf={row['haiku_confidence']}")
    print(f"Selected:   by {row['selected_by'] or '-'}  at {row['selected_at'] or '-'}")
    print(f"Published:  {row['published_at'] or '-'}")
    print()
    print(f"Summary:")
    print(f"  {row['haiku_summary'] or '(none)'}")
    if row['body_first_para']:
        print()
        print(f"First paragraph:")
        print(f"  {row['body_first_para'][:400]}")
    return 0


def cmd_select(args, conn) -> int:
    now = datetime.now(timezone.utc).isoformat()
    by = args.by or os.environ.get("USER", "cli@local")

    updated = 0
    not_found = []
    not_selectable = []
    for item_id in args.ids:
        row = conn.execute(
            "SELECT status, classified_category, title "
            "FROM pending_curation WHERE id = ?",
            (item_id,),
        ).fetchone()
        if not row:
            not_found.append(item_id)
            continue
        if row["status"] not in ("pending", "selected"):
            not_selectable.append((item_id, row["status"]))
            continue
        conn.execute("""
            UPDATE pending_curation
               SET status='selected', selected_by=?, selected_at=?
             WHERE id = ?
        """, (by, now, item_id))
        updated += 1
        print(f"  ✓ {item_id}  {row['classified_category']:<14s} "
              f"{truncate(row['title'], 55)}")
    conn.commit()

    print(f"\nSelected {updated} item(s)")
    if not_found:
        print(f"Not found: {not_found}")
    if not_selectable:
        print(f"Not selectable (wrong status): {not_selectable}")
    return 0 if updated > 0 else 1


def cmd_unselect(args, conn) -> int:
    updated = 0
    for item_id in args.ids:
        row = conn.execute(
            "SELECT status FROM pending_curation WHERE id = ?",
            (item_id,),
        ).fetchone()
        if not row or row["status"] != "selected":
            continue
        conn.execute("""
            UPDATE pending_curation
               SET status='pending', selected_by=NULL, selected_at=NULL
             WHERE id = ?
        """, (item_id,))
        updated += 1
    conn.commit()
    print(f"Unselected {updated} item(s)")
    return 0


def cmd_status(args, conn) -> int:
    today = datetime.now().strftime("%Y-%m-%d")

    rows = conn.execute("""
        SELECT classified_category, status, COUNT(*) AS n
          FROM pending_curation
         WHERE fetch_date = ?
         GROUP BY classified_category, status
    """, (today,)).fetchall()

    cats: dict[str, dict[str, int]] = {}
    for r in rows:
        cats.setdefault(r["classified_category"], {})[r["status"]] = r["n"]

    print(f"State for {today}:")
    print(f"  CATEGORY        PENDING SELECTED PUBLISHED EXPIRED")
    print(f"  " + "-" * 53)
    totals = {"pending": 0, "selected": 0, "published": 0, "expired": 0}
    for slug in CATEGORIES:
        c = cats.get(slug, {})
        for k in totals:
            totals[k] += c.get(k, 0)
        print(f"  {slug:<14s}  {c.get('pending',0):>6}   {c.get('selected',0):>6}  "
              f"{c.get('published',0):>6}    {c.get('expired',0):>6}")
    print(f"  " + "-" * 53)
    print(f"  {'TOTAL':<14s}  {totals['pending']:>6}   {totals['selected']:>6}  "
          f"{totals['published']:>6}    {totals['expired']:>6}")
    print()

    pubs = conn.execute("""
        SELECT publish_date, triggered_by, total_items, created_at
          FROM publish_log ORDER BY id DESC LIMIT 3
    """).fetchall()
    if pubs:
        print(f"Recent publishes:")
        for p in pubs:
            print(f"  {p['created_at'][:19]}  by {p['triggered_by']:<20s} "
                  f"{p['total_items']:>3} items")
    else:
        print(f"No publishes recorded yet.")
    return 0


def cmd_publish(args, conn) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    sel = conn.execute("""
        SELECT COUNT(*) FROM pending_curation
         WHERE fetch_date = ? AND status = 'selected'
    """, (today,)).fetchone()[0]
    if sel == 0:
        print(f"Nothing selected for {today}.")
        print(f"Use 'curate_cli.py select <id>...' first.")
        return 1
    conn.close()  # free the DB before subprocess opens it

    cmd = [PYTHON_BIN, PUBLISH_SCRIPT,
           "--db", args.db,
           "--triggered-by", args.by or os.environ.get("USER", "cli@local")]
    if args.output_dir:
        cmd.extend(["--output-dir", args.output_dir])
    if args.dry_run:
        cmd.append("--dry-run")
    if args.no_yodeck:
        cmd.append("--no-yodeck")
    if args.no_carry_over:
        cmd.append("--no-carry-over")
    if args.verbose:
        cmd.append("--verbose")

    env = load_env_into(os.environ.copy())

    print(f"→ {' '.join(cmd)}\n")
    result = subprocess.run(cmd, env=env)
    return result.returncode


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="KTEO curation CLI (Sprint 2)")
    p.add_argument("--db", default=DEFAULT_DB)
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="List items for a date")
    pl.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    pl.add_argument("--category", choices=CATEGORIES)
    pl.add_argument("--status",
                    choices=["pending", "selected", "published",
                             "expired", "rejected"])

    ps = sub.add_parser("show", help="Show one item's full detail")
    ps.add_argument("id", type=int)

    psel = sub.add_parser("select", help="Mark items as selected")
    psel.add_argument("ids", nargs="+", type=int)
    psel.add_argument("--by", help="Identifier for selected_by")

    pun = sub.add_parser("unselect", help="Revert selected → pending")
    pun.add_argument("ids", nargs="+", type=int)

    sub.add_parser("status", help="Summary of selection state + recent publishes")

    pp = sub.add_parser("publish", help="Trigger publish_curated.py")
    pp.add_argument("--by", help="Identifier for triggered_by")
    pp.add_argument("--output-dir",
                    help="Override XML output directory (default: /var/www/html)")
    pp.add_argument("--no-yodeck", action="store_true",
                    help="Skip playlist_sync")
    pp.add_argument("--no-carry-over", action="store_true",
                    help="Skip carry_over")
    pp.add_argument("--dry-run", action="store_true",
                    help="Preview only — no writes, no DB mutations")
    pp.add_argument("--verbose", "-v", action="store_true")

    args = p.parse_args()

    conn = connect(args.db)
    handlers = {
        "list":     cmd_list,
        "show":     cmd_show,
        "select":   cmd_select,
        "unselect": cmd_unselect,
        "status":   cmd_status,
        "publish":  cmd_publish,
    }
    try:
        return handlers[args.cmd](args, conn)
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        sys.exit(0)
