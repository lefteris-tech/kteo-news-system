"""Source logo fetcher with multi-strategy chain.

Strategies tried in order until one succeeds:
  1. Clearbit Logo API           — clean PNG with transparency, brand-quality
  2. HTML parse                   — apple-touch-icon (180px), then og:image
  3. Google favicon service       — always works, 128px max, last resort

Output is normalized to LOGO_SIZE × LOGO_SIZE PNG with alpha, centered on a
transparent canvas. Designed for circular avatar rendering in the widget
(border-radius: 50% in CSS).

The module has no Anthropic API dependency — safe to run during the active
spend-cap incident.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

log = logging.getLogger(__name__)

LOGO_DIR = Path("/var/www/html/news/logos")
LOGO_SIZE = 128
HTTP_TIMEOUT = 8
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 KTEO-News-Bot/1.0"
)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _fetch(url: str, timeout: int = HTTP_TIMEOUT) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                log.debug("fetch %s -> HTTP %s", url, r.status)
                return None
            data = r.read()
            if not data or len(data) < 100:
                log.debug("fetch %s -> too small (%d bytes)", url, len(data))
                return None
            return data
    except Exception as e:
        log.debug("fetch %s failed: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Image normalization
# ---------------------------------------------------------------------------
def _normalize(raw: bytes) -> Optional[bytes]:
    """Resize to fit LOGO_SIZE square, center on transparent canvas, return PNG."""
    try:
        img = Image.open(BytesIO(raw))
        # Convert SVG/WebP/ICO/etc to RGBA
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        # Strip animation if present (use first frame)
        if getattr(img, "is_animated", False):
            img.seek(0)
            img = img.copy().convert("RGBA")

        img.thumbnail((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)

        canvas = Image.new("RGBA", (LOGO_SIZE, LOGO_SIZE), (0, 0, 0, 0))
        x = (LOGO_SIZE - img.width) // 2
        y = (LOGO_SIZE - img.height) // 2
        canvas.paste(img, (x, y), img)

        out = BytesIO()
        canvas.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception as e:
        log.debug("normalize failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
def _domain_of(url_or_domain: str) -> str:
    """Reduce input to bare domain, stripping protocol, www., and trailing slash."""
    s = url_or_domain.strip()
    if "://" in s:
        s = urllib.parse.urlparse(s).netloc
    s = s.replace("www.", "", 1).strip("/")
    return s.lower()


def _strategy_clearbit(domain: str) -> Optional[bytes]:
    return _fetch(f"https://logo.clearbit.com/{domain}")


_ICON_PATTERNS = [
    # apple-touch-icon variants (preferred — typically 180x180 or larger)
    r'<link[^>]+rel=["\'](?:apple-touch-icon(?:-precomposed)?)["\'][^>]+href=["\']([^"\']+)["\']',
    r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\'](?:apple-touch-icon(?:-precomposed)?)["\']',
    # og:image
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    # any icon link as last fallback
    r'<link[^>]+rel=["\'](?:icon|shortcut icon)["\'][^>]+href=["\']([^"\']+)["\']',
    r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\'](?:icon|shortcut icon)["\']',
]


def _strategy_html_parse(domain: str) -> Optional[bytes]:
    homepage = f"https://{domain}/"
    html_bytes = _fetch(homepage)
    if not html_bytes:
        return None
    try:
        html = html_bytes.decode("utf-8", errors="ignore")
    except Exception:
        return None

    for pattern in _ICON_PATTERNS:
        m = re.search(pattern, html, re.IGNORECASE)
        if not m:
            continue
        icon_url = urllib.parse.urljoin(homepage, m.group(1))
        data = _fetch(icon_url)
        if data:
            log.info("html_parse hit: %s", icon_url)
            return data
    return None


def _strategy_google_favicon(domain: str) -> Optional[bytes]:
    return _fetch(f"https://www.google.com/s2/favicons?domain={domain}&sz=128")


STRATEGIES: list[tuple[str, Callable[[str], Optional[bytes]]]] = [
    ("clearbit", _strategy_clearbit),
    ("html_parse", _strategy_html_parse),
    ("google_favicon", _strategy_google_favicon),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def fetch_logo(url_or_domain: str, slug: str,
               out_dir: Path = LOGO_DIR) -> Optional[Path]:
    """Fetch + normalize logo for a source.

    Args:
        url_or_domain: source URL or bare domain (e.g. 'newsbeast.gr')
        slug:          stable identifier used for filename (e.g. 'newsbeast')
        out_dir:       output directory (default /var/www/html/news/logos)

    Returns:
        Absolute Path to saved PNG on success, None if all strategies failed.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    domain = _domain_of(url_or_domain)

    for name, fn in STRATEGIES:
        log.info("Trying strategy '%s' for domain '%s'", name, domain)
        raw = fn(domain)
        if not raw:
            continue
        normalized = _normalize(raw)
        if not normalized:
            log.info("  strategy '%s' fetched data but normalize failed", name)
            continue
        out_path = out_dir / f"{slug}.png"
        out_path.write_bytes(normalized)
        # Make readable by nginx
        out_path.chmod(0o644)
        log.info("Logo saved: %s (strategy=%s, %d bytes)",
                 out_path, name, len(normalized))
        return out_path

    log.warning("All strategies failed for domain '%s'", domain)
    return None


def save_uploaded_logo(file_bytes: bytes, slug: str,
                       out_dir: Path = LOGO_DIR) -> Optional[Path]:
    """Save a manually uploaded logo, normalizing to the same dimensions."""
    out_dir.mkdir(parents=True, exist_ok=True)
    normalized = _normalize(file_bytes)
    if not normalized:
        return None
    out_path = out_dir / f"{slug}.png"
    out_path.write_bytes(normalized)
    out_path.chmod(0o644)
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    ap = argparse.ArgumentParser(description="Fetch logo for a news source")
    ap.add_argument("url_or_domain", help="e.g. https://www.newsbeast.gr/ or newsbeast.gr")
    ap.add_argument("slug", help="filename slug (e.g. 'newsbeast')")
    ap.add_argument("--out-dir", type=Path, default=LOGO_DIR)
    args = ap.parse_args()

    result = fetch_logo(args.url_or_domain, args.slug, args.out_dir)
    if result:
        print(f"OK: {result}")
        raise SystemExit(0)
    else:
        print("FAILED: all strategies exhausted")
        raise SystemExit(1)
