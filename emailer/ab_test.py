"""
emailer/ab_test.py
==================
Advanced Feature — A/B Subject Line Testing

How it works:
  1. Define 2-4 subject line variants
  2. Split your lead list evenly between variants
  3. After sending, compare open rates per variant
  4. Pick the winner and use it for remaining leads

Usage:
    from emailer.ab_test import ABTest

    test = ABTest(
        variants=[
            "Quick question about {{ company }}",
            "{{ first_name }}, got a minute?",
            "Opportunity for {{ company }}",
        ]
    )

    # Split leads into groups
    groups = test.split_leads(leads)

    # Send each group with its variant
    for variant, group in zip(test.variants, groups):
        bulk_send(group, subject_template=variant, custom_message="...")

    # After some time, check results
    results = test.get_results()
    winner  = test.get_winner()
    print(f"Winner: {winner}")
"""

import sqlite3
import logging
import random
from typing import Optional
from pathlib import Path

log = logging.getLogger(__name__)


class ABTest:
    """
    Manages an A/B test across multiple email subject line variants.
    Stores send and open data in the existing sent_log.db.
    """

    def __init__(
        self,
        variants: list[str],
        test_name: str = "ab_test",
        db_path: str = "data/sent_log.db",
    ):
        """
        Args:
            variants:  2–4 subject line templates (Jinja2 supported).
            test_name: Identifier stored in DB (no spaces).
            db_path:   Path to SQLite DB.
        """
        if len(variants) < 2:
            raise ValueError("A/B test requires at least 2 variants")
        if len(variants) > 4:
            raise ValueError("Maximum 4 variants supported")

        self.variants  = variants
        self.test_name = test_name.replace(" ", "_")
        self.db_path   = db_path
        self._init_ab_table()

    # ── DB setup ───────────────────────────────────────────────────────────────

    def _init_ab_table(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ab_results (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                test_name   TEXT NOT NULL,
                variant_idx INTEGER NOT NULL,
                variant_txt TEXT NOT NULL,
                email       TEXT NOT NULL,
                sent        INTEGER DEFAULT 0,
                opened      INTEGER DEFAULT 0,
                sent_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    # ── Lead splitting ─────────────────────────────────────────────────────────

    def split_leads(
        self,
        leads: list[dict],
        shuffle: bool = True,
    ) -> list[list[dict]]:
        """
        Evenly split leads into N groups (one per variant).
        Returns a list of lists in the same order as self.variants.

        Example:
            100 leads, 2 variants → [[50 leads], [50 leads]]
        """
        if shuffle:
            leads = leads.copy()
            random.shuffle(leads)

        n      = len(self.variants)
        size   = len(leads) // n
        groups = []

        for i in range(n):
            start = i * size
            # Last group gets any remainder
            end   = start + size if i < n - 1 else len(leads)
            groups.append(leads[start:end])

        log.info(
            "A/B split: %d leads into %d groups of ~%d each (test=%s)",
            len(leads), n, size, self.test_name,
        )
        return groups

    # ── Recording ─────────────────────────────────────────────────────────────

    def record_send(self, variant_idx: int, email: str) -> None:
        """Call this after successfully sending to a lead."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT OR IGNORE INTO ab_results
               (test_name, variant_idx, variant_txt, email, sent)
               VALUES (?, ?, ?, ?, 1)""",
            (self.test_name, variant_idx, self.variants[variant_idx], email),
        )
        conn.commit()
        conn.close()

    def record_open(self, email: str) -> None:
        """
        Call this when an open pixel fires.
        Matches the email to its variant automatically.
        """
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """UPDATE ab_results SET opened=1
               WHERE test_name=? AND email=? AND sent=1""",
            (self.test_name, email),
        )
        conn.commit()
        conn.close()

    # ── Results ────────────────────────────────────────────────────────────────

    def get_results(self) -> list[dict]:
        """
        Return per-variant stats.

        Returns list of dicts:
            [{variant_idx, subject, sent, opens, open_rate_%}, ...]
        """
        conn   = sqlite3.connect(self.db_path)
        rows   = conn.execute(
            """SELECT variant_idx, variant_txt,
                      COUNT(*) as sent,
                      SUM(opened) as opens
               FROM ab_results
               WHERE test_name=?
               GROUP BY variant_idx
               ORDER BY variant_idx""",
            (self.test_name,),
        ).fetchall()
        conn.close()

        results = []
        for row in rows:
            idx, subject, sent, opens = row
            opens     = opens or 0
            open_rate = round(opens / sent * 100, 1) if sent else 0
            results.append({
                "variant_idx":  idx,
                "subject":      subject,
                "sent":         sent,
                "opens":        opens,
                "open_rate_%":  open_rate,
            })
        return results

    def get_winner(self) -> Optional[str]:
        """
        Return the subject template with the highest open rate.
        Returns None if no data is available yet.
        """
        results = self.get_results()
        if not results:
            return None
        # Need minimum 10 sends per variant for statistical meaning
        valid = [r for r in results if r["sent"] >= 10]
        if not valid:
            log.warning("Not enough sends yet to determine a winner (need ≥10 per variant)")
            return None
        winner = max(valid, key=lambda r: r["open_rate_%"])
        log.info(
            "A/B winner: '%s' with %.1f%% open rate",
            winner["subject"], winner["open_rate_%"],
        )
        return winner["subject"]

    def print_results(self) -> None:
        """Pretty-print results to stdout."""
        results = self.get_results()
        if not results:
            print("No A/B test data yet.")
            return

        print(f"\n{'='*60}")
        print(f"  A/B Test Results — {self.test_name}")
        print(f"{'='*60}")
        for r in results:
            bar = "█" * int(r["open_rate_%"] / 2)
            print(
                f"  Variant {r['variant_idx'] + 1}: {r['open_rate_%']:5.1f}%  {bar}"
            )
            print(f"    Subject : {r['subject'][:55]}")
            print(f"    Sent    : {r['sent']}   Opens: {r['opens']}")
            print()

        winner = self.get_winner()
        if winner:
            print(f"  🏆 WINNER: {winner}")
        print(f"{'='*60}\n")


# ── Convenience wrapper ────────────────────────────────────────────────────────

def run_ab_campaign(
    leads: list[dict],
    variants: list[str],
    custom_message: str,
    test_name: str = "campaign_ab",
    dry_run: bool = False,
) -> ABTest:
    """
    High-level helper: split leads, send each group, return ABTest object.

    Example:
        test = run_ab_campaign(
            leads=all_leads,
            variants=["Hi {{ first_name }}!", "Quick question for {{ company }}"],
            custom_message="I wanted to reach out about...",
            dry_run=True,
        )
        test.print_results()
    """
    from emailer.sender import bulk_send

    ab   = ABTest(variants=variants, test_name=test_name)
    groups = ab.split_leads(leads)

    for idx, (variant, group) in enumerate(zip(variants, groups)):
        log.info("Sending variant %d to %d leads: %s", idx + 1, len(group), variant[:50])
        bulk_send(
            leads=group,
            subject_template=variant,
            custom_message=custom_message,
            dry_run=dry_run,
        )
        # Record sends in A/B table
        for lead in group:
            if lead.get("email"):
                ab.record_send(idx, lead["email"])

    return ab