"""The private runtime directory, and refusing to write anywhere unsafe."""

import os
import pathlib
import stat
import tempfile
import unittest
from unittest.mock import patch

from support import IsolatedRuntimeDir
from omapass import paths


class TestRuntimeDir(IsolatedRuntimeDir):
    def test_a_private_xdg_dir_is_used(self):
        os.chmod(self.runtime, 0o700)
        self.assertEqual(paths.runtime_dir(), self.runtime)

    def test_a_world_accessible_xdg_dir_is_refused(self):
        """Predictable names in a shared dir let another user pre-create them."""
        os.chmod(self.runtime, 0o777)
        self.assertNotEqual(paths.runtime_dir(), self.runtime)

    def test_a_missing_xdg_dir_falls_back(self):
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/nonexistent-omapass"}):
            got = paths.runtime_dir()
        self.assertTrue(got.is_dir())
        self.assertEqual(stat.S_IMODE(got.stat().st_mode) & 0o077, 0)

    def test_every_runtime_path_sits_inside_the_runtime_dir(self):
        os.chmod(self.runtime, 0o700)
        for fn in (paths.socket_path, paths.cache_path, paths.daemon_lock_path,
                   paths.startup_lock_path, paths.clipboard_lock_path,
                   paths.wipe_pid_path, paths.token_path):
            self.assertEqual(fn().parent, self.runtime, fn.__name__)


class TestPrivateWrites(IsolatedRuntimeDir):
    def setUp(self):
        super().setUp()
        os.chmod(self.runtime, 0o700)

    def test_write_private_is_owner_only_from_creation(self):
        p = self.runtime / "secret"
        paths.write_private(p, "x")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)

    def test_write_private_refuses_to_follow_a_symlink(self):
        """A symlink left in our place must not redirect the write."""
        target = self.runtime / "elsewhere"
        link = self.runtime / "cache"
        link.symlink_to(target)
        with self.assertRaises(OSError):
            paths.write_private(link, "x")
        self.assertFalse(target.exists())

    def test_open_private_refuses_to_follow_a_symlink(self):
        target = self.runtime / "elsewhere2"
        link = self.runtime / "lock"
        link.symlink_to(target)
        with self.assertRaises(OSError):
            paths.open_private(link)


class TestWipeProcessIdentity(unittest.TestCase):
    def test_our_own_process_is_not_mistaken_for_a_wipe_worker(self):
        """PID reuse must not turn a stale pid file into a kill of something else."""
        self.assertFalse(paths.is_our_wipe_process(os.getpid()))

    def test_a_dead_pid_is_not_ours(self):
        self.assertFalse(paths.is_our_wipe_process(999999999))


class TestEntryScript(unittest.TestCase):
    def test_entry_script_is_settable(self):
        original = paths.entry_script()
        try:
            paths.set_entry_script("/tmp/whatever.py")
            self.assertEqual(paths.entry_script(), pathlib.Path("/tmp/whatever.py"))
        finally:
            paths.set_entry_script(original)


if __name__ == "__main__":
    unittest.main()
