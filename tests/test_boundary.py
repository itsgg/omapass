"""The process boundary: what runs, from where, with which environment.

These are the three properties the marketplace's security review asked for,
each tested in the direction it can fail: an environment that leaks, a
program taken from somewhere writable, an interpreter that is not the fixed
one. The widget's half lives in QML, which these tests read as text, since
the two allowlists have to be the same list.
"""

import os
import pathlib
import re
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from support import ROOT, IsolatedRuntimeDir  # noqa: F401  (sys.path)

from omapass import boundary, daemon

WIDGET = (ROOT / "BarWidget.qml").read_text()


class TestChildEnvironment(unittest.TestCase):
    def test_nothing_that_steers_a_loader_or_interpreter_survives(self):
        poisoned = {
            "PATH": "/tmp/evil:/usr/bin",
            "LD_PRELOAD": "/tmp/evil.so",
            "LD_LIBRARY_PATH": "/tmp",
            "PYTHONPATH": "/tmp/evil",
            "PYTHONSTARTUP": "/tmp/evil.py",
            "PYTHONHOME": "/tmp",
            "TMPDIR": "/tmp/evil",
            "BROWSER": "/tmp/evil",
            "HOME": "/home/someone",
            "XDG_RUNTIME_DIR": "/run/user/1000",
        }
        with patch.dict(os.environ, poisoned, clear=True):
            env = boundary.child_environment()

        self.assertEqual(env["PATH"], "/usr/local/bin:/usr/bin:/bin")
        for name in ("LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH",
                     "PYTHONSTARTUP", "PYTHONHOME", "TMPDIR", "BROWSER"):
            self.assertNotIn(name, env)
        self.assertEqual(env["HOME"], "/home/someone")
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1000")
        # Every name in the result is on the allowlist, or PATH.
        self.assertTrue(set(env) <= set(boundary.SESSION_VARS) | {"PATH"})

    def test_extra_values_travel_and_win(self):
        with patch.dict(os.environ, {"HOME": "/h", "OMAPASS_WIPE_TOKEN": "stale"}, clear=True):
            env = boundary.child_environment(OMAPASS_WIPE_TOKEN="fresh")
        self.assertEqual(env["OMAPASS_WIPE_TOKEN"], "fresh")

    def test_an_empty_variable_is_not_passed_on(self):
        with patch.dict(os.environ, {"DISPLAY": ""}, clear=True):
            self.assertNotIn("DISPLAY", boundary.child_environment())

    def test_the_widget_and_the_helper_allow_the_same_names(self):
        """Two copies of one list, kept identical by this test.

        The widget clears the helper's environment down to its list; the
        helper then passes its list on to op and the clipboard. A name on one
        side only is either a variable the helper never receives or one it
        would pass on without the widget having agreed to it.
        """
        m = re.search(r"helperEnvironmentNames:\s*\[(.*?)\]", WIDGET, re.S)
        self.assertIsNotNone(m, "helperEnvironmentNames not found in BarWidget.qml")
        names = re.findall(r'"([A-Z0-9_]+)"', m.group(1))
        self.assertEqual(names, list(boundary.SESSION_VARS))


class TestTool(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _executable(self, name, mode=0o755):
        path = self.dir / name
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(mode)
        return str(path)

    def test_a_bare_name_is_looked_up_only_in_the_trusted_directories(self):
        self._executable("op")
        with patch.object(boundary, "TRUSTED_BIN_DIRS", (str(self.dir),)):
            self.assertEqual(boundary.tool("op"), str(self.dir / "op"))
            self.assertIsNone(boundary.tool("missing"))
        # The same name, with the real directories: our temp dir is not one.
        with patch.object(boundary, "TRUSTED_BIN_DIRS", ("/nonexistent",)):
            self.assertIsNone(boundary.tool("op"))

    def test_a_relative_path_is_refused(self):
        self._executable("op")
        with patch.object(boundary, "TRUSTED_BIN_DIRS", (str(self.dir),)):
            self.assertIsNone(boundary.tool("./op"))
            self.assertIsNone(boundary.tool("sub/op"))
            self.assertIsNone(boundary.tool(""))

    def test_a_world_writable_program_is_refused(self):
        path = self._executable("op", 0o777)
        with patch.object(boundary, "TRUSTED_BIN_DIRS", (str(self.dir),)):
            self.assertIsNone(boundary.tool("op"))
            os.chmod(path, 0o755)
            self.assertEqual(boundary.tool("op"), path)

    def test_a_program_in_a_world_writable_directory_is_refused(self):
        path = self._executable("op")
        os.chmod(self.dir, 0o777)
        try:
            with patch.object(boundary, "TRUSTED_BIN_DIRS", (str(self.dir),)):
                self.assertIsNone(boundary.tool("op"))
                self.assertIsNone(boundary.tool(path))
        finally:
            os.chmod(self.dir, 0o700)

    def test_a_non_executable_or_non_regular_file_is_refused(self):
        self._executable("op", 0o644)
        (self.dir / "dir").mkdir()
        with patch.object(boundary, "TRUSTED_BIN_DIRS", (str(self.dir),)):
            self.assertIsNone(boundary.tool("op"))
            self.assertIsNone(boundary.tool("dir"))

    def test_an_absolute_path_passes_the_same_checks(self):
        path = self._executable("op")
        self.assertEqual(boundary.tool(path), path)
        os.chmod(path, 0o777)
        self.assertIsNone(boundary.tool(path))


class TestRun(unittest.TestCase):
    def test_run_executes_the_resolved_path_under_the_closed_environment(self):
        with patch("omapass.boundary.tool", return_value="/usr/bin/op"):
            with patch("subprocess.run") as mock_run:
                with patch.dict(os.environ, {"LD_PRELOAD": "/tmp/x", "HOME": "/h"}, clear=True):
                    boundary.run(["op", "whoami"], timeout=1.0)
        argv = mock_run.call_args[0][0]
        kwargs = mock_run.call_args.kwargs
        # argv[0] stays the name; what runs is the trusted path.
        self.assertEqual(argv, ["op", "whoami"])
        self.assertEqual(kwargs["executable"], "/usr/bin/op")
        self.assertNotIn("LD_PRELOAD", kwargs["env"])
        self.assertEqual(kwargs["env"]["HOME"], "/h")
        self.assertEqual(kwargs["timeout"], 1.0)

    def test_a_program_outside_the_trusted_directories_never_runs(self):
        with patch("omapass.boundary.tool", return_value=None):
            with patch("subprocess.run") as mock_run:
                with self.assertRaises(FileNotFoundError):
                    boundary.run(["op", "whoami"])
                with self.assertRaises(FileNotFoundError):
                    boundary.popen(["wl-copy"])
        mock_run.assert_not_called()

    def test_an_explicit_environment_is_kept(self):
        with patch("omapass.boundary.tool", return_value="/usr/bin/x"):
            with patch("subprocess.Popen") as mock_popen:
                boundary.popen(["x"], env={"ONLY": "this"})
        self.assertEqual(mock_popen.call_args.kwargs["env"], {"ONLY": "this"})


class TestInterpreter(unittest.TestCase):
    def test_the_interpreter_is_the_fixed_python_in_isolated_mode(self):
        self.assertEqual(boundary.interpreter(), ["/usr/bin/python3", "-I"])
        self.assertEqual(boundary.PYTHON, "/usr/bin/python3")

    def test_a_missing_or_writable_interpreter_raises_rather_than_falling_back(self):
        with patch.object(boundary, "PYTHON", "/nonexistent/python3"):
            with self.assertRaises(FileNotFoundError):
                boundary.interpreter()

    def test_isolated_mode_really_ignores_the_environment(self):
        """The property the widget relies on, checked against the real python."""
        with tempfile.TemporaryDirectory() as d:
            evil = pathlib.Path(d) / "sitecustomize.py"
            evil.write_text("import sys; sys.stderr.write('POISONED\\n')\n")
            proc = subprocess.run(
                boundary.interpreter() + ["-c", "import sys; print(sys.flags.isolated)"],
                capture_output=True, text=True, timeout=30,
                env={"PYTHONPATH": d, "PYTHONSTARTUP": str(evil), "HOME": d},
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "1")
        self.assertNotIn("POISONED", proc.stderr)

    def test_the_entry_script_runs_under_the_same_interpreter(self):
        first = (ROOT / "omapass-agent.py").read_text().splitlines()[0]
        self.assertEqual(first, "#!/usr/bin/python3 -I")


class TestWidgetSide(unittest.TestCase):
    """The QML half, read as text: a fixed command, every process closed."""

    def test_the_helper_command_is_the_fixed_interpreter_in_isolated_mode(self):
        self.assertIn('return ["/usr/bin/python3", "-I", root.helperPath, "request", "-"]', WIDGET)
        self.assertNotRegex(WIDGET, r'\["python3"')

    def test_every_helper_process_clears_its_environment(self):
        blocks = re.findall(r"\n  Process \{\n(.*?)\n  \}\n", WIDGET, re.S)
        self.assertGreaterEqual(len(blocks), 6, "expected the six helper processes")
        for block in blocks:
            head = block[:400]
            self.assertIn("clearEnvironment: true", head, block[:120])
            self.assertIn("environment: root.helperEnvironment", head, block[:120])


class TestDaemonSpawn(IsolatedRuntimeDir):
    def test_the_daemon_is_started_by_the_fixed_interpreter_with_a_closed_environment(self):
        with patch.object(daemon, "daemon_is_current", side_effect=[False, False, True]):
            with patch.object(daemon, "stop_stale_daemon"):
                with patch("subprocess.Popen") as mock_popen:
                    with patch("time.sleep"):
                        with patch.dict(os.environ, {"PYTHONPATH": "/tmp/evil", "LD_PRELOAD": "/tmp/e.so"}):
                            self.assertTrue(daemon.ensure_daemon())
        argv = mock_popen.call_args[0][0]
        kwargs = mock_popen.call_args.kwargs
        self.assertEqual(argv[:2], ["/usr/bin/python3", "-I"])
        self.assertEqual(pathlib.Path(argv[2]).name, "omapass-agent.py")
        self.assertEqual(argv[3], "serve")
        self.assertEqual(kwargs["executable"], "/usr/bin/python3")
        self.assertNotIn("PYTHONPATH", kwargs["env"])
        self.assertNotIn("LD_PRELOAD", kwargs["env"])
        self.assertEqual(kwargs["env"]["XDG_RUNTIME_DIR"], str(self.runtime))


if __name__ == "__main__":
    unittest.main()
