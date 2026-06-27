"""
main.py
=======
Full Pipeline Orchestrator
  Step 1 → Scrape leads from websites
  Step 2 → Scrape leads from LinkedIn
  Step 3 → Clean & merge all leads
  Step 4 → Enrich with emails (Hunter.io)
  Step 5 → Bulk send personalised emails
  Step 6 → Print stats

Run:
    python main.py --mode scrape        # scrape only
    python main.py --mode email         # email only (uses existing leads.csv)
    python main.py --mode full          # end-to-end pipeline
    python main.py --mode stats         # show send + open stats
    python main.py --mode dry           # full pipeline, no real emails sent
"""

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/pipeline.log", mode="a"),
    ],
)
log = logging.getLogger(__name__)

# ── helpers ────────────────────────────────────────────────────────────────────

def load_config():
    """Import config and validate required fields for the chosen mode."""
    import config
    return config


def scrape_step(cfg) -> list[dict]:
    """Phase 1 — Web scraping."""
    from scraper.web_scraper import bulk_scrape, save_csv
    from scraper.cleaner import clean_leads

    # ── Edit these URLs with your real targets ──────────────────────────────
    target_urls = [
        # "https://example.com/contact",
        # "https://example.com/team",
    ]

    if not target_urls:
        log.warning("No target URLs defined in main.py → scrape_step(). Add URLs to target_urls list.")
        return []

    log.info("=== Step 1: Web Scraping %d URLs ===", len(target_urls))
    raw = bulk_scrape(target_urls, dynamic=False)

    # Flatten: each URL may yield multiple emails
    flat_leads = []
    for item in raw:
        for email in item.get("emails", []):
            flat_leads.append({
                "email":  email,
                "name":   "",
                "source": item["url"],
            })

    cleaned = clean_leads(flat_leads)
    log.info("Web scrape: %d valid leads", len(cleaned))
    return cleaned


def linkedin_step(cfg) -> list[dict]:
    """Phase 2 — LinkedIn scraping."""
    from scraper.linkedin_scraper import search_people, enrich_leads_with_emails
    from scraper.cleaner import clean_leads

    # ── Edit these search params ─────────────────────────────────────────────
    searches = [
        # {"keyword": "Marketing Manager", "location": "India", "count": 20},
        # {"keyword": "HR Director",       "location": "Germany", "count": 20},
    ]

    if not searches or not cfg.LINKEDIN_EMAIL:
        log.warning("LinkedIn scraping skipped — no searches defined or no credentials in config.py")
        return []

    log.info("=== Step 2: LinkedIn Scraping ===")
    all_li = []
    for s in searches:
        leads = search_people(
            keyword=s["keyword"],
            location=s["location"],
            count=s.get("count", 20),
            li_email=cfg.LINKEDIN_EMAIL,
            li_password=cfg.LINKEDIN_PASSWORD,
        )
        all_li.extend(leads)

    enriched = enrich_leads_with_emails(all_li, api_key=cfg.HUNTER_API_KEY)
    cleaned  = clean_leads(enriched)
    log.info("LinkedIn: %d valid leads after enrichment", len(cleaned))
    return cleaned


def merge_and_save(web_leads: list[dict], li_leads: list[dict], cfg) -> list[dict]:
    """Merge all lead sources, deduplicate, save CSV."""
    from scraper.cleaner import deduplicate
    from scraper.web_scraper import save_csv, load_csv

    all_leads = web_leads + li_leads

    # Also load any existing leads from previous runs
    existing_path = cfg.LEADS_CSV
    if Path(existing_path).exists():
        existing = load_csv(existing_path)
        log.info("Loaded %d existing leads from %s", len(existing), existing_path)
        all_leads = existing + all_leads

    all_leads = deduplicate(all_leads, key="email")
    log.info("=== Step 3: Merged leads: %d unique ===", len(all_leads))

    if all_leads:
        save_csv(all_leads, existing_path)
    return all_leads


def email_step(leads: list[dict], cfg, dry_run: bool = False) -> dict:
    """Phase 3 — Bulk email sending."""
    from emailer.sender import bulk_send
    from scraper.cleaner import is_valid_email

    # Filter out unsubscribed
    from emailer.tracker import is_unsubscribed
    sendable = [
        l for l in leads
        if l.get("email") and is_valid_email(l["email"]) and not is_unsubscribed(l["email"])
    ]

    if not sendable:
        log.warning("No sendable leads — check your leads.csv has valid emails.")
        return {"sent": 0, "failed": 0, "skipped": 0}

    log.info("=== Step 4: Sending emails to %d leads (dry_run=%s) ===", len(sendable), dry_run)

    # ── Customise your email content here ────────────────────────────────────
    subject  = "Quick question about {{ company if company else 'your work' }}"
    message  = (
        "I came across your profile and thought there might be a great opportunity "
        "for us to connect. I'd love to learn more about what you're working on."
    )
    # ─────────────────────────────────────────────────────────────────────────

    return bulk_send(
        leads=sendable,
        subject_template=subject,
        custom_message=message,
        dry_run=dry_run,
    )


def stats_step():
    """Print send + open stats."""
    from emailer.tracker import get_open_stats
    stats = get_open_stats()
    print("\n=== Campaign Stats ===")
    for k, v in stats.items():
        print(f"  {k:<20}: {v}")
    print()


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scraper + Email Pipeline")
    parser.add_argument(
        "--mode",
        choices=["scrape", "email", "full", "dry", "stats", "enroll", "warmup"],
        default="full",
        help=(
            "scrape=only scrape | email=only send (existing CSV) | "
            "full=end-to-end | dry=full but no real emails | "
            "stats=show metrics | enroll=enroll leads into drip | "
            "warmup=show warmup status"
        ),
    )
    args = parser.parse_args()
    cfg  = load_config()

    Path("data").mkdir(exist_ok=True)

    if args.mode == "stats":
        stats_step()
        return

    if args.mode == "warmup":
        from emailer.warmup import WarmupSchedule
        WarmupSchedule().print_report()
        return

    if args.mode == "enroll":
        from scraper.web_scraper import load_csv
        from emailer.followup import enroll_leads
        leads = load_csv(cfg.LEADS_CSV)
        if not leads:
            log.warning("No leads found in %s — run scrape first", cfg.LEADS_CSV)
        else:
            n = enroll_leads(leads)
            print(f"\nEnrolled {n} new leads into drip sequence.")
            print(f"Run 'python main.py --mode stats' to see sequence stats.\n")
        return

    web_leads = li_leads = []

    if args.mode in ("scrape", "full", "dry"):
        web_leads = scrape_step(cfg)
        li_leads  = linkedin_step(cfg)
        all_leads = merge_and_save(web_leads, li_leads, cfg)
    else:
        # email-only: load from CSV
        from scraper.web_scraper import load_csv
        all_leads = load_csv(cfg.LEADS_CSV)
        log.info("Loaded %d leads from %s", len(all_leads), cfg.LEADS_CSV)

    if args.mode in ("email", "full", "dry"):
        dry = args.mode == "dry"
        summary = email_step(all_leads, cfg, dry_run=dry)
        print(f"\nEmail summary: {summary}")

    stats_step()


if __name__ == "__main__":
    main()