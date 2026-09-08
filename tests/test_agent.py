#!/usr/bin/env python3
import importlib.util
import pathlib
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("omapass_agent", ROOT / "omapass-agent.py")
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


class TestOmaPassService(unittest.TestCase):
    def setUp(self):
        self.service = agent.OmaPassService()
        self.service.items = [
            {
                "id": "item1",
                "title": "GitHub",
                "category": "LOGIN",
                "username": "itsgg",
                "url": "https://github.com",
                "vault": "Personal",
                "favorite": True,
            },
            {
                "id": "item2",
                "title": "Google Account",
                "category": "LOGIN",
                "username": "me@example.com",
                "url": "https://google.com",
                "vault": "Personal",
                "favorite": False,
            },
            {
                "id": "item3",
                "title": "Bank of America",
                "category": "BANK_ACCOUNT",
                "username": "user123",
                "url": "https://bankofamerica.com",
                "vault": "Financial",
                "favorite": True,
            },
            {
                "id": "item4",
                "title": "Server SSH Key",
                "category": "SECURE_NOTE",
                "username": "",
                "url": "",
                "vault": "Work",
                "favorite": False,
            },
        ]

    def test_search_all(self):
        res = self.service.search_items()
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["items"]), 4)
        self.assertTrue(res["items"][0]["favorite"])

    def test_search_query_prefix(self):
        res = self.service.search_items(query="git")
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["items"]), 1)
        self.assertEqual(res["items"][0]["id"], "item1")

    def test_search_category_filter(self):
        res = self.service.search_items(category="CARDS")
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["items"]), 1)
        self.assertEqual(res["items"][0]["title"], "Bank of America")

        res_notes = self.service.search_items(category="NOTES")
        self.assertEqual(len(res_notes["items"]), 1)
        self.assertEqual(res_notes["items"][0]["title"], "Server SSH Key")

        res_fav = self.service.search_items(category="FAVORITES")
        self.assertEqual(len(res_fav["items"]), 2)

    def test_dispatch_ping(self):
        res = self.service.dispatch({"action": "ping"})
        self.assertTrue(res["ok"])
        self.assertIn("pong", res)

    def test_dispatch_unknown(self):
        res = self.service.dispatch({"action": "nonexistent"})
        self.assertFalse(res["ok"])

    @patch("subprocess.run")
    def test_fetch_field_preserves_whitespace(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "  secret password with spaces  \n"
        mock_run.return_value = mock_proc

        ok, val = self.service.fetch_field("item1", "password")
        self.assertTrue(ok)
        self.assertEqual(val, "  secret password with spaces  ")

    def test_cancel_previous_wipe_cleans_file_isolated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_pid_file = pathlib.Path(tmpdir) / "omapass-wipe.pid"
            test_pid_file.write_text("999999999")
            with patch.object(agent, "wipe_pid_path", return_value=test_pid_file):
                self.assertTrue(test_pid_file.exists())
                agent.cancel_previous_wipe()
                self.assertFalse(test_pid_file.exists())

    @patch("subprocess.Popen")
    def test_copy_to_clipboard_error_handling(self, mock_popen):
        mock_popen.side_effect = FileNotFoundError("wl-copy not found")
        with patch.object(self.service, "fetch_field", return_value=(True, "secret123")):
            res = self.service.copy_to_clipboard("item1", "password")
            self.assertFalse(res["ok"])
            self.assertIn("error", res)
            self.assertIn("Failed to run wl-copy", res["error"])

    @patch("subprocess.run")
    def test_get_status_desktop_integration(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '{"id": "USER123", "name": "Ganesh", "email": "me@example.com"}'
        mock_run.return_value = mock_proc

        with patch.object(self.service, "check_op_installed", return_value=True):
            status = self.service.get_status(force=True)
            self.assertTrue(status["ok"])
            self.assertTrue(status["unlocked"])
            self.assertEqual(status["account"], "me@example.com")
            self.assertEqual(status["name"], "Ganesh")

            # Verify fast cache returns without subprocess call
            mock_run.reset_mock()
            cached_status = self.service.get_status(force=False)
            self.assertTrue(cached_status["unlocked"])
            mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_get_item_parsing_and_caching(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '''{
            "id": "item123",
            "title": "Amazon",
            "category": "LOGIN",
            "vault": {"name": "Personal"},
            "favorite": true,
            "urls": [{"label": "website", "href": "https://amazon.com", "primary": true}],
            "fields": [
                {"id": "username", "type": "STRING", "purpose": "USERNAME", "label": "username", "value": "user@example.com"},
                {"id": "password", "type": "CONCEALED", "purpose": "PASSWORD", "label": "password", "value": "supersecret"},
                {"id": "totp", "type": "OTP", "purpose": "", "label": "one-time password", "value": "otpauth://...", "totp": "123456"},
                {"id": "notesPlain", "type": "STRING", "purpose": "NOTES", "label": "notes", "value": "my secret note"}
            ]
        }'''
        mock_run.return_value = mock_proc

        with patch.object(self.service, "check_op_installed", return_value=True):
            res = self.service.get_item("item123")
            self.assertTrue(res["ok"])
            item = res["item"]
            self.assertEqual(item["title"], "Amazon")
            self.assertEqual(item["vault"], "Personal")
            self.assertEqual(item["totp"], "123456")
            self.assertEqual(item["notes"], "my secret note")
            self.assertEqual(len(item["fields"]), 3)
            self.assertTrue(item["fields"][1]["concealed"])

            # Test in-memory cache hit
            mock_run.reset_mock()
            cached_res = self.service.get_item("item123")
            self.assertTrue(cached_res["ok"])
            self.assertEqual(cached_res["item"]["title"], "Amazon")
            mock_run.assert_not_called()

    @patch("subprocess.Popen")
    def test_open_desktop(self, mock_popen):
        res = self.service.open_desktop("item123")
        self.assertTrue(res["ok"])
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        self.assertIn("onepassword://item?i=item123", args)

    @patch("subprocess.Popen")
    def test_copy_direct_value(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        with patch("shutil.which", return_value="/usr/bin/wl-copy"):
            res = self.service.copy_to_clipboard(value="my-password-value", field="Password", title="Amazon")
            self.assertTrue(res["ok"])
            self.assertEqual(res["field"], "Password")

    @patch("subprocess.run")
    def test_fetch_field_reads_from_item_cache(self, mock_run):
        self.service.item_details_cache["item123"] = {
            "timestamp": 123456,
            "data": {
                "id": "item123",
                "title": "Amazon",
                "totp": "654321",
                "fields": [
                    {"id": "username", "label": "username", "value": "myuser@example.com", "purpose": "USERNAME"},
                    {"id": "password", "label": "password", "value": "cachedsecretpass", "purpose": "PASSWORD"},
                ],
            },
        }

        with patch.object(self.service, "check_op_installed", return_value=True):
            ok_pw, pw = self.service.fetch_field("item123", "password")
            self.assertTrue(ok_pw)
            self.assertEqual(pw, "cachedsecretpass")

            ok_user, user = self.service.fetch_field("item123", "username")
            self.assertTrue(ok_user)
            self.assertEqual(user, "myuser@example.com")

            ok_totp, totp = self.service.fetch_field("item123", "otp")
            self.assertTrue(ok_totp)
            self.assertEqual(totp, "654321")

            # subprocess.run must not be called when item is in cache
            mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_get_status_locked_fast_account_list(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '[{"email": "ganesh@example.com", "url": "my.1password.com", "user_uuid": "U123"}]'
        mock_run.return_value = mock_proc

        self.service.is_unlocked = False

        with patch.object(self.service, "check_op_installed", return_value=True):
            status = self.service.get_status(force=False)
            self.assertTrue(status["ok"])
            self.assertFalse(status["unlocked"])
            self.assertEqual(status["account"], "ganesh@example.com")
            # Should have called op account list, NOT op user get
            mock_run.assert_called_once()
            self.assertEqual(mock_run.call_args[0][0], ["op", "account", "list", "--format=json"])


if __name__ == "__main__":
    unittest.main()


