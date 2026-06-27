"""
tests/test_scraper.py
=====================
Tests for web_scraper.py and cleaner.py — including the
list-field handling added for real-world bulk_scrape output.
"""

import gc
import os
import sys
import time
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def safe_unlink(path: str, retries: int = 5) -> None:
    gc.collect()
    for i in range(retries):
        try:
            os.unlink(path)
            return
        except (PermissionError, FileNotFoundError):
            time.sleep(0.1 * (i + 1))
    try:
        os.unlink(path)
    except (PermissionError, FileNotFoundError):
        pass


# ── web_scraper tests ──────────────────────────────────────────────────────────

class TestFetchHtml(unittest.TestCase):

    def test_returns_html_on_success(self):
        from scraper.web_scraper import fetch_html
        mock_resp = MagicMock()
        mock_resp.text = "<html><body>Hello</body></html>"
        mock_resp.raise_for_status = MagicMock()
        with patch("scraper.web_scraper.requests.get", return_value=mock_resp):
            result = fetch_html("http://fake.com")
        self.assertEqual(result, "<html><body>Hello</body></html>")

    def test_returns_none_on_network_error(self):
        from scraper.web_scraper import fetch_html
        import requests
        with patch("scraper.web_scraper.requests.get", side_effect=requests.RequestException("fail")):
            result = fetch_html("http://fake.com")
        self.assertIsNone(result)


class TestExtractEmails(unittest.TestCase):

    def _mock_get(self, html):
        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def test_finds_plain_emails(self):
        from scraper.web_scraper import extract_emails
        html = "<html><body>Contact: hello@school.com and info@test.org</body></html>"
        with patch("scraper.web_scraper.requests.get", return_value=self._mock_get(html)):
            result = extract_emails("http://fake.com")
        self.assertIn("hello@school.com", result)
        self.assertIn("info@test.org", result)

    def test_finds_mailto_links(self):
        from scraper.web_scraper import extract_emails
        html = '<html><body><a href="mailto:admin@school.com">Email us</a></body></html>'
        with patch("scraper.web_scraper.requests.get", return_value=self._mock_get(html)):
            result = extract_emails("http://fake.com")
        self.assertIn("admin@school.com", result)

    def test_deduplicates_emails(self):
        from scraper.web_scraper import extract_emails
        html = "<html><body>hello@school.com hello@school.com</body></html>"
        with patch("scraper.web_scraper.requests.get", return_value=self._mock_get(html)):
            result = extract_emails("http://fake.com")
        self.assertEqual(result.count("hello@school.com"), 1)

    def test_filters_junk_emails(self):
        from scraper.web_scraper import extract_emails
        html = "<html><body>real@school.com test@example.com noreply@site.com</body></html>"
        with patch("scraper.web_scraper.requests.get", return_value=self._mock_get(html)):
            result = extract_emails("http://fake.com")
        self.assertIn("real@school.com", result)
        self.assertNotIn("test@example.com", result)
        self.assertNotIn("noreply@site.com", result)

    def test_returns_empty_on_fetch_failure(self):
        from scraper.web_scraper import extract_emails
        with patch("scraper.web_scraper.fetch_html", return_value=None):
            result = extract_emails("http://fake.com")
        self.assertEqual(result, [])


class TestExtractContacts(unittest.TestCase):

    def test_no_names_in_output(self):
        """Names removed from scraper output — only email, phone, website"""
        from scraper.web_scraper import extract_contacts
        html = "<html><head><title>DPS School Delhi</title></head><body>info@school.com</body></html>"
        with patch("scraper.web_scraper.fetch_html", return_value=html):
            with patch("scraper.web_scraper.find_contact_page", return_value=None):
                result = extract_contacts("http://fake.com")
        self.assertNotIn("names", result)
        self.assertIn("emails", result)
        self.assertIn("phones", result)

    def test_extracts_phone_numbers(self):
        from scraper.web_scraper import extract_contacts
        html = "<html><body>Call us: +91 9876543210</body></html>"
        with patch("scraper.web_scraper.fetch_html", return_value=html):
            with patch("scraper.web_scraper.find_contact_page", return_value=None):
                result = extract_contacts("http://fake.com")
        self.assertTrue(len(result["phones"]) > 0)

    def test_returns_empty_on_failed_fetch(self):
        from scraper.web_scraper import extract_contacts
        with patch("scraper.web_scraper.fetch_html", return_value=None):
            result = extract_contacts("http://fake.com")
        self.assertEqual(result["emails"], [])
        self.assertEqual(result["phones"], [])

    def test_follows_contact_page_fallback(self):
        from scraper.web_scraper import extract_contacts
        homepage_html = "<html><body>No emails here</body></html>"
        contact_html  = "<html><body>Email: principal@school.com</body></html>"
        def mock_fetch(url, **kwargs):
            if "contact" in url:
                return contact_html
            return homepage_html
        with patch("scraper.web_scraper.fetch_html", side_effect=mock_fetch):
            with patch("scraper.web_scraper.find_contact_page", return_value="http://fake.com/contact"):
                result = extract_contacts("http://fake.com")
        self.assertIn("principal@school.com", result["emails"])


class TestBulkScrape(unittest.TestCase):

    def test_handles_all_failures_gracefully(self):
        from scraper.web_scraper import bulk_scrape
        with patch("scraper.web_scraper.extract_contacts", side_effect=Exception("fail")):
            results = bulk_scrape(["http://a.com", "http://b.com"])
        self.assertEqual(results, [])

    def test_static_mode_returns_contacts(self):
        from scraper.web_scraper import bulk_scrape
        fake_data = {"url": "http://a.com", "names": ["School A"],
                     "emails": ["a@school.com"], "phones": ["+911234567890"]}
        with patch("scraper.web_scraper.extract_contacts", return_value=fake_data):
            with patch("scraper.web_scraper.time.sleep"):
                results = bulk_scrape(["http://a.com"], dynamic=False)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["emails"], ["a@school.com"])

    def test_dynamic_falls_back_to_static(self):
        from scraper.web_scraper import bulk_scrape
        fake_data = {"url": "http://a.com", "names": ["School A"],
                     "emails": ["a@school.com"], "phones": []}
        with patch("scraper.web_scraper.scrape_dynamic", return_value=None):
            with patch("scraper.web_scraper.extract_contacts", return_value=fake_data):
                with patch("scraper.web_scraper.time.sleep"):
                    results = bulk_scrape(["http://a.com"], dynamic=True)
        self.assertEqual(results[0]["emails"], ["a@school.com"])


class TestSaveLoadCsv(unittest.TestCase):

    def test_save_and_reload(self):
        from scraper.web_scraper import save_csv, load_csv
        data = [
            {"email": "a@b.com", "name": "Alice", "phone": ""},
            {"email": "c@d.com", "name": "Bob",   "phone": ""},
        ]
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            tmp = f.name
        try:
            save_csv(data, tmp)
            loaded = load_csv(tmp)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0]["email"], "a@b.com")
        finally:
            safe_unlink(tmp)

    def test_save_empty_returns_empty_string(self):
        from scraper.web_scraper import save_csv
        result = save_csv([])
        self.assertEqual(result, "")

    def test_load_missing_file_returns_empty(self):
        from scraper.web_scraper import load_csv
        result = load_csv("nonexistent_file_xyz.csv")
        self.assertEqual(result, [])

    def test_list_fields_joined_with_pipe(self):
        from scraper.web_scraper import save_csv, load_csv
        data = [{"url": "http://a.com", "emails": ["a@b.com", "c@d.com"], "names": ["School A"], "phones": []}]
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            tmp = f.name
        try:
            save_csv(data, tmp)
            loaded = load_csv(tmp)
            self.assertIn("|", loaded[0]["emails"])  # joined with |
        finally:
            safe_unlink(tmp)


# ── cleaner tests ──────────────────────────────────────────────────────────────

class TestCleaner(unittest.TestCase):

    def test_valid_emails(self):
        from scraper.cleaner import is_valid_email
        self.assertTrue(is_valid_email("user@school.com"))
        self.assertFalse(is_valid_email("notanemail"))
        self.assertFalse(is_valid_email("bad@mailinator.com"))
        self.assertFalse(is_valid_email(""))

    def test_clean_name_titlecase(self):
        from scraper.cleaner import clean_name
        self.assertEqual(clean_name("  john   doe  "), "John Doe")
        self.assertEqual(clean_name("DPS SCHOOL"), "Dps School")

    def test_clean_phone_strips_non_digits(self):
        from scraper.cleaner import clean_phone
        self.assertEqual(clean_phone("+91 98765-43210"), "+919876543210")
        self.assertEqual(clean_phone("(011) 2345-6789"), "01123456789"  )  # 10 digits

    def test_deduplicate_by_email(self):
        from scraper.cleaner import deduplicate
        leads = [
            {"email": "a@b.com", "name": "Alice"},
            {"email": "a@b.com", "name": "Alice2"},
            {"email": "c@d.com", "name": "Carol"},
        ]
        result = deduplicate(leads)
        self.assertEqual(len(result), 2)

    def test_clean_leads_flat_format(self):
        """Standard flat dict format {email, phone}"""
        from scraper.cleaner import clean_leads
        leads = [
            {"email": "good@school.com", "phone": "+911234567890"},
            {"email": "notvalid"},
            {"email": ""},
        ]
        result = clean_leads(leads)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["email"], "good@school.com")
        self.assertIn("phone", result[0])
        self.assertNotIn("name", result[0])

    def test_clean_leads_bulk_scrape_format(self):
        """bulk_scrape returns {url, emails:[], phones:[]} — no names"""
        from scraper.cleaner import clean_leads
        leads = [
            {
                "url":    "https://school.com",
                "emails": ["principal@school.com", "info@school.com"],
                "phones": ["+91 9876543210"],
            }
        ]
        result = clean_leads(leads)
        emails_found = [r["email"] for r in result]
        self.assertIn("principal@school.com", emails_found)
        self.assertIn("info@school.com", emails_found)
        self.assertNotIn("name", result[0])
        self.assertEqual(result[0]["website"], "https://school.com")

    def test_clean_leads_pipe_separated_strings(self):
        """CSV reload produces pipe-separated strings instead of lists"""
        from scraper.cleaner import clean_leads
        leads = [
            {
                "emails":  "principal@school.com|info@school.com",
                "names":   "DPS School Delhi|Delhi Public School",
                "phones":  "+91 9876543210",
                "website": "https://school.com",
            }
        ]
        result = clean_leads(leads)
        emails_found = [r["email"] for r in result]
        self.assertIn("principal@school.com", emails_found)

    def test_clean_leads_drops_junk(self):
        from scraper.cleaner import clean_leads
        leads = [
            {"email": "real@school.com"},
            {"email": "bad@mailinator.com"},
            {"email": ""},
            {"email": "notvalid"},
        ]
        result = clean_leads(leads)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["email"], "real@school.com")

    def test_clean_leads_expands_multiple_emails(self):
        """Each email in the list becomes its own lead row"""
        from scraper.cleaner import clean_leads
        leads = [{
            "url":    "https://school.com",
            "emails": ["a@school.com", "b@school.com", "c@school.com"],
            "names":  ["School Name"],
            "phones": [],
        }]
        result = clean_leads(leads)
        self.assertEqual(len(result), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)