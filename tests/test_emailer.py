"""
tests/test_emailer.py
=====================
Tests for sender, tracker, A/B testing modules.
"""

import gc
import os
import sys
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def safe_unlink(path: str, retries: int = 5) -> None:
    """Delete a file, retrying on Windows WinError 32 (file in use)."""
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
        pass  # best-effort on Windows



class TestSender(unittest.TestCase):

    def test_render_email_contains_name(self):
        from emailer.sender import render_email
        lead = {"email": "a@b.com", "name": "Alice Smith", "company": "Acme", "title": "CEO"}
        html = render_email(lead, "Test message")
        self.assertIn("Alice", html)

    def test_render_email_contains_company(self):
        from emailer.sender import render_email
        lead = {"email": "a@b.com", "name": "Bob", "company": "TechCorp", "title": ""}
        html = render_email(lead, "Custom body")
        self.assertIn("TechCorp", html)

    def test_render_email_no_name_uses_fallback(self):
        from emailer.sender import render_email
        lead = {"email": "a@b.com", "name": "", "company": "", "title": ""}
        html = render_email(lead, "Hello")
        self.assertIn("there", html)  # fallback "Hi there"

    def test_init_db_creates_table(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp = f.name
        try:
            from emailer.sender import init_db
            conn = init_db(tmp)
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            names = [t[0] for t in tables]
            self.assertIn("sent_emails", names)
            conn.close()
        finally:
            safe_unlink(tmp)

    def test_daily_send_count_zero_initially(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp = f.name
        try:
            from emailer.sender import init_db, daily_send_count
            conn = init_db(tmp)
            self.assertEqual(daily_send_count(conn), 0)
            conn.close()
        finally:
            safe_unlink(tmp)

    def test_log_email_increments_count(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp = f.name
        try:
            from emailer.sender import init_db, log_email, daily_send_count
            conn = init_db(tmp)
            log_email(conn, "x@y.com", "Hello", "sent")
            log_email(conn, "a@b.com", "Hello", "sent")
            log_email(conn, "f@g.com", "Hello", "failed")
            self.assertEqual(daily_send_count(conn), 2)  # only 'sent' counted
            conn.close()
        finally:
            safe_unlink(tmp)

    def test_bulk_send_dry_run_no_smtp(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp = f.name
        try:
            leads = [
                {"email": "test1@example.com", "name": "Alice", "company": "Co1"},
                {"email": "test2@example.com", "name": "Bob",   "company": "Co2"},
            ]
            with patch("config.TRACKING_SERVER", "http://localhost:5000"):
                from emailer.sender import bulk_send
                summary = bulk_send(leads, "Hello {{ company }}", "Test msg", dry_run=True, db_path=tmp)
            self.assertEqual(summary["sent"], 2)
            self.assertEqual(summary["failed"], 0)
        finally:
            safe_unlink(tmp)

    def test_bulk_send_respects_daily_limit(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp = f.name
        try:
            leads = [{"email": f"u{i}@test.com", "name": f"User{i}", "company": "Co"}
                     for i in range(10)]
            import config as _cfg
            original = _cfg.DAILY_SEND_LIMIT
            _cfg.DAILY_SEND_LIMIT = 3
            try:
                with patch("config.TRACKING_SERVER", "http://localhost:5000"):
                    from emailer.sender import bulk_send
                    summary = bulk_send(leads, "Hi", "Msg", dry_run=True, db_path=tmp)
                self.assertLessEqual(summary["sent"], 3)
            finally:
                _cfg.DAILY_SEND_LIMIT = original
        finally:
            safe_unlink(tmp)

    def test_send_via_mailgun_missing_config(self):
        from emailer.sender import send_via_mailgun
        result = send_via_mailgun("x@y.com", "Subj", "<p>body</p>", api_key="", domain="")
        self.assertFalse(result)


class TestTracker(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db  = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        safe_unlink(self.db)

    def test_record_and_check_open(self):
        from emailer.tracker import record_open, init_tracking_db
        record_open("tok1", "open@test.com", db_path=self.db)
        conn = init_tracking_db(self.db)
        row  = conn.execute("SELECT email FROM email_opens WHERE token='tok1'").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "open@test.com")

    def test_record_open_no_duplicate(self):
        from emailer.tracker import record_open, init_tracking_db
        record_open("tok2", "x@y.com", db_path=self.db)
        record_open("tok2", "x@y.com", db_path=self.db)  # same token — should be ignored
        conn  = init_tracking_db(self.db)
        count = conn.execute("SELECT COUNT(*) FROM email_opens WHERE token='tok2'").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_unsubscribe_flow(self):
        from emailer.tracker import record_unsubscribe, is_unsubscribed
        self.assertFalse(is_unsubscribed("new@test.com", db_path=self.db))
        record_unsubscribe("tok3", "new@test.com", db_path=self.db)
        self.assertTrue(is_unsubscribed("new@test.com", db_path=self.db))

    def test_get_open_stats_zero(self):
        # sent_emails table is created by sender.init_db; tracker.get_open_stats
        # queries it, so we must initialise it first.
        from emailer.sender import init_db
        from emailer.tracker import get_open_stats
        conn = init_db(self.db)
        conn.close()
        stats = get_open_stats(db_path=self.db)
        self.assertEqual(stats["total_sent"],   0)
        self.assertEqual(stats["total_opens"],  0)
        self.assertEqual(stats["open_rate_%"],  0)

    def test_tracking_pixel_format(self):
        from emailer.tracker import add_tracking_pixel
        with patch("config.TRACKING_SERVER", "http://localhost:5000"):
            html, token = add_tracking_pixel("<p>Hello</p>", "a@b.com")
        self.assertIn("<img", html)
        self.assertIn(token, html)
        self.assertEqual(len(token), 32)  # uuid hex = 32 chars


class TestABTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db  = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        safe_unlink(self.db)

    def test_requires_at_least_two_variants(self):
        from emailer.ab_test import ABTest
        with self.assertRaises(ValueError):
            ABTest(variants=["only one"], db_path=self.db)

    def test_split_leads_even(self):
        from emailer.ab_test import ABTest
        ab     = ABTest(["Sub A", "Sub B"], db_path=self.db)
        leads  = [{"email": f"u{i}@t.com"} for i in range(10)]
        groups = ab.split_leads(leads, shuffle=False)
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(groups[0]), 5)
        self.assertEqual(len(groups[1]), 5)

    def test_split_leads_three_variants(self):
        from emailer.ab_test import ABTest
        ab     = ABTest(["A", "B", "C"], db_path=self.db)
        leads  = [{"email": f"u{i}@t.com"} for i in range(30)]
        groups = ab.split_leads(leads, shuffle=False)
        self.assertEqual(len(groups), 3)
        total  = sum(len(g) for g in groups)
        self.assertEqual(total, 30)

    def test_record_and_get_results(self):
        from emailer.ab_test import ABTest
        ab = ABTest(["Sub A", "Sub B"], test_name="test_campaign", db_path=self.db)
        for i in range(5):
            ab.record_send(0, f"a{i}@test.com")
        for i in range(5):
            ab.record_send(1, f"b{i}@test.com")
        ab.record_open("a1@test.com")
        ab.record_open("a2@test.com")
        ab.record_open("b1@test.com")
        results = ab.get_results()
        self.assertEqual(len(results), 2)
        v0 = next(r for r in results if r["variant_idx"] == 0)
        v1 = next(r for r in results if r["variant_idx"] == 1)
        self.assertEqual(v0["opens"], 2)
        self.assertEqual(v1["opens"], 1)

    def test_get_winner_needs_min_sends(self):
        from emailer.ab_test import ABTest
        ab = ABTest(["Sub A", "Sub B"], db_path=self.db)
        # Only 3 sends — below the 10 minimum
        ab.record_send(0, "a@test.com")
        ab.record_send(1, "b@test.com")
        winner = ab.get_winner()
        self.assertIsNone(winner)

    def test_max_four_variants(self):
        from emailer.ab_test import ABTest
        with self.assertRaises(ValueError):
            ABTest(["A","B","C","D","E"], db_path=self.db)


class TestProxy(unittest.TestCase):

    def test_proxy_url_format(self):
        from scraper.proxy import Proxy
        p = Proxy(ip="1.2.3.4", port="8080", protocol="http")
        self.assertEqual(p.url, "http://1.2.3.4:8080")
        self.assertEqual(p.dict, {"http": "http://1.2.3.4:8080", "https": "http://1.2.3.4:8080"})

    def test_pool_empty_returns_none(self):
        from scraper.proxy import ProxyPool
        pool = ProxyPool()
        self.assertIsNone(pool.get())

    def test_pool_manual_proxies(self):
        from scraper.proxy import ProxyPool
        pool = ProxyPool(manual_proxies=["1.2.3.4:8080", "5.6.7.8:3128"])
        self.assertEqual(pool.size(), 2)
        self.assertIsNotNone(pool.get())

    def test_mark_failure_retires_proxy(self):
        from scraper.proxy import ProxyPool
        pool = ProxyPool(manual_proxies=["1.2.3.4:8080"])
        proxy_dict = pool.get()
        for _ in range(3):
            pool.mark_failure(proxy_dict)
        self.assertEqual(pool.size(), 0)

    def test_make_session_returns_session(self):
        from scraper.proxy import ProxyPool
        import requests
        pool    = ProxyPool(manual_proxies=["1.2.3.4:8080"])
        session = pool.make_session()
        self.assertIsInstance(session, requests.Session)


if __name__ == "__main__":
    unittest.main(verbosity=2)