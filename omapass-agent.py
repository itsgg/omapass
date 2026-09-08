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
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

PLUGIN_ID = "gg.omapass"
DEFAULT_CLIPBOARD_TIMEOUT = 30


def runtime_dir() -> pathlib.Path:
    """Returns secure user runtime directory."""
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        p = pathlib.Path(xdg)
        if p.exists() and p.is_dir():
            return p
    fallback = pathlib.Path(f"/tmp/omapass-{os.getuid()}")
    fallback.mkdir(mode=0o700, parents=True, exist_ok=True)
    return fallback


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
    """Invalidates the wipe token and terminates any previously running clipboard wipe subshell."""
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
                os.kill(old_pid, signal.SIGTERM)
        except (OSError, ValueError):
            pass
        finally:
            try:
                if wp.exists():
                    wp.unlink()
            except OSError:
                pass


def wipe_clipboard_now() -> None:
    """Immediately clears the clipboard and invalidates any scheduled wipe under clipboard lock."""
    clp = clipboard_lock_path()
    try:
        with open(clp, "w") as clf:
            os.chmod(clp, 0o600)
            fcntl.flock(clf, fcntl.LOCK_EX)
            try:
                cancel_previous_wipe()
                subprocess.run(["wl-copy", "--clear"], timeout=2.0, check=False)
            finally:
                try:
                    fcntl.flock(clf, fcntl.LOCK_UN)
                except OSError:
                    pass
    except Exception:
        cancel_previous_wipe()
        try:
            subprocess.run(["wl-copy", "--clear"], timeout=2.0, check=False)
        except Exception:
            pass


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
        self._load_cache()

    def _load_cache(self) -> None:
        """Loads cached metadata from secure runtime file if available."""
        c = cache_path()
        if c.exists():
            try:
                with open(c, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.items = data.get("items", [])
                    self.last_sync_time = data.get("timestamp", 0)
                    self.account_info = data.get("account_info", {})
                    if self.items:
                        self.is_unlocked = True
                        self.last_status_check = time.time()
            except Exception:
                self.items = []

    def _save_cache(self) -> None:
        """Persists non-sensitive metadata cache with 0600 permissions."""
        c = cache_path()
        try:
            temp_file = c.with_suffix(".tmp")
            payload = {
                "timestamp": self.last_sync_time,
                "items": self.items,
                "account_info": self.account_info,
            }
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.chmod(temp_file, 0o600)
            temp_file.replace(c)
        except Exception:
            pass

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

    def get_status(self, force: bool = False) -> Dict[str, Any]:
        """Checks 1Password CLI status and unlock state with fast cache."""
        now = time.time()
        with self.lock:
            if not force and self.is_unlocked and (now - self.last_status_check < 60.0):
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
        wipe_clipboard_now()
        with self.lock:
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

        field = field.lower().strip()
        cmd: List[str] = []

        if field == "otp":
            cmd = ["op", "item", "get", item_id, "--otp"]
        elif field == "password":
            cmd = ["op", "item", "get", item_id, "--fields", "label=password", "--reveal"]
        elif field == "username":
            cmd = ["op", "item", "get", item_id, "--fields", "label=username"]
        else:
            return False, f"Unsupported field '{field}'"

        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=8.0,
            )
            if res.returncode == 0:
                raw_val = res.stdout.removesuffix("\r\n").removesuffix("\n")
                return True, raw_val
            else:
                err = res.stderr.strip() or f"Failed to retrieve {field}"
                return False, err
        except subprocess.TimeoutExpired:
            return False, f"Retrieving {field} timed out"
        except Exception as e:
            return False, str(e)

    def copy_to_clipboard(
        self,
        item_id: str = "",
        field: str = "",
        title: str = "",
        value: str = "",
        timeout_seconds: int = DEFAULT_CLIPBOARD_TIMEOUT,
    ) -> Dict[str, Any]:
        """Writes field or raw value to wl-copy and serializes token/timer publication under flock."""
        if not value:
            if not item_id or not field:
                return {"ok": False, "error": "No value or item_id provided"}
            ok, fetched = self.fetch_field(item_id, field)
            if not ok:
                return {"ok": False, "error": fetched}
            value = fetched

        if not shutil.which("wl-copy"):
            return {"ok": False, "error": "wl-copy is not installed"}

        clp = clipboard_lock_path()
        with self.clipboard_lock:
            with open(clp, "w") as clf:
                os.chmod(clp, 0o600)
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
                    except Exception as e:
                        return {"ok": False, "error": f"Failed to run wl-copy: {e}"}

                    # Kill previous wipe process if running
                    wp = wipe_pid_path()
                    if wp.exists():
                        try:
                            old_pid = int(wp.read_text().strip())
                            os.kill(old_pid, signal.SIGTERM)
                        except (OSError, ValueError):
                            pass
                        try:
                            wp.unlink()
                        except OSError:
                            pass

                    tp = token_path()
                    f_lower = (field or "").lower()
                    is_sensitive = (
                        not field
                        or any(s in f_lower for s in ("password", "otp", "cvv", "ccnum", "pin", "card", "secret", "token"))
                    )
                    if is_sensitive and timeout_seconds > 0:
                        token = f"{time.time()}-{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}"
                        unique_tmp = tp.parent / f"omapass-token.{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}.tmp"
                        unique_tmp.write_text(token)
                        os.chmod(unique_tmp, 0o600)
                        unique_tmp.replace(tp)

                        cmd = (
                            f"sleep {int(timeout_seconds)} && "
                            f"[ \"$(cat '{tp}' 2>/dev/null)\" = '{token}' ] && "
                            f"wl-copy --clear && rm -f '{tp}' '{wp}'"
                        )
                        try:
                            wipe_proc = subprocess.Popen(
                                ["bash", "-c", cmd],
                                start_new_session=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL,
                            )
                            wp.write_text(str(wipe_proc.pid))
                            os.chmod(wp, 0o600)
                        except Exception:
                            pass
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
        if timeout_seconds > 0 and is_sensitive:
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
                subprocess.run(["wtype", "--", value], check=False, timeout=3.0)
            except Exception:
                pass

        threading.Thread(target=_do_type, daemon=True).start()
        return {"ok": True, "typed": field or "value"}

    def get_item(self, item_id: str) -> Dict[str, Any]:
        """Fetches full item details (fields, urls, notes) with in-memory caching."""
        if not self.check_op_installed():
            return {"ok": False, "error": "op CLI not installed"}

        now = time.time()
        with self.lock:
            cached = self.item_details_cache.get(item_id)
            if cached and (now - cached["timestamp"] < 300.0):
                return {"ok": True, "item": cached["data"]}

        try:
            res = subprocess.run(
                ["op", "item", "get", item_id, "--format=json"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=12.0,
            )
            if res.returncode != 0:
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
                        notes_text = str(fval).strip()
                    continue

                if ftype == "OTP":
                    has_otp = True
                    if f.get("totp"):
                        totp_code = str(f.get("totp"))

                concealed = ftype in ("CONCEALED", "CREDIT_CARD_NUMBER") or fpurpose in ("PASSWORD",)

                # Filter out any field with no content
                val_str = str(fval).strip() if fval is not None else ""
                if not val_str:
                    continue

                display_label = flabel or fid
                # Clean up ugly field IDs like "customField1" if label is missing
                fields_list.append({
                    "id": fid,
                    "label": display_label,
                    "value": val_str,
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

            if has_otp and not totp_code:
                try:
                    otp_res = subprocess.run(
                        ["op", "item", "get", item_id, "--otp"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=5.0,
                    )
                    if otp_res.returncode == 0:
                        totp_code = otp_res.stdout.strip()
                except Exception:
                    pass

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
            }

            with self.lock:
                self.item_details_cache[item_id] = {
                    "timestamp": now,
                    "data": item_data,
                }

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

    def open_url(self, url: str) -> Dict[str, Any]:
        """Opens URL in default web browser."""
        if not url:
            return {"ok": False, "error": "No URL provided"}
        try:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def dispatch(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches an RPC action request."""
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
            limit = int(request.get("limit", 50))
            return self.search_items(query=q, category=cat, limit=limit)
        elif action == "get_item":
            item_id = request.get("id", "")
            return self.get_item(item_id=item_id)
        elif action == "open_desktop":
            item_id = request.get("id", "")
            return self.open_desktop(item_id=item_id)
        elif action == "copy":
            item_id = request.get("id", "")
            field = request.get("field", "password")
            title = request.get("title", "")
            value = request.get("value", "")
            timeout = int(request.get("timeout", DEFAULT_CLIPBOARD_TIMEOUT))
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
    with open(slp, "w") as sf:
        os.chmod(slp, 0o600)
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
    lock_file = open(dlp, "w")
    os.chmod(dlp, 0o600)

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
    server.bind(str(sp))
    os.chmod(str(sp), 0o600)
    server.listen(10)

    def handle_client(conn: socket.socket):
        try:
            conn.settimeout(40.0)
            buffer = ""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8")
                if "\n" in buffer:
                    line, _, _ = buffer.partition("\n")
                    req = json.loads(line)
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
            client_thread = threading.Thread(target=handle_client, args=(conn,), daemon=True)
            client_thread.start()
        except Exception:
            break


def handle_request(req: Dict[str, Any]):
    """Dispatches request via daemon socket, auto-starting daemon if needed, with structured error handling."""
    try:
        ensure_daemon()
        timeout = 35.0 if req.get("action") == "sync" else 10.0
        resp = send_socket_request(req, timeout=timeout)
        if resp is not None:
            print(json.dumps(resp))
            return

        # Direct fallback if socket failed
        service = OmaPassService()
        resp = service.dispatch(req)
        print(json.dumps(resp))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


def main():
    parser = argparse.ArgumentParser(description="OmaPass 1Password Helper")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("serve", help="Run background socket daemon")

    req_parser = subparsers.add_parser("request", help="Send JSON request")
    req_parser.add_argument("json_payload", nargs="?", default="{}", help="JSON payload string")

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
    elif args.command == "request":
        try:
            req = json.loads(args.json_payload) if args.json_payload else {}
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
