"""Clipboard ownership: putting a secret on it, and getting it off again.

A copy is only safe if something removes it. Ownership is a token file: the
newest copy writes it, and a wipe worker only clears the clipboard if the token
it was given is still the current one. That is what stops an old timer wiping a
credential the user copied a second ago.

The token only speaks for copies made through here. The user copying something
of their own takes the secret off the clipboard just as effectively, and a
worker that cleared on the token alone destroyed whatever they had copied in
the meantime. So a wipe also checks that the clipboard still holds the value it
was sent to remove, by fingerprint, never by keeping the secret around.
"""

import fcntl
import hashlib
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


def digest_of(value: str) -> str:
    """Fingerprint of a copied value, for recognising it on the clipboard later."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def clipboard_holds(digest: str, timeout: float = 3.0) -> bool:
    """Whether the clipboard still holds the value that digest came from.

    A wipe exists to remove our secret, and once the user has copied something
    else the secret is already gone: clearing then destroys their data and
    protects nothing. `wl-paste --no-newline` returns the stored bytes exactly
    (the flag suppresses an appended newline rather than stripping one), and it
    can read a --sensitive copy, so the comparison is exact.

    Unknowable cases resolve towards clearing. Being unable to read the
    clipboard is not evidence the secret has gone.
    """
    if not digest:
        return True
    try:
        res = subprocess.run(
            ["wl-paste", "--no-newline"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout,
        )
    except Exception:
        return True
    if res.returncode != 0:
        # wl-paste reports an empty clipboard and an unreachable compositor
        # identically: exit 1, differing only in a stderr string. Reading the
        # second as "the secret has gone" would skip the clear and the warning
        # that follows it. Clearing an already-empty clipboard costs nothing,
        # so both resolve towards clearing.
        return True
    # A successful read that does not match is the only sound evidence that
    # the secret is off the clipboard.
    return hashlib.sha256(res.stdout).hexdigest() == digest


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


def wipe_clipboard_now(digest: str = "") -> bool:
    """Clears the clipboard now and cancels any scheduled wipe. Returns success.

    The scheduled wipe is only cancelled once the clipboard is actually clear:
    tearing down the safety net and then failing to clear left the credential
    sitting there with nothing left to remove it.

    `digest` names the value this is meant to remove, when the caller knows it.
    Given one, a clipboard holding something else is left alone.
    """
    if not digest and not token_path().exists():
        # No copy of ours is outstanding: either this daemon never made one,
        # or the wipe already took it off. Clearing now could have no effect
        # except to destroy whatever the user has copied for themselves.
        return True

    clp = clipboard_lock_path()
    try:
        with open_private(clp) as clf:
            fcntl.flock(clf, fcntl.LOCK_EX)
            try:
                if not clipboard_holds(digest):
                    # The user has copied something since. Our secret is
                    # already off the clipboard, so there is nothing to take
                    # off it, and taking their data off would be the only
                    # effect this could have.
                    cancel_previous_wipe()
                    return True
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


def run_wipe_worker(delay_seconds: int, token: str, digest: str = "") -> None:
    """Clears the clipboard after delay_seconds, unless a newer copy superseded us.

    Ownership is the token file: a later copy overwrites it, so the older
    worker wakes up, sees a token that is not its own, and does nothing.
    """
    if not token:
        return
    time.sleep(max(0, delay_seconds))

    tp = token_path()

    # One lock acquisition per attempt, never held across a sleep: a concurrent
    # copy waits only for the clipboard read and the clear, and a copy landing
    # between attempts takes over the token, which ends this worker's job on
    # the next pass.
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

                # The token only proves no later copy of ours superseded this
                # one. It says nothing about the user copying something of
                # their own in the meantime, which takes the secret off the
                # clipboard just as effectively and must not be clobbered.
                if not clipboard_holds(digest):
                    for path in (tp, wipe_pid_path()):
                        try:
                            path.unlink()
                        except OSError:
                            pass
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
