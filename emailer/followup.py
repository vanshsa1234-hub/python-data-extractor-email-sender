"""
emailer/followup.py
===================
Phase 4 — Automated Follow-Up Drip Sequences

What this does:
  • Manages a 3-email drip sequence per lead (Day 0, Day 3, Day 7)
  • Tracks which sequence step each lead is on in SQLite
  • Skips leads who are unsubscribed
  • APScheduler runs the job daily at a configured time
  • Safe to restart — picks up from wherever it left off
  • Credentials (email + app password) are passed in at runtime — never hardcoded
"""

import logging
import argparse
import sqlite3
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler

log = logging.getLogger(__name__)

# Indian Standard Time is UTC+5:30, fixed offset (no daylight saving in India)
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist_str() -> str:
    """Current datetime in IST, formatted like SQLite's default TIMESTAMP."""
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return date.today().isoformat()


# ── Drip sequence definition ───────────────────────────────────────────────────
# Each step: (day_offset, subject_template, body_message)
DRIP_SEQUENCE = [
    (
        0,
        "Quick question about {{ company if company else 'your work' }}",
        (
            "I came across your profile and thought there could be a great synergy "
            "between what you're doing and what we offer. Would love to connect briefly."
        ),
    ),
    (
        3,
        "Following up — {{ first_name }}",
        (
            "Just circling back on my previous email. I know things get busy — "
            "I wanted to make sure this didn't get lost. Even a 10-minute call "
            "could be worth it for both of us."
        ),
    ),
    (
        7,
        "Last note — {{ first_name }}",
        (
            "I'll keep this short — this is my last follow-up. "
            "If there's ever a good time to connect in the future, "
            "feel free to reach out. Wishing you all the best!"
        ),
    ),
]


# ── Sequence DB helpers ────────────────────────────────────────────────────────

def init_sequence_db(db_path: str = "data/sent_log.db") -> sqlite3.Connection:
    """Add sequence-tracking table to the existing DB."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS drip_sequence (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT    NOT NULL UNIQUE,
            current_step  INTEGER DEFAULT 0,
            next_send_at  DATE    NOT NULL,
            status        TEXT    DEFAULT 'active',
            enrolled_at   TEXT
        )
    """)
    conn.commit()
    return conn


def enroll_leads(leads: list[dict], db_path: str = "data/sent_log.db") -> int:
    """
    Enroll a list of leads into the drip sequence.
    Skips leads already enrolled.
    Returns count of newly enrolled leads.
    """
    conn = init_sequence_db(db_path)
    enrolled = 0
    today = today_str()

    for lead in leads:
        email = lead.get("email", "").strip().lower()
        if not email:
            continue
        try:
            conn.execute(
                """INSERT OR IGNORE INTO drip_sequence
                   (email, current_step, next_send_at, status, enrolled_at)
                   VALUES (?, 0, ?, 'active', ?)""",
                (email, today, today),   # enrolled_at stored as plain DATE string
            )
            if conn.total_changes:
                enrolled += 1
        except sqlite3.Error as exc:
            log.warning("Could not enroll %s: %s", email, exc)

    conn.commit()
    conn.close()
    log.info("Enrolled %d new leads into drip sequence", enrolled)
    return enrolled


def get_due_leads(db_path: str = "data/sent_log.db") -> list[dict]:
    """Return leads whose next_send_at is today or earlier and are still active."""
    conn = init_sequence_db(db_path)
    today = today_str()
    rows = conn.execute(
        """SELECT email, current_step FROM drip_sequence
           WHERE status = 'active'
             AND next_send_at <= ?
             AND current_step < ?""",
        (today, len(DRIP_SEQUENCE)),
    ).fetchall()
    conn.close()
    return [{"email": r[0], "current_step": r[1]} for r in rows]


def advance_step(email: str, db_path: str = "data/sent_log.db") -> None:
    """Move a lead to the next sequence step, or mark as completed."""
    conn = init_sequence_db(db_path)
    row = conn.execute(
        "SELECT current_step, enrolled_at FROM drip_sequence WHERE email = ?", (email,)
    ).fetchone()

    if not row:
        conn.close()
        return

    current_step, enrolled_at_raw = row
    next_step = current_step + 1

    if next_step >= len(DRIP_SEQUENCE):
        conn.execute(
            "UPDATE drip_sequence SET status='completed', current_step=? WHERE email=?",
            (next_step, email),
        )
        log.info("Sequence completed for %s", email)
    else:
        # enrolled_at is stored as a plain DATE string (YYYY-MM-DD)
        try:
            enrolled_date = date.fromisoformat(str(enrolled_at_raw).strip()[:10])
        except Exception:
            enrolled_date = date.today()

        day_offset = DRIP_SEQUENCE[next_step][0]
        next_date  = (enrolled_date + timedelta(days=day_offset)).isoformat()

        conn.execute(
            "UPDATE drip_sequence SET current_step=?, next_send_at=? WHERE email=?",
            (next_step, next_date, email),
        )
        log.info("Advanced %s to step %d, next send %s", email, next_step, next_date)

    conn.commit()
    conn.close()


def pause_lead(email: str, db_path: str = "data/sent_log.db") -> None:
    conn = init_sequence_db(db_path)
    conn.execute("UPDATE drip_sequence SET status='paused' WHERE email=?", (email,))
    conn.commit()
    conn.close()
    log.info("Paused drip sequence for %s", email)


def reactivate_lead(email: str, db_path: str = "data/sent_log.db") -> bool:
    conn = init_sequence_db(db_path)
    cur = conn.execute("UPDATE drip_sequence SET status='active' WHERE email=?", (email,))
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def delete_lead_from_sequence(email: str, db_path: str = "data/sent_log.db") -> bool:
    conn = init_sequence_db(db_path)
    cur = conn.execute("DELETE FROM drip_sequence WHERE email=?", (email,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def set_lead_step(email: str, step: int, db_path: str = "data/sent_log.db") -> bool:
    """Manually set a lead's step and make it due today."""
    if step < 0 or step >= len(DRIP_SEQUENCE):
        raise ValueError(f"step must be 0–{len(DRIP_SEQUENCE) - 1}")
    conn = init_sequence_db(db_path)
    cur = conn.execute(
        "UPDATE drip_sequence SET current_step=?, next_send_at=?, status='active' WHERE email=?",
        (step, today_str(), email),
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def get_all_sequence_leads(db_path: str = "data/sent_log.db") -> list[dict]:
    conn = init_sequence_db(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM drip_sequence ORDER BY enrolled_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_sequence_stats(db_path: str = "data/sent_log.db") -> dict:
    conn = init_sequence_db(db_path)
    stats = {}
    for status in ("active", "paused", "completed"):
        count = conn.execute(
            "SELECT COUNT(*) FROM drip_sequence WHERE status=?", (status,)
        ).fetchone()[0]
        stats[status] = count
    stats["total"] = sum(stats.values())
    conn.close()
    return stats


# ── Core drip runner ───────────────────────────────────────────────────────────

def run_drip_job(
    db_path: str = "data/sent_log.db",
    dry_run: bool = False,
    sender_email: str = None,
    sender_password: str = None,
    sender_name: str = None,
    user_leads: list[dict] = None,   # pass leads from DB so we get name/company
) -> dict:
    """
    The main job: check who is due, send the right sequence email, advance their step.

    Args:
        db_path:         SQLite path.
        dry_run:         If True, log but do NOT actually send.
        sender_email:    Gmail address (overrides config).
        sender_password: Gmail App Password (overrides config).
        sender_name:     Display name (overrides config).
        user_leads:      List of lead dicts from the user DB for personalisation.
                         If None, only email field is available (no name/company).

    Returns:
        Summary dict: {sent, skipped, failed, details}
    """
    from emailer.sender import send_email, init_db, log_email, daily_send_count
    from emailer.tracker import add_tracking_pixel, is_unsubscribed
    from jinja2 import Template
    from config import DAILY_SEND_LIMIT

    due = get_due_leads(db_path)
    log.info("Drip job: %d leads due today (dry_run=%s)", len(due), dry_run)

    if not due:
        return {"sent": 0, "skipped": 0, "failed": 0, "details": []}

    # Build lookup from user leads for personalisation (name, company, etc.)
    lead_lookup = {}
    if user_leads:
        for l in user_leads:
            em = l.get("email", "").strip().lower()
            if em:
                lead_lookup[em] = l

    email_conn = init_db(db_path)
    sent = skipped = failed = 0
    details = []

    for item in due:
        email    = item["email"]
        step_idx = item["current_step"]

        # Skip unsubscribed
        if is_unsubscribed(email, db_path):
            pause_lead(email, db_path)
            skipped += 1
            details.append({"email": email, "step": step_idx + 1, "result": "skipped (unsubscribed)"})
            continue

        # Daily cap
        if daily_send_count(email_conn) >= DAILY_SEND_LIMIT:
            log.warning("Daily send limit reached — stopping drip job")
            skipped += len(due) - (sent + skipped + failed)
            break

        # Build personalised email
        _, subject_tmpl, body_msg = DRIP_SEQUENCE[step_idx]

        lead = lead_lookup.get(email, {"email": email, "name": "", "company": "", "title": ""})
        name_parts = str(lead.get("name", "")).split()
        first_name = name_parts[0] if name_parts else "there"

        try:
            subject = Template(subject_tmpl).render(
                **lead,
                first_name=first_name,
                company=lead.get("company", ""),
            )
        except Exception:
            subject = f"Following up (step {step_idx + 1})"

        # Build HTML body
        safe_body = body_msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        body_html = (
            f'<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;'
            f'font-size:15px;color:#222;max-width:600px;line-height:1.6;">'
            f'<p>Hi {first_name},</p>'
            f'<p>{safe_body}</p>'
            f'<p>Best regards,<br><strong>{sender_name or "The Team"}</strong></p>'
            f'<hr style="border:none;border-top:1px solid #eee;margin:24px 0;">'
            f'<p style="font-size:12px;color:#999;">You received this because your '
            f'contact info is publicly available.</p>'
            f'</body></html>'
        )
        body_html, open_token = add_tracking_pixel(body_html, email)

        log.info("Drip step %d → %s | Subject: %s | dry=%s",
                 step_idx + 1, email, subject, dry_run)

        if dry_run:
            log_email(email_conn, email, subject, f"dry_drip_step_{step_idx + 1}", open_token)
            advance_step(email, db_path)
            sent += 1
            details.append({"email": email, "step": step_idx + 1, "result": "dry_run ✓"})
        else:
            ok = send_email(
                email, subject, body_html,
                from_email=sender_email,
                from_password=sender_password,
                from_name=sender_name,
            )
            if ok:
                log_email(email_conn, email, subject, f"drip_step_{step_idx + 1}", open_token)
                advance_step(email, db_path)
                sent += 1
                details.append({"email": email, "step": step_idx + 1, "result": "sent ✓"})
            else:
                log_email(email_conn, email, subject, f"failed_drip_step_{step_idx + 1}")
                failed += 1
                details.append({"email": email, "step": step_idx + 1, "result": "failed ✗"})

    email_conn.close()
    summary = {"sent": sent, "skipped": skipped, "failed": failed, "details": details}
    log.info("Drip job complete: %s", {k: v for k, v in summary.items() if k != "details"})
    return summary


# ── Scheduler ─────────────────────────────────────────────────────────────────

def start_scheduler(
    hour: int = 9,
    minute: int = 0,
    db_path: str = "data/sent_log.db",
    background: bool = False,
    sender_email: str = None,
    sender_password: str = None,
    sender_name: str = None,
):
    SchedulerClass = BackgroundScheduler if background else BlockingScheduler
    scheduler = SchedulerClass()

    scheduler.add_job(
        func=lambda: run_drip_job(
            db_path=db_path,
            sender_email=sender_email,
            sender_password=sender_password,
            sender_name=sender_name,
        ),
        trigger="cron",
        hour=hour,
        minute=minute,
        id="drip_job",
        name="Daily drip email sequence",
        replace_existing=True,
    )

    log.info("Scheduler started — drip job will run daily at %02d:%02d", hour, minute)

    if background:
        scheduler.start()
        return scheduler
    else:
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            log.info("Scheduler stopped.")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Drip Email Sequence Scheduler")
    parser.add_argument("--now",    action="store_true", help="Run drip job once immediately")
    parser.add_argument("--hour",   type=int, default=9,  help="Hour to run daily (default 9)")
    parser.add_argument("--min",    type=int, default=0,  help="Minute to run daily (default 0)")
    parser.add_argument("--enroll", type=str, default="", help="CSV path to enroll leads from")
    parser.add_argument("--stats",  action="store_true",  help="Print sequence stats and exit")
    args = parser.parse_args()

    if args.stats:
        print("\n=== Drip Sequence Stats ===")
        for k, v in get_sequence_stats().items():
            print(f"  {k:<12}: {v}")

    elif args.enroll:
        from scraper.web_scraper import load_csv
        leads = load_csv(args.enroll)
        n = enroll_leads(leads)
        print(f"Enrolled {n} leads from {args.enroll}")

    elif args.now:
        print("Running drip job now...")
        summary = run_drip_job()
        print(f"Done: {summary}")

    else:
        start_scheduler(hour=args.hour, minute=args.min)