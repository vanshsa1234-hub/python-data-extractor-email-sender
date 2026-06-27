"""
tests/test_admin_auth.py
========================
Tests for admin_auth.py
"""

import gc, os, sys, time, tempfile, unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def safe_unlink(path, retries=5):
    gc.collect()
    for i in range(retries):
        try:
            os.unlink(path)
            return
        except (PermissionError, FileNotFoundError):
            time.sleep(0.1 * (i + 1))


class TestAdminAuth(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db  = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        safe_unlink(self.db)

    def _patch(self):
        return patch("admin_auth.DB_PATH", self.db)

    def test_init_creates_default_admin(self):
        with self._patch():
            from admin_auth import init_admin_db, list_admins
            init_admin_db()
            admins = list_admins()
        self.assertIn("admin", admins)

    def test_verify_default_credentials(self):
        with self._patch():
            from admin_auth import init_admin_db, verify_credentials
            init_admin_db()
            self.assertTrue(verify_credentials("admin", "admin123"))

    def test_wrong_password_fails(self):
        with self._patch():
            from admin_auth import init_admin_db, verify_credentials
            init_admin_db()
            self.assertFalse(verify_credentials("admin", "wrongpass"))

    def test_wrong_username_fails(self):
        with self._patch():
            from admin_auth import init_admin_db, verify_credentials
            init_admin_db()
            self.assertFalse(verify_credentials("hacker", "admin123"))

    def test_change_password(self):
        with self._patch():
            from admin_auth import init_admin_db, verify_credentials, change_password
            init_admin_db()
            result = change_password("admin", "admin123", "newpass456")
            self.assertTrue(result)
            self.assertTrue(verify_credentials("admin",  "newpass456"))
            self.assertFalse(verify_credentials("admin", "admin123"))

    def test_change_password_wrong_old(self):
        with self._patch():
            from admin_auth import init_admin_db, change_password
            init_admin_db()
            result = change_password("admin", "wrongold", "newpass")
            self.assertFalse(result)

    def test_add_new_admin(self):
        with self._patch():
            from admin_auth import init_admin_db, add_admin, verify_credentials
            init_admin_db()
            result = add_admin("vansh", "pass123")
            self.assertTrue(result)
            self.assertTrue(verify_credentials("vansh", "pass123"))

    def test_add_duplicate_admin_fails(self):
        with self._patch():
            from admin_auth import init_admin_db, add_admin
            init_admin_db()
            add_admin("vansh", "pass123")
            result = add_admin("vansh", "otherpass")
            self.assertFalse(result)

    def test_delete_admin(self):
        with self._patch():
            from admin_auth import init_admin_db, add_admin, delete_admin, list_admins
            init_admin_db()
            add_admin("vansh", "pass123")
            result = delete_admin("vansh")
            self.assertTrue(result)
            self.assertNotIn("vansh", list_admins())

    def test_cannot_delete_last_admin(self):
        with self._patch():
            from admin_auth import init_admin_db, delete_admin
            init_admin_db()
            result = delete_admin("admin")
            self.assertFalse(result)

    def test_list_admins(self):
        with self._patch():
            from admin_auth import init_admin_db, add_admin, list_admins
            init_admin_db()
            add_admin("user2", "pass")
            admins = list_admins()
            self.assertIn("admin",  admins)
            self.assertIn("user2",  admins)
            self.assertEqual(len(admins), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)