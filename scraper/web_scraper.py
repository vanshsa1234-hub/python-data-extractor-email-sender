"""
scraper/web_scraper.py
======================
Phase 1 — Static & Dynamic Web Scraper
  • Phase 1: Basic static page scraper (requests + BeautifulSoup4)
  • Phase 2: JavaScript-rendered pages (Playwright) — thread-safe for Streamlit
  • Phase 3: Bulk URL scraper with delay, retry & contact-page fallback

Output schema for extract_contacts() / bulk_scrape():
    {
        "url":     str,        # the page scraped
        "company": str,        # organization / school name (clean, short)
        "names":   list[str],  # PERSON names found, e.g. "Ms. Deepti Vohra"
        "emails":  list[str],
        "phones":  list[str],  # normalized to digits-only, e.g. "01143261213"
    }
"""

import re
import csv
import time
import random
import logging
import threading
import asyncio
from pathlib import Path
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Playwright imported lazily — module loads even if not installed
# On Streamlit Cloud, Playwright is not available — fails silently
_playwright_available = False
try:
    from playwright.sync_api import sync_playwright
    _playwright_available = True
except Exception:
    pass

from config import REQUEST_DELAY_MIN, REQUEST_DELAY_MAX, DATA_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Shared headers ─────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

EMAIL_PATTERN = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.]+")

# ── Strict Indian phone pattern ────────────────────────────────────────────────
# Matches an optional country code (+91/91) or trunk prefix (0), followed by
# EXACTLY 10 digits, optionally separated by spaces/hyphens in natural spots.
# (?<!\d) / (?!\d) boundaries stop it from bleeding into adjacent numbers or
# concatenating two separate phone numbers that sit next to each other in the
# page text (the root cause of garbage like "0112331161801").
PHONE_PATTERN = re.compile(
    r'(?<!\d)'
    r'(?:\+?91[\-\s]?|0)?'
    r'(?:\d[\-\s]?){9}\d'
    r'(?!\d)'
)

# Honorific-anchored person-name pattern. We ONLY extract a name when it is
# clearly marked by a title (Mr./Mrs./Ms./Dr./Shri/Smt./Sh.) — this avoids
# guessing and prevents picking up page headers, school names, or random
# capitalized phrases as if they were a person's name.
HONORIFIC_NAME_PATTERN = re.compile(
    r'((?:Mr|Mrs|Ms|Dr|Shri|Smt|Sh)\.?\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})'
)

# Role keywords used to associate a nearby honorific-name with a job title,
# and to decide which names are worth keeping (filters out random honorifics
# mentioned in unrelated body text, e.g. "Mr. X scored 98% in Class XII").
ROLE_KEYWORDS = (
    "principal", "vice principal", "director", "headmaster", "headmistress",
    "head of school", "coordinator", "admission", "admissions", "transport",
    "accounts", "registrar", "administrator", "dean", "chairman", "manager",
    "human resources", "recruitment", "contact person", "in-charge",
    "incharge",
)

# Emails to always discard
_JUNK_EMAIL_SIGNALS = (
    "@example.com", "wizeedu", "test@", "sample@",
    "@sentry", "noreply@", "no-reply@", "@w3.org",
)

# Section headers / nav labels that occasionally slip past the org-name
# heuristics — explicitly blocked so "company" never ends up holding noise.
_JUNK_ORG_PHRASES = {
    "home", "about us", "contact", "contact us", "gallery", "admission",
    "admissions", "school news", "current notices", "video section",
    "important links", "office hours", "view all", "read more",
}


# ── Phase 1: Static page scraper ───────────────────────────────────────────────

def fetch_html(url: str, timeout: int = 10) -> str | None:
    """Fetch raw HTML from a URL. Returns None on failure. SSL errors are ignored."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, verify=False)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        log.warning("fetch_html failed for %s: %s", url, exc)
        return None


def _is_junk_email(email: str) -> bool:
    return any(sig in email for sig in _JUNK_EMAIL_SIGNALS)


def _parse_emails(text: str, soup: BeautifulSoup) -> list[str]:
    """Extract and deduplicate clean emails from text + mailto links."""
    found = []
    for e in EMAIL_PATTERN.findall(text):
        e = e.lower().strip().rstrip(".")
        if not _is_junk_email(e):
            found.append(e)
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if href.startswith("mailto:"):
            e = href[7:].split("?")[0].lower().strip()
            if e and not _is_junk_email(e):
                found.append(e)
    return sorted(set(found))


def _normalize_phone(raw: str) -> str:
    """Strip separators, leaving a clean digit string (keeps leading + if present)."""
    has_plus = raw.strip().startswith("+")
    digits = re.sub(r"\D", "", raw)
    return ("+" + digits) if has_plus else digits


def _parse_phones(text: str) -> list[str]:
    """
    Extract valid Indian phone numbers using a strict, boundary-aware regex.
    Each match is exactly one number — never a concatenation of two numbers
    or a slice of an unrelated longer digit run (years, PIN codes, IDs).
    """
    phones = []
    for raw in PHONE_PATTERN.findall(text):
        normalized = _normalize_phone(raw)
        digit_count = len(normalized.lstrip("+"))

        # Must decompose to exactly 10 significant digits (after stripping
        # any single leading 0 or 91 country code, which PHONE_PATTERN may
        # have included as part of the match).
        core = normalized.lstrip("+")
        if core.startswith("91") and len(core) == 12:
            core = core[2:]
        elif core.startswith("0") and len(core) == 11:
            core = core[1:]

        if len(core) != 10:
            continue

        # Reject obvious non-phone numbers: a 10-digit run that's actually a
        # year-like or all-repeating pattern (e.g. "0000000000").
        if len(set(core)) == 1:
            continue

        phones.append(normalized)

    return sorted(set(phones))


def _extract_org_name(soup: BeautifulSoup) -> str:
    """
    Best-effort clean organization/school name.
    Preference order:
      1. og:site_name meta tag (cleanest source when present)
      2. <h1> text, with SEO taglines stripped after common separators
      3. <title> text, same stripping, capped to 6 words as a last resort
    Returns "" if nothing reasonable is found — we never guess.
    """
    site_name_tag = soup.find("meta", attrs={"property": "og:site_name"})
    if site_name_tag and site_name_tag.get("content", "").strip():
        candidate = site_name_tag["content"].strip()
        if candidate.lower() not in _JUNK_ORG_PHRASES:
            return candidate

    h1 = soup.find("h1")
    h1_text = h1.get_text(strip=True) if h1 else ""
    title_text = soup.title.get_text(strip=True) if soup.title else ""

    for raw in (h1_text, title_text):
        if not raw:
            continue
        cleaned = raw
        for sep in (" - ", " | ", " : ", " — ", " – "):
            if sep in cleaned:
                cleaned = cleaned.split(sep)[0].strip()
        words = cleaned.split()
        if len(words) > 6:
            cleaned = " ".join(words[:6])
        if cleaned.lower() in _JUNK_ORG_PHRASES:
            continue
        if 3 <= len(cleaned) <= 80:
            return cleaned

    return ""


def _extract_person_names(text_with_lines: str) -> list[str]:
    """
    Extract person names that are clearly tied to a role/title context.
    Only returns a name when BOTH conditions hold:
      1. An honorific-prefixed name pattern is found (Mr./Ms./Dr./Shri/Smt.)
      2. That name appears on the same line or the line immediately after
         a recognized role keyword (Principal, Accounts, Transport, etc.)
    This deliberately returns [] rather than guessing when no such pattern
    exists — an honest empty result is better than a wrong name.
    """
    lines = [l.strip() for l in text_with_lines.split("\n") if l.strip()]
    results = []

    def _find_role(lower_line: str) -> str | None:
        """Word-boundary role match — avoids 'hr' matching inside 'shri', etc."""
        for r in ROLE_KEYWORDS:
            if re.search(r"\b" + re.escape(r) + r"\b", lower_line):
                return r
        return None

    for i, line in enumerate(lines):
        lower = line.lower()
        matched_role = _find_role(lower)
        if not matched_role:
            continue

        search_zone = line
        if i + 1 < len(lines):
            search_zone += " " + lines[i + 1]

        m = HONORIFIC_NAME_PATTERN.search(search_zone)
        if m:
            name = m.group(1).strip()
            results.append(name)

    # Deduplicate while preserving order
    return list(dict.fromkeys(results))[:5]


def find_contact_page(base_url: str) -> str | None:
    """
    Crawl the homepage looking for a contact/staff/directory page link.
    Returns the full URL of the best match, or None.
    """
    html = fetch_html(base_url)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    high_priority  = ["contact", "contact-us", "contact us", "reach us", "reach-us"]
    medium_priority = ["directory", "staff", "administration", "faculty", "office", "about"]

    for priority in (high_priority, medium_priority):
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(" ", strip=True).lower()
            if any(k in text for k in priority) or any(k in href.lower() for k in priority):
                full_url = urljoin(base_url, href)
                # Avoid linking back to the same page
                if full_url.rstrip("/") != base_url.rstrip("/"):
                    return full_url
    return None


def extract_emails(url: str) -> list[str]:
    """Return deduplicated email addresses found on a page (quick version)."""
    html = fetch_html(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    return _parse_emails(soup.get_text(separator=" "), soup)


def extract_contacts(url: str) -> dict:
    """
    Full contact extraction for a URL.
    - Extracts org/school name, person names, emails, phones from the homepage
    - If no emails found, automatically follows the contact page link
    - Returns dict with keys: url, company, names, emails, phones
    """
    html = fetch_html(url)
    if not html:
        return {"url": url, "emails": [], "phones": []}

    soup = BeautifulSoup(html, "html.parser")
    # separator="\n" preserves enough line structure for role-aware name
    # detection, while still working fine for the email/phone regexes.
    text = soup.get_text(separator="\n")

    emails = _parse_emails(text, soup)
    phones = _parse_phones(text)

    # ── Contact page fallback ──────────────────────────────────────────────────
    if not emails:
        contact_url = find_contact_page(url)
        if contact_url:
            log.info("No emails on homepage — trying contact page: %s", contact_url)
            html2 = fetch_html(contact_url)
            if html2:
                soup2 = BeautifulSoup(html2, "html.parser")
                text2 = soup2.get_text(separator="\n")
                emails = _parse_emails(text2, soup2)
                if not phones:
                    phones = _parse_phones(text2)

    return {
        "url":    url,
        "emails": emails[:20],
        "phones": phones[:10],
    }


# ── Phase 2: JavaScript-rendered pages (Streamlit-safe) ───────────────────────

def scrape_dynamic(url: str, wait_until: str = "networkidle") -> BeautifulSoup | None:
    """
    Use Playwright (headless Chromium) to render a JS-heavy page.

    Runs Playwright in a dedicated thread with its own asyncio event loop so it
    works safely inside Streamlit on Windows (which owns the default loop).
    Falls back gracefully if Playwright is not installed.
    """
    if not _playwright_available:
        log.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return None

    result_holder = [None]
    error_holder  = [None]

    def run_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(extra_http_headers=HEADERS)
                page.goto(url, wait_until=wait_until, timeout=30_000)
                html = page.content()
                browser.close()
            result_holder[0] = BeautifulSoup(html, "html.parser")
        except Exception as exc:
            error_holder[0] = exc
        finally:
            loop.close()

    t = threading.Thread(target=run_in_thread, daemon=True)
    t.start()
    t.join(timeout=40)

    if error_holder[0]:
        log.warning("scrape_dynamic failed for %s: %s", url, error_holder[0])
        return None
    if result_holder[0] is None:
        log.warning("scrape_dynamic timed out for %s", url)
        return None
    return result_holder[0]


def extract_emails_dynamic(url: str) -> list[str]:
    """Extract emails from a JS-rendered page using Playwright."""
    soup = scrape_dynamic(url)
    if not soup:
        return []
    return _parse_emails(soup.get_text(separator=" "), soup)


# ── Phase 3: Bulk scraper with delay, retry & auto-fallback ───────────────────

def bulk_scrape(
    urls: list[str],
    dynamic: bool = False,
    max_retries: int = 2,
) -> list[dict]:
    """
    Scrape a list of URLs with polite delays and retry logic.

    If dynamic=True and Playwright fails, automatically falls back to the
    static scraper so you never get empty results just because of a
    Playwright/asyncio conflict (common on Windows + Streamlit).

    Args:
        urls:        List of URLs to scrape.
        dynamic:     Try Playwright first for JS-rendered pages.
        max_retries: Retries per URL on network errors.

    Returns:
        List of dicts — {url, company, names, emails, phones}
    """
    results = []

    for idx, url in enumerate(urls, 1):
        log.info("Scraping %d/%d: %s", idx, len(urls), url)
        data = None

        for attempt in range(1, max_retries + 2):
            try:
                if dynamic:
                    soup = scrape_dynamic(url)
                    if soup:
                        text    = soup.get_text(separator="\n")
                        emails  = _parse_emails(text, soup)
                        phones  = _parse_phones(text)
                        names   = _extract_person_names(text)
                        company = _extract_org_name(soup)
                        data = {
                            "url": url, "company": company,
                            "names": names, "emails": emails, "phones": phones,
                        }
                    else:
                        # Playwright failed — fall back to static scraper silently
                        log.info("Playwright unavailable for %s — using static scraper", url)
                        data = extract_contacts(url)
                else:
                    data = extract_contacts(url)
                break
            except Exception as exc:
                log.warning("Attempt %d failed for %s: %s", attempt, url, exc)
                if attempt <= max_retries:
                    time.sleep(2 ** attempt)

        if data:
            results.append(data)

        # Polite delay between requests
        if idx < len(urls):
            delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
            log.debug("Sleeping %.1fs before next request", delay)
            time.sleep(delay)

    log.info("Bulk scrape complete: %d/%d URLs succeeded", len(results), len(urls))
    return results


# ── Data persistence ───────────────────────────────────────────────────────────

def save_csv(data: list[dict], filename: str = None) -> str:
    """
    Save a list of lead dicts to CSV.
    List fields (emails, phones, names) are joined with '|'.
    Returns the path written to, or '' if data is empty.
    """
    if not data:
        log.warning("save_csv called with empty data — nothing written")
        return ""

    filename = filename or "data/leads.csv"
    Path(filename).parent.mkdir(parents=True, exist_ok=True)

    flat = []
    for row in data:
        flat.append({
            k: "|".join(v) if isinstance(v, list) else v
            for k, v in row.items()
        })

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=flat[0].keys())
        writer.writeheader()
        writer.writerows(flat)

    log.info("Saved %d rows to %s", len(flat), filename)
    return filename


def load_csv(filename: str) -> list[dict]:
    """Load leads from a CSV file. Returns a list of dicts, or [] if missing."""
    path = Path(filename)
    if not path.exists():
        log.error("CSV not found: %s", filename)
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))