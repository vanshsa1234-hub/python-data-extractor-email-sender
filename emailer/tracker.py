"""
emailer/tracker.py
==================
Phase 4 — Open Tracking Pixel + Unsubscribe Handler

How it works:
  1. Each email gets a unique UUID token embedded as a 1x1 pixel <img>
  2. When recipient opens the email, their client loads the pixel URL
  3. Your tracking server logs the open event
  4. Flask server (run locally + exposed via ngrok) handles the hits

Run the tracking server:
    python -m emailer.tracker
    ngrok http 5000   →  copy the https URL into config.py TRACKING_SERVER
"""

import uuid
import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

# Indian Standard Time is UTC+5:30, fixed offset (no daylight saving in India)
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist_str() -> str:
    """Current time in IST, formatted to match SQLite's default TIMESTAMP style."""
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

# ── Token generation ───────────────────────────────────────────────────────────

def generate_token() -> str:
    return uuid.uuid4().hex


def add_tracking_pixel(html_body: str, email: str) -> tuple[str, str]:
    """
    Append a 1x1 invisible tracking pixel to an HTML email body.

    Returns:
        (modified_html, token)  — store the token in the DB to match opens later.
    """
    from config import TRACKING_SERVER
    token = generate_token()
    pixel = (
        f'<img src="{TRACKING_SERVER}/open?t={token}&e={email}" '
        f'width="1" height="1" alt="" style="display:block;"/>'
    )
    return html_body + pixel, token


def build_unsubscribe_link(email: str) -> tuple[str, str]:
    """
    Generate a unique unsubscribe link for an email address.

    Returns:
        (unsubscribe_url, token)
    """
    from config import TRACKING_SERVER
    token = generate_token()
    url = f"{TRACKING_SERVER}/unsub?t={token}&e={email}"
    return url, token


# ── SQLite tracking store ──────────────────────────────────────────────────────

def init_tracking_db(db_path: str = "data/sent_log.db") -> sqlite3.Connection:
    """Ensure tracking tables exist alongside the sent_emails table."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_opens (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            token       TEXT    NOT NULL UNIQUE,
            email       TEXT    NOT NULL,
            opened_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_agent  TEXT,
            ip_address  TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS unsubscribes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            token         TEXT    NOT NULL UNIQUE,
            email         TEXT    NOT NULL UNIQUE,
            unsubbed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    return conn


def record_open(
    token: str,
    email: str,
    user_agent: str = "",
    ip_address: str = "",
    db_path: str = "data/sent_log.db",
) -> None:
    conn = init_tracking_db(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO email_opens (token, email, user_agent, ip_address, opened_at) VALUES (?,?,?,?,?)",
            (token, email, user_agent, ip_address, now_ist_str()),
        )
        conn.commit()
        log.info("Open recorded for %s (token=%s)", email, token[:8])
    except sqlite3.Error as exc:
        log.error("DB error recording open: %s", exc)
    finally:
        conn.close()


def record_unsubscribe(
    token: str,
    email: str,
    db_path: str = "data/sent_log.db",
) -> None:
    conn = init_tracking_db(db_path)
    email = email.lower().strip()  # normalize to match is_unsubscribed query
    try:
        conn.execute(
            "INSERT OR IGNORE INTO unsubscribes (token, email, unsubbed_at) VALUES (?,?,?)",
            (token, email, now_ist_str()),
        )
        conn.commit()
        log.info("Unsubscribe recorded for %s", email)
    except sqlite3.Error as exc:
        log.error("DB error recording unsubscribe: %s", exc)
    finally:
        conn.close()


def is_unsubscribed(email: str, db_path: str = "data/sent_log.db") -> bool:
    """Check if an email address has unsubscribed."""
    conn = init_tracking_db(db_path)
    row = conn.execute(
        "SELECT 1 FROM unsubscribes WHERE email = ?", (email.lower(),)
    ).fetchone()
    conn.close()
    return row is not None


def manual_unsubscribe(email: str, db_path: str = "data/sent_log.db") -> None:
    """Manually mark an email as unsubscribed (e.g. they asked by phone/reply)."""
    record_unsubscribe(generate_token(), email, db_path)


def remove_unsubscribe(email: str, db_path: str = "data/sent_log.db") -> bool:
    """Remove an email from the unsubscribe list (re-permit sending). Returns True if removed."""
    conn = init_tracking_db(db_path)
    cur = conn.execute(
        "DELETE FROM unsubscribes WHERE email = ?", (email.lower().strip(),)
    )
    conn.commit()
    removed = cur.rowcount > 0
    conn.close()
    return removed


def get_unsubscribes(db_path: str = "data/sent_log.db") -> list[dict]:
    """Return all unsubscribed emails, most recent first."""
    conn = init_tracking_db(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM unsubscribes ORDER BY unsubbed_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_sent_record(record_id: int, db_path: str = "data/sent_log.db") -> bool:
    """Delete a single row from sent_emails by its id. Returns True if deleted."""
    conn = init_tracking_db(db_path)
    cur = conn.execute("DELETE FROM sent_emails WHERE id = ?", (record_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def clear_all_sent_records(db_path: str = "data/sent_log.db") -> int:
    """Delete ALL rows from sent_emails. Returns number of rows deleted. Use with caution."""
    conn = init_tracking_db(db_path)
    cur = conn.execute("DELETE FROM sent_emails")
    conn.commit()
    count = cur.rowcount
    conn.close()
    return count


def get_open_stats(db_path: str = "data/sent_log.db") -> dict:
    """Return basic open-rate statistics."""
    conn = init_tracking_db(db_path)

    # Ensure sent_emails table exists (normally created by sender.init_db)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent_emails (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            email     TEXT NOT NULL,
            subject   TEXT,
            status    TEXT NOT NULL,
            open_token TEXT,
            sent_date DATE,
            sent_at   TIMESTAMP
        )
    """)
    conn.commit()

    total_sent = conn.execute(
        "SELECT COUNT(*) FROM sent_emails WHERE status IN ('sent', 'dry_run')"
    ).fetchone()[0]

    total_opens = conn.execute(
        "SELECT COUNT(*) FROM email_opens"
    ).fetchone()[0]

    unique_opens = conn.execute(
        "SELECT COUNT(DISTINCT email) FROM email_opens"
    ).fetchone()[0]

    total_unsubs = conn.execute(
        "SELECT COUNT(*) FROM unsubscribes"
    ).fetchone()[0]

    conn.close()

    open_rate = round(unique_opens / total_sent * 100, 1) if total_sent else 0
    return {
        "total_sent":   total_sent,
        "total_opens":  total_opens,
        "unique_opens": unique_opens,
        "open_rate_%":  open_rate,
        "unsubscribes": total_unsubs,
    }


# ── Flask tracking server ──────────────────────────────────────────────────────

def run_tracking_server(host: str = "0.0.0.0", port: int = 5000) -> None:
    """
    Lightweight Flask server that handles:
      GET /open?t=TOKEN&e=EMAIL   — pixel loads, records open
      GET /unsub?t=TOKEN&e=EMAIL  — records unsubscribe

    Usage:
        python -m emailer.tracker
        # In a second terminal:
        ngrok http 5000
        # Paste the ngrok HTTPS URL into config.py → TRACKING_SERVER
    """
    try:
        from flask import Flask, request, Response
    except ImportError:
        log.error("Flask not installed. Run: pip install flask")
        return

    app = Flask(__name__)

    # 1x1 transparent GIF
    PIXEL = bytes([
        0x47,0x49,0x46,0x38,0x39,0x61,0x01,0x00,0x01,0x00,0x80,
        0x00,0x00,0xff,0xff,0xff,0x00,0x00,0x00,0x21,0xf9,0x04,
        0x00,0x00,0x00,0x00,0x00,0x2c,0x00,0x00,0x00,0x00,0x01,
        0x00,0x01,0x00,0x00,0x02,0x02,0x44,0x01,0x00,0x3b,
    ])

    @app.route("/open")
    def track_open():
        token = request.args.get("t", "")
        email = request.args.get("e", "")
        ua    = request.headers.get("User-Agent", "")
        ip    = request.remote_addr or ""
        if token and email:
            record_open(token, email, ua, ip)
        return Response(PIXEL, mimetype="image/gif")

    @app.route("/unsub")
    def track_unsub():
        token = request.args.get("t", "")
        email = request.args.get("e", "")
        if token and email:
            record_unsubscribe(token, email)
        return "<h3>You have been unsubscribed successfully.</h3>", 200

    @app.route("/stats")
    def stats():
        import json
        return Response(json.dumps(get_open_stats(), indent=2), mimetype="application/json")

    log.info("Tracking server starting on http://%s:%d", host, port)
    log.info("Endpoints: /open  /unsub  /stats")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_tracking_server()