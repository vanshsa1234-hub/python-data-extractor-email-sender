"""
scraper/linkedin_scraper.py
============================
Phase 2 — LinkedIn Data Extractor

IMPORTANT: Use a throwaway/dummy LinkedIn account.
linkedin-api is an unofficial library that may violate LinkedIn ToS.
Use at your own risk.

Methods offered:
  1. linkedin-api (unofficial Python lib) — rich data, medium ban risk
  2. Hunter.io API  — email finder by name + domain
"""

import logging
import requests as req

log = logging.getLogger(__name__)

# ── linkedin-api wrapper ───────────────────────────────────────────────────────

def get_linkedin_api(email: str, password: str):
    """
    Return an authenticated Linkedin API object.
    Raises ImportError if linkedin-api is not installed.
    """
    try:
        from linkedin_api import Linkedin
    except ImportError:
        raise ImportError(
            "linkedin-api not installed. Run: pip install linkedin-api"
        )
    return Linkedin(email, password)


def search_people(
    keyword: str,
    location: str,
    count: int = 50,
    li_email: str = "",
    li_password: str = "",
) -> list[dict]:
    """
    Search LinkedIn for people matching a keyword + location.

    Args:
        keyword:     Job title or skill (e.g. 'Marketing Manager')
        location:    Region name (e.g. 'France', 'New York')
        count:       Max number of profiles to fetch
        li_email:    LinkedIn account email (use a dummy account)
        li_password: LinkedIn account password

    Returns:
        List of lead dicts with: name, title, company, linkedin_id
    """
    if not li_email or not li_password:
        from config import LINKEDIN_EMAIL, LINKEDIN_PASSWORD
        li_email, li_password = LINKEDIN_EMAIL, LINKEDIN_PASSWORD

    try:
        api = get_linkedin_api(li_email, li_password)
    except Exception as exc:
        log.error("LinkedIn auth failed: %s", exc)
        return []

    try:
        results = api.search_people(keywords=keyword, regions=[location], limit=count)
    except Exception as exc:
        log.error("LinkedIn search failed: %s", exc)
        return []

    leads = []
    for r in results:
        try:
            profile = api.get_profile(r["public_id"])
            leads.append({
                "name":        profile.get("firstName", "") + " " + profile.get("lastName", ""),
                "title":       profile.get("headline", ""),
                "company":     profile.get("companyName", ""),
                "linkedin_id": r.get("public_id", ""),
                "source":      "linkedin",
            })
        except Exception as exc:
            log.warning("Could not fetch profile %s: %s", r.get("public_id"), exc)

    log.info("LinkedIn search returned %d leads for '%s' in '%s'", len(leads), keyword, location)
    return leads


# ── Email finder via Hunter.io ─────────────────────────────────────────────────

def find_email_hunter(
    first_name: str,
    last_name: str,
    domain: str,
    api_key: str = "",
) -> str | None:
    """
    Use Hunter.io to guess / verify an email address.
    Free tier: 25 searches/month.

    Returns the email string or None if not found.
    """
    if not api_key:
        from config import HUNTER_API_KEY
        api_key = HUNTER_API_KEY

    if not api_key:
        log.error("No Hunter.io API key set in config.py")
        return None

    try:
        resp = req.get(
            "https://api.hunter.io/v2/email-finder",
            params={
                "first_name": first_name,
                "last_name":  last_name,
                "domain":     domain,
                "api_key":    api_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        email = data.get("data", {}).get("email")
        if email:
            log.info("Hunter.io found email for %s %s: %s", first_name, last_name, email)
        return email
    except Exception as exc:
        log.warning("Hunter.io lookup failed: %s", exc)
        return None


def guess_email(first: str, last: str, domain: str) -> list[str]:
    """
    Generate common email pattern guesses when no API is available.
    Returns a list of candidate addresses to verify.
    """
    f, l, d = first.lower(), last.lower(), domain.lower()
    return [
        f"{f}.{l}@{d}",
        f"{f[0]}{l}@{d}",
        f"{f}@{d}",
        f"{f}{l}@{d}",
        f"{f[0]}.{l}@{d}",
    ]


def enrich_leads_with_emails(
    leads: list[dict],
    api_key: str = "",
) -> list[dict]:
    """
    Attempt to add an 'email' field to each lead using Hunter.io.
    Falls back to pattern guessing if no API key provided.
    Leads must have 'name' and 'company' fields.
    """
    enriched = []
    for lead in leads:
        name_parts = lead.get("name", "").split()
        if len(name_parts) < 2:
            enriched.append(lead)
            continue

        first, last = name_parts[0], name_parts[-1]
        company = lead.get("company", "")

        # Try to derive a domain from company name (rough heuristic)
        domain = company.lower().replace(" ", "") + ".com" if company else ""

        email = None
        if api_key or True:  # always attempt Hunter first
            email = find_email_hunter(first, last, domain, api_key)

        if not email:
            guesses = guess_email(first, last, domain)
            log.debug("Email guesses for %s: %s", lead["name"], guesses)
            lead["email_guesses"] = "|".join(guesses)
        else:
            lead["email"] = email

        enriched.append(lead)
    return enriched