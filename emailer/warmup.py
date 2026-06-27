"""
emailer/warmup.py
=================
Advanced Feature — Email Account Warmup

Sending bulk email from a fresh Gmail account triggers spam filters.
This module implements a gradual ramp-up schedule so your account
builds sending reputation before hitting full volume.

Warmup schedule (safe defaults):
  Week 1:  20  emails/day
  Week 2:  40  emails/day
  Week 3:  75  emails/day
  Week 4:  150 emails/day
  Week 5:  300 emails/day
  Week 6+: 500 emails/day (Gmail practical daily limit)

Usage:
    from emailer.warmup import WarmupSchedule

    schedule = WarmupSchedule()
    limit = schedule.todays_limit()          # how many to send today
    schedule.log_sends(25)                   # record 25 sends
    report = schedule.report()               # print full status
"""

import sqlite3
import logging
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# Warmup ramp: (days_since_start, daily_limit)
WARMUP_RAMP = [
    (7,   20),
    (14,  40),
    (21,  75),
    (28,  150),
    (35,  300),
    (999, 500),   # Week 6+ — steady state
]


class WarmupSchedule:
    """
    Tracks your warmup phase and enforces daily send limits.
    Stores state in SQLite so it persists across runs.
    """

    def __init__(self, db_path: str = "data/sent_log.db", start_date: date = None):
        """
        Args:
            db_path:    Path to SQLite DB (shared with sender).
            start_date: Override the warmup start date (default: today on first run).
        """
        self.db_path = db_path
        self._init_table(start_date)

    def _conn(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path)

    def _init_table(self, start_date: date = None) -> None:
        conn = self._conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS warmup_config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS warmup_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                log_date  DATE    NOT NULL,
                sends     INTEGER NOT NULL DEFAULT 0,
                logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Set start date if not already set
        existing = conn.execute(
            "SELECT value FROM warmup_config WHERE key='start_date'"
        ).fetchone()
        if not existing:
            sd = (start_date or date.today()).isoformat()
            conn.execute(
                "INSERT INTO warmup_config (key, value) VALUES ('start_date', ?)", (sd,)
            )
        conn.commit()
        conn.close()

    def start_date(self) -> date:
        conn = self._conn()
        row = conn.execute(
            "SELECT value FROM warmup_config WHERE key='start_date'"
        ).fetchone()
        conn.close()
        return date.fromisoformat(row[0])

    def days_since_start(self) -> int:
        return (date.today() - self.start_date()).days + 1

    def todays_limit(self) -> int:
        """Return the max emails allowed to send today based on warmup phase."""
        days = self.days_since_start()
        for threshold, limit in WARMUP_RAMP:
            if days <= threshold:
                return limit
        return WARMUP_RAMP[-1][1]

    def todays_sent(self) -> int:
        """Return how many emails have been logged as sent today."""
        conn = self._conn()
        row = conn.execute(
            "SELECT COALESCE(SUM(sends), 0) FROM warmup_log WHERE log_date = ?",
            (date.today().isoformat(),)
        ).fetchone()
        conn.close()
        return row[0] if row else 0

    def remaining_today(self) -> int:
        """How many more emails can be sent today."""
        return max(0, self.todays_limit() - self.todays_sent())

    def log_sends(self, count: int) -> None:
        """Record that `count` emails were sent today."""
        if count <= 0:
            return
        conn = self._conn()
        conn.execute(
            "INSERT INTO warmup_log (log_date, sends) VALUES (?, ?)",
            (date.today().isoformat(), count)
        )
        conn.commit()
        conn.close()
        log.info("Warmup: logged %d sends. Total today: %d / %d",
                 count, self.todays_sent(), self.todays_limit())

    def current_phase(self) -> str:
        """Return a human-readable phase label."""
        days = self.days_since_start()
        if days <= 7:   return "Week 1 (building reputation)"
        if days <= 14:  return "Week 2 (ramping up)"
        if days <= 21:  return "Week 3 (growing volume)"
        if days <= 28:  return "Week 4 (mid-scale)"
        if days <= 35:  return "Week 5 (near full volume)"
        return "Week 6+ (full send capacity)"

    def report(self) -> dict:
        """Return a full status report as a dict."""
        return {
            "start_date":     self.start_date().isoformat(),
            "days_active":    self.days_since_start(),
            "current_phase":  self.current_phase(),
            "todays_limit":   self.todays_limit(),
            "todays_sent":    self.todays_sent(),
            "remaining_today": self.remaining_today(),
            "next_limit":     self._next_limit(),
            "days_to_next":   self._days_to_next(),
        }

    def _next_limit(self) -> int:
        """The daily limit for the next phase."""
        days = self.days_since_start()
        for i, (threshold, limit) in enumerate(WARMUP_RAMP):
            if days <= threshold and i + 1 < len(WARMUP_RAMP):
                return WARMUP_RAMP[i + 1][1]
        return WARMUP_RAMP[-1][1]

    def _days_to_next(self) -> int:
        """Days until the next phase begins."""
        days = self.days_since_start()
        for threshold, _ in WARMUP_RAMP:
            if days <= threshold:
                return threshold - days + 1
        return 0

    def print_report(self) -> None:
        r = self.report()
        print(f"\n{'='*50}")
        print(f"  📧 Email Warmup Status")
        print(f"{'='*50}")
        print(f"  Started:       {r['start_date']}  ({r['days_active']} days ago)")
        print(f"  Phase:         {r['current_phase']}")
        print(f"  Today's limit: {r['todays_limit']} emails")
        print(f"  Sent today:    {r['todays_sent']}")
        print(f"  Remaining:     {r['remaining_today']}")
        if r['days_to_next']:
            print(f"  Next limit:    {r['next_limit']} (in {r['days_to_next']} days)")
        print(f"{'='*50}\n")