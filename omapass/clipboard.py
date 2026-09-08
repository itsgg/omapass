"""Clipboard ownership: putting a secret on it, and getting it off again.

A copy is only safe if something removes it. Ownership is a token file: the
newest copy writes it, and a wipe worker only clears the clipboard if the token
it was given is still the current one. That is what stops an old timer wiping a
credential the user copied a second ago.
"""

import fcntl
import os
import signal
import subprocess
import time

from .config import WIPE_RETRY_DELAYS
from .paths import (
    clipboard_lock_path,
    is_our_wipe_process,
    open_private,
    token_path,
    wipe_pid_path,
)

def cancel_previous_wipe() -> None:
    """Invalidates the wipe token and terminates any previously running clipboard wipe child."""
    tp = token_path()
    try:
        if tp.exists():
            tp.unlink()
    except OSError:
        pass

    wp = wipe_pid_path()
    if wp.exists():
        try:
            pid_str = wp.read_text().strip()
            if pid_str.isdigit():
                old_pid = int(pid_str)
                if is_our_wipe_process(old_pid):
                    os.kill(old_pid, signal.SIGTERM)
        except (OSError, ValueError):
            pass
        finally:
            try:
                if wp.exists():
                    wp.unlink()
            except OSError:
                pass


def clear_clipboard_once(timeout: float = 3.0) -> bool:
    """Runs `wl-copy --clear`, returning whether it actually succeeded."""
    try:
        res = subprocess.run(["wl-copy", "--clear"], timeout=timeout, check=False)
        return res.returncode == 0
    except Exception:
        return False


def warn_clipboard_not_cleared() -> None:
    """Tells the user a secret is still on the clipboard.

    Failing to clear is the one outcome the user must know about: silently
    giving up leaves a password sitting in the clipboard indefinitely.
    """
    try:
        subprocess.run(
            [
                "notify-send",
                "-a", "OmaPass",
                "-u", "critical",
                "-i", "dialog-warning",
                "OmaPass",
                "Could not clear the clipboard. A copied credential may still be there.",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=3.0,
        )
    except Exception:
        pass


def wipe_clipboard_now() -> bool:
    """Clears the clipboard now and cancels any scheduled wipe. Returns success.

    The scheduled wipe is only cancelled once the clipboard is actually clear:
    tearing down the safety net and then failing to clear left the credential
    sitting there with nothing left to remove it.
    """
    clp = clipboard_lock_path()
    try:
        with open_private(clp) as clf:
            fcntl.flock(clf, fcntl.LOCK_EX)
            try:
                cleared = False
                for attempt in range(2):
                    if attempt:
                        time.sleep(0.5)
                    if clear_clipboard_once():
                        cleared = True
                        break
                if cleared:
                    cancel_previous_wipe()
                return cleared
            finally:
                try:
                    fcntl.flock(clf, fcntl.LOCK_UN)
                except OSError:
                    pass
    except Exception:
        return clear_clipboard_once()


def run_wipe_worker(delay_seconds: int, token: str) -> None:
    """Clears the clipboard after delay_seconds, unless a newer copy superseded us.

    Ownership is the token file: a later copy overwrites it, so the older
    worker wakes up, sees a token that is not its own, and does nothing.
    """
    if not token:
        return
    time.sleep(max(0, delay_seconds))

    tp = token_path()

    # One lock acquisition per attempt, never held across a sleep: a concurrent
    # copy waits only for the clear itself, and a copy landing between attempts
    # takes over the token, which ends this worker's job on the next pass.
    for delay in WIPE_RETRY_DELAYS:
        with open_private(clipboard_lock_path()) as clf:
            fcntl.flock(clf, fcntl.LOCK_EX)
            try:
                try:
                    current = tp.read_text()
                except OSError:
                    return
                if current != token:
                    return

                if clear_clipboard_once():
                    for path in (tp, wipe_pid_path()):
                        try:
                            path.unlink()
                        except OSError:
                            pass
                    return
            finally:
                try:
                    fcntl.flock(clf, fcntl.LOCK_UN)
                except OSError:
                    pass
        if delay:
            time.sleep(delay)

    # Out of attempts. The token file stays, so a later copy or an explicit
    # lock still knows a secret was left behind, and the user is told rather
    # than left with a password sitting in the clipboard silently.
    try:
        wipe_pid_path().unlink()
    except OSError:
        pass
    warn_clipboard_not_cleared()
