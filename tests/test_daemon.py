"""The socket protocol: framing, limits, and what a malformed request gets."""

import json
import os
import socket
import threading
import time
import unittest
from unittest.mock import patch

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

    def test_a_stale_daemon_is_answered_around_not_through(self):
        stale = daemon.PROTOCOL_VERSION - 1
        sent = []

        def never_dies(*_a, **_k):
            return None

        def request(req, timeout=15.0):
            sent.append(req)
            return {"ok": True, "version": stale, "from": "the old daemon"}

        with patch.object(daemon, "send_socket_request", side_effect=request):
            with patch.object(daemon, "stop_stale_daemon", side_effect=never_dies):
                with patch("subprocess.Popen"):
                    with patch("time.sleep"):
                        with patch.object(daemon, "OmaPassService") as svc:
                            svc.return_value.dispatch.return_value = {"ok": True, "from": "in process"}
                            daemon.handle_request({"action": "edit_item", "id": "i1"})

        # The edit was served by this build, not by whatever is on the socket.
        svc.return_value.dispatch.assert_called_once()
        self.assertEqual(
            [r for r in sent if r.get("action") == "edit_item"], [],
            "an edit reached a daemon running a different protocol version",
        )

    def test_ensure_daemon_reports_failure_when_the_old_one_survives(self):
        stale = daemon.PROTOCOL_VERSION - 1
        with patch.object(daemon, "send_socket_request",
                          return_value={"ok": True, "version": stale}):
            with patch.object(daemon, "stop_stale_daemon"):
                with patch("subprocess.Popen"):
                    with patch("time.sleep"):
                        self.assertFalse(daemon.ensure_daemon())

    def test_ensure_daemon_reports_success_at_our_version(self):
        with patch.object(daemon, "send_socket_request",
                          return_value={"ok": True, "version": daemon.PROTOCOL_VERSION}):
            self.assertTrue(daemon.ensure_daemon())


if __name__ == "__main__":
    unittest.main()
