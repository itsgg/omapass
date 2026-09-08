"""Shared test scaffolding.

Every test runs against a throwaway runtime directory. The suite once wrote
into the live $XDG_RUNTIME_DIR and corrupted the running daemon's clipboard
wipe protocol; isolation is not optional here.
"""

import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class IsolatedRuntimeDir(unittest.TestCase):
    """Redirects every runtime path into a temp dir for the life of one test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.runtime = pathlib.Path(self._tmp.name)
        patcher = patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)


LOGIN_ITEM = {
    "id": "item1", "title": "GitHub", "category": "LOGIN", "username": "octocat",
    "url": "https://github.com", "vault": "Personal", "favorite": True,
}
CARD_ITEM = {
    "id": "item3", "title": "Acme Bank Visa", "category": "CREDIT_CARD",
    "username": "4242 **** 4242", "url": "", "vault": "Finance", "favorite": True,
}
