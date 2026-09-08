"""The socket protocol: framing, limits, and what a malformed request gets."""

import json
import os
import socket
import threading
import time
import unittest
from unittest.mock import mock_open, patch

from support import IsolatedRuntimeDir
from omapass import daemon, paths


class TestRequestHandling(IsolatedRuntimeDir):
    """Drives a real daemon over a real Unix socket."""

    def setUp(self):
        super().setUp()
        os.chmod(self.runtime, 0o700)
        self._server = None
        self.thread = threading.Thread(
            target=daemon.run_daemon,
            kwargs={"install_signals": False, "on_ready": self._capture},
            daemon=True,
        )
        self.thread.start()
        for _ in range(100):
            if paths.socket_path().exists():
                break
            time.sleep(0.02)
        self.assertTrue(paths.socket_path().exists(), "daemon did not bind its socket")
        self.addCleanup(self._stop)

    def _capture(self, server):
        self._server = server

    def _stop(self):
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        try:
            paths.socket_path().unlink()
        except OSError:
            pass

    def _send_raw(self, payload: bytes, timeout=5.0):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(str(paths.socket_path()))
            s.sendall(payload)
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            return json.loads(buf.split(b"\n")[0].decode()) if buf else None
        finally:
            s.close()

    def test_the_socket_is_owner_only(self):
        mode = paths.socket_path().stat().st_mode & 0o777
        self.assertEqual(mode & 0o077, 0, f"socket is reachable by others: {oct(mode)}")

    def test_ping_reports_the_version_and_the_build(self):
        """Both are what the client compares against; a ping missing either
        would make every daemon look stale, or every stale one look current."""
        res = daemon.send_socket_request({"action": "ping"}, timeout=2)
        self.assertEqual(res["version"], daemon.PROTOCOL_VERSION)
        self.assertEqual(res["build"], daemon.build_id())

    def test_ping_round_trips(self):
        res = daemon.send_socket_request({"action": "ping"}, timeout=5.0)
        self.assertTrue(res["ok"])
        self.assertIn("pong", res)

    def test_unknown_action_is_refused_not_crashed(self):
        res = daemon.send_socket_request({"action": "nope"}, timeout=5.0)
        self.assertFalse(res["ok"])
        self.assertIn("Unknown action", res["error"])

    def test_malformed_json_gets_an_error_and_the_daemon_survives(self):
        res = self._send_raw(b"{not json at all}\n")
        self.assertFalse(res["ok"])
        # still serving
        self.assertTrue(daemon.send_socket_request({"action": "ping"}, timeout=5.0)["ok"])

    def test_a_non_object_request_is_refused(self):
        res = self._send_raw(b'["status"]\n')
        self.assertFalse(res["ok"])

    def test_an_oversized_request_is_cut_off(self):
        """A local client must not be able to grow the daemon without bound.

        The daemon may refuse with an error or simply hang up mid-write; both
        are fine. What matters is that it stops reading and keeps serving.
        """
        payload = b'{"action":"list","query":"' + b"x" * (2 * 1024 * 1024) + b'"}\n'
        try:
            res = self._send_raw(payload)
            self.assertTrue(res is None or res.get("ok") is False)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.assertTrue(daemon.send_socket_request({"action": "ping"}, timeout=5.0)["ok"])

    def test_junk_numbers_are_coerced_not_fatal(self):
        res = daemon.send_socket_request(
            {"action": "list", "query": "", "limit": "not-a-number"}, timeout=5.0)
        self.assertTrue(res["ok"])


class TestNoDaemon(IsolatedRuntimeDir):
    def test_request_to_a_missing_socket_returns_none(self):
        os.chmod(self.runtime, 0o700)
        self.assertIsNone(daemon.send_socket_request({"action": "ping"}, timeout=0.2))



class TestVersionHandshake(IsolatedRuntimeDir):
    """A daemon from an older build must never be handed a request.

    The protocol version exists because the daemon outlives a plugin update.
    An old one that survives its SIGTERM keeps the lifetime lock, so the
    replacement exits at once and the old one goes on answering.
    """

    def setUp(self):
        super().setUp()
        os.chmod(self.runtime, 0o700)

    def _pong(self, version=None, build=None, extra=None):
        out = {"ok": True,
               "version": daemon.PROTOCOL_VERSION if version is None else version}
        if build is not None:
            out["build"] = build
        if extra:
            out.update(extra)
        return out

    def test_a_stale_daemon_is_answered_around_not_through(self):
        sent = []

        def request(req, timeout=15.0):
            sent.append(req)
            return self._pong(version=daemon.PROTOCOL_VERSION - 1, build="old",
                              extra={"from": "the old daemon"})

        with patch.object(daemon, "send_socket_request", side_effect=request):
            with patch.object(daemon, "stop_stale_daemon"):
                with patch("subprocess.Popen"):
                    with patch("time.sleep"):
                        with patch.object(daemon, "OmaPassService") as svc:
                            svc.return_value.dispatch.return_value = {"ok": True}
                            daemon.handle_request({"action": "edit_item", "id": "i1"})

        svc.return_value.dispatch.assert_called_once()
        self.assertEqual(
            [r for r in sent if r.get("action") == "edit_item"], [],
            "an edit reached a daemon running a different protocol version")

    def test_a_build_mismatch_is_answered_around_even_at_our_version(self):
        """The case the protocol version cannot catch, because nobody bumped
        it. Removing the build comparison makes this test fail."""
        sent = []

        def request(req, timeout=15.0):
            sent.append(req)
            return self._pong(build="a-different-build",
                              extra={"from": "the old daemon"})

        with patch.object(daemon, "send_socket_request", side_effect=request):
            with patch.object(daemon, "stop_stale_daemon"):
                with patch("subprocess.Popen"):
                    with patch("time.sleep"):
                        with patch.object(daemon, "OmaPassService") as svc:
                            svc.return_value.dispatch.return_value = {"ok": True}
                            daemon.handle_request({"action": "edit_item", "id": "i1"})

        svc.return_value.dispatch.assert_called_once()
        self.assertEqual(
            [r for r in sent if r.get("action") == "edit_item"], [],
            "an edit reached a daemon running different code")

    def test_a_daemon_running_other_code_is_stopped(self):
        with patch.object(daemon, "send_socket_request",
                          return_value=self._pong(build="a-different-build")):
            with patch.object(daemon, "stop_stale_daemon") as stop:
                with patch("subprocess.Popen"):
                    with patch("time.sleep"):
                        self.assertFalse(daemon.ensure_daemon())
        stop.assert_called()

    def test_a_daemon_that_reports_no_build_is_stopped(self):
        """A daemon from before build ids existed."""
        with patch.object(daemon, "send_socket_request", return_value=self._pong()):
            with patch.object(daemon, "stop_stale_daemon") as stop:
                with patch("subprocess.Popen"):
                    with patch("time.sleep"):
                        self.assertFalse(daemon.ensure_daemon())
        stop.assert_called()

    def test_a_stale_daemon_answering_only_under_the_lock_is_still_stopped(self):
        """The probe before the lock can time out. If the stale daemon only
        answers once we hold the lock, it still holds the lifetime lock, so a
        replacement would exit at once and leave it serving."""
        answers = [None, self._pong(build="a-different-build")]

        def request(req, timeout=15.0):
            return answers.pop(0) if answers else self._pong(build="a-different-build")

        with patch.object(daemon, "send_socket_request", side_effect=request):
            with patch.object(daemon, "stop_stale_daemon") as stop:
                with patch("subprocess.Popen"):
                    with patch("time.sleep"):
                        daemon.ensure_daemon()
        stop.assert_called()

    def test_a_version_mismatch_alone_is_enough_to_replace(self):
        """Isolates the version half of the handshake: same build, older
        protocol. Without it, only the build check would be under test."""
        with patch.object(daemon, "send_socket_request",
                          return_value=self._pong(version=daemon.PROTOCOL_VERSION - 1,
                                                  build=daemon.build_id())):
            with patch.object(daemon, "stop_stale_daemon") as stop:
                with patch("subprocess.Popen"):
                    with patch("time.sleep"):
                        self.assertFalse(daemon.ensure_daemon())
        stop.assert_called()

    def test_a_hung_daemon_is_stopped_rather_than_waited_on(self):
        """It answers no ping, so it reads as None rather than False, but it
        still holds the lifetime lock: every replacement exits at once, and
        skipping the stop wedges the machine permanently."""
        with patch.object(daemon, "send_socket_request", return_value=None):
            with patch.object(daemon, "stop_stale_daemon") as stop:
                with patch("subprocess.Popen"):
                    with patch("time.sleep"):
                        daemon.ensure_daemon()
        stop.assert_called()

    def test_our_daemon_is_recognised_only_by_script_then_serve(self):
        """A process is ours only if the entry script is immediately followed
        by `serve`. Anything looser matches an editor holding the file open,
        and the pid this is asked about is then signalled."""
        entry = str(paths.entry_script())
        name = os.path.basename(entry)
        cases = {
            ("/usr/bin/python3", entry, "serve"): True,
            ("/usr/bin/python3.14", entry, "serve"): True,
            # A different checkout of the same project, still ours by name.
            ("/usr/bin/python3", "/somewhere/else/" + name, "serve"): True,
            # An editor opening this script beside a file called `serve`
            # has our exact shape. Only the interpreter separates them, and
            # testing "serve.py" here instead of "serve" hid that.
            ("/usr/bin/vim", entry, "serve"): False,
            ("/usr/bin/grep", "serve", entry): False,
            ("/usr/bin/python3", entry, "request"): False,
            ("/usr/bin/python3", "unrelated.py", "serve"): False,
        }
        for argv, expected in cases.items():
            blob = "\0".join(argv).encode()
            with patch("builtins.open", mock_open(read_data=blob)):
                self.assertEqual(daemon._is_our_daemon(4242), expected, argv)

    def test_a_recycled_pid_is_not_signalled(self):
        """The pid file outlives its process; the number gets reused."""
        paths.write_private(paths.daemon_pid_path(), "4242")
        with patch.object(daemon, "_is_our_daemon", return_value=False):
            with patch("os.kill") as kill:
                daemon.stop_stale_daemon()
        kill.assert_not_called()

    def test_ensure_daemon_reports_success_at_our_version_and_build(self):
        with patch.object(daemon, "send_socket_request",
                          return_value=self._pong(build=daemon.build_id())):
            self.assertTrue(daemon.ensure_daemon())


if __name__ == "__main__":
    unittest.main()
