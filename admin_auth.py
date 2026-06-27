"""
admin_auth.py
=============
Simple admin authentication for the Streamlit dashboard.
Credentials are stored as a bcrypt hash in data/admin.db — 
the plain-text password is never saved anywhere.

Default credentials (change immediately after first login):
  Username: admin
  Password: admin123

To change password, run:
    python admin_auth.py --change
"""

import sqlite3
import hashlib
import logging
import argparse
from pathlib import Path

log = logging.getLogger(__name__)

DB_PATH = "data/admin.db"


def _conn() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def _hash(password: str) -> str:
    """SHA-256 hash of the password (good enough for local tool)."""
    return hashlib.sha256(password.strip().encode()).hexdigest()


def init_admin_db() -> None:
    """Create admin table and set default credentials if not already set."""
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id          INTEGER PRIMARY KEY,
            username    TEXT    NOT NULL UNIQUE,
            password_hash TEXT  NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # Insert default admin if no users exist
    existing = conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
    if existing == 0:
        conn.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            ("admin", _hash("admin123"))
        )
        conn.commit()
        log.info("Default admin created. Username: admin | Password: admin123")
    conn.close()


def verify_credentials(username: str, password: str) -> bool:
    """Return True if username + password match stored credentials."""
    init_admin_db()
    conn = _conn()
    row = conn.execute(
        "SELECT password_hash FROM admin_users WHERE username = ?",
        (username.strip().lower(),)
    ).fetchone()
    conn.close()
    if not row:
        return False
    return row[0] == _hash(password)


def change_password(username: str, old_password: str, new_password: str) -> bool:
    """Change password for an existing admin user."""
    if not verify_credentials(username, old_password):
        return False
    conn = _conn()
    conn.execute(
        "UPDATE admin_users SET password_hash = ? WHERE username = ?",
        (_hash(new_password), username.strip().lower())
    )
    conn.commit()
    conn.close()
    return True


def add_admin(username: str, password: str) -> bool:
    """Add a new admin user. Returns False if username already exists."""
    init_admin_db()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            (username.strip().lower(), _hash(password))
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def delete_admin(username: str) -> bool:
    """Remove an admin user (cannot remove last admin)."""
    conn = _conn()
    count = conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
    if count <= 1:
        conn.close()
        return False
    conn.execute("DELETE FROM admin_users WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    return True


def list_admins() -> list[str]:
    """Return list of admin usernames."""
    init_admin_db()
    conn = _conn()
    rows = conn.execute("SELECT username FROM admin_users").fetchall()
    conn.close()
    return [r[0] for r in rows]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Admin credential manager")
    parser.add_argument("--change",   action="store_true", help="Change admin password")
    parser.add_argument("--add",      action="store_true", help="Add new admin user")
    parser.add_argument("--list",     action="store_true", help="List all admins")
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()

    init_admin_db()

    if args.list:
        print("Admin users:", list_admins())

    elif args.change:
        old = input("Current password: ")
        new = input("New password: ")
        confirm = input("Confirm new password: ")
        if new != confirm:
            print("Passwords don't match.")
        elif change_password(args.username, old, new):
            print(f"Password changed for '{args.username}'")
        else:
            print("Wrong current password.")

    elif args.add:
        uname = input("New admin username: ")
        pwd   = input("Password: ")
        if add_admin(uname, pwd):
            print(f"Admin '{uname}' created.")
        else:
            print(f"Username '{uname}' already exists.")

    else:
        parser.print_help()