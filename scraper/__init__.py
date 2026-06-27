"""
scraper package
===============
Exports the most commonly used functions so callers can do:
    from scraper import extract_emails, bulk_scrape, clean_leads
"""

from scraper.web_scraper import (
    extract_emails,
    extract_contacts,
    extract_emails_dynamic,
    bulk_scrape,
    save_csv,
    load_csv,
)
from scraper.cleaner import (
    clean_leads,
    deduplicate,
    is_valid_email,
    clean_email,
    clean_name,
)
from scraper.linkedin_scraper import (
    search_people,
    find_email_hunter,
    guess_email,
    enrich_leads_with_emails,
)

from scraper.email_verifier import (
    verify_email,
    verify_bulk,
    filter_verified,
)

__all__ = [
    "verify_email", "verify_bulk", "filter_verified",
    "extract_emails", "extract_contacts", "extract_emails_dynamic",
    "bulk_scrape", "save_csv", "load_csv",
    "clean_leads", "deduplicate", "is_valid_email", "clean_email", "clean_name",
    "search_people", "find_email_hunter", "guess_email", "enrich_leads_with_emails",
]