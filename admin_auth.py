"""
admin_auth.py
=============
Multi-user authentication system.

Every user must register before using the tool.
Each user's leads are stored separately — user A cannot see user B's data.

DB schema:
  users table:
    id, username, password_hash, role (admin/user), created_at

  leads table:
    id, user_id, email, phone, website, scraped_at

Roles:
  admin — can see all users, manage accounts
  user  — can only see their own leads
"""

import sqlite3
import hashlib
import logging
import re
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)

DB_PATH = "data/admin.db"


def _conn() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _hash(password: str) -> str:
    return hashlib.sha256(password.strip().encode()).hexdigest()


def init_db() -> None:
    """Create all tables. Safe to call multiple times."""
    conn = _conn()

    # Users table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    NOT NULL UNIQUE,
            password_hash TEXT    NOT NULL,
            role          TEXT    NOT NULL DEFAULT 'user',
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Per-user leads table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_leads (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            email       TEXT    NOT NULL,
            phone       TEXT    DEFAULT '',
            website     TEXT    DEFAULT '',
            scraped_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, email)
        )
    """)

    # Per-user sent emails log
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_sent_emails (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL,
            email     TEXT    NOT NULL,
            subject   TEXT,
            status    TEXT    NOT NULL,
            sent_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    conn.commit()

    # NO default credentials — admin must be created on first run via the app
    conn.commit()
    conn.close()


# ── Registration ───────────────────────────────────────────────────────────────

def register_user(username: str, password: str) -> tuple[bool, str]:
    """
    Register a new user account.
    Returns (success, message).
    """
    username = username.strip().lower()

    if not username or len(username) < 3:
        return False, "Username must be at least 3 characters."
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        return False, "Username can only contain letters, numbers and underscores."
    if len(password) < 6:
        return False, "Password must be at least 6 characters."

    init_db()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
            (username, _hash(password), "user")
        )
        conn.commit()
        conn.close()
        return True, f"Account created! Welcome, {username}."
    except sqlite3.IntegrityError:
        conn.close()
        return False, f"Username '{username}' is already taken."


# ── Login ──────────────────────────────────────────────────────────────────────

def login_user(username: str, password: str) -> tuple[bool, dict]:
    """
    Verify credentials.
    Returns (success, user_dict) where user_dict has id, username, role.
    """
    init_db()
    username = username.strip().lower()
    conn = _conn()
    row = conn.execute(
        "SELECT id, username, role FROM users WHERE username=? AND password_hash=?",
        (username, _hash(password))
    ).fetchone()
    conn.close()

    if row:
        return True, {"id": row["id"], "username": row["username"], "role": row["role"]}
    return False, {}


def verify_credentials(username: str, password: str) -> bool:
    """Legacy compatibility — returns True/False only."""
    success, _ = login_user(username, password)
    return success


# ── User leads (isolated per user) ────────────────────────────────────────────

def save_user_leads(user_id: int, leads: list[dict]) -> int:
    """
    Save scraped leads for a specific user.
    Skips duplicates (same user + email).
    Returns count of newly inserted leads.
    """
    init_db()
    conn = _conn()
    inserted = 0
    for lead in leads:
        email   = lead.get("email", "").strip().lower()
        phone   = str(lead.get("phone", "")).strip()
        website = lead.get("website", lead.get("url", lead.get("source", ""))).strip()
        if not email:
            continue
        try:
            conn.execute(
                "INSERT OR IGNORE INTO user_leads (user_id, email, phone, website) VALUES (?,?,?,?)",
                (user_id, email, phone, website)
            )
            if conn.total_changes > inserted:
                inserted += 1
        except sqlite3.Error:
            pass
    conn.commit()
    conn.close()
    return inserted


def get_user_leads(user_id: int) -> list[dict]:
    """Return all leads belonging to a specific user."""
    init_db()
    conn = _conn()
    rows = conn.execute(
        "SELECT email, phone, website, scraped_at FROM user_leads WHERE user_id=? ORDER BY scraped_at DESC",
        (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_user_lead(user_id: int, email: str) -> bool:
    """Delete a specific lead for a user."""
    init_db()
    conn = _conn()
    conn.execute(
        "DELETE FROM user_leads WHERE user_id=? AND email=?",
        (user_id, email.strip().lower())
    )
    changed = conn.total_changes > 0
    conn.commit()
    conn.close()
    return changed


def clear_user_leads(user_id: int) -> int:
    """Delete all leads for a user. Returns count deleted."""
    init_db()
    conn = _conn()
    conn.execute("DELETE FROM user_leads WHERE user_id=?", (user_id,))
    n = conn.total_changes
    conn.commit()
    conn.close()
    return n


def replace_user_leads(user_id: int, leads: list[dict]) -> int:
    """
    Replace ALL leads for a user with the given list.
    Deletes every existing lead first, then inserts the new ones.
    Use this when you want to overwrite (e.g. after verification removes invalid emails).
    Returns count of rows inserted.
    """
    init_db()
    conn = _conn()
    conn.execute("DELETE FROM user_leads WHERE user_id=?", (user_id,))
    inserted = 0
    for lead in leads:
        email   = lead.get("email", "").strip().lower()
        phone   = str(lead.get("phone", "")).strip()
        website = lead.get("website", lead.get("url", lead.get("source", ""))).strip()
        if not email:
            continue
        try:
            conn.execute(
                "INSERT INTO user_leads (user_id, email, phone, website) VALUES (?,?,?,?)",
                (user_id, email, phone, website)
            )
            inserted += 1
        except sqlite3.Error:
            pass
    conn.commit()
    conn.close()
    return inserted


def delete_emails_for_user(user_id: int, emails: list[str]) -> int:
    """
    Delete specific emails from a user's leads.
    Returns count deleted.
    """
    if not emails:
        return 0
    init_db()
    conn = _conn()
    deleted = 0
    for email in emails:
        conn.execute(
            "DELETE FROM user_leads WHERE user_id=? AND email=?",
            (user_id, email.strip().lower())
        )
        deleted += conn.total_changes
    conn.commit()
    conn.close()
    return deleted


# ── Admin functions ────────────────────────────────────────────────────────────

def list_users() -> list[dict]:
    """Return all users (admin only)."""
    init_db()
    conn = _conn()
    rows = conn.execute(
        "SELECT id, username, role, created_at FROM users ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_user(user_id: int) -> bool:
    """Delete a user and all their leads (admin only)."""
    init_db()
    conn = _conn()
    conn.execute("DELETE FROM user_leads WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    changed = conn.total_changes > 0
    conn.commit()
    conn.close()
    return changed


def change_password(username: str, old_password: str, new_password: str) -> bool:
    """Change password for a user."""
    if not verify_credentials(username, old_password):
        return False
    init_db()
    conn = _conn()
    conn.execute(
        "UPDATE users SET password_hash=? WHERE username=?",
        (_hash(new_password), username.strip().lower())
    )
    conn.commit()
    conn.close()
    return True


def get_user_lead_count(user_id: int) -> int:
    """Return how many leads a user has."""
    init_db()
    conn = _conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM user_leads WHERE user_id=?", (user_id,)
    ).fetchone()[0]
    conn.close()
    return n


def is_admin_user(user_id: int) -> bool:
    """Check if a user has admin role."""
    init_db()
    conn = _conn()
    row = conn.execute(
        "SELECT role FROM users WHERE id=?", (user_id,)
    ).fetchone()
    conn.close()
    return row and row["role"] == "admin"


# Legacy admin helpers (kept for backward compatibility with old admin panel)
def init_admin_db(): init_db()
def list_admins(): return [u["username"] for u in list_users() if u["role"] == "admin"]
def add_admin(username, password): return register_user(username, password)[0]