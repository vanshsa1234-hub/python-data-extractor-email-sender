"""
scraper/cleaner.py
==================
Data cleaning utilities for scraped leads.
  • Deduplicate by email
  • Validate email format
  • Normalise phone numbers
  • Drop obviously invalid entries
"""

import re
import logging

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[\w.+\-]+@[\w\-]+\.[a-zA-Z]{2,}$")
# Common disposable/spam domains to filter out
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com",
    "throwaway.email", "yopmail.com", "trashmail.com",
}


def is_valid_email(email: str) -> bool:
    """Basic regex + disposable-domain check."""
    if not email or not EMAIL_RE.match(email.strip()):
        return False
    domain = email.split("@")[-1].lower()
    return domain not in DISPOSABLE_DOMAINS


def clean_email(email: str) -> str:
    return email.strip().lower()


def clean_phone(phone: str) -> str:
    """Strip all non-digit characters except leading +."""
    digits = re.sub(r"[^\d+]", "", phone)
    return digits if len(digits) >= 7 else ""


def clean_name(name: str) -> str:
    """Title-case a name, strip extra whitespace."""
    return " ".join(name.strip().split()).title()


def deduplicate(leads: list[dict], key: str = "email") -> list[dict]:
    """
    Remove duplicate leads by a given key field.
    Keeps the first occurrence.
    """
    seen = set()
    unique = []
    for lead in leads:
        val = lead.get(key, "").strip().lower()
        if val and val not in seen:
            seen.add(val)
            unique.append(lead)
    removed = len(leads) - len(unique)
    if removed:
        log.info("Removed %d duplicate leads (key=%s)", removed, key)
    return unique


def clean_leads(leads: list[dict]) -> list[dict]:
    """
    Run all cleaning steps on a list of lead dicts.
    Only keeps: email, phone, website.
    Handles both flat dicts and bulk_scrape raw output (list fields).
    Drops leads with invalid emails.
    """
    cleaned = []
    dropped = 0

    for lead in leads:

        # ── Resolve all emails from this row ───────────────────────────────────
        emails_raw = lead.get("email", "") or lead.get("emails", [])
        if isinstance(emails_raw, str):
            emails_list = [e.strip() for e in emails_raw.split("|") if e.strip()]
        elif isinstance(emails_raw, list):
            emails_list = emails_raw
        else:
            emails_list = []

        valid_emails = [clean_email(e) for e in emails_list if is_valid_email(clean_email(e))]

        if not valid_emails:
            dropped += 1
            continue

        # ── Resolve phone ──────────────────────────────────────────────────────
        phone_raw = lead.get("phone", "") or lead.get("phones", [])
        if isinstance(phone_raw, list):
            phone = phone_raw[0] if phone_raw else ""
        elif isinstance(phone_raw, str) and "|" in phone_raw:
            parts = [p.strip() for p in phone_raw.split("|") if p.strip()]
            phone = parts[0] if parts else ""
        else:
            phone = str(phone_raw).strip()

        # ── Resolve website ────────────────────────────────────────────────────
        website = lead.get("url", lead.get("source", lead.get("website", "")))

        # ── One row per valid email ────────────────────────────────────────────
        for email in valid_emails:
            cleaned.append({
                "email":   email,
                "phone":   clean_phone(phone) if phone else "",
                "website": website,
            })

    log.info("clean_leads: %d valid, %d dropped", len(cleaned), dropped)
    return deduplicate(cleaned)