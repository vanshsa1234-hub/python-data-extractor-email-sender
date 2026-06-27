"""
tests/test_verifier.py
======================
Tests for email_verifier.py — all mocked, no real network calls.
"""

import sys, os, unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestSyntaxCheck(unittest.TestCase):

    def test_valid_email(self):
        from scraper.email_verifier import check_syntax
        ok, reason = check_syntax("principal@school.com")
        self.assertTrue(ok)

    def test_valid_edu_in(self):
        from scraper.email_verifier import check_syntax
        ok, _ = check_syntax("admin@dps.edu.in")
        self.assertTrue(ok)

    def test_invalid_no_at(self):
        from scraper.email_verifier import check_syntax
        ok, reason = check_syntax("notanemail")
        self.assertFalse(ok)

    def test_invalid_disposable(self):
        from scraper.email_verifier import check_syntax
        ok, reason = check_syntax("test@mailinator.com")
        self.assertFalse(ok)
        self.assertIn("Disposable", reason)

    def test_empty_string(self):
        from scraper.email_verifier import check_syntax
        ok, _ = check_syntax("")
        self.assertFalse(ok)

    def test_invalid_tld(self):
        from scraper.email_verifier import check_syntax
        ok, _ = check_syntax("user@domain.x")
        self.assertFalse(ok)


class TestDnsCheck(unittest.TestCase):

    def test_valid_domain_with_mx(self):
        from scraper.email_verifier import check_dns
        mock_record = MagicMock()
        mock_record.preference = 10
        mock_record.exchange = "mail.school.com."
        with patch("scraper.email_verifier._dns_available", True), \
             patch("scraper.email_verifier.dns.resolver.resolve", return_value=[mock_record]):
            ok, reason, mx = check_dns("principal@school.com")
        self.assertTrue(ok)
        self.assertEqual(mx[0], "mail.school.com")

    def test_no_mx_falls_back_to_a_record(self):
        from scraper.email_verifier import check_dns
        import socket
        with patch("scraper.email_verifier._dns_available", True), \
             patch("scraper.email_verifier.dns.resolver.resolve", side_effect=Exception("no MX")), \
             patch("scraper.email_verifier.socket.gethostbyname", return_value="1.2.3.4"):
            ok, reason, mx = check_dns("user@school.com")
        self.assertTrue(ok)

    def test_no_records_returns_false(self):
        from scraper.email_verifier import check_dns
        import socket
        with patch("scraper.email_verifier._dns_available", True), \
             patch("scraper.email_verifier.dns.resolver.resolve", side_effect=Exception("no MX")), \
             patch("scraper.email_verifier.socket.gethostbyname", side_effect=socket.gaierror):
            ok, reason, mx = check_dns("user@nonexistent.xyz")
        self.assertFalse(ok)
        self.assertEqual(mx, [])

    def test_dns_unavailable_returns_empty(self):
        from scraper.email_verifier import check_dns
        with patch("scraper.email_verifier._dns_available", False):
            ok, reason, mx = check_dns("user@school.com")
        # Falls back to socket check — patch that too
        import socket
        with patch("scraper.email_verifier._dns_available", False), \
             patch("scraper.email_verifier.socket.gethostbyname", return_value="1.2.3.4"):
            ok, reason, mx = check_dns("user@school.com")
        self.assertTrue(ok)


class TestSmtpCheck(unittest.TestCase):

    def _make_smtp(self, rcpt_code, rcpt_msg=b"OK"):
        smtp = MagicMock()
        smtp.__enter__ = MagicMock(return_value=smtp)
        smtp.__exit__ = MagicMock(return_value=False)
        smtp.rcpt.return_value = (rcpt_code, rcpt_msg)
        return smtp

    def test_valid_address(self):
        from scraper.email_verifier import check_smtp
        smtp_mock = self._make_smtp(250, b"OK")
        with patch("scraper.email_verifier.smtplib.SMTP", return_value=smtp_mock), \
             patch("scraper.email_verifier._is_catch_all", return_value=False):
            status, reason = check_smtp("a@school.com", "mail.school.com")
        self.assertEqual(status, "valid")

    def test_catch_all_domain(self):
        from scraper.email_verifier import check_smtp
        smtp_mock = self._make_smtp(250, b"OK")
        with patch("scraper.email_verifier.smtplib.SMTP", return_value=smtp_mock), \
             patch("scraper.email_verifier._is_catch_all", return_value=True):
            status, reason = check_smtp("a@school.com", "mail.school.com")
        self.assertEqual(status, "catch_all")

    def test_invalid_address(self):
        from scraper.email_verifier import check_smtp
        smtp_mock = self._make_smtp(550, b"User does not exist")
        with patch("scraper.email_verifier.smtplib.SMTP", return_value=smtp_mock):
            status, reason = check_smtp("bad@school.com", "mail.school.com")
        self.assertEqual(status, "invalid")

    def test_timeout(self):
        import socket
        from scraper.email_verifier import check_smtp
        with patch("scraper.email_verifier.smtplib.SMTP", side_effect=socket.timeout):
            status, reason = check_smtp("a@school.com", "mail.school.com")
        self.assertEqual(status, "timeout")

    def test_connection_refused(self):
        from scraper.email_verifier import check_smtp
        with patch("scraper.email_verifier.smtplib.SMTP", side_effect=ConnectionRefusedError):
            status, reason = check_smtp("a@school.com", "mail.school.com")
        self.assertEqual(status, "risky")


class TestVerifyEmail(unittest.TestCase):

    def test_invalid_syntax_returns_immediately(self):
        from scraper.email_verifier import verify_email
        result = verify_email("notanemail")
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["mx"], "")

    def test_disposable_domain_invalid(self):
        from scraper.email_verifier import verify_email
        result = verify_email("test@mailinator.com")
        self.assertEqual(result["status"], "invalid")

    def test_full_valid_flow(self):
        from scraper.email_verifier import verify_email
        mock_mx = MagicMock()
        mock_mx.preference = 10
        mock_mx.exchange = "mail.school.com."
        smtp_mock = MagicMock()
        smtp_mock.__enter__ = MagicMock(return_value=smtp_mock)
        smtp_mock.__exit__ = MagicMock(return_value=False)
        smtp_mock.rcpt.return_value = (250, b"OK")
        with patch("scraper.email_verifier._dns_available", True), \
             patch("scraper.email_verifier.dns.resolver.resolve", return_value=[mock_mx]), \
             patch("scraper.email_verifier.smtplib.SMTP", return_value=smtp_mock), \
             patch("scraper.email_verifier._is_catch_all", return_value=False):
            result = verify_email("principal@school.com")
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["mx"], "mail.school.com")
        self.assertEqual(result["email"], "principal@school.com")

    def test_smtp_skipped_returns_risky(self):
        from scraper.email_verifier import verify_email
        mock_mx = MagicMock()
        mock_mx.preference = 10
        mock_mx.exchange = "mail.school.com."
        with patch("scraper.email_verifier._dns_available", True), \
             patch("scraper.email_verifier.dns.resolver.resolve", return_value=[mock_mx]):
            result = verify_email("a@school.com", smtp_check=False)
        self.assertEqual(result["status"], "risky")


class TestVerifyBulk(unittest.TestCase):

    def test_bulk_returns_all_results(self):
        from scraper.email_verifier import verify_bulk
        emails = ["a@school.com", "notvalid", "b@college.edu"]
        with patch("scraper.email_verifier.verify_email", side_effect=[
            {"email": "a@school.com",  "status": "valid",   "reason": "OK", "mx": "mx.school.com",  "checks": {}},
            {"email": "notvalid",      "status": "invalid", "reason": "Bad format", "mx": "", "checks": {}},
            {"email": "b@college.edu", "status": "valid",   "reason": "OK", "mx": "mx.college.edu", "checks": {}},
        ]):
            results = verify_bulk(emails, smtp_check=False, delay=0)
        self.assertEqual(len(results), 3)

    def test_filter_verified_removes_invalid(self):
        from scraper.email_verifier import filter_verified
        results = [
            {"email": "a@b.com", "status": "valid"},
            {"email": "b@b.com", "status": "invalid"},
            {"email": "c@b.com", "status": "risky"},
            {"email": "d@b.com", "status": "timeout"},
            {"email": "e@b.com", "status": "catch_all"},
        ]
        kept = filter_verified(results)
        statuses = [r["status"] for r in kept]
        self.assertIn("valid",     statuses)
        self.assertIn("risky",     statuses)
        self.assertIn("catch_all", statuses)
        self.assertNotIn("invalid", statuses)
        self.assertNotIn("timeout", statuses)

    def test_filter_verified_custom_statuses(self):
        from scraper.email_verifier import filter_verified
        results = [
            {"email": "a@b.com", "status": "valid"},
            {"email": "b@b.com", "status": "risky"},
        ]
        kept = filter_verified(results, include_statuses=["valid"])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["status"], "valid")


if __name__ == "__main__":
    unittest.main(verbosity=2)