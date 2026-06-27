import os
from dotenv import load_dotenv
load_dotenv()

try:
    import streamlit as st
    _s = st.secrets
except Exception:
    _s = {}

def _get(key, default=""):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)

# ── Email / SMTP ───────────────────────────────────────────
SMTP_HOST      = "smtp.gmail.com"
SMTP_PORT      = 587
EMAIL_ADDRESS  = _get("EMAIL_ADDRESS")
EMAIL_PASSWORD = _get("EMAIL_PASSWORD")
SENDER_NAME    = _get("SENDER_NAME", "Your Name")

# ── Mailgun ────────────────────────────────────────────────
MAILGUN_API_KEY = _get("MAILGUN_API_KEY")
MAILGUN_DOMAIN  = _get("MAILGUN_DOMAIN")

# ── Hunter.io ──────────────────────────────────────────────
HUNTER_API_KEY = _get("HUNTER_API_KEY")

# ── LinkedIn ───────────────────────────────────────────────
LINKEDIN_EMAIL    = _get("LINKEDIN_EMAIL")
LINKEDIN_PASSWORD = _get("LINKEDIN_PASSWORD")

# ── Tracking ───────────────────────────────────────────────
TRACKING_SERVER = _get("TRACKING_SERVER", "http://localhost:5000")

# ── Scraping behaviour ─────────────────────────────────────
REQUEST_DELAY_MIN = 1.5
REQUEST_DELAY_MAX = 3.5

# ── Email behaviour ────────────────────────────────────────
SEND_DELAY_MIN   = 3
SEND_DELAY_MAX   = 8
DAILY_SEND_LIMIT = 50

# ── Paths ──────────────────────────────────────────────────
DATA_DIR    = "data"
LEADS_CSV   = "data/leads.csv"
SENT_LOG_DB = "data/sent_log.db"