#!/usr/bin/env python3
import importlib.util
import json
import os
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("omapass_agent", ROOT / "omapass-agent.py")
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


class IsolatedRuntimeDir(unittest.TestCase):
    """Base case that redirects every runtime path into a temp dir.

    Without this the suite writes to the live $XDG_RUNTIME_DIR: it has
    truncated the real clipboard lock, overwritten the real wipe pid file with
    a mock repr, and published a bogus clipboard token to the running daemon.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.runtime = pathlib.Path(self._tmp.name)
        patcher = patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)


class TestOmaPassService(IsolatedRuntimeDir):
    def setUp(self):
        super().setUp()
        self.service = agent.OmaPassService()
        self.service.items = [
            {
                "id": "item1",
                "title": "GitHub",
                "category": "LOGIN",
                "username": "itsgg",
                "url": "https://github.com",
                "vault": "Personal",
                "favorite": True,
            },
            {
                "id": "item2",
                "title": "Google Account",
                "category": "LOGIN",
                "username": "me@example.com",
                "url": "https://google.com",
                "vault": "Personal",
                "favorite": False,
            },
            {
                "id": "item3",
                "title": "Bank of America",
                "category": "BANK_ACCOUNT",
                "username": "user123",
                "url": "https://bankofamerica.com",
                "vault": "Financial",
                "favorite": True,
            },
            {
                "id": "item4",
                "title": "Server SSH Key",
                "category": "SECURE_NOTE",
                "username": "",
                "url": "",
                "vault": "Work",
                "favorite": False,
            },
        ]

    def test_search_all(self):
        res = self.service.search_items()
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["items"]), 4)
        self.assertTrue(res["items"][0]["favorite"])

    def test_search_query_prefix(self):
        res = self.service.search_items(query="git")
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["items"]), 1)
        self.assertEqual(res["items"][0]["id"], "item1")

    def test_search_category_filter(self):
        res = self.service.search_items(category="CARDS")
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["items"]), 1)
        self.assertEqual(res["items"][0]["title"], "Bank of America")

        res_notes = self.service.search_items(category="NOTES")
        self.assertEqual(len(res_notes["items"]), 1)
        self.assertEqual(res_notes["items"][0]["title"], "Server SSH Key")

        res_fav = self.service.search_items(category="FAVORITES")
        self.assertEqual(len(res_fav["items"]), 2)

    def test_dispatch_ping(self):
        res = self.service.dispatch({"action": "ping"})
        self.assertTrue(res["ok"])
        self.assertIn("pong", res)

    def test_dispatch_unknown(self):
        res = self.service.dispatch({"action": "nonexistent"})
        self.assertFalse(res["ok"])

    @patch("subprocess.run")
    def test_fetch_field_preserves_whitespace(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout=json.dumps({
            "id": "item1", "title": "T", "category": "LOGIN",
            "fields": [{"id": "password", "type": "CONCEALED", "purpose": "PASSWORD",
                        "label": "password", "value": "  secret password with spaces  "}],
        }))

        with patch.object(self.service, "check_op_installed", return_value=True):
            ok, val = self.service.fetch_field("item1", "password")
        self.assertTrue(ok)
        self.assertEqual(val, "  secret password with spaces  ")

    def test_cancel_previous_wipe_cleans_file(self):
        pid_file = agent.wipe_pid_path()
        pid_file.write_text("999999999")
        agent.cancel_previous_wipe()
        self.assertFalse(pid_file.exists())

    @patch("subprocess.Popen")
    def test_copy_to_clipboard_error_handling(self, mock_popen):
        # shutil.which is patched too: without it this passes only on a machine
        # that happens to have wl-copy, and takes the "not installed" branch
        # everywhere else.
        mock_popen.side_effect = FileNotFoundError("wl-copy not found")
        with patch("shutil.which", return_value="/usr/bin/wl-copy"):
            with patch.object(self.service, "fetch_field", return_value=(True, "secret123")):
                res = self.service.copy_to_clipboard("item1", "password")
                self.assertFalse(res["ok"])
                self.assertIn("error", res)
                self.assertIn("Failed to run wl-copy", res["error"])

    @patch("subprocess.run")
    def test_get_status_desktop_integration(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '{"id": "USER123", "name": "Ganesh", "email": "me@example.com"}'
        mock_run.return_value = mock_proc

        with patch.object(self.service, "check_op_installed", return_value=True):
            status = self.service.get_status(force=True)
            self.assertTrue(status["ok"])
            self.assertTrue(status["unlocked"])
            self.assertEqual(status["account"], "me@example.com")
            self.assertEqual(status["name"], "Ganesh")

            # Verify fast cache returns without subprocess call
            mock_run.reset_mock()
            cached_status = self.service.get_status(force=False)
            self.assertTrue(cached_status["unlocked"])
            mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_get_item_parsing_and_caching(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '''{
            "id": "item123",
            "title": "Amazon",
            "category": "LOGIN",
            "vault": {"name": "Personal"},
            "favorite": true,
            "urls": [{"label": "website", "href": "https://amazon.com", "primary": true}],
            "fields": [
                {"id": "username", "type": "STRING", "purpose": "USERNAME", "label": "username", "value": "user@example.com"},
                {"id": "password", "type": "CONCEALED", "purpose": "PASSWORD", "label": "password", "value": "supersecret"},
                {"id": "totp", "type": "OTP", "purpose": "", "label": "one-time password", "value": "otpauth://...", "totp": "123456"},
                {"id": "notesPlain", "type": "STRING", "purpose": "NOTES", "label": "notes", "value": "my secret note"}
            ]
        }'''
        mock_run.return_value = mock_proc

        with patch.object(self.service, "check_op_installed", return_value=True):
            res = self.service.get_item("item123")
            self.assertTrue(res["ok"])
            item = res["item"]
            self.assertEqual(item["title"], "Amazon")
            self.assertEqual(item["vault"], "Personal")
            self.assertEqual(item["totp"], "123456")
            self.assertEqual(item["notes"], "my secret note")
            self.assertEqual(len(item["fields"]), 3)
            self.assertTrue(item["fields"][1]["concealed"])

            # Test in-memory cache hit
            mock_run.reset_mock()
            cached_res = self.service.get_item("item123")
            self.assertTrue(cached_res["ok"])
            self.assertEqual(cached_res["item"]["title"], "Amazon")
            mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_totp_is_never_written_to_the_details_cache(self, mock_run):
        """The README says TOTP is never cached; make that true, not aspirational."""
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout=json.dumps({
            "id": "t1", "title": "Bank", "category": "LOGIN",
            "fields": [
                {"id": "password", "type": "CONCEALED", "purpose": "PASSWORD",
                 "label": "password", "value": "pw"},
                {"id": "totp", "type": "OTP", "purpose": "", "label": "one-time password",
                 "value": "otpauth://totp/x?secret=SEED", "totp": "123456"},
            ],
        }))

        with patch.object(self.service, "check_op_installed", return_value=True):
            res = self.service.get_item("t1")

        # The caller is given the code once, for the first render.
        self.assertEqual(res["item"]["totp"], "123456")
        # It is not retained...
        self.assertEqual(self.service.item_details_cache["t1"]["data"]["totp"], "")
        # ...but the fact that this item has one is, or reopening it within the
        # cache window would hide the TOTP banner and never refresh it.
        self.assertTrue(res["item"]["hasTotp"])
        self.assertTrue(self.service.item_details_cache["t1"]["data"]["hasTotp"])

        # A cache hit still advertises the TOTP, with no code attached.
        again = self.service.get_item("t1")
        self.assertTrue(again["item"]["hasTotp"])
        self.assertEqual(again["item"]["totp"], "")

    @patch("subprocess.run")
    def test_item_without_totp_is_not_marked_as_having_one(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout=json.dumps({
            "id": "n1", "title": "Plain", "category": "LOGIN",
            "fields": [{"id": "password", "type": "CONCEALED", "purpose": "PASSWORD",
                        "label": "password", "value": "pw"}],
        }))
        with patch.object(self.service, "check_op_installed", return_value=True):
            res = self.service.get_item("n1")
        self.assertFalse(res["item"]["hasTotp"])

    @patch("subprocess.Popen")
    def test_open_desktop(self, mock_popen):
        res = self.service.open_desktop("item123")
        self.assertTrue(res["ok"])
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        self.assertIn("onepassword://item?i=item123", args)

    @patch("subprocess.Popen")
    def test_copy_direct_value(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        with patch("shutil.which", return_value="/usr/bin/wl-copy"):
            res = self.service.copy_to_clipboard(value="my-password-value", field="Password", title="Amazon")
            self.assertTrue(res["ok"])
            self.assertEqual(res["field"], "Password")

    def _seed_details_cache(self, timestamp=None):
        self.service.item_details_cache["item123"] = {
            "timestamp": time.time() if timestamp is None else timestamp,
            "data": {
                "id": "item123",
                "title": "Amazon",
                "totp": "654321",
                "fields": [
                    {"id": "username", "label": "username", "value": "myuser@example.com", "purpose": "USERNAME"},
                    {"id": "password", "label": "password", "value": "cachedsecretpass", "purpose": "PASSWORD"},
                ],
            },
        }

    @patch("subprocess.run")
    def test_fetch_field_reads_from_item_cache(self, mock_run):
        self._seed_details_cache()

        with patch.object(self.service, "check_op_installed", return_value=True):
            ok_pw, pw = self.service.fetch_field("item123", "password")
            self.assertTrue(ok_pw)
            self.assertEqual(pw, "cachedsecretpass")

            ok_user, user = self.service.fetch_field("item123", "username")
            self.assertTrue(ok_user)
            self.assertEqual(user, "myuser@example.com")

            # subprocess.run must not be called when item is in cache
            mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_fetch_field_never_serves_totp_from_cache(self, mock_run):
        """A cached TOTP is expired by definition; op must be asked for a fresh one."""
        self._seed_details_cache()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "111222\n"
        mock_run.return_value = mock_proc

        with patch.object(self.service, "check_op_installed", return_value=True):
            ok, code = self.service.fetch_field("item123", "otp")

        self.assertTrue(ok)
        self.assertEqual(code, "111222")
        self.assertIn("--otp", mock_run.call_args[0][0])

    @patch("subprocess.run")
    def test_expired_details_are_refetched(self, mock_run):
        self._seed_details_cache(timestamp=time.time() - agent.DETAILS_CACHE_TTL - 1)
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout=json.dumps({
            "id": "item123", "title": "Amazon", "category": "LOGIN",
            "fields": [{"id": "password", "type": "CONCEALED", "purpose": "PASSWORD",
                        "label": "password", "value": "freshpass"}],
        }))

        with patch.object(self.service, "check_op_installed", return_value=True):
            ok, pw = self.service.fetch_field("item123", "password")

        self.assertTrue(ok)
        self.assertEqual(pw, "freshpass")
        mock_run.assert_called_once()

    def test_purge_expired_details(self):
        self._seed_details_cache(timestamp=time.time() - agent.DETAILS_CACHE_TTL - 1)
        self.service.purge_expired_details()
        self.assertEqual(self.service.item_details_cache, {})

    @patch("subprocess.run")
    def test_fetch_field_matches_on_purpose_not_label(self, mock_run):
        """A password field labelled something other than "password" is still found."""
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout=json.dumps({
            "id": "item9",
            "title": "Router",
            "category": "LOGIN",
            "fields": [
                {"id": "credential", "type": "CONCEALED", "purpose": "PASSWORD",
                 "label": "admin key", "value": "hunter2"},
            ],
        }))

        with patch.object(self.service, "check_op_installed", return_value=True):
            ok, pw = self.service.fetch_field("item9", "password")

        self.assertTrue(ok)
        self.assertEqual(pw, "hunter2")

    @patch("subprocess.run")
    def test_fetch_card_fields(self, mock_run):
        """Card items have no password or username; these are what they do have."""
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout=json.dumps({
            "id": "card1", "title": "Emerald", "category": "CREDIT_CARD",
            "fields": [
                {"id": "cardholder", "type": "STRING", "label": "cardholder", "value": "A N OTHER"},
                {"id": "ccnum", "type": "CREDIT_CARD_NUMBER", "label": "ccnum", "value": "4242424242424242"},
                {"id": "cvv", "type": "CONCEALED", "label": "cvv", "value": "123"},
                {"id": "expiry", "type": "MONTH_YEAR", "label": "expiry", "value": "202812"},
            ],
        }))

        with patch.object(self.service, "check_op_installed", return_value=True):
            for field, expected in [
                ("ccnum", "4242424242424242"),
                ("cvv", "123"),
                ("cardholder", "A N OTHER"),
                ("expiry", "202812"),
            ]:
                ok, val = self.service.fetch_field("card1", field)
                self.assertTrue(ok, field)
                self.assertEqual(val, expected, field)

    @patch("subprocess.run")
    def test_fetch_notes_field(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout=json.dumps({
            "id": "note1", "title": "Wifi", "category": "SECURE_NOTE",
            "fields": [{"id": "notesPlain", "type": "STRING", "purpose": "NOTES",
                        "label": "notes", "value": "  ssid: home  "}],
        }))

        with patch.object(self.service, "check_op_installed", return_value=True):
            ok, val = self.service.fetch_field("note1", "notes")

        self.assertTrue(ok)
        self.assertEqual(val, "  ssid: home  ")

    def test_field_matching_prefers_the_strongest_signal(self):
        """A decoy that merely contains the right word must not beat the real field."""
        item = {"fields": [
            {"id": "verification_url", "type": "STRING", "label": "verification url", "value": "https://x.test"},
            {"id": "cvv", "type": "CONCEALED", "label": "cvv", "value": "123"},
            {"id": "old_card_number", "type": "STRING", "label": "old card number", "value": "1111222233334444"},
            {"id": "ccnum", "type": "CREDIT_CARD_NUMBER", "label": "number", "value": "4242424242424242"},
            {"id": "validFrom", "type": "MONTH_YEAR", "label": "valid from", "value": "202001"},
            {"id": "expiry", "type": "MONTH_YEAR", "label": "expires", "value": "202812"},
        ]}
        self.assertEqual(self.service._match_field(item, "cvv"), "123")
        self.assertEqual(self.service._match_field(item, "ccnum"), "4242424242424242")
        self.assertEqual(self.service._match_field(item, "expiry"), "202812")

    def test_expiry_not_shadowed_by_another_month_year_field(self):
        """Two MONTH_YEAR fields: the one that says expiry has to win."""
        item = {"fields": [
            {"id": "validFrom", "type": "MONTH_YEAR", "label": "valid from", "value": "202001"},
            {"id": "custom_1", "type": "MONTH_YEAR", "label": "expiration date", "value": "202812"},
        ]}
        self.assertEqual(self.service._match_field(item, "expiry"), "202812")

    def test_custom_cvv_label_is_found(self):
        item = {"fields": [
            {"id": "custom_9", "type": "CONCEALED", "label": "CVV code", "value": "321"},
        ]}
        self.assertEqual(self.service._match_field(item, "cvv"), "321")

    def test_field_matching_falls_back_to_type_only(self):
        """With no better signal, the type is still enough to find the field."""
        item = {"fields": [
            {"id": "custom1", "type": "CREDIT_CARD_NUMBER", "label": "the number", "value": "4242424242424242"},
        ]}
        self.assertEqual(self.service._match_field(item, "ccnum"), "4242424242424242")

    def test_field_matching_ignores_empty_values(self):
        item = {"fields": [
            {"id": "password", "purpose": "PASSWORD", "label": "password", "value": ""},
            {"id": "other", "type": "CONCEALED", "label": "passphrase", "value": "real"},
        ]}
        self.assertEqual(self.service._match_field(item, "password"), "real")

    def test_fetch_field_rejects_malformed_field_name(self):
        """A field name is either canonical or a plain field id, nothing else."""
        with patch("subprocess.run") as mock_run:
            with patch.object(self.service, "check_op_installed", return_value=True):
                for bad in ("../../etc/passwd", "a b", "x" * 100, "$(id)", ""):
                    ok, err = self.service.fetch_field("item1", bad)
                    self.assertFalse(ok, bad)
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_fetch_field_accepts_a_custom_field_id(self, mock_run):
        """The details view copies custom fields by id, never by value."""
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout=json.dumps({
            "id": "item1", "title": "Router", "category": "LOGIN",
            "fields": [{"id": "recovery_code", "type": "CONCEALED", "label": "recovery code",
                        "value": "abcd-efgh"}],
        }))
        with patch.object(self.service, "check_op_installed", return_value=True):
            ok, val = self.service.fetch_field("item1", "recovery_code")
        self.assertTrue(ok)
        self.assertEqual(val, "abcd-efgh")

    def test_auth_error_clears_unlocked_state(self):
        self.service.is_unlocked = True
        self._seed_details_cache()
        self.service._note_op_error("[ERROR] you are not currently signed in")
        self.assertFalse(self.service.is_unlocked)
        self.assertEqual(self.service.item_details_cache, {})

    def test_unrelated_error_keeps_unlocked_state(self):
        self.service.is_unlocked = True
        self.service._note_op_error("[ERROR] no item matches that id")
        self.assertTrue(self.service.is_unlocked)

    def test_stale_cache_does_not_claim_unlocked(self):
        agent.write_private(agent.cache_path(), json.dumps({
            "timestamp": time.time() - agent.UNLOCK_CACHE_TTL - 1,
            "items": [{"id": "a", "title": "Old"}],
            "account_info": {"email": "me@example.com"},
        }))
        service = agent.OmaPassService()
        self.assertFalse(service.is_unlocked)
        self.assertEqual(len(service.items), 1)

    def test_fresh_cache_restores_unlocked(self):
        agent.write_private(agent.cache_path(), json.dumps({
            "timestamp": time.time(),
            "items": [{"id": "a", "title": "New"}],
            "account_info": {"email": "me@example.com"},
        }))
        service = agent.OmaPassService()
        self.assertTrue(service.is_unlocked)

    def test_cache_with_null_account_info_loads(self):
        """A null account_info used to become None and crash every status call."""
        agent.write_private(agent.cache_path(), json.dumps({
            "timestamp": time.time(),
            "items": [{"id": "a", "title": "New"}],
            "account_info": None,
        }))
        service = agent.OmaPassService()
        self.assertEqual(service.account_info, {})
        self.assertTrue(service.get_status()["ok"])

    def test_auth_failure_survives_a_daemon_restart(self):
        """A recent metadata cache must not undo an observed authorization failure."""
        self.service.items = [{"id": "a", "title": "T"}]
        self.service.last_sync_time = time.time()
        self.service.is_unlocked = True
        self.service._save_cache()
        self.service._note_op_error("[ERROR] you are not currently signed in")

        restarted = agent.OmaPassService()
        self.assertTrue(restarted.auth_failed)
        self.assertFalse(restarted.is_unlocked)
        self.assertEqual(len(restarted.items), 1)

    def test_success_after_auth_failure_restores_unlocked(self):
        self.service.auth_failed = True
        self.service.is_unlocked = False
        self.service._note_op_success()
        self.assertTrue(self.service.is_unlocked)
        self.assertFalse(self.service.auth_failed)
        self.assertFalse(agent.OmaPassService().auth_failed)

    @patch("subprocess.run")
    def test_fetch_field_success_clears_auth_failure(self, mock_run):
        self.service.auth_failed = True
        self.service.is_unlocked = False
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout=json.dumps({
            "id": "item1", "title": "T", "category": "LOGIN",
            "fields": [{"id": "password", "purpose": "PASSWORD", "type": "CONCEALED",
                        "label": "password", "value": "secret"}],
        }))

        with patch.object(self.service, "check_op_installed", return_value=True):
            ok, _ = self.service.fetch_field("item1", "password")

        self.assertTrue(ok)
        self.assertTrue(self.service.is_unlocked)
        self.assertFalse(self.service.auth_failed)

    @patch("subprocess.run")
    def test_full_item_fallback_preserves_whitespace(self, mock_run):
        """The credential is copied verbatim, spaces included."""
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout=json.dumps({
            "id": "item9",
            "title": "Router",
            "fields": [
                {"id": "credential", "type": "CONCEALED", "purpose": "PASSWORD",
                 "label": "admin key", "value": "  secret  "},
            ],
        }))

        with patch.object(self.service, "check_op_installed", return_value=True):
            ok, pw = self.service.fetch_field("item9", "password")

        self.assertTrue(ok)
        self.assertEqual(pw, "  secret  ")

    @patch("subprocess.Popen")
    def test_copy_clears_clipboard_when_wipe_cannot_be_armed(self, mock_popen):
        """Never report a timed copy we have no worker to honour."""
        wl_copy = MagicMock()
        wl_copy.communicate.return_value = (b"", b"")
        wl_copy.returncode = 0
        mock_popen.side_effect = [wl_copy, OSError("no fork for you")]

        with patch("shutil.which", return_value="/usr/bin/wl-copy"):
            with patch.object(agent, "clear_clipboard_once", return_value=True) as mock_clear:
                res = self.service.copy_to_clipboard(value="topsecret", field="password", timeout_seconds=30)

        self.assertFalse(res["ok"])
        mock_clear.assert_called_once()
        self.assertFalse(agent.token_path().exists())

    def test_wipe_worker_retries_then_warns(self):
        """A clear that never succeeds must warn, not silently give up."""
        agent.write_private(agent.token_path(), "mine")
        agent.write_private(agent.wipe_pid_path(), "1234")
        with patch.object(agent, "clear_clipboard_once", return_value=False) as mock_clear:
            with patch.object(agent, "warn_clipboard_not_cleared") as mock_warn:
                with patch("time.sleep"):
                    agent.run_wipe_worker(0, "mine")

        self.assertEqual(mock_clear.call_count, len(agent.WIPE_RETRY_DELAYS))
        mock_warn.assert_called_once()
        # The token survives, so a later copy or lock still sees the leftover.
        self.assertTrue(agent.token_path().exists())
        self.assertFalse(agent.wipe_pid_path().exists())

    def test_wipe_worker_succeeds_on_a_retry(self):
        agent.write_private(agent.token_path(), "mine")
        with patch.object(agent, "clear_clipboard_once", side_effect=[False, True]) as mock_clear:
            with patch.object(agent, "warn_clipboard_not_cleared") as mock_warn:
                with patch("time.sleep"):
                    agent.run_wipe_worker(0, "mine")

        self.assertEqual(mock_clear.call_count, 2)
        mock_warn.assert_not_called()
        self.assertFalse(agent.token_path().exists())

    def test_wipe_worker_stops_when_a_newer_copy_takes_over_mid_retry(self):
        agent.write_private(agent.token_path(), "mine")

        def steal_token(*_args, **_kwargs):
            agent.write_private(agent.token_path(), "newer")
            return False

        with patch.object(agent, "clear_clipboard_once", side_effect=steal_token) as mock_clear:
            with patch("time.sleep"):
                agent.run_wipe_worker(0, "mine")

        # Second pass sees a token that is not ours and stands down.
        self.assertEqual(mock_clear.call_count, 1)
        self.assertEqual(agent.token_path().read_text(), "newer")

    @patch("subprocess.Popen")
    def test_copy_reports_when_clipboard_could_not_be_cleared(self, mock_popen):
        """Arming failed and clearing failed: say the secret is still there."""
        wl_copy = MagicMock()
        wl_copy.communicate.return_value = (b"", b"")
        wl_copy.returncode = 0
        mock_popen.side_effect = [wl_copy, OSError("no fork for you")]

        with patch("shutil.which", return_value="/usr/bin/wl-copy"):
            with patch.object(agent, "clear_clipboard_once", return_value=False):
                with patch.object(agent, "warn_clipboard_not_cleared") as mock_warn:
                    res = self.service.copy_to_clipboard(value="topsecret", field="password", timeout_seconds=30)

        self.assertFalse(res["ok"])
        self.assertIn("still on the clipboard", res["error"])
        mock_warn.assert_called_once()
        self.assertTrue(agent.token_path().exists())

    @patch("subprocess.run")
    def test_otp_action_always_asks_op(self, mock_run):
        self._seed_details_cache()
        mock_run.return_value = MagicMock(returncode=0, stdout="999888\n", stderr="")

        with patch.object(self.service, "check_op_installed", return_value=True):
            res = self.service.dispatch({"action": "otp", "id": "item123"})

        self.assertTrue(res["ok"])
        self.assertEqual(res["otp"], "999888")
        self.assertIn("--otp", mock_run.call_args[0][0])

    def test_otp_action_requires_id(self):
        self.assertFalse(self.service.dispatch({"action": "otp"})["ok"])

    def test_normalize_url(self):
        self.assertEqual(self.service.normalize_url("github.com"), "https://github.com")
        self.assertEqual(self.service.normalize_url("http://x.test/a"), "http://x.test/a")
        self.assertEqual(self.service.normalize_url("//x.test"), "https://x.test")
        self.assertIsNone(self.service.normalize_url("file:///etc/passwd"))
        self.assertIsNone(self.service.normalize_url("javascript:alert(1)"))
        self.assertIsNone(self.service.normalize_url("   "))

    def test_open_url_refuses_non_web_scheme(self):
        res = self.service.open_url("file:///etc/passwd")
        self.assertFalse(res["ok"])

    def test_dispatch_coerces_bad_numbers(self):
        res = self.service.dispatch({"action": "list", "limit": "not-a-number"})
        self.assertTrue(res["ok"])

    def test_dispatch_rejects_non_object(self):
        res = self.service.dispatch(["status"])
        self.assertFalse(res["ok"])

    def test_search_subsequence_fuzzy(self):
        res = self.service.search_items(query="gthb")
        self.assertEqual([i["id"] for i in res["items"]], ["item1"])

    def test_wipe_worker_skips_superseded_token(self):
        agent.write_private(agent.token_path(), "newer-token")
        with patch("subprocess.run") as mock_run:
            agent.run_wipe_worker(0, "older-token")
        mock_run.assert_not_called()
        self.assertTrue(agent.token_path().exists())

    def test_wipe_worker_clears_when_token_matches(self):
        agent.write_private(agent.token_path(), "mine")
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            agent.run_wipe_worker(0, "mine")
        mock_run.assert_called_once()
        self.assertIn("--clear", mock_run.call_args[0][0])
        self.assertFalse(agent.token_path().exists())

    def test_cancel_previous_wipe_ignores_foreign_pid(self):
        """PID reuse must not turn a stale pid file into a kill of someone else's process."""
        agent.write_private(agent.wipe_pid_path(), str(os.getpid()))
        with patch.object(agent, "is_our_wipe_process", return_value=False):
            with patch("os.kill") as mock_kill:
                agent.cancel_previous_wipe()
        mock_kill.assert_not_called()

    def test_runtime_files_are_private(self):
        self.service.items = [{"id": "a", "title": "T"}]
        self.service._save_cache()
        self.assertEqual(agent.cache_path().stat().st_mode & 0o777, 0o600)

    @patch("subprocess.run")
    def test_get_status_locked_fast_account_list(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '[{"email": "ganesh@example.com", "url": "my.1password.com", "user_uuid": "U123"}]'
        mock_run.return_value = mock_proc

        self.service.is_unlocked = False

        with patch.object(self.service, "check_op_installed", return_value=True):
            status = self.service.get_status(force=False)
            self.assertTrue(status["ok"])
            self.assertFalse(status["unlocked"])
            self.assertEqual(status["account"], "ganesh@example.com")
            # Should have called op account list, NOT op user get
            mock_run.assert_called_once()
            self.assertEqual(mock_run.call_args[0][0], ["op", "account", "list", "--format=json"])


if __name__ == "__main__":
    unittest.main()


