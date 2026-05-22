#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Autovision KTEO News Aggregator v2
==================================
Παράγει 6 per-category RSS XML feeds για Yodeck digital signage.

Pipeline:
  [0] Holiday check (Sunday + Greek public holidays)
  [1] RSS pull από newsbeast.gr/feed
  [2] SQLite dedup (7-day window)
  [3] Rule pre-filter (keyword blocklist, < 24h, photo exists)
  [4] Article fetch (og:image + body via BeautifulSoup)
  [5] Claude Haiku classification (6 categories + safety + summary)
  [6] Quality gate — top 3 ανά κατηγορία
  [7] Generate 6 separate RSS 2.0 XML files
  [8] Write στο /var/www/html/{slug}.xml
"""

import argparse
import logging
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

import feedparser
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RSS_SOURCE = "https://www.newsbeast.gr/feed"
USER_AGENT = "Mozilla/5.0 (KTEO News Aggregator/2.0)"
HTTP_TIMEOUT = 15
ARTICLE_MAX_AGE_HOURS = 24
DEDUP_WINDOW_DAYS = 7
TOP_N_PER_CATEGORY = 3
RATE_LIMIT_SECONDS = 13  # μεταξύ Claude API calls

# 6 κατηγορίες — Greek labels για το feed, slug για το filename
CATEGORIES = ["ΕΘΝΙΚΑ", "ΔΙΕΘΝΗ", "ΟΙΚΟΝΟΜΙΑ", "LIFESTYLE", "AUTO", "ΣΠΟΡ"]
SLUG_MAP = {
    "ΕΘΝΙΚΑ": "national",
    "ΔΙΕΘΝΗ": "international",
    "ΟΙΚΟΝΟΜΙΑ": "economy",
    "LIFESTYLE": "lifestyle",
    "AUTO": "auto",
    "ΣΠΟΡ": "sports",
}

# Keyword blocklist (case-insensitive substring match στο title)
# Στόχος: γρήγορος αποκλεισμός χωρίς να χρειαστεί Claude API call.
# Το semantic filtering γίνεται από το system prompt στο Claude.
BLOCKLIST_KEYWORDS = [
    # Τραγωδία/βία/εγκλήματα — ακατάλληλο για mixed audience
    "νεκρός", "νεκρή", "νεκροί", "δολοφον", "βιασμ", "αυτοκτον",
    "πτώμα", "πτώματα", "σορός", "πνίγηκε", "θρήνος",
    "τραγωδία", "φρικτ", "σοκ", "αποτροπιασμ",
    "βιάστ", "ξυλοδαρμ", "διαμελισμ",

    # Πόλεμος / συγκρούσεις (FIXED comma + expanded)
    "πόλεμ", "πολέμ",
    "σύγκρου", "συγκρού",
    "βομβαρδ",
    "πύραυλ", "πυραυλ",
    "μάχες", "μαχες",
    "εκεχειρ",
    "στρατιωτικ", "ένοπλ", "ενοπλ",
    "παραστρατιωτικ",
    "ομηρ",
    "εισβολ",
    "drone επίθεσ", "drone επιθεσ", "drone strike",

    # Υγεία / ασθένειες (FIX του παλιού "αρρωστ" + expansion)
    "αρρωστ", "αρρώστ",
    "ασθένει", "ασθενει",
    "επιδημ", "πανδημ",
    "νοσηλε", "νοσοκομει",
    "ιός", "ιού",
    "κορον", "covid",
    "γρίπη", "γριπη",
    "καρκίν", "καρκιν",
    "θάνατ", "θανατ", "θανάσιμ", "θανασιμ",
    "πένθο", "πενθο", "κηδει",

    # Καταστροφές / ατυχήματα
    "τρομοκρατ",
    "δυστύχημα", "δυστυχημα",
    "ατύχημα", "ατυχημα",

    # Επιδόματα / συντάξεις / γραφειοκρατία
    "επίδομα", "επιδομα",
    "συντάξ", "συνταξ",
    "συνταξιοδ",
    "εφκα", "οαεδ", "δυπα",
    "δικαιούχ", "δικαιουχ",
    "αφορολόγητο", "αφορολογητο",
    "voucher", "market pass",
    "ασφαλιστικ",

    # Τοπικότητα
    "εφημερεύ", "εφημερευ", "εφημερία", "εφημεριες",
    "ποιά φαρμακεία", "ποια φαρμακεία",

    # Πολιτικοί / κυβέρνηση
    "υπουργ", "πρωθυπουργ",
    "βουλευτ",
    "αντιπολίτευση", "αντιπολιτευση",
    "κυβερνητικ", "κομματικ",
]

# Greek public holidays (fixed-date subset — Pascha-relative dates παραλείπονται)
# Δεν είναι exhaustive, αλλά καλύπτει τα major. Επεκτείνεται εύκολα.
GREEK_HOLIDAYS_FIXED = [
    (1, 1),    # Πρωτοχρονιά
    (1, 6),    # Θεοφάνεια
    (3, 25),   # Εθνική Επέτειος
    (5, 1),    # Πρωτομαγιά
    (8, 15),   # Δεκαπενταύγουστος
    (10, 28),  # 28η Οκτωβρίου
    (12, 25),  # Χριστούγεννα
    (12, 26),  # Σύναξη Θεοτόκου
]

# System prompt για Claude classification
CLAUDE_SYSTEM_PROMPT = """⚠️ ΚΡΙΣΙΜΗ ΟΔΗΓΙΑ OUTPUT FORMAT — ΔΙΑΒΑΣΕ ΠΡΩΤΗ:
Η απάντησή σου πρέπει να είναι ΑΠΟΚΛΕΙΣΤΙΚΑ ΕΝΑ JSON object. Ξεκίνα με '{' και τελείωσε με '}'. ΑΠΑΓΟΡΕΥΕΤΑΙ:
- Οποιοδήποτε κείμενο πριν το JSON
- Οποιοδήποτε κείμενο μετά το JSON (όχι σχόλια, όχι εξηγήσεις, όχι "Note:", όχι παρατηρήσεις)
- Markdown code fences (όχι ```json, όχι ```)
- Δεύτερο JSON object
Η απάντησή σου τροφοδοτεί κατευθείαν Python json.loads(). Οτιδήποτε εκτός του JSON σπάει το pipeline.

============================================================

Είσαι content moderator και summarizer για digital signage σε ελληνικά KTEO (53 σημεία σε όλη την Ελλάδα, μικτό κοινό όλων των ηλικιών). Στόχος: ΠΟΙΟΤΙΚΕΣ, ΟΥΔΕΤΕΡΕΣ ειδήσεις γενικού/εθνικού ενδιαφέροντος. Όχι θόρυβος, όχι άγχος.

Schema του JSON που πρέπει να επιστρέψεις:

{
  "category": "ΕΘΝΙΚΑ" | "ΔΙΕΘΝΗ" | "ΟΙΚΟΝΟΜΙΑ" | "LIFESTYLE" | "AUTO" | "ΣΠΟΡ" | "SKIP",
  "safe_for_kteo": true | false,
  "skip_reason": null | "violence" | "local_only" | "welfare_payments" | "politics" | "geopolitical_tension" | "celebrity_gossip" | "tragedy" | "domestic_sports" | "other",
  "summary": "3 προτάσεις στα ελληνικά",
  "confidence": 0.0
}

============================================================
ΚΑΝΟΝΕΣ ΑΠΟΚΛΕΙΣΜΟΥ (επέστρεψε category="SKIP")
============================================================

Α. ΤΟΠΙΚΟΤΗΤΑ — skip_reason="local_only"
Αποκλείεις άρθρα που έχουν νόημα μόνο για κατοίκους μιας συγκεκριμένης πόλης ή περιοχής. Τα ΚΤΕΟ είναι σε όλη την Ελλάδα — μια συναυλία στην Αθήνα δεν αφορά τη Θεσσαλονίκη.
ΑΠΟΚΛΕΙΕΙΣ:
- Συναυλίες, εκδηλώσεις, εκθέσεις, εγκαίνια, φεστιβάλ ΣΕ συγκεκριμένη πόλη ή χώρο
- Φαρμακεία/νοσοκομεία που εφημερεύουν, βάρδιες, τοπικές υπηρεσίες
- Τοπική κίνηση/κυκλοφορία
- Τοπικός καιρός μιας μόνο περιοχής — επιτρέπεται μόνο εθνική κάλυψη
- Τοπικά αθλητικά (ντόπια πρωταθλήματα)
ΕΠΙΤΡΕΠΕΤΑΙ: εθνικά γεγονότα που συμβαίνουν σε μία πόλη αλλά αφορούν όλη τη χώρα.

Β. ΕΠΙΔΟΜΑΤΑ / ΣΥΝΤΑΞΕΙΣ / ΓΡΑΦΕΙΟΚΡΑΤΙΑ — skip_reason="welfare_payments"
Ενδιαφέρει μόνο τους άμεσους δικαιούχους, όχι το γενικό κοινό.
ΑΠΟΚΛΕΙΕΙΣ:
- Ημερομηνίες πληρωμής συντάξεων, ΕΦΚΑ, ΟΑΕΔ/ΔΥΠΑ, επιδομάτων
- Vouchers, market pass, Power pass, καλάθι του νοικοκυριού
- Κριτήρια ένταξης, αιτήσεις, προθεσμίες
- Φοροελαφρύνσεις, ΑΑΔΕ, ασφαλιστικές εισφορές, εφάπαξ

Γ. ΔΙΧΑΣΤΙΚΗ ΠΟΛΙΤΙΚΗ — skip_reason="politics"
Όχι κομματικός θόρυβος, όχι πολιτική αντιπαράθεση που διχάζει το κοινό.

Γ.1 ΕΛΛΗΝΙΚΗ ΠΟΛΙΤΙΚΗ:
ΑΠΟΚΛΕΙΕΙΣ άρθρα όπου κεντρικό πρόσωπο ή θέμα είναι:
- Έλληνας υπουργός, πρωθυπουργός, βουλευτής, αρχηγός κόμματος, στέλεχος κόμματος, Έλληνας Επίτροπος ΕΕ
- Δηλώσεις πολιτικών, διαμάχες κομμάτων, κοινοβουλευτικές διαδικασίες, ψηφοφορίες στη Βουλή
- Δημοσκοπήσεις, εκλογικά θέματα στην Ελλάδα
- "Πηγές της κυβέρνησης", "κυβερνητικοί κύκλοι"

Γ.2 ΔΙΕΘΝΗΣ ΔΙΧΑΣΤΙΚΗ ΠΟΛΙΤΙΚΗ:
ΑΠΟΚΛΕΙΕΙΣ άρθρα όπου κεντρικά πρόσωπα ή θέματα είναι:
- Διεθνείς ακροδεξιοί populist / εθνικιστές: Nigel Farage, Marine Le Pen, Viktor Orbán, Geert Wilders, Matteo Salvini, AfD, FPÖ, Vox, και παρόμοιοι
- Διεθνείς ακροαριστεροί ή πολιτικοί εξτρεμιστές
- Πολιτικοί που εξωτερικεύουν εθνική/εθνοτική/θρησκευτική κόντρα ή διενέξεις
- Πρόκληση/κόντρα μεταξύ πολιτικών διαφορετικών χωρών (π.χ. Farage vs Έντι Ράμα)
ΑΠΟΚΛΕΙΕΙΣ επίσης:
- Διπλωματικές κόντρες με εθνικό/εθνοτικό φόρτο (Ελληνοτουρκικά, Ελληνοαλβανικά, Σερβία-Κόσοβο, Ισραήλ-Παλαιστίνη όταν η ουσία είναι η σύγκρουση)
- Anti-immigration rhetoric, populist προκλήσεις
- Άρθρα που η ουσία είναι η ΑΝΤΙΠΑΡΑΘΕΣΗ, όχι η διπλωματική εξέλιξη

ΕΠΙΤΡΕΠΕΤΑΙ: ξένη πολιτική ως ΓΕΓΟΝΟΣ (εκλογές σε άλλη χώρα, νέα κυβέρνηση, διεθνείς συμφωνίες) — αλλά ΧΩΡΙΣ έμφαση σε αντιπαλότητες ή ονόματα Ελλήνων πολιτικών.

Δ. ΓΕΩΠΟΛΙΤΙΚΗ ΕΝΤΑΣΗ / ΑΕΡΟΠΟΡΙΚΑ ΣΥΜΒΑΝΤΑ — skip_reason="geopolitical_tension"
Δεν προβάλλουμε εντάσεις, απειλές, ή αεροπορικά/στρατιωτικά συμβάντα — προκαλούν άγχος σε χώρο αναμονής.
ΑΠΟΚΛΕΙΕΙΣ:
- Απειλές, τελεσίγραφα, escalation rhetoric: "δεν θα ανεχτούμε", "η αυτοσυγκράτηση τέλειωσε", "τελευταία προειδοποίηση", "οργισμένος ηγέτης"
- Aircraft/airline incidents: συντριβές, φωτιές σε αεροσκάφη, αναγκαστικές προσγειώσεις, εκκενώσεις πτήσεων — irrespective of casualties
- Στρατιωτικές ασκήσεις, επιδείξεις ισχύος, military buildups
- Drone/missile attacks, βομβαρδισμοί, στρατιωτικές επιχειρήσεις
- Διπλωματικές κρίσεις με πολεμικό vocabulary
- "Ένταση κλιμακώνεται", "φόβοι σύγκρουσης", "στα πρόθυρα πολέμου"
- Πυρηνικές απειλές, sanctions ως αντίποινα

ΕΠΙΤΡΕΠΕΤΑΙ: ειρηνευτικές εξελίξεις, summits με θετικό outcome, υπογραφές συμφωνιών, ανθρωπιστική βοήθεια. Αλλά ΟΧΙ όταν η κάλυψη εστιάζει στην ένταση.

============================================================
ΚΑΤΗΓΟΡΙΕΣ (όταν περνά τα παραπάνω φίλτρα)
============================================================
- ΕΘΝΙΚΑ: ελληνική κοινωνία, υγεία ως public service, παιδεία, υποδομές εθνικής εμβέλειας, εθνικός καιρός, τουρισμός. ΟΧΙ τοπικά, ΟΧΙ εγκλήματα, ΟΧΙ διασημότητες, ΟΧΙ πολιτικά.
- ΔΙΕΘΝΗ: διεθνείς εξελίξεις, διπλωματία, ΕΕ, παγκόσμια θέματα. ΟΧΙ συγκρούσεις, ΟΧΙ διχαστικά, ΟΧΙ ένταση μεταξύ κρατών.
- ΟΙΚΟΝΟΜΙΑ: αγορές, τράπεζες, μεγάλες εταιρείες, εθνική οικονομία, καταναλωτικά. ΟΧΙ stock tips, ΟΧΙ επιδόματα/συντάξεις.
- LIFESTYLE: ταινίες, μουσική, βιβλία, πολιτισμός, νέα τεχνολογία, επιστήμη γενικού ενδιαφέροντος, ταξίδια. ΟΧΙ τοπικές εκδηλώσεις, ΟΧΙ celebrity gossip.
- AUTO: μοντέλα, δοκιμές, ηλεκτροκίνηση, τεχνολογία αυτοκινήτου, μηχανοκίνητος αθλητισμός. ΟΧΙ τροχαία.
- ΣΠΟΡ: ΜΟΝΟ διεθνείς διοργανώσεις (Champions League, Europa League, EPL, La Liga, Serie A, Bundesliga, Eurobasket, Euroleague, NBA, World Cup, Olympics, F1, Tennis Slams). ΟΧΙ ελληνικά πρωταθλήματα, ΟΧΙ οπαδικά.

============================================================
ΚΑΝΟΝΕΣ ΓΛΩΣΣΑΣ ΓΙΑ ΤΟ "summary" — ΥΠΟΧΡΕΩΤΙΚΟΙ
============================================================
1. Χρησιμοποίησε ΑΠΟΚΛΕΙΣΤΙΚΑ σύγχρονη καθομιλουμένη Νέα Ελληνική, όπως θα έγραφε ένας δημοσιογράφος σε εφημερίδα ή ειδησεογραφικό site σήμερα.
2. ΑΠΑΓΟΡΕΥΕΤΑΙ να εφευρίσκεις λέξεις, να φτιάχνεις νεολογισμούς, ή να σχηματίζεις παράγωγα που δεν υπάρχουν στο λεξικό.
3. Απόφυγε λόγιους, αρχαιοπρεπείς, καθαρευουσιάνικους ή σπάνιους τύπους:
   - "ανακοίνωση" (ΟΧΙ "ανακοίνωμα")
   - "δήλωση" (ΟΧΙ "απόφανση")
   - "εξήγησε" (ΟΧΙ "διεσαφήνισε")
   - "συνάντηση" (ΟΧΙ "σύσκεψις")
4. Όταν ένας όρος του πρωτοτύπου είναι λόγιος, μετάφρασέ τον σε καθημερινή λέξη.
5. Απόφυγε clickbait ("σοκαριστικό", "δεν θα πιστέψετε", "απίστευτο") και ρητορικές ερωτήσεις.
6. Ενεργητική σύνταξη όπου είναι φυσική.
7. 3 πλήρεις προτάσεις, καθεμία στέκεται μόνη της.
8. ΜΗΝ αναφέρεις ονόματα Ελλήνων πολιτικών.
9. ΜΗΝ αναφέρεις references σε video/φωτογραφίες ("δείτε βίντεο", "(βίντεο)") — οι οθόνες ΚΤΕΟ δεν παίζουν video.

============================================================
ΥΠΕΝΘΥΜΙΣΗ ΤΕΛΟΥΣ
============================================================
Επιστρέφεις ΜΟΝΟ το JSON object. Καμία λέξη πριν, καμία λέξη μετά."""

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("news_aggregator")


def setup_logging(verbose: bool) -> None:
    """Ρυθμίζει console logging με ώρα και επίπεδο."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Article:
    """Internal representation ενός άρθρου σε όλα τα στάδια."""
    title: str
    link: str
    published: datetime
    source_category: str = ""
    description: str = ""
    image_url: str = ""
    body_text: str = ""
    classified_category: str = ""
    summary: str = ""
    safe: bool = False
    skip_reason: Optional[str] = None
    confidence: float = 0.0
    # S5.1 — source attribution for the widget avatar pill
    source_name: str = ""
    source_logo_url: str = ""


# ---------------------------------------------------------------------------
# Holiday check
# ---------------------------------------------------------------------------

def is_holiday(date: datetime) -> tuple[bool, str]:
    """Επιστρέφει (True, λόγος) αν η ημέρα είναι Κυριακή ή ελληνική αργία."""
    if date.weekday() == 6:  # Κυριακή
        return True, "Κυριακή"
    for month, day in GREEK_HOLIDAYS_FIXED:
        if date.month == month and date.day == day:
            return True, f"Αργία {day}/{month}"
    return False, ""


# ---------------------------------------------------------------------------
# SQLite cache (dedup + audit)
# ---------------------------------------------------------------------------

EXPECTED_COLUMNS = {"link", "title", "category", "published_iso", "seen_at_iso"}


def init_db(path: str) -> sqlite3.Connection:
    """
    Δημιουργεί/ανοίγει το SQLite cache για deduplication.
    Ανιχνεύει incompatible schema από προηγούμενες εκδόσεις και κάνει
    auto-migration (drop & recreate). Χάνει το dedup ιστορικό αλλά αποφεύγει
    "no such column" errors κατά την αναβάθμιση.
    """
    conn = sqlite3.connect(path)

    # Έλεγχος αν υπάρχει ήδη το table
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='seen_articles'"
    )
    table_exists = cur.fetchone() is not None

    if table_exists:
        cur = conn.execute("PRAGMA table_info(seen_articles)")
        existing_cols = {row[1] for row in cur.fetchall()}
        if not EXPECTED_COLUMNS.issubset(existing_cols):
            missing = EXPECTED_COLUMNS - existing_cols
            log.warning(
                "Παλιό schema στο %s (λείπουν: %s) — drop & recreate",
                path, ", ".join(sorted(missing)),
            )
            conn.execute("DROP TABLE seen_articles")
            conn.commit()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_articles (
            link TEXT PRIMARY KEY,
            title TEXT,
            category TEXT,
            published_iso TEXT,
            seen_at_iso TEXT
        )
    """)
    conn.commit()
    return conn


def already_seen(conn: sqlite3.Connection, link: str) -> bool:
    """True αν το link υπάρχει ήδη στο cache."""
    cur = conn.execute("SELECT 1 FROM seen_articles WHERE link = ?", (link,))
    return cur.fetchone() is not None


def mark_seen(conn: sqlite3.Connection, art: Article) -> None:
    """Καταγράφει το άρθρο ως seen με timestamp."""
    conn.execute(
        "INSERT OR REPLACE INTO seen_articles VALUES (?, ?, ?, ?, ?)",
        (
            art.link,
            art.title,
            art.classified_category or art.source_category,
            art.published.isoformat(),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def cleanup_old_entries(conn: sqlite3.Connection, days: int = DEDUP_WINDOW_DAYS) -> int:
    """Διαγράφει entries παλιότερα από `days` ημέρες. Επιστρέφει αριθμό deleted."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cur = conn.execute("DELETE FROM seen_articles WHERE seen_at_iso < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# RSS fetching
# ---------------------------------------------------------------------------

def fetch_rss(url: str) -> list[Article]:
    """Διαβάζει RSS feed και επιστρέφει λίστα από Article objects."""
    log.info("Fetching RSS: %s", url)
    try:
        feed = feedparser.parse(url, agent=USER_AGENT)
    except Exception as e:
        log.error("RSS fetch απέτυχε: %s", e)
        return []

    if feed.bozo and not feed.entries:
        log.warning("RSS bozo flag set, no entries: %s", feed.bozo_exception)
        return []

    articles: list[Article] = []
    for entry in feed.entries:
        try:
            link = entry.get("link", "").strip()
            title = entry.get("title", "").strip()
            if not link or not title:
                continue

            # Parse published date
            pub_struct = entry.get("published_parsed") or entry.get("updated_parsed")
            if pub_struct:
                published = datetime(*pub_struct[:6], tzinfo=timezone.utc)
            else:
                published = datetime.now(timezone.utc)

            # Source category από feed (αν υπάρχει)
            source_cat = ""
            if "tags" in entry and entry.tags:
                source_cat = entry.tags[0].get("term", "")

            articles.append(Article(
                title=title,
                link=link,
                published=published,
                source_category=source_cat,
                description=entry.get("summary", "")[:500],
            ))
        except Exception as e:
            log.debug("Skip entry due to parse error: %s", e)
            continue

    log.info("RSS returned %d articles", len(articles))
    return articles


# ---------------------------------------------------------------------------
# Pre-filter (rule-based, πριν το expensive Claude call)
# ---------------------------------------------------------------------------

def passes_prefilter(art: Article) -> tuple[bool, str]:
    """
    Rule-based pre-filter πριν το LLM call.
    Επιστρέφει (True, "") αν περάσει, αλλιώς (False, λόγος).
    """
    # 1. Age check
    age = datetime.now(timezone.utc) - art.published
    if age > timedelta(hours=ARTICLE_MAX_AGE_HOURS):
        return False, f"too old ({age})"

    # 2. Keyword blocklist (case-insensitive)
    title_lower = art.title.lower()
    for kw in BLOCKLIST_KEYWORDS:
        if kw in title_lower:
            return False, f"blocked keyword: {kw}"

    # 3. Title length sanity
    if len(art.title) < 15:
        return False, "title too short"

    return True, ""


# ---------------------------------------------------------------------------
# Article fetching (og:image + body)
# ---------------------------------------------------------------------------

def fetch_article_content(url: str) -> tuple[str, str]:
    """
    Κατεβάζει το HTML σελίδας και εξάγει (image_url, body_text).
    Επιστρέφει ("", "") σε σφάλμα.
    """
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        log.warning("Article fetch απέτυχε για %s: %s", url, e)
        return "", ""

    soup = BeautifulSoup(resp.text, "html.parser")

    # og:image
    image_url = ""
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        image_url = og_image["content"].strip()

    # Body — combination strategies
    body_parts: list[str] = []

    # Strategy 1: <article> tag
    article_tag = soup.find("article")
    if article_tag:
        for p in article_tag.find_all("p"):
            text = p.get_text(strip=True)
            if text and len(text) > 30:
                body_parts.append(text)

    # Strategy 2: fallback σε όλα τα <p> αν δεν βρέθηκε article
    if not body_parts:
        for p in soup.find_all("p"):
            text = p.get_text(strip=True)
            if text and len(text) > 50:
                body_parts.append(text)
            if len(body_parts) >= 10:
                break

    body_text = "\n".join(body_parts[:15])  # max 15 paragraphs
    body_text = body_text[:4000]  # cap στα 4K chars για το LLM

    return image_url, body_text


# ---------------------------------------------------------------------------
# Claude classification
# ---------------------------------------------------------------------------

def _extract_first_json_object(raw: str) -> dict:
    """
    Robust JSON parser για responses του Claude.
    Χρησιμοποιεί JSONDecoder.raw_decode() για να εξάγει το πρώτο valid
    JSON object, αγνοώντας τυχόν trailing markdown/commentary.

    Π.χ. αν ο Claude επιστρέψει:
        {"category": "X", ...}

        Note: I categorized this as X because...

    το raw_decode πιάνει μόνο το JSON και αγνοεί το trailing.
    """
    import json as _json

    # Strip markdown fences
    cleaned = re.sub(r"^\s*```(?:json)?\s*", "", raw)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)

    # Βρες το πρώτο '{' και δοκίμασε raw_decode από εκεί
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response")

    decoder = _json.JSONDecoder()
    obj, _end = decoder.raw_decode(cleaned[start:])
    return obj


def classify_with_claude(client, title: str, body: str) -> dict:
    """
    Καλεί Claude Haiku για classification + summarization.
    Επιστρέφει dict με keys: category, safe_for_kteo, skip_reason, summary, confidence.
    Σε σφάλμα επιστρέφει SKIP fallback.
    """
    user_msg = f"ΤΙΤΛΟΣ: {title}\n\nΣΩΜΑ ΑΡΘΡΟΥ:\n{body[:3000]}"

    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=500,
            system=CLAUDE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()

        # Robust parsing — δέχεται και responses με trailing text
        result = _extract_first_json_object(raw)

        # Sanity defaults
        result.setdefault("category", "SKIP")
        result.setdefault("safe_for_kteo", False)
        result.setdefault("skip_reason", "parse_fallback")
        result.setdefault("summary", "")
        result.setdefault("confidence", 0.0)

        return result
    except Exception as e:
        log.warning("Claude classification απέτυχε: %s", e)
        return {
            "category": "SKIP",
            "safe_for_kteo": False,
            "skip_reason": "api_error",
            "summary": "",
            "confidence": 0.0,
        }


def mock_classify(art: Article, idx: int) -> dict:
    """Mock classifier για --dry-run (round-robin πάνω στις 6 κατηγορίες)."""
    cat = CATEGORIES[idx % len(CATEGORIES)]
    return {
        "category": cat,
        "safe_for_kteo": True,
        "skip_reason": None,
        "summary": f"[DRY-RUN] {art.title[:80]}. Mock summary για το άρθρο. Test μόνο.",
        "confidence": 0.95,
    }


# ---------------------------------------------------------------------------
# Title cleanup
# ---------------------------------------------------------------------------

def clean_title(title: str) -> str:
    """
    Καθαρίζει τον τίτλο για display στο XML output:
    - Αφαιρεί [CATEGORY] prefixes
    - Αφαιρεί trailing source tags ("- Newsbeast" κλπ)
    - Αφαιρεί video/photo references ("Δείτε βίντεο", "(βίντεο)" κλπ)
    - Αφαιρεί residual trailing separators
    """
    # 1. Leading [BRACKET] prefix
    cleaned = re.sub(r"^\s*\[[^\]]+\]\s*", "", title)
    # 2. Trailing source tag
    cleaned = re.sub(r"\s*[-–—|]\s*Newsbeast.*$", "", cleaned, flags=re.IGNORECASE)
    # 3. Video/photo references (Greek + English)
    media_patterns = [
        r"\s*[-–—|]\s*δε[ίι]τε\s*(?:το\s+)?β[ίι]ντεο\b.*$",
        r"\s*[-–—|]\s*δε[ίι]τε\s*(?:τις\s+)?φωτογραφ[ίι]ε[ςσ]\b.*$",
        r"\s*[-–—|]\s*δε[ίι]τε\s+εικ[όο]νε[ςσ]\b.*$",
        r"\s*[\(\[\{]\s*β[ίι]ντεο\s*[\)\]\}]",
        r"\s*[\(\[\{]\s*φωτογραφ[ίι]ε?[ςσ]?\s*[\)\]\}]",
        r"\s*[\(\[\{]\s*φ[ωώ]το\s*[\)\]\}]",
        r"\s*[-–—|]\s*β[ίι]ντεο\s*$",
        r"\s*[-–—|]\s*φωτογραφ[ίι]ε?[ςσ]?\s*$",
        r"\s*[-–—|]\s*φ[ωώ]το\s*$",
        r"\s*[\(\[\{]\s*VIDEO\s*[\)\]\}]",
        r"\s*[-–—|]\s*VIDEO\s*$",
        r"\s*[\(\[\{]\s*PHOTOS?\s*[\)\]\}]",
        r"\s*[-–—|]\s*PHOTOS?\s*$",
        r"\s*[-–—|]\s*pics?\s*$",
        r"^\s*β[ίι]ντεο\s*[:|\-]\s*",
    ]
    for pat in media_patterns:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)
    # 4. Residual trailing separators
    cleaned = re.sub(r"\s*[-–—|]\s*$", "", cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# RSS XML generation (per category)
# ---------------------------------------------------------------------------

def build_rss_xml(category: str, articles: list[Article]) -> str:
    """
    Παράγει RSS 2.0 XML string για 1 κατηγορία.
    Yodeck-compatible — απλό και valid.
    """
    now_str = format_datetime(datetime.now(timezone.utc))
    slug = SLUG_MAP[category]
    feed_url = f"https://kteo-news.dronepros.gr/{slug}.xml"

    items_xml: list[str] = []
    for art in articles:
        pub_date_str = format_datetime(art.published)
        title_clean = clean_title(art.title)
        description = art.summary or art.description or title_clean

        # Συνδυασμός description + image (Yodeck δείχνει image από enclosure)
        enclosure_xml = ""
        if art.image_url:
            enclosure_xml = (
                f'<enclosure url="{xml_escape(art.image_url, {chr(34): "&quot;"})}" '
                f'type="image/jpeg" length="0"/>'
            )

        # S5.1 — source attribution in custom namespace; widget reads these to
        # draw the circular avatar pill. Emitted only when data is present so
        # carry-over items (which may pre-date the source registry) degrade
        # cleanly to "no avatar".
        source_xml = ""
        if art.source_name:
            source_xml += (
                f"\n      <kteo:source_name>"
                f"{xml_escape(art.source_name)}"
                f"</kteo:source_name>"
            )
        if art.source_logo_url:
            source_xml += (
                f"\n      <kteo:source_logo>"
                f"{xml_escape(art.source_logo_url)}"
                f"</kteo:source_logo>"
            )

        items_xml.append(f"""    <item>
      <title>{xml_escape(title_clean)}</title>
      <link>{xml_escape(art.link)}</link>
      <description>{xml_escape(description)}</description>
      <category>{xml_escape(category)}</category>
      <pubDate>{pub_date_str}</pubDate>
      <guid isPermaLink="true">{xml_escape(art.link)}</guid>
      {enclosure_xml}{source_xml}
    </item>""")

    items_str = "\n".join(items_xml)

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:kteo="https://kteo-news.dronepros.gr/ns/1.0">
  <channel>
    <title>KTEO Autovision - {xml_escape(category)}</title>
    <link>{feed_url}</link>
    <description>Ειδήσεις {xml_escape(category)} για Autovision KTEO digital signage</description>
    <language>el-GR</language>
    <lastBuildDate>{now_str}</lastBuildDate>
    <generator>KTEO News Aggregator v2</generator>
{items_str}
  </channel>
</rss>"""

    return rss


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="KTEO News Aggregator v2 — per-category RSS feeds",
    )
    parser.add_argument(
        "--output-dir",
        default="./output",
        help="Directory όπου θα γραφτούν τα {category}.xml αρχεία",
    )
    parser.add_argument(
        "--db",
        default="./news_cache.db",
        help="Path στο SQLite cache (dedup)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Παράκαμψη Claude API (mock classification round-robin)",
    )
    parser.add_argument(
        "--limit-feed",
        action="store_true",
        help="Test mode — μόνο τα πρώτα 6 articles από το feed",
    )
    parser.add_argument(
        "--ignore-holiday",
        action="store_true",
        help="Αγνόηση Κυριακής/αργίας — συνέχισε ούτως ή άλλως",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="DEBUG logging",
    )
    args = parser.parse_args()

    setup_logging(args.verbose)

    # ----------------------------------------------------------------- Step 0
    now = datetime.now()
    holiday, reason = is_holiday(now)
    if holiday and not args.ignore_holiday:
        log.info("Σήμερα είναι %s — skip run. (use --ignore-holiday για override)", reason)
        return 0

    # ----------------------------------------------------------------- Step 1
    raw_articles = fetch_rss(RSS_SOURCE)
    if not raw_articles:
        log.error("Κανένα άρθρο από το RSS feed — abort")
        return 1

    if args.limit_feed:
        raw_articles = raw_articles[:6]
        log.info("--limit-feed → %d articles", len(raw_articles))

    # ----------------------------------------------------------------- Step 2
    db = init_db(args.db)
    deleted = cleanup_old_entries(db)
    if deleted:
        log.info("Cleaned up %d old cache entries", deleted)

    # ----------------------------------------------------------------- Step 3
    candidates: list[Article] = []
    for art in raw_articles:
        if already_seen(db, art.link):
            log.debug("Skip (seen): %s", art.title[:60])
            continue
        ok, reason = passes_prefilter(art)
        if not ok:
            log.debug("Skip (prefilter %s): %s", reason, art.title[:60])
            continue
        candidates.append(art)

    log.info("Pre-filter pass: %d / %d articles", len(candidates), len(raw_articles))


    # Cap στα top 40 για cost control (~9 min total run time)
    # Σημ.: με αυστηρά φίλτρα (τοπικότητα, επιδόματα, πολιτικά) η rejection rate
    # είναι υψηλή — χρειαζόμαστε περισσότερους candidates για επαρκή coverage.
    candidates = candidates[:40]

    # ----------------------------------------------------------------- Steps 4-5
    claude_client = None
    if not args.dry_run:
        try:
            from anthropic import Anthropic
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                log.error("ANTHROPIC_API_KEY δεν έχει οριστεί. Use --dry-run για test.")
                return 2
            claude_client = Anthropic(api_key=api_key)
        except ImportError:
            log.error("Δεν είναι εγκατεστημένο το `anthropic` package")
            return 2

    classified: list[Article] = []
    for idx, art in enumerate(candidates):
        log.info("[%d/%d] %s", idx + 1, len(candidates), art.title[:70])

        # Article fetch (og:image + body)
        image_url, body_text = fetch_article_content(art.link)
        art.image_url = image_url
        art.body_text = body_text

        if not body_text:
            log.debug("  → empty body, skip")
            continue
        if not image_url:
            log.debug("  → no image, skip")
            continue

        # Classification
        if args.dry_run:
            result = mock_classify(art, idx)
        else:
            result = classify_with_claude(claude_client, art.title, body_text)
            time.sleep(RATE_LIMIT_SECONDS)  # rate limit

        category = result.get("category", "SKIP")
        if category == "SKIP" or not result.get("safe_for_kteo"):
            log.debug("  → SKIP (%s)", result.get("skip_reason"))
            continue
        if category not in CATEGORIES:
            log.debug("  → unknown category: %s", category)
            continue

        art.classified_category = category
        art.summary = result.get("summary", "")
        art.safe = bool(result.get("safe_for_kteo"))
        art.confidence = float(result.get("confidence", 0.0))
        classified.append(art)

        # Mark seen ΜΟΝΟ μετά από επιτυχή classification — έτσι αν αποτύχει
        # σε intermittent error το ίδιο άρθρο θα ξανατζακαριστεί στο επόμενο run.
        mark_seen(db, art)

    log.info("Classification pass: %d / %d articles", len(classified), len(candidates))

    # ----------------------------------------------------------------- Step 6
    by_category: dict[str, list[Article]] = {cat: [] for cat in CATEGORIES}
    for art in classified:
        by_category[art.classified_category].append(art)

    # Top N ανά κατηγορία (sorted by recency desc, then confidence desc)
    for cat in CATEGORIES:
        by_category[cat].sort(key=lambda a: (a.published, a.confidence), reverse=True)
        by_category[cat] = by_category[cat][:TOP_N_PER_CATEGORY]

    # ----------------------------------------------------------------- Steps 7-8
    os.makedirs(args.output_dir, exist_ok=True)
    written_summary: list[tuple[str, int, int]] = []
    for cat in CATEGORIES:
        items = by_category[cat]
        slug = SLUG_MAP[cat]
        out_path = os.path.join(args.output_dir, f"{slug}.xml")
        xml_str = build_rss_xml(cat, items)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(xml_str)
        size = os.path.getsize(out_path)
        written_summary.append((slug, size, len(items)))
        log.info("Wrote %s — %d items, %d bytes", out_path, len(items), size)

    # Summary
    log.info("=" * 60)
    log.info("Run complete:")
    for slug, size, count in written_summary:
        log.info("  %-15s %5d items   %6d bytes", f"{slug}.xml", count, size)
    log.info("=" * 60)

    db.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        log.exception("Μη αναμενόμενο σφάλμα: %s", e)
        sys.exit(1)
