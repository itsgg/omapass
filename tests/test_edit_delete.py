"""Editing and removing items.

Editing is the dangerous one: it rewrites an item that already exists. These
tests hold the two properties that make it safe - the round trip is lossless,
and an item carrying a passkey is refused rather than silently damaged.
"""

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from support import IsolatedRuntimeDir
from omapass import service

FULL_ITEM = {
    "id": "i1",
    "title": "GitHub",
    "category": "LOGIN",
    "vault": {"id": "v1", "name": "Personal"},
    "urls": [{"label": "website", "primary": True, "href": "https://github.com"}],
    "sections": [{"id": "sec1", "label": "Recovery"}],
    "fields": [
        {"id": "username", "type": "STRING", "purpose": "USERNAME", "value": "octocat"},
        {"id": "password", "type": "CONCEALED", "purpose": "PASSWORD", "value": "old"},
        {"id": "custom1", "type": "STRING", "label": "Backup code",
         "section": {"id": "sec1"}, "value": "keep-me"},
    ],
    "someFutureKey": {"op": "knows about this", "we": "do not"},
}


class TestEditItem(IsolatedRuntimeDir):
    def setUp(self):
        super().setUp()
        os.chmod(self.runtime, 0o700)
        self.service = service.OmaPassService()

    def _edit(self, item=None, **kwargs):
        sent = {}

        def run(cmd, *a, **k):
            if cmd[:3] == ["op", "item", "get"]:
                return MagicMock(returncode=0, stdout=json.dumps(item or FULL_ITEM), stderr="")
            path = [c for c in cmd if c.startswith("--template=")]
            if path:
                with open(path[0].split("=", 1)[1]) as fh:
                    sent["body"] = json.load(fh)
                sent["argv"] = cmd
            return MagicMock(returncode=0, stdout="{}", stderr="")

        with patch.object(self.service, "check_op_installed", return_value=True):
            with patch("subprocess.run", side_effect=run):
                with patch.object(self.service, "sync"):
                    res = self.service.edit_item(item_id="i1", **kwargs)
        return res, sent

    def test_an_edit_preserves_everything_it_did_not_touch(self):
        """The whole point: op's own payload is edited, never rebuilt."""
        res, sent = self._edit(changes={"username": "new-user"})
        self.assertTrue(res["ok"])
        body = sent["body"]

        # The changed field changed.
        by_id = {f["id"]: f for f in body["fields"]}
        self.assertEqual(by_id["username"]["value"], "new-user")
        # Everything else survived, including things this code does not model.
        self.assertEqual(by_id["password"]["value"], "old")
        self.assertEqual(by_id["custom1"]["value"], "keep-me")
        self.assertEqual(by_id["custom1"]["section"], {"id": "sec1"})
        self.assertEqual(body["sections"], [{"id": "sec1", "label": "Recovery"}])
        self.assertEqual(body["someFutureKey"], {"op": "knows about this", "we": "do not"})

    def test_an_item_with_a_passkey_is_refused(self):
        """op overwrites a passkey when edited through a template."""
        item = dict(FULL_ITEM)
        item["fields"] = FULL_ITEM["fields"] + [{"id": "passkey", "type": "PASSKEY", "value": "x"}]
        res, sent = self._edit(item=item, changes={"username": "new"})
        self.assertFalse(res["ok"])
        self.assertIn("passkey", res["error"].lower())
        self.assertNotIn("body", sent, "it must not even build a template")

    def test_a_passkey_category_is_refused(self):
        item = dict(FULL_ITEM, category="PASSKEY")
        res, _ = self._edit(item=item, changes={"username": "new"})
        self.assertFalse(res["ok"])

    def test_secrets_do_not_travel_in_argv(self):
        _, sent = self._edit(changes={"password": "brand-new-secret"})
        self.assertNotIn("brand-new-secret", " ".join(sent["argv"]))

    def test_nothing_changed_means_nothing_written(self):
        res, sent = self._edit(changes={"username": "octocat"})
        self.assertTrue(res["ok"])
        self.assertTrue(res["unchanged"])
        self.assertNotIn("body", sent)

    def test_an_unknown_field_is_ignored_not_invented(self):
        res, sent = self._edit(changes={"not_a_field": "x", "username": "u2"})
        ids = [f["id"] for f in sent["body"]["fields"]]
        self.assertNotIn("not_a_field", ids)
        self.assertEqual(res["changed"], ["username"])

    def test_the_primary_url_is_updated_in_place(self):
        _, sent = self._edit(url="example.com")
        self.assertEqual(sent["body"]["urls"][0]["href"], "https://example.com")
        self.assertTrue(sent["body"]["urls"][0]["primary"])

    def test_a_non_web_url_is_refused(self):
        res, sent = self._edit(url="javascript:alert(1)")
        self.assertFalse(res["ok"])
        self.assertNotIn("body", sent)

    def test_the_template_is_removed_afterwards(self):
        seen = {}

        def run(cmd, *a, **k):
            if cmd[:3] == ["op", "item", "get"]:
                return MagicMock(returncode=0, stdout=json.dumps(FULL_ITEM), stderr="")
            p = [c for c in cmd if c.startswith("--template=")]
            if p:
                seen["path"] = p[0].split("=", 1)[1]
                seen["mode"] = os.stat(seen["path"]).st_mode & 0o777
            return MagicMock(returncode=0, stdout="{}", stderr="")

        with patch.object(self.service, "check_op_installed", return_value=True):
            with patch("subprocess.run", side_effect=run):
                with patch.object(self.service, "sync"):
                    self.service.edit_item(item_id="i1", changes={"username": "u"})

        self.assertEqual(seen["mode"], 0o600)
        self.assertFalse(os.path.exists(seen["path"]))

    def test_an_edit_drops_the_cached_copy(self):
        self.service.item_details_cache["i1"] = {"timestamp": 0, "data": {"id": "i1"}}
        self._edit(changes={"username": "u2"})
        self.assertNotIn("i1", self.service.item_details_cache)

    def test_editing_refuses_once_the_vault_locked_mid_request(self):
        def run(cmd, *a, **k):
            if cmd[:3] == ["op", "item", "get"]:
                return MagicMock(returncode=0, stdout=json.dumps(FULL_ITEM), stderr="")
            self.service.lock_epoch += 1
            return MagicMock(returncode=0, stdout="{}", stderr="")

        with patch.object(self.service, "check_op_installed", return_value=True):
            with patch("subprocess.run", side_effect=run):
                with patch.object(self.service, "sync") as sync:
                    res = self.service.edit_item(item_id="i1", changes={"username": "u"})
        self.assertFalse(res["ok"])
        sync.assert_not_called()


class TestDeleteItem(IsolatedRuntimeDir):
    def setUp(self):
        super().setUp()
        os.chmod(self.runtime, 0o700)
        self.service = service.OmaPassService()
        self.service.items = [{"id": "i1", "title": "Gone"}, {"id": "i2", "title": "Stays"}]

    def _delete(self, **kwargs):
        with patch.object(self.service, "check_op_installed", return_value=True):
            with patch("subprocess.run", return_value=MagicMock(
                    returncode=0, stdout="", stderr="")) as run:
                with patch.object(self.service, "sync"):
                    res = self.service.delete_item(item_id="i1", **kwargs)
        return res, run.call_args[0][0]

    def test_removing_archives_by_default(self):
        """A misclick should be recoverable."""
        res, argv = self._delete()
        self.assertTrue(res["ok"])
        self.assertIn("--archive", argv)
        self.assertTrue(res["archived"])

    def test_permanent_deletion_takes_an_explicit_choice(self):
        _, argv = self._delete(archive=False)
        self.assertNotIn("--archive", argv)

    def test_dispatch_archives_unless_told_otherwise(self):
        """A missing flag must never mean permanent deletion."""
        with patch.object(self.service, "delete_item", return_value={"ok": True}) as d:
            self.service.dispatch({"action": "delete_item", "id": "i1"})
        self.assertTrue(d.call_args.kwargs["archive"])

    def test_the_item_leaves_the_list_immediately(self):
        self._delete()
        self.assertEqual([i["id"] for i in self.service.items], ["i2"])

    def test_an_id_is_required(self):
        with patch.object(self.service, "check_op_installed", return_value=True):
            self.assertFalse(self.service.delete_item(item_id="")["ok"])


if __name__ == "__main__":
    unittest.main()
