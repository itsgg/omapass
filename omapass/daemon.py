"""The socket the widget talks to, and the daemon behind it.

A persistent daemon exists so the widget never waits for a cold `op` start.
One instance at a time is enforced by an exclusive lock on a file, and the
socket lives in the private runtime directory rather than a shared one.
"""

import fcntl
import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional

from .config import DETAILS_CACHE_TTL, MAX_REQUEST_BYTES
from .paths import (
    daemon_lock_path,
    entry_script,
    open_private,
    socket_path,
    startup_lock_path,
)
from .service import OmaPassService

def send_socket_request(request: Dict[str, Any], timeout: float = 15.0) -> Optional[Dict[str, Any]]:
    """Sends JSON request to daemon socket and returns JSON response."""
    sp = socket_path()
    if not sp.exists():
        return None

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(sp))
        data = json.dumps(request) + "\n"
        sock.sendall(data.encode("utf-8"))

        buffer = ""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk.decode("utf-8")
            if "\n" in buffer:
                break

        line = buffer.strip().split("\n")[0]
        return json.loads(line)
    except Exception:
        return None
    finally:
        sock.close()


def ensure_daemon() -> None:
    """Ensures background daemon is running, using serialized file lock to prevent race conditions."""
    if send_socket_request({"action": "ping"}, timeout=0.3) is not None:
        return

    # Serialize startup across concurrent calls
    slp = startup_lock_path()
    with open_private(slp) as sf:
        try:
            fcntl.flock(sf, fcntl.LOCK_EX)
            # Re-check under lock; another process may have just started the daemon
            if send_socket_request({"action": "ping"}, timeout=0.3) is not None:
                return

            script_path = entry_script()
            subprocess.Popen(
                [sys.executable, str(script_path), "serve"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            # Wait up to 1.5s for socket to become responsive
            for _ in range(15):
                time.sleep(0.1)
                if send_socket_request({"action": "ping"}, timeout=0.2) is not None:
                    break
        finally:
            try:
                fcntl.flock(sf, fcntl.LOCK_UN)
            except OSError:
                pass


def run_daemon(install_signals: bool = True, on_ready=None):
    """Runs OmaPass background daemon with an exclusive lifetime file lock.

    `install_signals` exists so tests can drive a real daemon in a thread:
    signal handlers can only be installed from the main thread, and the
    protocol is worth testing against a real socket rather than a mock.
    `on_ready` is handed the listening socket so a test can close it.
    """
    dlp = daemon_lock_path()
    lock_file = open_private(dlp)

    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        sys.exit(0)

    sp = socket_path()
    if sp.exists():
        try:
            sp.unlink()
        except OSError:
            pass

    service = OmaPassService()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # bind() honours the umask, so the socket is never briefly group/world
    # reachable the way a bind-then-chmod would leave it.
    old_umask = os.umask(0o177)
    try:
        server.bind(str(sp))
    finally:
        os.umask(old_umask)
    server.listen(10)

    def handle_client(conn: socket.socket):
        try:
            conn.settimeout(40.0)
            buffer = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > MAX_REQUEST_BYTES:
                    raise ValueError("Request too large")
                if b"\n" in buffer:
                    line, _, _ = buffer.partition(b"\n")
                    req = json.loads(line.decode("utf-8"))
                    resp = service.dispatch(req)
                    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
                    break
        except Exception as e:
            try:
                err_resp = json.dumps({"ok": False, "error": str(e)}) + "\n"
                conn.sendall(err_resp.encode("utf-8"))
            except Exception:
                pass
        finally:
            conn.close()

    def reap_expired_details():
        """Keeps decrypted items from outliving their TTL in a long-lived daemon."""
        while True:
            time.sleep(DETAILS_CACHE_TTL / 10)
            try:
                service.purge_expired_details()
                service.reap_wipe_children()
            except Exception:
                pass

    threading.Thread(target=reap_expired_details, daemon=True).start()

    def shutdown(signum, frame):
        try:
            server.close()
            if sp.exists():
                sp.unlink()
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()
        except Exception:
            pass
        sys.exit(0)

    if install_signals:
        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

    if on_ready is not None:
        on_ready(server)

    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            # The listening socket is gone (shutdown, or the runtime dir was
            # cleaned out); anything else is transient, so keep serving.
            break
        except Exception:
            continue
        try:
            threading.Thread(target=handle_client, args=(conn,), daemon=True).start()
        except Exception:
            conn.close()


def handle_request(req: Dict[str, Any]):
    """Dispatches request via daemon socket, auto-starting daemon if needed, with structured error handling."""
    try:
        ensure_daemon()
        action = req.get("action", "")
        timeout = 40.0 if action in ("sync", "get_item", "unlock", "copy", "type") else 10.0
        resp = send_socket_request(req, timeout=timeout)
        if resp is not None:
            print(json.dumps(resp))
            return

        # Direct fallback only if daemon socket is unreachable
        if not socket_path().exists():
            service = OmaPassService()
            resp = service.dispatch(req)
            print(json.dumps(resp))
        else:
            print(json.dumps({"ok": False, "error": "Request timed out waiting for 1Password response"}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
