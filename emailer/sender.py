"""
emailer/sender.py
=================
Phase 2–3 — Bulk Email Sender

Phases:
  • Phase 1: Basic SMTP sender (Gmail App Password)
  • Phase 2: Jinja2 personalised HTML templates
  • Phase 3: Bulk send with rate limiting, SQLite logging, daily cap
  • Phase 4: Mailgun API alternative (100 emails/day free)
"""

import smtplib
import sqlite3
import time
import random
import logging
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from datetime import date

from jinja2 import Template

from config import (
    SMTP_HOST, SMTP_PORT, EMAIL_ADDRESS, EMAIL_PASSWORD, SENDER_NAME,
    MAILGUN_API_KEY, MAILGUN_DOMAIN,
    TRACKING_SERVER,
    SEND_DELAY_MIN, SEND_DELAY_MAX, SENT_LOG_DB,
)

log = logging.getLogger(__name__)

# ── Phase 1: Core SMTP send ────────────────────────────────────────────────────

def send_email(
    to: str,
    subject: str,
    body_html: str,
    reply_to: str = "",
) -> bool:
    """
    Send a single HTML email via SMTP (Gmail).

    Prerequisites:
        1. Enable 2-Factor Auth on your Gmail account.
        2. Generate an App Password at myaccount.google.com/apppasswords
        3. Set EMAIL_ADDRESS and EMAIL_PASSWORD in config.py

    Returns True on success, False on failure.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{SENDER_NAME} <{EMAIL_ADDRESS}>"
    msg["To"]      = to
    if reply_to:
        msg["Reply-To"] = reply_to

    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)
        return True
    except smtplib.SMTPException as exc:
        log.error("SMTP error sending to %s: %s", to, exc)
        return False


# ── Phase 2: Jinja2 personalised templates ────────────────────────────────────

DEFAULT_TEMPLATE = """
<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; font-size: 15px; color: #222; max-width: 600px;">
  <p>Hi {{ first_name }},</p>

  {% if company %}
  <p>I noticed you work at <strong>{{ company }}</strong>
  {% if title %}as <em>{{ title }}</em>{% endif %} — I wanted to reach out.</p>
  {% endif %}

  <p>{{ custom_message }}</p>

  <p>Would you be open to a quick chat?</p>

  <p>Best regards,<br>
  <strong>{{ sender_name }}</strong></p>

  <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;">
  <p style="font-size: 12px; color: #999;">
    You received this email because your contact info is publicly available.
    <a href="{{ unsubscribe_link }}">Unsubscribe</a>
  </p>
</body>
</html>
"""


def render_email(
    lead: dict,
    custom_message: str,
    template_str: str = DEFAULT_TEMPLATE,
) -> str:
    """
    Render a Jinja2 HTML email template with lead data.

    Lead dict should have: email, name, company (opt), title (opt)
    """
    name_parts = lead.get("name", "Friend").split()
    first_name = name_parts[0] if name_parts else "there"

    token = uuid.uuid4().hex
    unsubscribe_link = f"{TRACKING_SERVER}/unsub?t={token}&e={lead.get('email','')}"

    # Build context explicitly to avoid duplicate-keyword errors
    ctx = {
        "first_name":       first_name,
        "email":            lead.get("email", ""),
        "name":             lead.get("name", ""),
        "company":          lead.get("company", ""),
        "title":            lead.get("title", ""),
        "custom_message":   custom_message,
        "sender_name":      SENDER_NAME,
        "unsubscribe_link": unsubscribe_link,
    }
    # Merge any extra lead fields that aren't already set
    for k, v in lead.items():
        ctx.setdefault(k, v)
    return Template(template_str).render(**ctx)


# ── Phase 3: SQLite logging ────────────────────────────────────────────────────

def init_db(db_path: str = SENT_LOG_DB) -> sqlite3.Connection:
    """Create the SQLite database and sent_emails table if they don't exist."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent_emails (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT    NOT NULL,
            subject     TEXT,
            status      TEXT    NOT NULL,
            open_token  TEXT,
            sent_date   DATE    DEFAULT (date('now')),
            sent_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def daily_send_count(conn: sqlite3.Connection) -> int:
    """Return how many emails have been sent today."""
    row = conn.execute(
        "SELECT COUNT(*) FROM sent_emails WHERE sent_date = date('now') AND status IN ('sent', 'dry_run')"
    ).fetchone()
    return row[0] if row else 0


def log_email(
    conn: sqlite3.Connection,
    email: str,
    subject: str,
    status: str,
    open_token: str = "",
) -> None:
    conn.execute(
        "INSERT INTO sent_emails (email, subject, status, open_token) VALUES (?, ?, ?, ?)",
        (email, subject, status, open_token),
    )
    conn.commit()


# ── Phase 3: Bulk sender ──────────────────────────────────────────────────────

def bulk_send(
    leads: list[dict],
    subject_template: str,
    custom_message: str,
    email_template: str = DEFAULT_TEMPLATE,
    dry_run: bool = False,
    db_path: str = None,
) -> dict:
    """
    Send personalised emails to a list of leads with rate limiting and logging.

    Args:
        leads:            List of lead dicts (must have 'email' field).
        subject_template: Subject line (may contain {{ company }} etc.)
        custom_message:   The main body paragraph.
        email_template:   Jinja2 HTML template string.
        dry_run:          If True, render but do NOT send. Useful for testing.
        db_path:          Override SQLite path (tests use this).

    Returns:
        Summary dict: {sent, failed, skipped}
    """
    import config as _cfg
    _db = db_path or _cfg.SENT_LOG_DB
    conn = init_db(_db)
    sent = failed = skipped = 0

    for idx, lead in enumerate(leads, 1):
        email = lead.get("email", "").strip()
        if not email:
            log.warning("Lead %d has no email — skipping", idx)
            skipped += 1
            continue

        # Daily cap check (read fresh from config so runtime patches work)
        from config import DAILY_SEND_LIMIT as _LIMIT
        if daily_send_count(conn) >= _LIMIT:
            log.warning("Daily send limit (%d) reached — stopping", _LIMIT)
            skipped += len(leads) - idx + 1
            break

        # Check if already sent to this address today
        already = conn.execute(
            "SELECT 1 FROM sent_emails WHERE email=? AND sent_date=date('now') AND status='sent'",
            (email,)
        ).fetchone()
        if already:
            log.info("Already sent to %s today — skipping", email)
            skipped += 1
            continue

        # Subject — plain text, no Jinja2 needed
        subject = subject_template.strip()

        # Body — user writes exactly what they want, just wrap in basic HTML
        # Convert newlines to <br> so formatting is preserved in email clients
        safe_body = custom_message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe_body_html = safe_body.replace("\n", "<br>")
        body_html = f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;font-size:15px;color:#222;max-width:600px;line-height:1.6;">
{safe_body_html}
</body></html>"""

        # Add open-tracking pixel
        from emailer.tracker import add_tracking_pixel
        body_html, open_token = add_tracking_pixel(body_html, email)

        log.info("[%d/%d] %s to %s", idx, len(leads), "DRY RUN" if dry_run else "Sending", email)

        if dry_run:
            log_email(conn, email, subject, "dry_run", open_token)
            sent += 1
        else:
            success = send_email(email, subject, body_html)
            if success:
                log_email(conn, email, subject, "sent", open_token)
                sent += 1
            else:
                log_email(conn, email, subject, "failed")
                failed += 1

        # Polite delay between sends
        if idx < len(leads):
            delay = random.uniform(SEND_DELAY_MIN, SEND_DELAY_MAX)
            time.sleep(delay)

    summary = {"sent": sent, "failed": failed, "skipped": skipped}
    log.info("Bulk send complete: %s", summary)

    # ── Update warmup tracker automatically ───────────────────────────────────
    if sent > 0:
        try:
            from emailer.warmup import WarmupSchedule
            WarmupSchedule(db_path=_db).log_sends(sent)
        except Exception as exc:
            log.debug("Warmup log skipped: %s", exc)

    return summary


# ── Phase 4: Mailgun alternative ──────────────────────────────────────────────

def send_via_mailgun(
    to: str,
    subject: str,
    html: str,
    api_key: str = MAILGUN_API_KEY,
    domain: str = MAILGUN_DOMAIN,
) -> bool:
    """
    Send via Mailgun REST API (100 free emails/day).
    Requires MAILGUN_API_KEY and MAILGUN_DOMAIN in config.py.
    """
    import requests as req
    if not api_key or not domain:
        log.error("Mailgun not configured. Set MAILGUN_API_KEY and MAILGUN_DOMAIN in config.py")
        return False
    try:
        resp = req.post(
            f"https://api.mailgun.net/v3/{domain}/messages",
            auth=("api", api_key),
            data={
                "from":    f"{SENDER_NAME} <you@{domain}>",
                "to":      to,
                "subject": subject,
                "html":    html,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        log.error("Mailgun send failed to %s: %s", to, exc)
        return False