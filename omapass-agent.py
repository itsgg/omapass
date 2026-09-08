#!/usr/bin/env python3
"""
OmaPass Agent - Secret-isolating, high-performance backend helper for 1Password on Omarchy.

Features:
- Connects to 1Password CLI (op) and desktop daemon
- Auto-spawns persistent background daemon listening on secure Unix domain socket ($XDG_RUNTIME_DIR/omapass.sock)
- Mutex-protected daemon lifetime and serialized startup locks (fcntl.flock)
- Keeps non-sensitive vault item metadata (IDs, titles, usernames, categories) in fast memory cache
- Ultra-responsive in-memory fuzzy search (<1ms)
- Fetches secrets (passwords, TOTPs, secure notes) only on-demand
- Pipes credentials directly to wl-copy with serialized token ownership for automatic clipboard wipe
- Robust structured error handling across both daemon and direct fallback execution
- Preserves intentional whitespace in credentials
- Optional autotype into active Wayland window via wtype
- Supports direct CLI invocations for easy testing and terminal usage
"""

import argparse
import fcntl
import json
import os
import pathlib
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

PLUGIN_ID = "gg.omapass"
DEFAULT_CLIPBOARD_TIMEOUT = 30
MAX_CLIPBOARD_TIMEOUT = 3600

# How long a decrypted item stays in the daemon's memory before it is refetched.
# Short on purpose: it bounds how long plaintext secrets live in RAM and keeps
# TOTP codes from going stale (a code is only valid for a 30s window).
DETAILS_CACHE_TTL = 90.0

# How long a cached "vault is unlocked" verdict survives a daemon restart.
# The on-disk cache lives in the tmpfs runtime dir, so it never outlives a
# login, but 1Password locks itself on idle long before that.
UNLOCK_CACHE_TTL = 8 * 3600.0

# Largest single RPC request the daemon will buffer, so a local client cannot
# grow the daemon's memory without bound.
MAX_REQUEST_BYTES = 1 << 20

# Substrings 1Password uses when the CLI is not authorized to read the vault.
AUTH_ERROR_MARKERS = (
    "not currently signed in",
    "not signed in",
    "session expired",
    "authorization prompt",
    "no account found",
    "you are not authorized",
    "is locked",
    "vault is locked",
    "connect to the 1password app",
)

# Backoff between clipboard-clear attempts, in seconds. The trailing 0 is the
# final attempt, after which the user is warned rather than left unaware that a
# credential is still on the clipboard.
WIPE_RETRY_DELAYS = (0.5, 2.0, 5.0, 15.0, 0)

# Schemes an item URL may be opened with. Everything else (file:, javascript:,
# and friends) is refused rather than handed to xdg-open.
ALLOWED_URL_SCHEMES = ("http", "https")


def _is_private_dir(path: pathlib.Path) -> bool:
    """True when path is a real directory owned by us and closed to others."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return (
        stat.S_ISDIR(st.st_mode)
        and st.st_uid == os.getuid()
        and not (st.st_mode & 0o077)
    )


def runtime_dir() -> pathlib.Path:
    """Returns secure user runtime directory.

    XDG_RUNTIME_DIR is checked rather than trusted: everything here has a
    predictable name, so a world-writable or foreign-owned directory would let
    another user pre-create our files and read what lands in them.
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg and _is_private_dir(pathlib.Path(xdg)):
        return pathlib.Path(xdg)

    fallback = pathlib.Path(f"/tmp/omapass-{os.getuid()}")
    fallback.mkdir(mode=0o700, parents=True, exist_ok=True)
    # A pre-existing /tmp path could be a symlink or another user's directory.
    st = os.lstat(fallback)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise RuntimeError(f"{fallback} is not a directory owned by this user")
    if st.st_mode & 0o077:
        os.chmod(fallback, 0o700)
        st = os.lstat(fallback)
    if not _is_private_dir(fallback):
        raise RuntimeError(f"{fallback} could not be made private")
    return fallback


def write_private(path: pathlib.Path, text: str) -> None:
    """Writes text to path, 0600 from the moment the file exists.

    O_NOFOLLOW so a symlink left in our place is refused rather than followed.
    """
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def open_private(path: pathlib.Path):
    """Opens (creating if needed) a 0600 lock file for flock."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    return os.fdopen(fd, "w")


def is_our_wipe_process(pid: int) -> bool:
    """True when pid is one of our own clipboard-wipe children.

    Guards against PID reuse: without this a recycled pid means we SIGTERM an
    unrelated process of the user's.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().decode("utf-8", "replace")
    except OSError:
        return False
    return "omapass" in cmdline and "_wipe" in cmdline


def socket_path() -> pathlib.Path:
    return runtime_dir() / "omapass.sock"


def cache_path() -> pathlib.Path:
    return runtime_dir() / "omapass-cache.json"


def daemon_lock_path() -> pathlib.Path:
    return runtime_dir() / "omapass-daemon.lock"


def startup_lock_path() -> pathlib.Path:
    return runtime_dir() / "omapass-startup.lock"


def clipboard_lock_path() -> pathlib.Path:
    return runtime_dir() / "omapass-clipboard.lock"


def wipe_pid_path() -> pathlib.Path:
    return runtime_dir() / "omapass-wipe.pid"


def token_path() -> pathlib.Path:
    return runtime_dir() / "omapass-clipboard.token"


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


class OmaPassService:
    def __init__(self):
        self.lock = threading.RLock()
        self.clipboard_lock = threading.Lock()
        self.items: List[Dict[str, Any]] = []
        self.item_details_cache: Dict[str, Any] = {}
        self.last_sync_time: float = 0
        self.is_unlocked: bool = False
        self.account_info: Dict[str, Any] = {}
        self.last_status_check: float = 0
        # Sticky across daemon restarts: once op has told us we are not
        # authorized, a merely recent metadata cache must not undo that.
        self.auth_failed: bool = False
        # Incremented by lock_vault. Results fetched before the bump belong to
        # a session that is over and are discarded.
        self.lock_epoch: int = 0
        # Spawned clipboard-wipe children. The daemon is long-lived, so
        # something has to wait() on them or every copy leaves a zombie.
        self._wipe_procs: List[subprocess.Popen] = []
        self._load_cache()

    def reap_wipe_children(self) -> None:
        """Reaps finished clipboard-wipe children."""
        with self.lock:
            self._wipe_procs = [p for p in self._wipe_procs if p.poll() is None]

    def _load_cache(self) -> None:
        """Loads cached metadata from secure runtime file if available.

        A cache alone is not proof the vault is still unlocked, so the unlocked
        verdict is only carried over while the cache is fresh; anything older is
        treated as unknown and re-verified on the next forced status check.
        """
        c = cache_path()
        if not c.exists():
            return
        try:
            with open(c, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            self.items = []
            return

        if not isinstance(data, dict):
            return

        items = data.get("items")
        self.items = items if isinstance(items, list) else []
        try:
            self.last_sync_time = float(data.get("timestamp") or 0)
        except (TypeError, ValueError):
            self.last_sync_time = 0
        account_info = data.get("account_info")
        self.account_info = account_info if isinstance(account_info, dict) else {}
        self.auth_failed = bool(data.get("authFailed", False))

        if (
            self.items
            and not self.auth_failed
            and (time.time() - self.last_sync_time) < UNLOCK_CACHE_TTL
        ):
            self.is_unlocked = True
            self.last_status_check = time.time()

    def _save_cache(self) -> None:
        """Persists non-sensitive metadata cache with 0600 permissions."""
        c = cache_path()
        try:
            temp_file = c.with_suffix(".tmp")
            payload = {
                "timestamp": self.last_sync_time,
                "items": self.items,
                "account_info": self.account_info,
                "authFailed": self.auth_failed,
            }
            write_private(temp_file, json.dumps(payload))
            temp_file.replace(c)
        except Exception:
            pass

    def _note_op_error(self, stderr: str) -> None:
        """Flips the cached unlocked verdict when op reports an authorization failure.

        Without this the widget keeps claiming "unlocked" after 1Password has
        locked itself, and every copy fails with no visible explanation.
        """
        blob = (stderr or "").lower()
        if any(marker in blob for marker in AUTH_ERROR_MARKERS):
            with self.lock:
                self.is_unlocked = False
                self.auth_failed = True
                self.last_status_check = time.time()
                self.item_details_cache.clear()
                # Persisted, or a daemon restart would read the still-recent
                # metadata cache and go back to claiming "unlocked".
                self._save_cache()

    def _note_op_success(self) -> None:
        """Records that op served a request, undoing an earlier auth failure.

        Retrieval succeeding is proof of authorization, so a copy that works
        after re-authenticating must not leave the widget stuck on "locked".
        """
        with self.lock:
            if self.is_unlocked and not self.auth_failed:
                return
            self.is_unlocked = True
            self.auth_failed = False
            self.last_status_check = time.time()
            self._save_cache()

    def _cached_details(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Returns still-fresh cached item details, dropping them once expired."""
        with self.lock:
            entry = self.item_details_cache.get(item_id)
            if not entry or "data" not in entry:
                return None
            if (time.time() - entry.get("timestamp", 0)) > DETAILS_CACHE_TTL:
                self.item_details_cache.pop(item_id, None)
                return None
            return entry["data"]

    def purge_expired_details(self) -> None:
        """Drops decrypted items whose TTL has passed so secrets do not linger in RAM."""
        now = time.time()
        with self.lock:
            for key in [
                k for k, v in self.item_details_cache.items()
                if (now - v.get("timestamp", 0)) > DETAILS_CACHE_TTL
            ]:
                self.item_details_cache.pop(key, None)

    def _clear_cache(self) -> None:
        """Clears memory and disk cache."""
        with self.lock:
            self.items = []
            self.item_details_cache.clear()
            self.last_sync_time = 0
            self.is_unlocked = False
            self.account_info = {}
            self.last_status_check = 0
            c = cache_path()
            if c.exists():
                try:
                    c.unlink()
                except OSError:
                    pass

    def check_op_installed(self) -> bool:
        return shutil.which("op") is not None

    def get_account_info_fast(self) -> Optional[Dict[str, str]]:
        """Quickly retrieves account info via `op account list` which never prompts on desktop."""
        try:
            res = subprocess.run(
                ["op", "account", "list", "--format=json"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2.5,
            )
            if res.returncode == 0:
                accounts = json.loads(res.stdout)
                if accounts and isinstance(accounts, list):
                    acc = accounts[0]
                    return {
                        "email": acc.get("email", ""),
                        "url": acc.get("url", ""),
                        "user_id": acc.get("user_uuid", ""),
                        "name": acc.get("email", ""),
                    }
        except Exception:
            pass
        return None

    def get_status(self, force: bool = False) -> Dict[str, Any]:
        """Checks 1Password CLI status and unlock state with fast cache."""
        now = time.time()
        with self.lock:
            # The cached unlocked verdict expires in a running daemon too, not
            # only across a restart: 1Password locks itself on idle long before
            # this, and a daemon that never restarts would otherwise claim
            # "unlocked" indefinitely.
            if (
                self.is_unlocked
                and self.last_status_check > 0
                and (now - self.last_status_check) > UNLOCK_CACHE_TTL
            ):
                self.is_unlocked = False
                self.item_details_cache.clear()

            # Once unlocked, return cached state immediately. Never poll `op` in background!
            if not force and self.is_unlocked:
                return {
                    "ok": True,
                    "installed": True,
                    "unlocked": True,
                    "account": self.account_info.get("email", ""),
                    "name": self.account_info.get("name", ""),
                    "server": self.account_info.get("url", ""),
                    "userId": self.account_info.get("user_id", ""),
                    "itemCount": len(self.items),
                    "lastSync": self.last_sync_time,
                }

        if not self.check_op_installed():
            return {
                "ok": False,
                "installed": False,
                "unlocked": False,
                "error": "1Password CLI (op) is not installed.",
            }

        # An unforced status must never reach a command that can prompt. Only
        # `op account list` is safe; everything below it blocks on the desktop
        # app's authorization dialog, which is what "Unlock" is for. Falling
        # through here used to hang an unforced check for the full timeout.
        if not force and not self.is_unlocked:
            fast_info = self.get_account_info_fast()
            with self.lock:
                if fast_info:
                    self.account_info = fast_info
                return {
                    "ok": True,
                    "installed": True,
                    "unlocked": False,
                    "account": self.account_info.get("email", ""),
                    "name": self.account_info.get("name", ""),
                    "server": self.account_info.get("url", ""),
                    "userId": self.account_info.get("user_id", ""),
                    "itemCount": len(self.items),
                    "lastSync": self.last_sync_time,
                    "message": "Vault is locked or unauthorized.",
                }

        try:
            # Check status via `op user get --me` (1Password desktop app integration)
            res = subprocess.run(
                ["op", "user", "get", "--me", "--format=json"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=12.0,
            )
            # Fallback for standalone CLI session tokens
            if res.returncode != 0:
                res = subprocess.run(
                    ["op", "whoami", "--format=json"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=4.0,
                )

            if res.returncode == 0:
                try:
                    info = json.loads(res.stdout)
                    email = info.get("email", "")
                    url = info.get("url", "")
                    user_id = info.get("id") or info.get("user_id", "")
                    name = info.get("name", "")
                except Exception:
                    email = res.stdout.strip()
                    url = ""
                    user_id = ""
                    name = ""

                with self.lock:
                    self.is_unlocked = True
                    self.auth_failed = False
                    self.last_status_check = now
                    self.account_info = {
                        "email": email,
                        "url": url,
                        "user_id": user_id,
                        "name": name,
                    }
                    has_items = bool(self.items)

                if not has_items:
                    threading.Thread(target=self.sync, daemon=True).start()

                with self.lock:
                    return {
                        "ok": True,
                        "installed": True,
                        "unlocked": True,
                        "account": email,
                        "name": name,
                        "server": url,
                        "userId": user_id,
                        "itemCount": len(self.items),
                        "lastSync": self.last_sync_time,
                    }
            else:
                with self.lock:
                    self.is_unlocked = False
                    self.last_status_check = now
                    return {
                        "ok": True,
                        "installed": True,
                        "unlocked": False,
                        "itemCount": len(self.items),
                        "lastSync": self.last_sync_time,
                        "message": "Vault is locked or unauthorized.",
                    }
        except subprocess.TimeoutExpired:
            with self.lock:
                if self.is_unlocked and (now - self.last_status_check < 120.0):
                    return {
                        "ok": True,
                        "installed": True,
                        "unlocked": True,
                        "account": self.account_info.get("email", ""),
                        "name": self.account_info.get("name", ""),
                        "server": self.account_info.get("url", ""),
                        "userId": self.account_info.get("user_id", ""),
                        "itemCount": len(self.items),
                        "lastSync": self.last_sync_time,
                    }
                return {
                    "ok": True,
                    "installed": True,
                    "unlocked": False,
                    "itemCount": len(self.items),
                    "lastSync": self.last_sync_time,
                    "message": "Status check timed out.",
                }
        except Exception as e:
            return {
                "ok": False,
                "installed": True,
                "unlocked": False,
                "error": str(e),
            }

    def unlock(self) -> Dict[str, Any]:
        """Triggers 1Password unlock prompt."""
        if shutil.which("1password"):
            try:
                subprocess.Popen(
                    ["1password", "--quick-access"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {"ok": True, "method": "desktop", "message": "Triggered 1Password quick access"}
            except Exception:
                pass

        term = os.environ.get("TERMINAL")
        if not term:
            for candidate in ["alacritty", "foot", "kitty", "ghostty", "xterm"]:
                if shutil.which(candidate):
                    term = candidate
                    break

        if term:
            try:
                cmd = "op signin && sleep 1"
                subprocess.Popen(
                    [term, "-e", "bash", "-c", cmd],
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {"ok": True, "method": "terminal", "message": f"Launched terminal signin via {term}"}
            except Exception:
                pass

        return {"ok": False, "error": "Could not launch unlock prompt."}

    def lock_vault(self) -> Dict[str, Any]:
        """Locks 1Password, immediately clears clipboard, and clears metadata cache."""
        cleared = wipe_clipboard_now()
        with self.lock:
            # Anything already in flight belongs to the session being locked.
            # Bumping the epoch makes those results unusable, so a fetch that
            # lands after the lock cannot repopulate decrypted fields or flip
            # the service back to unlocked.
            self.lock_epoch += 1
            self._clear_cache()

        if shutil.which("1password"):
            try:
                subprocess.run(["1password", "--lock"], check=False, timeout=3.0)
            except Exception:
                pass

        try:
            subprocess.run(["op", "signout"], check=False, timeout=3.0)
        except Exception:
            pass

        if not cleared:
            warn_clipboard_not_cleared()
            return {
                "ok": False,
                "error": "Vault locked and cache cleared, but the clipboard could not be cleared.",
            }
        return {"ok": True, "message": "Vault locked, clipboard wiped, and cache cleared."}

    def sync(self) -> Dict[str, Any]:
        """Syncs item metadata from 1Password into memory cache."""
        if not self.check_op_installed():
            return {"ok": False, "error": "op CLI not installed"}

        try:
            res = subprocess.run(
                ["op", "item", "list", "--format=json"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30.0,
            )
            if res.returncode != 0:
                self._note_op_error(res.stderr)
                return {
                    "ok": False,
                    "error": res.stderr.strip() or "Failed to list items from 1Password",
                }

            raw_items = json.loads(res.stdout)
            parsed: List[Dict[str, Any]] = []

            for item in raw_items:
                item_id = item.get("id", "")
                if not item_id:
                    continue

                title = item.get("title", "Untitled")
                category = item.get("category", "OTHER").upper()
                username = item.get("additional_information", "")
                vault_info = item.get("vault", {})
                vault_name = vault_info.get("name", "") if isinstance(vault_info, dict) else ""
                favorite = bool(item.get("favorite", False))

                urls_list: List[str] = []
                for u in item.get("urls", []):
                    if isinstance(u, dict) and u.get("href"):
                        urls_list.append(u["href"])
                primary_url = urls_list[0] if urls_list else ""

                parsed.append({
                    "id": item_id,
                    "title": title,
                    "category": category,
                    "username": username,
                    "url": primary_url,
                    "vault": vault_name,
                    "favorite": favorite,
                    "updatedAt": item.get("updated_at", ""),
                })

            with self.lock:
                self.items = parsed
                self.last_sync_time = time.time()
                self.is_unlocked = True
                self.auth_failed = False
                self.last_status_check = time.time()
                self._save_cache()

            return {"ok": True, "count": len(parsed)}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Listing items timed out"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _subsequence_match(query: str, haystack: str) -> bool:
        """True when every character of query appears in haystack, in order.

        This is what makes "gthb" find "GitHub" - substring matching alone
        cannot, and the README promises fuzzy search.
        """
        if not query or not haystack:
            return False
        pos = 0
        for ch in query:
            pos = haystack.find(ch, pos)
            if pos < 0:
                return False
            pos += 1
        return True

    def search_items(self, query: str = "", category: str = "", limit: int = 50) -> Dict[str, Any]:
        """Performs fast in-memory search and category filtering."""
        with self.lock:
            items = list(self.items)

        q = query.strip().lower()
        cat = category.strip().upper()

        results: List[Tuple[int, Dict[str, Any]]] = []

        for item in items:
            item_cat = item.get("category", "")
            if cat and cat != "ALL":
                if cat == "FAVORITES":
                    if not item.get("favorite"):
                        continue
                elif cat == "LOGINS":
                    if item_cat not in ("LOGIN", "PASSWORD"):
                        continue
                elif cat == "CARDS":
                    if item_cat not in ("CREDIT_CARD", "BANK_ACCOUNT"):
                        continue
                elif cat == "NOTES":
                    if item_cat not in ("SECURE_NOTE", "DOCUMENT"):
                        continue
                elif cat != item_cat:
                    continue

            if not q:
                score = 100 if item.get("favorite") else 10
                results.append((score, item))
                continue

            title = item.get("title", "").lower()
            username = item.get("username", "").lower()
            url = item.get("url", "").lower()
            vault = item.get("vault", "").lower()

            score = 0
            if title == q:
                score = 1000
            elif title.startswith(q):
                score = 500
            elif q in title:
                score = 200
            elif username.startswith(q):
                score = 150
            elif q in username:
                score = 100
            elif q in url:
                score = 50
            elif q in vault:
                score = 20
            elif len(q) >= 3 and self._subsequence_match(q, title):
                # Titles only, and only for queries long enough to be
                # discriminating: a subsequence match on usernames turned
                # "gthb" into a match for every "Signs in with GitHub" item.
                score = 10

            if score > 0:
                if item.get("favorite"):
                    score += 50
                results.append((score, item))

        results.sort(key=lambda pair: (-pair[0], pair[1].get("title", "").lower()))
        matched = [r[1] for r in results[:limit]]

        return {
            "ok": True,
            "items": matched,
            "totalMatched": len(results),
            "totalItems": len(items),
        }

    # How each requested field is recognised, strongest signal first.
    #   ids      exact field id, the canonical 1Password name
    #   purposes op's own semantic tag
    #   types    op's field type
    #   weak     substrings, only consulted when nothing stronger matched
    # Scoring rather than first-match matters: an item with a
    # "verification_url" field used to beat its real "cvv", and a "validFrom"
    # of type MONTH_YEAR used to beat the actual expiry.
    FIELD_RULES: Dict[str, Dict[str, Any]] = {
        "password": {"ids": {"password"}, "purposes": {"PASSWORD"}, "weak": ("password", "passphrase", "pin")},
        "username": {"ids": {"username"}, "purposes": {"USERNAME"}, "weak": ("username", "login name", "email")},
        "ccnum": {"ids": {"ccnum"}, "types": {"CREDIT_CARD_NUMBER"}, "weak": ("card number", "cardnumber")},
        "cvv": {"ids": {"cvv"}, "weak": ("cvv", "security code", "verification code", "card verification")},
        "cardholder": {"ids": {"cardholder"}, "weak": ("cardholder", "name on card")},
        "expiry": {"ids": {"expiry", "expires"}, "weak": ("expiry", "expiration"), "types": {"MONTH_YEAR"}},
        "accountNo": {"ids": {"accountno", "accountnumber"}, "weak": ("account number", "account no")},
        "owner": {"ids": {"owner", "accountholder"}, "weak": ("account holder", "owner")},
        "pin": {"ids": {"pin"}, "weak": ("pin",)},
    }

    @classmethod
    def _field_score(cls, f: Dict[str, Any], field: str) -> int:
        """Ranks one item field as a candidate for `field`. 0 means no match."""
        rule = cls.FIELD_RULES.get(field)
        fid = str(f.get("id", "")).lower()
        label = str(f.get("label", "")).lower()
        purpose = str(f.get("purpose", "")).upper()
        ftype = str(f.get("type", "")).upper()

        if rule is None:
            return 3 if fid == field or label == field else 0

        # Every signal is scored and the best wins. Returning on the first
        # hit let a weak signal shadow a stronger one: two MONTH_YEAR fields
        # both scored 1, so "valid from" could answer a request for "expiry".
        hay = label + " " + fid
        score = 0
        if fid in rule.get("ids", ()) or label in rule.get("ids", ()):
            score = max(score, 4)
        if purpose in rule.get("purposes", ()):
            score = max(score, 3)
        if any(token in hay for token in rule.get("weak", ())):
            score = max(score, 2)
        if ftype in rule.get("types", ()):
            # A type alone is the weakest evidence: MONTH_YEAR is worn by
            # validFrom as well as expiry.
            score = max(score, 1)
        return score

    @classmethod
    def _match_field(cls, item_data: Dict[str, Any], field: str) -> Optional[str]:
        """Picks a named credential out of parsed item details.

        Matching is by score, not by position: 1Password lets the user rename
        any field, so a login's password can be labelled "admin key", while an
        unrelated field can carry a word that merely looks right.
        """
        if field == "notes":
            return str(item_data.get("notes") or "") or None

        best_score = 0
        best_value: Optional[str] = None
        for f in item_data.get("fields", []):
            score = cls._field_score(f, field)
            if score > best_score:
                value = str(f.get("value", ""))
                if value:
                    best_score = score
                    best_value = value
        return best_value

    # Fields the widget can ask for. `otp` is always fetched live; the rest
    # come from the parsed item, because `op item get --fields label=X` only
    # finds fields the user happened to label exactly that.
    SUPPORTED_FIELDS = (
        "password", "username", "otp", "ccnum", "cvv", "cardholder", "expiry", "notes",
        "accountNo", "owner", "pin",
    )

    # A field may also be named by its own id, so the details view can copy a
    # custom field without shipping its plaintext back to us. Bounded and
    # conservative: this only ever selects a field of an item we already hold.
    FIELD_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

    def fetch_field(self, item_id: str, field: str) -> Tuple[bool, str]:
        """Fetches a specific secret or credential field from 1Password without trimming whitespace."""
        if not self.check_op_installed():
            return False, "op CLI not installed"
        if not item_id:
            return False, "No item id provided"

        field = field.lower().strip()

        if field not in [f.lower() for f in self.SUPPORTED_FIELDS] and not self.FIELD_ID_RE.match(field):
            return False, f"Unsupported field '{field}'"

        # A TOTP code is only valid for its 30s window, so it is never served
        # from cache: always ask op for a fresh one.
        if field == "otp":
            try:
                res = subprocess.run(
                    ["op", "item", "get", item_id, "--otp"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=8.0,
                )
            except subprocess.TimeoutExpired:
                return False, "Retrieving otp timed out"
            except Exception as e:
                return False, str(e)

            if res.returncode == 0:
                code = res.stdout.removesuffix("\r\n").removesuffix("\n")
                if code:
                    self._note_op_success()
                    return True, code
                return False, "This item has no one-time password"
            self._note_op_error(res.stderr)
            return False, res.stderr.strip() or "Failed to retrieve otp"

        cached = self._cached_details(item_id)
        if cached:
            value = self._match_field(cached, field)
            if value:
                return True, value

        details = self.get_item(item_id)
        if not details.get("ok"):
            return False, str(details.get("error") or f"Failed to retrieve {field}")

        value = self._match_field(details["item"], field)
        if value:
            return True, value
        return False, f"No {field} field on this item"

    def copy_to_clipboard(
        self,
        item_id: str = "",
        field: str = "",
        title: str = "",
        value: str = "",
        timeout_seconds: int = DEFAULT_CLIPBOARD_TIMEOUT,
    ) -> Dict[str, Any]:
        """Writes field or raw value to wl-copy and serializes token/timer publication under flock."""
        with self.lock:
            epoch = self.lock_epoch

        if not value:
            if not item_id or not field:
                return {"ok": False, "error": "No value or item_id provided"}
            ok, fetched = self.fetch_field(item_id, field)
            if not ok:
                return {"ok": False, "error": fetched}
            value = fetched

        with self.lock:
            if epoch != self.lock_epoch:
                return {"ok": False, "error": "Vault was locked during this request"}

        if not shutil.which("wl-copy"):
            return {"ok": False, "error": "wl-copy is not installed"}

        try:
            timeout_seconds = int(timeout_seconds)
        except (TypeError, ValueError):
            timeout_seconds = DEFAULT_CLIPBOARD_TIMEOUT
        timeout_seconds = max(0, min(timeout_seconds, MAX_CLIPBOARD_TIMEOUT))

        self.reap_wipe_children()

        clp = clipboard_lock_path()
        with self.clipboard_lock:
            with open_private(clp) as clf:
                fcntl.flock(clf, fcntl.LOCK_EX)
                try:
                    try:
                        proc = subprocess.Popen(
                            ["wl-copy"],
                            stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        proc.communicate(input=value.encode("utf-8"), timeout=2.0)
                        if proc.returncode != 0:
                            return {"ok": False, "error": f"wl-copy failed with exit status {proc.returncode}"}
                    except subprocess.TimeoutExpired:
                        # It may already own the selection, or be about to.
                        # Kill it and clear, rather than leaving an unreaped
                        # process holding a credential.
                        try:
                            proc.kill()
                            proc.communicate(timeout=1.0)
                        except Exception:
                            pass
                        clear_clipboard_once()
                        return {"ok": False, "error": "wl-copy timed out; clipboard cleared"}
                    except Exception as e:
                        return {"ok": False, "error": f"Failed to run wl-copy: {e}"}

                    # Kill previous wipe process if running
                    wp = wipe_pid_path()
                    if wp.exists():
                        try:
                            old_pid = int(wp.read_text().strip())
                            if is_our_wipe_process(old_pid):
                                os.kill(old_pid, signal.SIGTERM)
                        except (OSError, ValueError):
                            pass
                        try:
                            wp.unlink()
                        except OSError:
                            pass

                    # Everything copied out of a password manager gets wiped:
                    # a URL or note pulled from a vault item is still the user's
                    # data, and the UI promises a wipe for every copy.
                    tp = token_path()
                    if timeout_seconds > 0:
                        token = f"{time.time()}-{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}"
                        unique_tmp = tp.parent / f"omapass-token.{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}.tmp"

                        # The token travels in the environment, not argv:
                        # /proc/<pid>/cmdline is world-readable, environ is not.
                        wipe_env = dict(os.environ, OMAPASS_WIPE_TOKEN=token)
                        try:
                            write_private(unique_tmp, token)
                            unique_tmp.replace(tp)
                            wipe_proc = subprocess.Popen(
                                [
                                    sys.executable,
                                    str(pathlib.Path(__file__).resolve()),
                                    "_wipe",
                                    str(timeout_seconds),
                                ],
                                env=wipe_env,
                                start_new_session=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL,
                            )
                            write_private(wp, str(wipe_proc.pid))
                            with self.lock:
                                self._wipe_procs.append(wipe_proc)
                        except Exception as e:
                            # The wipe could not be armed. Never leave a secret
                            # on the clipboard while reporting a timeout we
                            # cannot honour: clear it now and fail loudly.
                            try:
                                unique_tmp.unlink()
                            except OSError:
                                pass
                            cleared = clear_clipboard_once()
                            if cleared:
                                try:
                                    tp.unlink()
                                except OSError:
                                    pass
                                return {
                                    "ok": False,
                                    "error": f"Could not schedule clipboard wipe, clipboard cleared: {e}",
                                }
                            # Clearing failed too. Keep the token so a later
                            # copy or lock still knows a secret is out there,
                            # and say so instead of implying it is gone.
                            warn_clipboard_not_cleared()
                            return {
                                "ok": False,
                                "error": (
                                    "Could not schedule clipboard wipe and could not clear "
                                    f"the clipboard: {e}. The credential is still on the clipboard."
                                ),
                            }
                    else:
                        if tp.exists():
                            try:
                                tp.unlink()
                            except OSError:
                                pass
                finally:
                    try:
                        fcntl.flock(clf, fcntl.LOCK_UN)
                    except OSError:
                        pass

        # Desktop notification
        label = "One-Time Password (TOTP)" if field == "otp" else (field.capitalize() if field else "Credential")
        name = title or "credential"
        msg = f"Copied {label} for '{name}' to clipboard."
        if timeout_seconds > 0:
            msg += f" Clears in {timeout_seconds}s."

        try:
            subprocess.run(
                [
                    "notify-send",
                    "-a", "OmaPass",
                    "-i", "dialog-password",
                    "-t", "3000",
                    "1Password",
                    msg,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass

        return {"ok": True, "field": field, "title": title, "expiresIn": timeout_seconds}

    def type_credentials(self, item_id: str = "", field: str = "", value: str = "") -> Dict[str, Any]:
        """Types credentials into active window using wtype with a brief focus delay."""
        if not shutil.which("wtype"):
            return {"ok": False, "error": "wtype is not installed."}

        if not value:
            if not item_id or not field:
                return {"ok": False, "error": "No value or item_id provided"}
            ok, fetched = self.fetch_field(item_id, field)
            if not ok:
                return {"ok": False, "error": fetched}
            value = fetched

        def _do_type():
            time.sleep(0.35)
            try:
                # `wtype -` reads the text from stdin. Passing it as an
                # argument would publish the credential in the process list
                # for as long as wtype runs.
                proc = subprocess.Popen(
                    ["wtype", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.communicate(input=value.encode("utf-8"), timeout=5.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        threading.Thread(target=_do_type, daemon=True).start()
        return {"ok": True, "typed": field or "value"}

    def get_item(self, item_id: str) -> Dict[str, Any]:
        """Fetches full item details (fields, urls, notes) with in-memory caching."""
        if not item_id:
            return {"ok": False, "error": "No item id provided"}
        if not self.check_op_installed():
            return {"ok": False, "error": "op CLI not installed"}

        cached = self._cached_details(item_id)
        if cached:
            return {"ok": True, "item": cached}

        with self.lock:
            epoch = self.lock_epoch

        try:
            res = subprocess.run(
                ["op", "item", "get", item_id, "--format=json"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=12.0,
            )
            if res.returncode != 0:
                self._note_op_error(res.stderr)
                return {"ok": False, "error": res.stderr.strip() or "Failed to retrieve item details"}

            raw = json.loads(res.stdout)
            title = raw.get("title", "Untitled")
            category = raw.get("category", "OTHER").upper()
            vault_info = raw.get("vault", {})
            vault_name = vault_info.get("name", "") if isinstance(vault_info, dict) else ""
            favorite = bool(raw.get("favorite", False))
            updated_at = raw.get("updated_at", "")

            urls_list: List[Dict[str, Any]] = []
            for u in raw.get("urls", []):
                if isinstance(u, dict) and u.get("href"):
                    href_str = str(u["href"]).strip()
                    if href_str:
                        urls_list.append({
                            "label": u.get("label", "website"),
                            "href": href_str,
                            "primary": bool(u.get("primary", False)),
                        })

            fields_list: List[Dict[str, Any]] = []
            notes_text = ""
            has_otp = False
            totp_code = ""

            for f in raw.get("fields", []):
                if not isinstance(f, dict):
                    continue
                fid = f.get("id", "")
                ftype = f.get("type", "STRING")
                fpurpose = f.get("purpose", "")
                flabel = f.get("label", fid)
                fval = f.get("value", "")
                fsection = ""
                sec = f.get("section")
                if isinstance(sec, dict):
                    fsection = sec.get("label", "")

                if fpurpose == "NOTES" or fid == "notesPlain" or flabel.lower() in ("notes", "secure notes"):
                    if fval and not notes_text:
                        notes_text = str(fval)
                    continue

                if ftype == "OTP":
                    has_otp = True
                    if f.get("totp"):
                        totp_code = str(f.get("totp"))

                # An OTP field's value is the otpauth:// URI, which embeds the
                # long-lived shared secret: more sensitive than the password,
                # and it was being displayed in the clear.
                concealed = (
                    ftype in ("CONCEALED", "CREDIT_CARD_NUMBER", "OTP")
                    or fpurpose in ("PASSWORD",)
                )

                # Filter out any field with no content. The value itself is
                # kept verbatim: these are credentials, and a password may end
                # in a space on purpose - only the emptiness test is trimmed.
                raw_val = str(fval) if fval is not None else ""
                if not raw_val.strip():
                    continue

                display_label = flabel or fid
                # Clean up ugly field IDs like "customField1" if label is missing
                fields_list.append({
                    "id": fid,
                    "label": display_label,
                    "value": raw_val,
                    "type": ftype,
                    "purpose": fpurpose,
                    "section": fsection,
                    "concealed": concealed,
                })

            def _field_priority(f: Dict[str, Any]) -> int:
                fid = f.get("id", "").lower()
                purpose = f.get("purpose", "").upper()
                lbl = f.get("label", "").lower()
                if purpose == "USERNAME" or "username" in fid or "email" in fid or "user" in fid or "login" in fid:
                    return 0
                if purpose == "PASSWORD" or "password" in fid or "pin" in fid or "pass" in lbl:
                    return 1
                if "ccnum" in fid or "card" in fid or "cardnumber" in lbl:
                    return 2
                if "expiry" in fid or "exp" in fid or "month" in fid or "year" in fid:
                    return 3
                if "cvv" in fid or "verification" in fid or "security" in lbl:
                    return 4
                if "cardholder" in fid or "holder" in lbl:
                    return 5
                return 10

            fields_list.sort(key=_field_priority)

            item_data = {
                "id": item_id,
                "title": title,
                "category": category,
                "vault": vault_name,
                "favorite": favorite,
                "updatedAt": updated_at,
                "urls": urls_list,
                "notes": notes_text,
                "fields": fields_list,
                "totp": totp_code,
                # Survives caching, unlike the code itself, so the UI still
                # knows to show the banner and fetch a live code on reopen.
                "hasTotp": bool(has_otp or totp_code),
            }

            with self.lock:
                if epoch != self.lock_epoch:
                    # The vault was locked while this was in flight. Dropping
                    # the result keeps lock meaning "the secrets are gone".
                    return {"ok": False, "error": "Vault was locked during this request"}
                # The cache never keeps a one-time code. It is valid for one
                # 30s window, so a cached copy is stale by definition, and
                # holding it only widens the window in which it can leak. The
                # caller gets this one; the next code is fetched live.
                cached_copy = dict(item_data)
                cached_copy["totp"] = ""
                self.item_details_cache[item_id] = {
                    "timestamp": time.time(),
                    "data": cached_copy,
                }
            self._note_op_success()

            return {"ok": True, "item": item_data}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Fetching item details timed out"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_desktop(self, item_id: str) -> Dict[str, Any]:
        """Opens item in 1Password desktop app via onepassword URI."""
        uri = f"onepassword://item?i={item_id}"
        try:
            subprocess.Popen(["xdg-open", uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def normalize_url(url: str) -> Optional[str]:
        """Returns a safe http(s) URL, or None if it cannot be one.

        1Password items routinely store bare hosts ("github.com"), which
        xdg-open cannot resolve; anything with a scheme we do not allow
        (file:, javascript:, ...) is refused rather than launched.
        """
        candidate = (url or "").strip()
        if not candidate:
            return None
        if "://" not in candidate:
            if candidate.startswith("//"):
                candidate = "https:" + candidate
            elif ":" in candidate.split("/")[0]:
                # A scheme-like prefix with no "//" (javascript:, mailto:, ...)
                return None
            else:
                candidate = "https://" + candidate
        scheme = candidate.split("://", 1)[0].lower()
        if scheme not in ALLOWED_URL_SCHEMES:
            return None
        return candidate

    def open_url(self, url: str) -> Dict[str, Any]:
        """Opens URL in default web browser."""
        if not url:
            return {"ok": False, "error": "No URL provided"}
        target = self.normalize_url(url)
        if not target:
            return {"ok": False, "error": "Refusing to open a non-web URL"}
        try:
            subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True, "url": target}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _as_int(value: Any, fallback: int, low: int, high: int) -> int:
        """Coerces an RPC number into range instead of raising on junk input."""
        try:
            return max(low, min(int(value), high))
        except (TypeError, ValueError):
            return fallback

    def dispatch(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches an RPC action request."""
        if not isinstance(request, dict):
            return {"ok": False, "error": "Request must be a JSON object"}
        action = request.get("action", "")
        if action == "status":
            force = bool(request.get("force", False))
            return self.get_status(force=force)
        elif action == "unlock":
            return self.unlock()
        elif action == "lock":
            return self.lock_vault()
        elif action == "sync":
            return self.sync()
        elif action == "list":
            q = request.get("query", "")
            cat = request.get("category", "")
            limit = self._as_int(request.get("limit", 50), 50, 1, 1000)
            return self.search_items(query=q, category=cat, limit=limit)
        elif action == "get_item":
            item_id = request.get("id", "")
            return self.get_item(item_id=item_id)
        elif action == "otp":
            # Always a live code: a TOTP shown for longer than its window is
            # simply wrong, so this never reads the details cache.
            item_id = request.get("id", "")
            if not item_id:
                return {"ok": False, "error": "No item id provided"}
            ok, value = self.fetch_field(item_id, "otp")
            return {"ok": True, "otp": value} if ok else {"ok": False, "error": value}
        elif action == "open_desktop":
            item_id = request.get("id", "")
            return self.open_desktop(item_id=item_id)
        elif action == "copy":
            item_id = request.get("id", "")
            field = request.get("field", "password")
            title = request.get("title", "")
            value = request.get("value", "")
            timeout = self._as_int(
                request.get("timeout", DEFAULT_CLIPBOARD_TIMEOUT),
                DEFAULT_CLIPBOARD_TIMEOUT,
                0,
                MAX_CLIPBOARD_TIMEOUT,
            )
            return self.copy_to_clipboard(item_id=item_id, field=field, title=title, value=value, timeout_seconds=timeout)
        elif action == "type":
            item_id = request.get("id", "")
            field = request.get("field", "password")
            value = request.get("value", "")
            return self.type_credentials(item_id=item_id, field=field, value=value)
        elif action == "open_url":
            url = request.get("url", "")
            return self.open_url(url)
        elif action == "ping":
            return {"ok": True, "pong": time.time()}
        else:
            return {"ok": False, "error": f"Unknown action '{action}'"}


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

            script_path = pathlib.Path(__file__).resolve()
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


def run_daemon():
    """Runs OmaPass background daemon with an exclusive lifetime file lock."""
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

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

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


def main():
    parser = argparse.ArgumentParser(description="OmaPass 1Password Helper")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("serve", help="Run background socket daemon")

    wipe_parser = subparsers.add_parser("_wipe", help=argparse.SUPPRESS)
    wipe_parser.add_argument("delay", type=int)

    req_parser = subparsers.add_parser("request", help="Send JSON request")
    req_parser.add_argument(
        "json_payload",
        nargs="?",
        default="{}",
        help="JSON payload string, or '-' to read it from stdin. Prefer '-': "
             "an argument is visible to every process on the machine.",
    )

    subparsers.add_parser("status", help="Get 1Password status")
    subparsers.add_parser("unlock", help="Trigger unlock prompt")
    subparsers.add_parser("lock", help="Lock vault")
    subparsers.add_parser("sync", help="Sync item metadata")

    list_parser = subparsers.add_parser("list", help="List and search items")
    list_parser.add_argument("query", nargs="?", default="", help="Search query")
    list_parser.add_argument("--category", "-c", default="", help="Category filter")

    copy_parser = subparsers.add_parser("copy", help="Copy field to clipboard")
    copy_parser.add_argument("id", help="Item ID")
    copy_parser.add_argument("field", default="password", nargs="?", choices=["password", "username", "otp"])
    copy_parser.add_argument("--title", "-t", default="", help="Item title")
    copy_parser.add_argument("--timeout", type=int, default=DEFAULT_CLIPBOARD_TIMEOUT, help="Clipboard wipe timeout")

    type_parser = subparsers.add_parser("type", help="Type field via wtype")
    type_parser.add_argument("id", help="Item ID")
    type_parser.add_argument("field", default="password", nargs="?", choices=["password", "username"])

    args = parser.parse_args()

    if args.command == "serve":
        run_daemon()
    elif args.command == "_wipe":
        run_wipe_worker(args.delay, os.environ.get("OMAPASS_WIPE_TOKEN", ""))
    elif args.command == "request":
        payload = args.json_payload
        if payload == "-":
            payload = sys.stdin.read()
        try:
            req = json.loads(payload) if payload else {}
        except json.JSONDecodeError as e:
            print(json.dumps({"ok": False, "error": f"Invalid JSON: {e}"}))
            sys.exit(1)
        handle_request(req)
    elif args.command == "status":
        handle_request({"action": "status"})
    elif args.command == "unlock":
        handle_request({"action": "unlock"})
    elif args.command == "lock":
        handle_request({"action": "lock"})
    elif args.command == "sync":
        handle_request({"action": "sync"})
    elif args.command == "list":
        handle_request({"action": "list", "query": args.query, "category": args.category})
    elif args.command == "copy":
        handle_request({"action": "copy", "id": args.id, "field": args.field, "title": args.title, "timeout": args.timeout})
    elif args.command == "type":
        handle_request({"action": "type", "id": args.id, "field": args.field})
    else:
        handle_request({"action": "status"})


if __name__ == "__main__":
    main()
