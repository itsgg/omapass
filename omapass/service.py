"""The service: vault state, the item cache, and every action the widget can ask for.

Two rules shape this file. Decrypted material is held for as short a time as
the UI can tolerate and never written to disk. And `lock` means locked: a lock
raises an epoch, and any work already in flight is discarded rather than
allowed to land afterwards.
"""

import fcntl
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import fields
from .clipboard import (
    cancel_previous_wipe,
    clear_clipboard_once,
    warn_clipboard_not_cleared,
    wipe_clipboard_now,
)
from .config import (
    AUTH_ERROR_MARKERS,
    PROTOCOL_VERSION,
    DEFAULT_PASSWORD_RECIPE,
    PASSWORD_RECIPE_RE,
    DEFAULT_CLIPBOARD_TIMEOUT,
    DEFAULT_TOTP_PERIOD,
    DETAILS_CACHE_TTL,
    MAX_CLIPBOARD_TIMEOUT,
    UNLOCK_CACHE_TTL,
)
from .paths import (
    cache_path,
    runtime_dir,
    clipboard_lock_path,
    entry_script,
    is_our_wipe_process,
    open_private,
    token_path,
    wipe_pid_path,
    write_private,
)

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
                # Same epoch bump as a lock: revoking authorization has to
                # discard retrievals already running, or one completing a
                # moment later restores the cache and the unlocked state.
                self.lock_epoch += 1
                self.is_unlocked = False
                self.auth_failed = True
                self.last_status_check = time.time()
                self.item_details_cache.clear()
                # Persisted, or a daemon restart would read the still-recent
                # metadata cache and go back to claiming "unlocked".
                self._save_cache()

    def _note_op_success(self, epoch: Optional[int] = None) -> None:
        """Records that op served a request, undoing an earlier auth failure.

        Retrieval succeeding is proof of authorization, so a copy that works
        after re-authenticating must not leave the widget stuck on "locked".
        """
        with self.lock:
            # A result from a session that has since been locked proves nothing
            # about the current one.
            if epoch is not None and epoch != self.lock_epoch:
                return
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
            epoch = self.lock_epoch
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
                    if epoch != self.lock_epoch:
                        # Locked while this check was running.
                        return {
                            "ok": True, "installed": True, "unlocked": False,
                            "itemCount": len(self.items), "lastSync": self.last_sync_time,
                            "message": "Vault was locked during this check.",
                        }
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
                # A forced check that came back unauthorized is itself the
                # evidence. Revoke unconditionally rather than relying on
                # recognising op's wording, which varies by version.
                with self.lock:
                    self.lock_epoch += 1
                    self.is_unlocked = False
                    self.auth_failed = True
                    self.item_details_cache.clear()
                    self.last_status_check = now
                    self._save_cache()
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

    @staticmethod
    def desktop_app_running() -> bool:
        """True when the 1Password desktop app is up to answer `op`."""
        try:
            return subprocess.run(
                ["pgrep", "-x", "1password"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            ).returncode == 0
        except Exception:
            return False

    def unlock(self) -> Dict[str, Any]:
        """Puts the desktop app in a position to authorize the CLI.

        Nothing here unlocks anything by itself. What authorizes `op` is an
        `op` command running while the app is unlocked: the app then shows its
        own approval prompt, and the caller's forced status check is what
        triggers that. So this only makes sure the app is running.

        It used to launch `1password --quick-access` as well, which is the
        app's search window and authorizes nothing: the user got two windows
        and only one of them was the one to answer.
        """
        if shutil.which("1password"):
            if self.desktop_app_running():
                return {
                    "ok": True,
                    "method": "desktop",
                    "message": "Approve the OmaPass request in 1Password.",
                }
            try:
                subprocess.Popen(
                    ["1password", "--silent"],
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {
                    "ok": True,
                    "method": "desktop",
                    "message": "Started 1Password. Approve the OmaPass request when it appears.",
                }
            except Exception:
                pass

        # There is deliberately no terminal `op signin` fallback. A manual
        # sign-in exports its session token into that terminal's environment,
        # which this daemon never sees, so the user would sign in and stay
        # locked. Carrying the token across would mean writing it to disk,
        # which is exactly what this helper promises not to do.
        if not shutil.which("op"):
            return {"ok": False, "error": "1Password CLI (op) is not installed."}
        return {
            "ok": False,
            "error": "OmaPass needs the 1Password desktop app with "
                     "'Integrate with 1Password CLI' enabled.",
        }

    def lock_vault(self) -> Dict[str, Any]:
        """Locks 1Password, immediately clears clipboard, and clears metadata cache."""
        with self.lock:
            # Bumped before the clipboard is touched, not after: a copy already
            # waiting on the clipboard flock would otherwise be released the
            # moment the clear finishes and write its credential with an epoch
            # that still looked current.
            self.lock_epoch += 1
        cleared = wipe_clipboard_now()
        with self.lock:
            self._clear_cache()

        locked_app = None
        if shutil.which("1password"):
            try:
                locked_app = subprocess.run(
                    ["1password", "--lock"], check=False, timeout=3.0
                ).returncode == 0
            except Exception:
                locked_app = False

        signed_out = None
        try:
            signed_out = subprocess.run(
                ["op", "signout"], check=False, timeout=3.0
            ).returncode == 0
        except Exception:
            signed_out = False

        if not cleared:
            warn_clipboard_not_cleared()
            return {
                "ok": False,
                "error": "Cache cleared, but the clipboard could not be cleared.",
            }
        # The local cache is always dropped, but saying "vault locked" when
        # neither 1Password nor op actually locked would be a lie about where
        # the secrets are.
        attempted = [r for r in (locked_app, signed_out) if r is not None]
        if attempted and not any(attempted):
            return {
                "ok": False,
                "error": "Cache and clipboard cleared, but 1Password did not lock. "
                         "Lock it from the app to be sure.",
            }
        return {"ok": True, "message": "Vault locked, clipboard wiped, and cache cleared."}

    def sync(self) -> Dict[str, Any]:
        """Syncs item metadata from 1Password into memory cache."""
        if not self.check_op_installed():
            return {"ok": False, "error": "op CLI not installed"}

        with self.lock:
            epoch = self.lock_epoch

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
                if epoch != self.lock_epoch:
                    return {"ok": False, "error": "Vault was locked during this sync"}
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
            elif len(q) >= 3 and fields.subsequence_match(q, title):
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

    def fetch_field(self, item_id: str, field: str) -> Tuple[bool, str]:
        """Fetches a specific secret or credential field from 1Password without trimming whitespace."""
        if not self.check_op_installed():
            return False, "op CLI not installed"
        if not item_id:
            return False, "No item id provided"

        field = field.lower().strip()

        if field not in [f.lower() for f in fields.SUPPORTED_FIELDS] and not fields.FIELD_ID_RE.match(field):
            return False, f"Unsupported field '{field}'"

        # A TOTP code is only valid for its 30s window, so it is never served
        # from cache: always ask op for a fresh one.
        if field == "otp":
            with self.lock:
                epoch = self.lock_epoch
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
                    self._note_op_success(epoch)
                    return True, code
                return False, "This item has no one-time password"
            self._note_op_error(res.stderr)
            return False, res.stderr.strip() or "Failed to retrieve otp"

        cached = self._cached_details(item_id)
        if cached:
            entry = fields.match_field_entry(cached, field)
            if entry is not None:
                return self._value_or_live_code(item_id, entry)

        details = self.get_item(item_id)
        if not details.get("ok"):
            return False, str(details.get("error") or f"Failed to retrieve {field}")

        entry = fields.match_field_entry(details["item"], field)
        if entry is not None:
            return self._value_or_live_code(item_id, entry)
        if field == "notes":
            notes = str(details["item"].get("notes") or "")
            if notes:
                return True, notes
        return False, f"No {field} field on this item"

    def _value_or_live_code(self, item_id: str, entry: Dict[str, Any]) -> Tuple[bool, str]:
        """Returns a field's value, except for an OTP field.

        An OTP field's stored value is the otpauth:// URI, which carries the
        permanent shared secret. Handing that to the clipboard or to autotype
        would paste a reusable second factor into a login form, so the request
        is answered with a live code instead.
        """
        if str(entry.get("type", "")).upper() == "OTP":
            return self.fetch_field(item_id, "otp")
        return True, str(entry.get("value", ""))

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
                    # Re-checked under the lock, not just before it: a lock
                    # landing between the first check and this write would
                    # otherwise still put the credential on the clipboard.
                    with self.lock:
                        if epoch != self.lock_epoch:
                            return {"ok": False, "error": "Vault was locked during this request"}
                    try:
                        # --sensitive, always. Omarchy's clipboard plugin
                        # keeps a history in ~/.local/state, and without this
                        # hint every copied credential is written there and
                        # survives the wipe entirely. Verified: a plain copy
                        # lands in clipboard-history.json, a sensitive one
                        # does not.
                        proc = subprocess.Popen(
                            ["wl-copy", "--sensitive"],
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
                        if clear_clipboard_once():
                            return {"ok": False, "error": "wl-copy timed out; clipboard cleared"}
                        warn_clipboard_not_cleared()
                        return {
                            "ok": False,
                            "error": "wl-copy timed out and the clipboard could not be cleared. "
                                     "The credential may still be on it.",
                        }
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
                                    # The entry script, never __file__: a module
                                    # inside the package cannot be run on its own,
                                    # and Popen succeeds either way, so the copy
                                    # would promise a wipe that never happened.
                                    str(entry_script()),
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

        def _do_type():
            time.sleep(0.35)
            # Checked again on the far side of the focus delay: locking during
            # that window must stop the keystrokes, not merely precede them.
            with self.lock:
                if epoch != self.lock_epoch:
                    return
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
            totp_period = DEFAULT_TOTP_PERIOD

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
                    # The field's value is the otpauth:// URI, which carries
                    # this token's own period.
                    totp_period = fields.totp_period_from_uri(fval)

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
                "totpPeriod": totp_period,
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
            self._note_op_success(epoch)

            return {"ok": True, "item": item_data}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Fetching item details timed out"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_vaults(self) -> Dict[str, Any]:
        """Vault names and ids, for the picker on the create form."""
        if not self.check_op_installed():
            return {"ok": False, "error": "op CLI not installed"}
        try:
            res = subprocess.run(
                ["op", "vault", "list", "--format=json"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=10.0,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Listing vaults timed out"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

        if res.returncode != 0:
            self._note_op_error(res.stderr)
            return {"ok": False, "error": res.stderr.strip() or "Failed to list vaults"}

        try:
            raw = json.loads(res.stdout)
        except Exception:
            return {"ok": False, "error": "Could not read the vault list"}

        vaults = [
            {"id": v.get("id", ""), "name": v.get("name", "")}
            for v in raw if isinstance(v, dict) and v.get("name")
        ]
        self._note_op_success()
        return {"ok": True, "vaults": vaults}

    # How a spec's field type reaches op. These are 1Password's own type
    # names, so a category is described once in Model.js and needs no code.
    FIELD_TYPE_MAP = {
        "STRING": "STRING",
        "CONCEALED": "CONCEALED",
        "MONTH_YEAR": "MONTH_YEAR",
        "EMAIL": "EMAIL",
        "PHONE": "PHONE",
        "MULTILINE": "STRING",
    }

    # Categories the widget may create. Anything else is refused rather than
    # passed through to op.
    CREATABLE = ("LOGIN", "PASSWORD", "CREDIT_CARD", "SECURE_NOTE")

    def create_item(
        self,
        category: str = "LOGIN",
        title: str = "",
        item_fields: Optional[Dict[str, Any]] = None,
        url: str = "",
        vault: str = "",
        generate_field: str = "",
        password_recipe: str = DEFAULT_PASSWORD_RECIPE,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Creates an item of any supported category.

        Where the category has a field 1Password can generate (a login's
        password), `generate_field` names it and op produces and stores it, so
        no credential passes through this process at all. Everything the user
        typed travels in a 0600 template file in tmpfs rather than in argv,
        which every process on the machine can read.
        """
        if not self.check_op_installed():
            return {"ok": False, "error": "op CLI not installed"}

        category = (category or "LOGIN").strip().upper()
        if category not in self.CREATABLE:
            return {"ok": False, "error": f"Cannot create a {category} item"}

        title = (title or "").strip()
        if not title:
            return {"ok": False, "error": "An item needs a title"}

        values = dict(item_fields or {})

        def field_value(spec: Any) -> str:
            raw = spec.get("value", "") if isinstance(spec, dict) else spec
            return "" if raw is None else str(raw)

        recipe = (password_recipe or "").strip() or DEFAULT_PASSWORD_RECIPE
        # Generate only when the user left that field empty. A field carries
        # its type alongside its value, so the value has to be read out rather
        # than the whole spec stringified.
        generating = (
            bool(generate_field)
            and not field_value(values.get(generate_field, "")).strip()
        )
        if generating and not PASSWORD_RECIPE_RE.match(recipe):
            return {"ok": False, "error": f"Unsupported password recipe '{recipe}'"}

        normalized_url = fields.normalize_url(url) if url else None
        if url and not normalized_url:
            return {"ok": False, "error": "Refusing to store a non-web URL"}

        template: Dict[str, Any] = {"title": title, "category": category, "fields": []}
        for field_id, spec in values.items():
            value = field_value(spec)
            if not value:
                continue
            if generating and field_id == generate_field:
                # op is generating this one; sending a blank would override it.
                continue
            declared = (spec.get("type", "STRING") if isinstance(spec, dict) else "STRING")
            entry: Dict[str, Any] = {
                "id": field_id,
                "type": self.FIELD_TYPE_MAP.get(str(declared).upper(), "STRING"),
                "label": field_id,
                "value": value,
            }
            if field_id == "notesPlain":
                entry["purpose"] = "NOTES"
            elif field_id == "username":
                entry["purpose"] = "USERNAME"
            elif field_id == "password":
                entry["purpose"] = "PASSWORD"
            template["fields"].append(entry)

        if normalized_url:
            template["urls"] = [{"label": "website", "primary": True, "href": normalized_url}]

        with self.lock:
            epoch = self.lock_epoch

        template_path = runtime_dir() / f"omapass-new.{os.getpid()}.json"
        try:
            write_private(template_path, json.dumps(template))

            cmd = ["op", "item", "create", f"--template={template_path}", "--format=json"]
            if generating:
                cmd.append(f"--generate-password={recipe}")
            if vault:
                cmd += ["--vault", vault]
            if dry_run:
                cmd.append("--dry-run")

            try:
                res = subprocess.run(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, timeout=30.0,
                )
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": "Creating the item timed out"}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        finally:
            # Whatever the user typed has no reason to outlive the command.
            try:
                template_path.unlink()
            except OSError:
                pass

        if res.returncode != 0:
            self._note_op_error(res.stderr)
            return {"ok": False, "error": res.stderr.strip() or "Failed to create the item"}

        with self.lock:
            if epoch != self.lock_epoch:
                return {"ok": False, "error": "Vault was locked during this request"}

        try:
            created = json.loads(res.stdout)
        except Exception:
            created = {}

        self._note_op_success(epoch)
        if not dry_run:
            threading.Thread(target=self.sync, daemon=True).start()

        return {
            "ok": True,
            "dryRun": dry_run,
            "id": created.get("id", ""),
            "title": created.get("title", title),
            "vault": (created.get("vault") or {}).get("name", vault),
        }

    def create_login(
        self,
        title: str = "",
        username: str = "",
        url: str = "",
        vault: str = "",
        password_recipe: str = DEFAULT_PASSWORD_RECIPE,
        password: str = "",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Creates a login. Kept as the named path the CLI and tests use."""
        return self.create_item(
            category="LOGIN",
            title=title,
            item_fields={
                "username": {"value": username, "type": "STRING"},
                "password": {"value": password, "type": "CONCEALED"},
            },
            url=url,
            vault=vault,
            generate_field="password",
            password_recipe=password_recipe,
            dry_run=dry_run,
        )

    def open_desktop(self, item_id: str) -> Dict[str, Any]:
        """Opens item in 1Password desktop app via onepassword URI."""
        uri = f"onepassword://item?i={item_id}"
        try:
            subprocess.Popen(["xdg-open", uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_url(self, url: str) -> Dict[str, Any]:
        """Opens URL in default web browser."""
        if not url:
            return {"ok": False, "error": "No URL provided"}
        target = fields.normalize_url(url)
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
            with self.lock:
                epoch = self.lock_epoch
            ok, value = self.fetch_field(item_id, "otp")
            if not ok:
                return {"ok": False, "error": value}
            with self.lock:
                if epoch != self.lock_epoch:
                    # Copy and type check this; the standalone action did not,
                    # and handed back a live code after the vault was locked.
                    return {"ok": False, "error": "Vault was locked during this request"}
            return {"ok": True, "otp": value}
        elif action == "vaults":
            return self.list_vaults()
        elif action == "create_item":
            return self.create_item(
                category=str(request.get("category", "LOGIN")),
                title=str(request.get("title", "")),
                item_fields=request.get("fields") or {},
                url=str(request.get("url", "")),
                vault=str(request.get("vault", "")),
                generate_field=str(request.get("generateField", "")),
                password_recipe=str(request.get("recipe", DEFAULT_PASSWORD_RECIPE)),
                dry_run=bool(request.get("dryRun", False)),
            )
        elif action == "create_login":
            return self.create_login(
                title=str(request.get("title", "")),
                username=str(request.get("username", "")),
                url=str(request.get("url", "")),
                vault=str(request.get("vault", "")),
                password_recipe=str(request.get("recipe", DEFAULT_PASSWORD_RECIPE)),
                password=str(request.get("password", "")),
                dry_run=bool(request.get("dryRun", False)),
            )
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
            return {"ok": True, "pong": time.time(), "version": PROTOCOL_VERSION}
        else:
            return {"ok": False, "error": f"Unknown action '{action}'"}
