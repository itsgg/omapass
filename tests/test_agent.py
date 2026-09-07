#!/usr/bin/env python3
import importlib.util
import pathlib
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
        # Favorites should come first
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


if __name__ == "__main__":
    unittest.main()
