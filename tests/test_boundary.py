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
import time
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
    """run() against real processes: what it closes, bounds and kills."""

    def setUp(self):
        sh = patch("omapass.boundary.tool", return_value="/bin/sh")
        sh.start()
        self.addCleanup(sh.stop)

    def test_run_executes_the_resolved_path_under_the_closed_environment(self):
        with patch.dict(os.environ, {"LD_PRELOAD": "/tmp/x", "HOME": "/h"}, clear=True):
            # argv[0] is not a shell; what runs is the resolved path.
            res = boundary.run(["notsh", "-c", "/usr/bin/env"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("HOME=/h\n", res.stdout)
        self.assertIn("PATH=/usr/local/bin:/usr/bin:/bin\n", res.stdout)
        self.assertNotIn("LD_PRELOAD", res.stdout)

    def test_an_explicit_environment_is_kept(self):
        with patch.dict(os.environ, {"HOME": "/h", "XDG_RUNTIME_DIR": "/r"}, clear=True):
            res = boundary.run(["sh", "-c", "/usr/bin/env"], env={"ONLY": "this"},
                               stdout=subprocess.PIPE, text=True, timeout=10)
        # The shell adds PWD, SHLVL and _ of its own; nothing of ours is inherited.
        names = {line.split("=")[0] for line in res.stdout.splitlines()}
        self.assertIn("ONLY", names)
        self.assertNotIn("HOME", names)
        self.assertNotIn("XDG_RUNTIME_DIR", names)
        self.assertNotIn("PATH", names)

    def test_a_program_outside_the_trusted_directories_never_runs(self):
        with patch("omapass.boundary.tool", return_value=None):
            with patch("subprocess.Popen") as mock_popen:
                with self.assertRaises(FileNotFoundError):
                    boundary.run(["op", "whoami"])
                with self.assertRaises(FileNotFoundError):
                    boundary.popen(["wl-copy"])
        mock_popen.assert_not_called()

    def test_output_past_the_cap_stops_the_program_while_it_is_talking(self):
        started = time.monotonic()
        with self.assertRaises(boundary.OutputTooLarge):
            boundary.run(["sh", "-c", "yes | head -c 50000000"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, cap=100000)
        # Stopped at the cap, not after 50 MB or after the deadline. Generous
        # for a loaded runner: the kill itself lands within milliseconds.
        self.assertLess(time.monotonic() - started, 20)

    def test_the_deadline_kills_the_whole_process_group_and_reaps(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile = pathlib.Path(d) / "pid"
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                boundary.run(["sh", "-c", f"sleep 30 & echo $! > {pidfile}; sleep 30"],
                             stdout=subprocess.PIPE, timeout=0.5)
            self.assertLess(time.monotonic() - started, 8)
            child = int(pidfile.read_text().strip())
        # The grandchild sleep went with the group, not just the shell.
        for _ in range(50):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            os.kill(child, 9)
            self.fail("the grandchild survived the group kill")

    def test_a_deadline_applies_when_the_caller_names_none(self):
        with patch.object(boundary, "DEFAULT_DEADLINE", 0.3):
            with self.assertRaises(subprocess.TimeoutExpired):
                boundary.run(["sh", "-c", "sleep 30"], stdout=subprocess.DEVNULL)

    def test_input_and_check_work_as_with_subprocess_run(self):
        res = boundary.run(["sh", "-c", "cat"], input="hello", stdout=subprocess.PIPE, text=True, timeout=10)
        self.assertEqual(res.stdout, "hello")
        # A child that never reads its stdin cannot hold the deadline hostage
        # through a write bigger than the pipe.
        with self.assertRaises(subprocess.TimeoutExpired):
            boundary.run(["sh", "-c", "sleep 30"], input="x" * 200000, stdout=subprocess.DEVNULL, timeout=0.5)

    def test_a_timed_out_wl_copy_style_child_is_killed_with_its_group(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile = pathlib.Path(d) / "pid"
            proc = boundary.popen(["sh", "-c", f"sleep 30 & echo $! > {pidfile}; sleep 30"],
                                  stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with self.assertRaises(subprocess.TimeoutExpired):
                proc.communicate(input=b"secret", timeout=0.5)
            boundary.kill_group(proc)
            self.assertIsNotNone(proc.returncode)
            for _ in range(50):
                if not pidfile.exists() or not pidfile.read_text().strip():
                    time.sleep(0.05)
                    continue
                child = int(pidfile.read_text().strip())
                break
            else:
                self.fail("the shell never wrote its child's pid")
        for _ in range(50):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            os.kill(child, 9)
            self.fail("the background child survived the group kill")
        with self.assertRaises(subprocess.CalledProcessError):
            boundary.run(["sh", "-c", "exit 3"], check=True, timeout=10)


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

    def test_every_helper_process_clears_its_environment_and_has_a_deadline(self):
        blocks = re.findall(r"\n  Process \{\n(.*?)\n  \}\n", WIDGET, re.S)
        self.assertGreaterEqual(len(blocks), 6, "expected the six helper processes")
        ids = []
        for block in blocks:
            head = block[:500]
            self.assertIn("clearEnvironment: true", head, block[:120])
            self.assertIn("environment: root.helperEnvironment", head, block[:120])
            self.assertIn("property int deadlineMs", head, block[:120])
            ids.append(re.search(r"id: (\w+)", head).group(1))
        for pid in ids:
            self.assertIn(f"HelperDeadline {{ proc: {pid};", WIDGET, pid)
        self.assertIn("proc.deadlineMs = root.helperDeadlineFor(payload)", WIDGET)

    def test_every_collector_is_capped_and_watches_the_stream_live(self):
        # The component is the one StdioCollector; every use is the capped one.
        self.assertEqual(WIDGET.count("StdioCollector {"), 1)
        self.assertIn("component CappedCollector: StdioCollector {", WIDGET)
        self.assertEqual(WIDGET.count("CappedCollector {\n"), 6)
        self.assertNotIn("waitForEnd: true", WIDGET)
        block = WIDGET[WIDGET.index("component CappedCollector"):][:900]
        self.assertIn("waitForEnd: false", block)
        self.assertIn("collector.proc.signal(9)", block)
        # Nothing parses a truncated answer, whether the cap or the deadline stopped it.
        self.assertEqual(WIDGET.count("if (stopped)"), WIDGET.count("CappedCollector {\n"))
        self.assertIn("proc.stdout.stopped = false", WIDGET)
        self.assertIn("proc.stdout.stopped = true", WIDGET)


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
