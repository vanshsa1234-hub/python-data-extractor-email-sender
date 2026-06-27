"""
tests/test_followup.py
======================
Tests for drip sequence logic.
"""

import gc
import os
import sys
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def safe_unlink(path: str, retries: int = 5) -> None:
    gc.collect()
    for i in range(retries):
        try:
            os.unlink(path)
            return
        except PermissionError:
            time.sleep(0.1 * (i + 1))
    try:
        os.unlink(path)
    except PermissionError:
        pass



class TestFollowup(unittest.TestCase):

    def setUp(self):
        # Use a temp DB for every test
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db  = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        safe_unlink(self.db)

    def test_enroll_leads(self):
        from emailer.followup import enroll_leads, init_sequence_db
        leads = [{"email": "a@b.com"}, {"email": "c@d.com"}, {"email": ""}]
        n = enroll_leads(leads, db_path=self.db)
        self.assertEqual(n, 2)

    def test_enroll_no_duplicates(self):
        from emailer.followup import enroll_leads
        leads = [{"email": "a@b.com"}]
        n1 = enroll_leads(leads, db_path=self.db)
        n2 = enroll_leads(leads, db_path=self.db)
        self.assertEqual(n1, 1)
        self.assertEqual(n2, 0)  # already enrolled

    def test_get_due_leads(self):
        from emailer.followup import enroll_leads, get_due_leads
        leads = [{"email": "due@test.com"}]
        enroll_leads(leads, db_path=self.db)
        # Enrolled today — should be due
        due = get_due_leads(db_path=self.db)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["email"], "due@test.com")

    def test_advance_step(self):
        from emailer.followup import enroll_leads, advance_step, init_sequence_db
        enroll_leads([{"email": "step@test.com"}], db_path=self.db)
        advance_step("step@test.com", db_path=self.db)
        conn = init_sequence_db(self.db)
        row = conn.execute(
            "SELECT current_step FROM drip_sequence WHERE email=?",
            ("step@test.com",)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], 1)

    def test_sequence_completes_after_all_steps(self):
        from emailer.followup import enroll_leads, advance_step, init_sequence_db, DRIP_SEQUENCE
        enroll_leads([{"email": "finish@test.com"}], db_path=self.db)
        for _ in DRIP_SEQUENCE:
            advance_step("finish@test.com", db_path=self.db)
        conn = init_sequence_db(self.db)
        row = conn.execute(
            "SELECT status FROM drip_sequence WHERE email=?",
            ("finish@test.com",)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "completed")

    def test_pause_lead(self):
        from emailer.followup import enroll_leads, pause_lead, init_sequence_db
        enroll_leads([{"email": "pause@test.com"}], db_path=self.db)
        pause_lead("pause@test.com", db_path=self.db)
        conn = init_sequence_db(self.db)
        row = conn.execute(
            "SELECT status FROM drip_sequence WHERE email=?",
            ("pause@test.com",)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "paused")

    def test_get_sequence_stats(self):
        from emailer.followup import enroll_leads, pause_lead, get_sequence_stats
        enroll_leads([{"email": "a@b.com"}, {"email": "c@d.com"}], db_path=self.db)
        pause_lead("a@b.com", db_path=self.db)
        with patch("emailer.followup.init_sequence_db", return_value=sqlite3.connect(self.db)):
            stats = get_sequence_stats(db_path=self.db)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["paused"], 1)
        self.assertEqual(stats["active"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)