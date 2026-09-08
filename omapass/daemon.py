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

from .config import DETAILS_CACHE_TTL, MAX_REQUEST_BYTES, PROTOCOL_VERSION
from .paths import (
    daemon_lock_path,
    daemon_pid_path,
    entry_script,
    open_private,
    runtime_dir,
    socket_path,
    write_private,
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

        # Bytes accumulated and decoded once, never chunk by chunk: a
        # multi-byte character split across a recv boundary would raise
        # mid-stream. Responses are ASCII today because json.dumps escapes
        # non-ASCII, so this is a trap rather than a live bug, and the fix
        # costs nothing.
        buffer = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
            if len(buffer) > MAX_REQUEST_BYTES:
                raise ValueError("Response too large")
            if b"\n" in buffer:
                break

        line, _, _ = buffer.partition(b"\n")
        return json.loads(line.decode("utf-8"))
    except Exception:
        return None
    finally:
        sock.close()


def running_daemon_version() -> Optional[int]:
    """The protocol version of the daemon answering right now, if any."""
    pong = send_socket_request({"action": "ping"}, timeout=0.5)
    if pong is None:
        return None
    try:
        return int(pong.get("version", 0))
    except (TypeError, ValueError):
        return 0


def _is_our_daemon(pid: int) -> bool:
    """True only for a process running this exact entry script as `serve`.

    Matched on the full resolved script path, not a loose substring, and every
    candidate is checked in /proc before anything is signalled.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = f.read().decode("utf-8", "replace").split("\0")
    except OSError:
        return False
    return str(entry_script()) in argv and "serve" in argv


def _daemon_pid_from_file() -> Optional[int]:
    try:
        pid = int(daemon_pid_path().read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _is_our_daemon(pid) else None


def _daemon_pid_from_proc() -> Optional[int]:
    """Fallback for a daemon old enough to predate the pid file."""
    try:
        candidates = [int(e) for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return None
    for pid in candidates:
        if pid != os.getpid() and _is_our_daemon(pid):
            return pid
    return None


def stop_stale_daemon() -> None:
    """Stops a daemon left over from an older version of the plugin.

    Identified from its own lock file and checked against /proc before any
    signal is sent, so a recycled pid cannot make this kill something else.
    """
    pid = _daemon_pid_from_file() or _daemon_pid_from_proc()
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(30):
        time.sleep(0.1)
        if not pathlib.Path(f"/proc/{pid}").exists():
            break


def ensure_daemon() -> bool:
    """Starts the daemon if needed. True when one at our version is serving.

    The answer matters: a stale daemon that outlives its SIGTERM keeps holding
    the lifetime lock, so the replacement exits immediately and the old one
    goes on answering. Reporting that rather than assuming success is what
    lets the caller refuse to talk to it.
    """
    version = running_daemon_version()
    if version == PROTOCOL_VERSION:
        return True
    if version is not None:
        # Serving, but from an older build of the plugin.
        stop_stale_daemon()

    # Serialize startup across concurrent calls
    slp = startup_lock_path()
    with open_private(slp) as sf:
        try:
            fcntl.flock(sf, fcntl.LOCK_EX)
            # Re-check under lock; another process may have just started the daemon
            if running_daemon_version() == PROTOCOL_VERSION:
                return True

            script_path = entry_script()
            subprocess.Popen(
                [sys.executable, str(script_path), "serve"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            # Wait for a daemon at our version, not merely for one that
            # answers: a surviving old daemon answers too, and treating that
            # as success is how a request reaches the build we just replaced.
            for _ in range(15):
                time.sleep(0.1)
                if running_daemon_version() == PROTOCOL_VERSION:
                    return True
            return False
        finally:
            try:
                fcntl.flock(sf, fcntl.LOCK_UN)
            except OSError:
                pass


def sweep_orphaned_templates() -> None:
    """Removes item templates a previous daemon died before deleting.

    create_item unlinks its template in a finally block, but a SIGKILL, or a
    SIGTERM landing between the write and the run, leaves the plaintext of an
    item on disk. Only one daemon runs at a time and it holds an exclusive
    lock, so by the time this runs no live template can exist.
    """
    try:
        for stale in runtime_dir().glob("omapass-new.*.json"):
            try:
                stale.unlink()
            except OSError:
                pass
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

    write_private(daemon_pid_path(), str(os.getpid()))
    sweep_orphaned_templates()

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
        ours = ensure_daemon()
        action = req.get("action", "")
        if action in ("create_item", "create_login", "edit_item", "delete_item"):
            # A write can run op three times (read, preview, commit) at 25s
            # each. The old 10s budget timed out on the client while the
            # daemon went on writing, so the widget reported a failed save
            # for an edit that had in fact landed.
            timeout = 90.0
        elif action in ("sync", "get_item", "unlock", "copy", "type"):
            timeout = 40.0
        else:
            timeout = 10.0
        if not ours:
            # Something is serving, but not this build. Answering in process
            # is slower than the socket and always this build's own code,
            # which is the whole point of the version handshake.
            print(json.dumps(OmaPassService().dispatch(req)))
            return

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
