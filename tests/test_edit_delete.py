"""Editing and removing items.

Editing is the dangerous one: it rewrites an item that already exists. op
offers two ways to do it and they trade against each other. A JSON template
keeps secrets out of the argument list but, by op's own documentation, does
not carry passkeys and overwrites one when it writes. An assignment statement
names a single field and leaves the rest of the item alone, at the cost of
putting its value in argv.

This code takes assignments, because a destroyed passkey is silent and
permanent while the argv window is bounded and documented. These tests hold
what that choice has to buy: only named fields are ever written, and the edit
is previewed with --dry-run and the preview checked before anything lands.
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


def apply_assignments(item, argv):
    """What op would produce: the item with the argv assignments applied.

    Stands in for a well-behaved op, so the preview check under test sees the
    result it is meant to accept.
    """
    out = json.loads(json.dumps(item))
    by_id = {f["id"]: f for f in out["fields"]}
    for arg in argv[4:]:
        if arg.startswith("-") or "=" not in arg:
            continue
        field_id, value = arg.split("=", 1)
        if field_id in by_id:
            by_id[field_id]["value"] = value
        else:
            out["fields"].append({"id": field_id, "type": "STRING", "value": value})
    if "--generate-password" in " ".join(argv):
        # op adds the field when the item has none, which is a case the edit
        # has to handle rather than crash the stand-in on.
        if "password" in by_id:
            by_id["password"]["value"] = "generated-by-op"
        else:
            out["fields"].append(
                {"id": "password", "type": "CONCEALED", "value": "generated-by-op"})
    if "--title" in argv:
        out["title"] = argv[argv.index("--title") + 1]
    if "--url" in argv:
        out["urls"] = [{"label": "website", "primary": True,
                        "href": argv[argv.index("--url") + 1]}]
    return out


class TestEditItem(IsolatedRuntimeDir):
    def setUp(self):
        super().setUp()
        os.chmod(self.runtime, 0o700)
        self.service = service.OmaPassService()

    def _edit(self, item=None, preview=None, **kwargs):
        """Runs an edit against a mock op, returning the result and the calls.

        `preview` overrides what the dry run reports, which is how a
        misbehaving op is simulated.
        """
        item = item or FULL_ITEM
        calls = {"dry": [], "write": []}

        def run(cmd, *a, **k):
            if cmd[:3] == ["op", "item", "get"]:
                return MagicMock(returncode=0, stdout=json.dumps(item), stderr="")
            if "--dry-run" in cmd:
                calls["dry"].append(cmd)
                body = preview if preview is not None else apply_assignments(item, cmd)
                return MagicMock(returncode=0, stdout=json.dumps(body), stderr="")
            calls["write"].append(cmd)
            return MagicMock(returncode=0, stdout=json.dumps(
                apply_assignments(item, cmd)), stderr="")

        with patch.object(self.service, "check_op_installed", return_value=True):
            with patch("subprocess.run", side_effect=run):
                with patch.object(self.service, "sync"):
                    res = self.service.edit_item(item_id="i1", **kwargs)
        return res, calls

    def test_only_the_named_field_is_written(self):
        """The whole point: nothing the form did not show is mentioned at all."""
        res, calls = self._edit(changes={"username": "new-user"})
        self.assertTrue(res["ok"], res.get("error"))
        argv = calls["write"][0]
        self.assertIn("username=new-user", argv)
        # Nothing about the password, the custom field or the section, so op
        # has nothing to rewrite them from.
        joined = " ".join(argv)
        self.assertNotIn("password", joined)
        self.assertNotIn("custom1", joined)
        self.assertNotIn("keep-me", joined)

    def test_it_is_previewed_before_it_is_written(self):
        res, calls = self._edit(changes={"username": "u2"})
        self.assertEqual(len(calls["dry"]), 1)
        self.assertEqual(len(calls["write"]), 1)
        self.assertTrue(res["verified"])

    def test_a_preview_that_drops_a_field_is_not_written(self):
        """If op would lose something, the write never happens."""
        damaged = json.loads(json.dumps(FULL_ITEM))
        damaged["fields"] = [f for f in damaged["fields"] if f["id"] != "custom1"]
        res, calls = self._edit(changes={"username": "u2"}, preview=damaged)
        self.assertFalse(res["ok"])
        self.assertIn("custom1", res["error"])
        self.assertEqual(calls["write"], [])

    def test_a_preview_that_invents_a_field_is_not_written(self):
        """An assignment resolving to a new field is the failure mode to catch."""
        wrong = json.loads(json.dumps(FULL_ITEM))
        wrong["fields"].append({"id": "cardholder", "type": "STRING", "value": "x"})
        res, calls = self._edit(changes={"username": "u2"}, preview=wrong)
        self.assertFalse(res["ok"])
        self.assertEqual(calls["write"], [])

    def test_a_preview_that_ignores_the_change_is_not_written(self):
        res, calls = self._edit(changes={"username": "u2"}, preview=FULL_ITEM)
        self.assertFalse(res["ok"])
        self.assertIn("username", res["error"])
        self.assertEqual(calls["write"], [])

    def test_a_preview_that_downgrades_a_passkey_field_is_not_written(self):
        """Losing a passkey need not mean losing the field; the type is enough."""
        item = json.loads(json.dumps(FULL_ITEM))
        item["fields"].append({"id": "pk", "type": "PASSKEY", "value": "x"})
        damaged = json.loads(json.dumps(item))
        for f in damaged["fields"]:
            if f["id"] == "username":
                f["value"] = "u2"
            if f["id"] == "pk":
                f["type"] = "STRING"
        res, calls = self._edit(item=item, changes={"username": "u2"}, preview=damaged)
        self.assertFalse(res["ok"])
        self.assertIn("pk", res["error"])
        self.assertEqual(calls["write"], [])

    def test_a_preview_that_loses_a_passkey_category_is_not_written(self):
        """op reports the passkey through the category on some items."""
        item = dict(FULL_ITEM, category="PASSKEY")
        demoted = json.loads(json.dumps(item))
        demoted["category"] = "LOGIN"
        for f in demoted["fields"]:
            if f["id"] == "username":
                f["value"] = "u2"
        res, calls = self._edit(item=item, changes={"username": "u2"}, preview=demoted)
        self.assertFalse(res["ok"])
        self.assertIn("passkey", res["error"].lower())
        self.assertEqual(calls["write"], [])

    def test_a_preview_that_quietly_empties_another_field_is_not_written(self):
        """The damage the old check waved through: the change lands, and so
        does a change nobody asked for."""
        sneaky = json.loads(json.dumps(FULL_ITEM))
        for f in sneaky["fields"]:
            if f["id"] == "username":
                f["value"] = "u2"
            if f["id"] == "password":
                f["value"] = ""
        res, calls = self._edit(changes={"username": "u2"}, preview=sneaky)
        self.assertFalse(res["ok"])
        self.assertIn("password", res["error"])
        self.assertEqual(calls["write"], [])

    def test_a_preview_that_drops_a_website_is_not_written(self):
        sneaky = json.loads(json.dumps(FULL_ITEM))
        for f in sneaky["fields"]:
            if f["id"] == "username":
                f["value"] = "u2"
        sneaky["urls"] = []
        res, calls = self._edit(changes={"username": "u2"}, preview=sneaky)
        self.assertFalse(res["ok"])
        self.assertEqual(calls["write"], [])

    def test_a_preview_that_drops_a_section_is_not_written(self):
        sneaky = json.loads(json.dumps(FULL_ITEM))
        for f in sneaky["fields"]:
            if f["id"] == "username":
                f["value"] = "u2"
        sneaky["sections"] = []
        res, calls = self._edit(changes={"username": "u2"}, preview=sneaky)
        self.assertFalse(res["ok"])
        self.assertIn("section", res["error"])
        self.assertEqual(calls["write"], [])

    def test_a_preview_that_renames_the_item_is_not_written(self):
        sneaky = json.loads(json.dumps(FULL_ITEM))
        for f in sneaky["fields"]:
            if f["id"] == "username":
                f["value"] = "u2"
        sneaky["title"] = "Something else"
        res, calls = self._edit(changes={"username": "u2"}, preview=sneaky)
        self.assertFalse(res["ok"])
        self.assertIn("title", res["error"])
        self.assertEqual(calls["write"], [])

    def test_a_rename_that_the_preview_ignores_is_not_written(self):
        res, calls = self._edit(title="Renamed", preview=FULL_ITEM)
        self.assertFalse(res["ok"])
        self.assertIn("title", res["error"])
        self.assertEqual(calls["write"], [])

    def test_a_new_website_that_the_preview_ignores_is_not_written(self):
        res, calls = self._edit(url="example.com", preview=FULL_ITEM)
        self.assertFalse(res["ok"])
        self.assertIn("website", res["error"])
        self.assertEqual(calls["write"], [])

    def test_a_generation_the_preview_ignores_is_not_written(self):
        """The password has to actually come back different."""
        res, calls = self._edit(generate_field="password", preview=FULL_ITEM)
        self.assertFalse(res["ok"])
        self.assertIn("generate", res["error"].lower())
        self.assertEqual(calls["write"], [])

    def test_a_preview_that_drops_an_attachment_is_not_written(self):
        item = json.loads(json.dumps(FULL_ITEM))
        item["files"] = [{"id": "f1", "name": "recovery-codes.txt"}]
        stripped = json.loads(json.dumps(item))
        stripped["files"] = []
        for f in stripped["fields"]:
            if f["id"] == "username":
                f["value"] = "u2"
        res, calls = self._edit(item=item, changes={"username": "u2"}, preview=stripped)
        self.assertFalse(res["ok"])
        self.assertIn("attachment", res["error"])
        self.assertEqual(calls["write"], [])

    def test_a_preview_that_renames_a_field_is_not_written(self):
        renamed = json.loads(json.dumps(FULL_ITEM))
        for f in renamed["fields"]:
            if f["id"] == "username":
                f["value"] = "u2"
            if f["id"] == "custom1":
                f["label"] = "Something else"
        res, calls = self._edit(changes={"username": "u2"}, preview=renamed)
        self.assertFalse(res["ok"])
        self.assertIn("custom1", res["error"])
        self.assertEqual(calls["write"], [])

    def test_a_preview_that_moves_a_field_between_sections_is_not_written(self):
        moved = json.loads(json.dumps(FULL_ITEM))
        for f in moved["fields"]:
            if f["id"] == "username":
                f["value"] = "u2"
            if f["id"] == "custom1":
                f["section"] = {"id": "somewhere-else"}
        res, calls = self._edit(changes={"username": "u2"}, preview=moved)
        self.assertFalse(res["ok"])
        self.assertIn("section", res["error"])
        self.assertEqual(calls["write"], [])

    def test_a_generated_password_that_comes_back_empty_is_not_written(self):
        """Different from the old one is not enough: empty is different too."""
        emptied = json.loads(json.dumps(FULL_ITEM))
        for f in emptied["fields"]:
            if f["id"] == "password":
                f["value"] = ""
        res, calls = self._edit(generate_field="password", preview=emptied)
        self.assertFalse(res["ok"])
        self.assertIn("generate", res["error"].lower())
        self.assertEqual(calls["write"], [])

    def test_generating_a_password_an_item_never_had_is_allowed(self):
        """op adds the field, and that addition is the point, not a surprise."""
        item = json.loads(json.dumps(FULL_ITEM))
        item["fields"] = [f for f in item["fields"] if f["id"] != "password"]
        preview = json.loads(json.dumps(item))
        preview["fields"].append(
            {"id": "password", "type": "CONCEALED", "value": "generated-by-op"})
        res, calls = self._edit(item=item, generate_field="password", preview=preview)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(len(calls["write"]), 1)

    def test_op_may_canonicalise_a_month_and_year(self):
        """A card expiry op stores as 202812 is the 2028/12 that was asked for."""
        item = json.loads(json.dumps(FULL_ITEM))
        item["category"] = "CREDIT_CARD"
        item["fields"].append({"id": "expiry", "type": "MONTH_YEAR", "value": "202701"})
        preview = json.loads(json.dumps(item))
        for f in preview["fields"]:
            if f["id"] == "expiry":
                f["value"] = "202812"
        res, calls = self._edit(item=item, changes={"expiry": "2028/12"}, preview=preview)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(len(calls["write"]), 1)

    def test_a_month_and_year_that_really_is_wrong_is_still_caught(self):
        item = json.loads(json.dumps(FULL_ITEM))
        item["category"] = "CREDIT_CARD"
        item["fields"].append({"id": "expiry", "type": "MONTH_YEAR", "value": "202701"})
        preview = json.loads(json.dumps(item))
        for f in preview["fields"]:
            if f["id"] == "expiry":
                f["value"] = "202901"
        res, calls = self._edit(item=item, changes={"expiry": "2028/12"}, preview=preview)
        self.assertFalse(res["ok"])
        self.assertEqual(calls["write"], [])

    def test_a_preview_that_changes_the_tags_is_not_written(self):
        """Nothing here passes --tags, so op has no business touching them."""
        item = json.loads(json.dumps(FULL_ITEM))
        item["tags"] = ["work", "dev"]
        stripped = json.loads(json.dumps(item))
        stripped["tags"] = []
        for f in stripped["fields"]:
            if f["id"] == "username":
                f["value"] = "u2"
        res, calls = self._edit(item=item, changes={"username": "u2"}, preview=stripped)
        self.assertFalse(res["ok"])
        self.assertIn("tags", res["error"])
        self.assertEqual(calls["write"], [])

    def test_a_preview_that_renames_a_section_is_not_written(self):
        renamed = json.loads(json.dumps(FULL_ITEM))
        renamed["sections"] = [{"id": "sec1", "label": "Something else"}]
        for f in renamed["fields"]:
            if f["id"] == "username":
                f["value"] = "u2"
        res, calls = self._edit(changes={"username": "u2"}, preview=renamed)
        self.assertFalse(res["ok"])
        self.assertIn("section", res["error"])
        self.assertEqual(calls["write"], [])

    def test_a_preview_that_renames_an_untouched_website_is_not_written(self):
        renamed = json.loads(json.dumps(FULL_ITEM))
        renamed["urls"][0]["label"] = "somewhere else"
        for f in renamed["fields"]:
            if f["id"] == "username":
                f["value"] = "u2"
        res, calls = self._edit(changes={"username": "u2"}, preview=renamed)
        self.assertFalse(res["ok"])
        self.assertIn("github.com", res["error"])
        self.assertEqual(calls["write"], [])

    def test_setting_a_website_lets_op_label_the_one_it_replaced(self):
        """--url owns the primary, so its label is not ours to police."""
        preview = json.loads(json.dumps(FULL_ITEM))
        preview["urls"] = [{"label": "url", "primary": True, "href": "https://example.com"}]
        res, calls = self._edit(url="example.com", preview=preview)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(len(calls["write"]), 1)

    def test_a_preview_that_cannot_be_read_is_not_written(self):
        """An edit that cannot be checked is refused, not written and hoped over."""
        res, calls = self._edit(changes={"username": "u2"}, preview={})
        self.assertFalse(res["ok"])
        self.assertIn("preview", res["error"].lower())
        self.assertEqual(calls["write"], [])

    def test_a_new_website_keeps_the_other_addresses(self):
        item = json.loads(json.dumps(FULL_ITEM))
        item["urls"].append({"label": "admin", "primary": False,
                             "href": "https://admin.github.com"})
        preview = json.loads(json.dumps(item))
        preview["urls"] = [{"label": "website", "primary": True,
                            "href": "https://example.com"}]
        res, calls = self._edit(item=item, url="example.com", preview=preview)
        self.assertFalse(res["ok"])
        self.assertIn("admin.github.com", res["error"])
        self.assertEqual(calls["write"], [])

    def test_an_item_with_a_passkey_can_still_be_edited(self):
        """The old template path had to refuse these outright."""
        item = json.loads(json.dumps(FULL_ITEM))
        item["fields"].append({"id": "pk", "type": "PASSKEY", "value": "x"})
        res, calls = self._edit(item=item, changes={"username": "u2"})
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(len(calls["write"]), 1)

    def test_a_generated_password_never_reaches_argv(self):
        """The rotate path, which is the common one, keeps op's secret to op."""
        res, calls = self._edit(generate_field="password",
                                password_recipe="letters,digits,32")
        argv = calls["write"][0]
        self.assertIn("--generate-password=letters,digits,32", argv)
        self.assertFalse([a for a in argv if a.startswith("password=")])
        self.assertIn("password", res["changed"])

    def test_a_generated_password_wins_over_a_typed_one(self):
        """Sending both would race op's own write."""
        _, calls = self._edit(generate_field="password",
                              changes={"password": "typed", "username": "u2"})
        argv = calls["write"][0]
        self.assertNotIn("typed", " ".join(argv))
        self.assertIn("username=u2", argv)

    def test_a_bad_recipe_is_refused(self):
        res, calls = self._edit(generate_field="password", password_recipe="rm -rf /")
        self.assertFalse(res["ok"])
        self.assertEqual(calls["write"], [])

    def test_nothing_changed_means_nothing_written(self):
        res, calls = self._edit(changes={"username": "octocat"})
        self.assertTrue(res["ok"])
        self.assertTrue(res["unchanged"])
        self.assertEqual(calls["dry"], [])
        self.assertEqual(calls["write"], [])

    def test_blanking_a_field_the_item_never_had_is_not_a_change(self):
        res, _ = self._edit(changes={"cvv": ""})
        self.assertTrue(res["unchanged"])

    def test_a_note_body_is_edited_like_any_other_field(self):
        """Editing a secure note erased it once: the form loaded no note and
        the save wrote that emptiness back. Nothing covered the backend half."""
        item = json.loads(json.dumps(FULL_ITEM))
        item["category"] = "SECURE_NOTE"
        item["fields"] = [{"id": "notesPlain", "type": "STRING",
                           "purpose": "NOTES", "value": "line one\nline two"}]
        item["urls"] = []
        res, calls = self._edit(item=item,
                                changes={"notesPlain": "line one\nline two\nline three"})
        self.assertTrue(res["ok"], res.get("error"))
        argv = calls["write"][0]
        self.assertIn("notesPlain=line one\nline two\nline three", argv)

    def test_an_unchanged_note_is_not_rewritten(self):
        item = json.loads(json.dumps(FULL_ITEM))
        item["category"] = "SECURE_NOTE"
        item["fields"] = [{"id": "notesPlain", "type": "STRING",
                           "purpose": "NOTES", "value": "the note"}]
        item["urls"] = []
        res, calls = self._edit(item=item, changes={"notesPlain": "the note"})
        self.assertTrue(res["unchanged"])
        self.assertEqual(calls["write"], [])

    def test_clearing_a_note_is_sent_as_a_change(self):
        """Emptying it on purpose has to reach op, unlike an untouched one."""
        item = json.loads(json.dumps(FULL_ITEM))
        item["category"] = "SECURE_NOTE"
        item["fields"] = [{"id": "notesPlain", "type": "STRING",
                           "purpose": "NOTES", "value": "the note"}]
        item["urls"] = []
        res, calls = self._edit(item=item, changes={"notesPlain": ""})
        self.assertTrue(res["ok"], res.get("error"))
        self.assertIn("notesPlain=", calls["write"][0])

    def test_a_field_can_be_cleared(self):
        """`field=` is op's own way to empty one, and it must reach op."""
        res, calls = self._edit(changes={"username": ""})
        self.assertTrue(res["ok"], res.get("error"))
        self.assertIn("username=", calls["write"][0])

    def test_an_unmodelled_field_is_refused_not_guessed(self):
        """A free-form id could carry op's own "." and "[" syntax into argv."""
        res, calls = self._edit(changes={"custom1": "x", "username": "u2"})
        self.assertFalse(res["ok"])
        self.assertIn("custom1", res["error"])
        self.assertEqual(calls["write"], [])

    def test_the_title_travels_as_a_flag(self):
        res, calls = self._edit(title="Renamed")
        self.assertTrue(res["ok"], res.get("error"))
        argv = calls["write"][0]
        self.assertEqual(argv[argv.index("--title") + 1], "Renamed")

    def test_the_url_travels_as_a_flag_and_is_normalized(self):
        _, calls = self._edit(url="example.com")
        argv = calls["write"][0]
        self.assertEqual(argv[argv.index("--url") + 1], "https://example.com")

    def test_an_unchanged_url_is_not_resent(self):
        res, _ = self._edit(url="https://github.com")
        self.assertTrue(res["unchanged"])

    def test_an_empty_url_leaves_the_website_alone(self):
        """Most categories have no website box; empty means "not supplied"."""
        res, calls = self._edit(url="", changes={"username": "u2"})
        self.assertTrue(res["ok"], res.get("error"))
        self.assertNotIn("--url", calls["write"][0])

    def test_a_non_web_url_is_refused(self):
        res, calls = self._edit(url="javascript:alert(1)")
        self.assertFalse(res["ok"])
        self.assertEqual(calls["write"], [])

    def test_a_dry_run_stops_at_the_preview(self):
        res, calls = self._edit(changes={"username": "u2"}, dry_run=True)
        self.assertTrue(res["ok"])
        self.assertTrue(res["dryRun"])
        self.assertEqual(len(calls["dry"]), 1)
        self.assertEqual(calls["write"], [])

    def test_no_template_file_is_left_behind(self):
        """The edit path writes no plaintext to disk at all any more."""
        self._edit(changes={"password": "secret"})
        self.assertEqual(list(self.runtime.glob("omapass-edit*")), [])

    def test_an_edit_drops_the_cached_copy(self):
        self.service.item_details_cache["i1"] = {"timestamp": 0, "data": {"id": "i1"}}
        self._edit(changes={"username": "u2"})
        self.assertNotIn("i1", self.service.item_details_cache)

    def test_editing_refuses_once_the_vault_locked_mid_request(self):
        def run(cmd, *a, **k):
            if cmd[:3] == ["op", "item", "get"]:
                return MagicMock(returncode=0, stdout=json.dumps(FULL_ITEM), stderr="")
            if "--dry-run" in cmd:
                return MagicMock(returncode=0, stdout=json.dumps(
                    apply_assignments(FULL_ITEM, cmd)), stderr="")
            self.service.lock_epoch += 1
            return MagicMock(returncode=0, stdout="{}", stderr="")

        with patch.object(self.service, "check_op_installed", return_value=True):
            with patch("subprocess.run", side_effect=run):
                with patch.object(self.service, "sync") as sync:
                    res = self.service.edit_item(item_id="i1", changes={"username": "u"})
        self.assertFalse(res["ok"])
        sync.assert_not_called()

    def test_an_id_is_required(self):
        with patch.object(self.service, "check_op_installed", return_value=True):
            self.assertFalse(self.service.edit_item(item_id="")["ok"])

    # The list on screen is built from the helper's cached list. A write that
    # does not touch it leaves the change invisible until the background sync
    # happens to land, which is how a renamed item kept its old title.

    def test_a_rename_reaches_the_cached_list_at_once(self):
        self.service.items = [{"id": "i1", "title": "GitHub", "username": "octocat",
                               "url": "https://github.com", "category": "LOGIN"}]
        res, _ = self._edit(title="Renamed")
        self.assertTrue(res["ok"], res.get("error"))
        row = next(i for i in self.service.items if i["id"] == "i1")
        self.assertEqual(row["title"], "Renamed")

    def test_a_new_username_and_website_reach_the_cached_list(self):
        self.service.items = [{"id": "i1", "title": "GitHub", "username": "octocat",
                               "url": "https://github.com", "category": "LOGIN"}]
        self._edit(changes={"username": "someone-else"}, url="example.com")
        row = next(i for i in self.service.items if i["id"] == "i1")
        self.assertEqual(row["username"], "someone-else")
        self.assertEqual(row["url"], "https://example.com")

    # These call the cache writers directly. Driving them through edit_item
    # proved nothing: it checks the epoch itself and returns before the cache
    # is touched, so the test passed with the guard removed.

    def test_a_patch_from_a_locked_session_is_dropped(self):
        """A lock empties the cache and deletes it from disk. Writing a row
        back afterwards would rebuild both, and a later load would read that
        as a vault still unlocked."""
        self.service.items = [{"id": "i1", "title": "GitHub"}]
        stale = self.service.lock_epoch
        self.service.lock_epoch += 1
        self.service._patch_cached_item("i1", stale, {"title": "Renamed"})
        self.assertEqual(self.service.items[0]["title"], "GitHub")

    def test_a_patch_from_the_current_session_lands(self):
        self.service.items = [{"id": "i1", "title": "GitHub"}]
        self.service._patch_cached_item("i1", self.service.lock_epoch,
                                        {"title": "Renamed"})
        self.assertEqual(self.service.items[0]["title"], "Renamed")

    def test_an_edit_does_not_resurrect_an_item_deleted_under_it(self):
        """Lookup and write-back happen in one critical section, so a row
        removed in between stays removed."""
        self.service.items = []
        self.service._patch_cached_item("i1", self.service.lock_epoch,
                                        {"title": "Renamed"})
        self.assertEqual(self.service.items, [])

    def test_an_edit_does_not_invent_a_row_for_an_item_not_listed(self):
        """A vault the list has never synced must not gain a phantom entry."""
        self.service.items = []
        res, _ = self._edit(title="Renamed")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(self.service.items, [])


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
