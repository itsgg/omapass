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


if __name__ == "__main__":
    unittest.main()

