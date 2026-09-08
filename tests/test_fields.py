"""Field resolution: turning "the password" into a value of a real item."""

import unittest

from support import IsolatedRuntimeDir  # noqa: F401  (path setup)
from omapass import fields


class TestFieldScoring(unittest.TestCase):
    def test_exact_id_outranks_purpose_outranks_substring_outranks_type(self):
        exact = {"id": "password", "label": "x", "type": "STRING"}
        purpose = {"id": "c1", "label": "x", "purpose": "PASSWORD", "type": "STRING"}
        weak = {"id": "c2", "label": "my password", "type": "STRING"}
        none = {"id": "c3", "label": "nothing", "type": "STRING"}
        self.assertGreater(fields.field_score(exact, "password"),
                           fields.field_score(purpose, "password"))
        self.assertGreater(fields.field_score(purpose, "password"),
                           fields.field_score(weak, "password"))
        self.assertEqual(fields.field_score(none, "password"), 0)

    def test_a_decoy_never_beats_the_real_field(self):
        item = {"fields": [
            {"id": "verification_url", "type": "STRING", "label": "verification url",
             "value": "https://x.test"},
            {"id": "cvv", "type": "CONCEALED", "label": "cvv", "value": "123"},
            {"id": "old_card_number", "type": "STRING", "label": "old card number",
             "value": "1111222233334444"},
            {"id": "ccnum", "type": "CREDIT_CARD_NUMBER", "label": "number",
             "value": "4242424242424242"},
            {"id": "validFrom", "type": "MONTH_YEAR", "label": "valid from", "value": "202001"},
            {"id": "expiry", "type": "MONTH_YEAR", "label": "expires", "value": "202812"},
        ]}
        self.assertEqual(fields.match_field(item, "cvv"), "123")
        self.assertEqual(fields.match_field(item, "ccnum"), "4242424242424242")
        self.assertEqual(fields.match_field(item, "expiry"), "202812")

    def test_exact_id_beats_a_label_that_looks_like_one(self):
        item = {"fields": [
            {"id": "note_1", "type": "STRING", "label": "recovery_code", "value": "decoy"},
            {"id": "recovery_code", "type": "CONCEALED", "label": "Recovery", "value": "real"},
        ]}
        self.assertEqual(fields.match_field(item, "recovery_code"), "real")

    def test_empty_values_are_skipped(self):
        item = {"fields": [
            {"id": "password", "purpose": "PASSWORD", "label": "password", "value": ""},
            {"id": "other", "type": "CONCEALED", "label": "passphrase", "value": "real"},
        ]}
        self.assertEqual(fields.match_field(item, "password"), "real")

    def test_an_exact_id_outranks_an_exact_label(self):
        """A custom field labelled "password" must not beat the real one."""
        item = {"fields": [
            {"id": "custom_a", "type": "STRING", "label": "password", "value": "decoy"},
            {"id": "password", "type": "CONCEALED", "label": "Login secret", "value": "real"},
        ]}
        self.assertEqual(fields.match_field(item, "password"), "real")

    def test_bank_account_fields_resolve(self):
        """The rule key is lowercase because requests arrive lowercased."""
        item = {"fields": [
            {"id": "custom_1", "type": "STRING", "label": "Account number", "value": "12345678"},
            {"id": "owner", "type": "STRING", "label": "owner", "value": "A N Other"},
        ]}
        self.assertEqual(fields.match_field(item, "accountno"), "12345678")
        self.assertEqual(fields.match_field(item, "owner"), "A N Other")

    def test_notes_come_from_their_own_key(self):
        self.assertEqual(fields.match_field({"notes": "  keep  ", "fields": []}, "notes"), "  keep  ")
        self.assertIsNone(fields.match_field({"notes": "", "fields": []}, "notes"))

    def test_unknown_field_matches_nothing(self):
        item = {"fields": [{"id": "a", "label": "a", "value": "v"}]}
        self.assertIsNone(fields.match_field(item, "nope"))


class TestSearchMatching(unittest.TestCase):
    def test_subsequence(self):
        self.assertTrue(fields.subsequence_match("gthb", "github"))
        self.assertTrue(fields.subsequence_match("abc", "aXbXc"))
        self.assertFalse(fields.subsequence_match("bca", "abc"))
        self.assertFalse(fields.subsequence_match("x", ""))
        self.assertFalse(fields.subsequence_match("", "abc"))


class TestUrlNormalisation(unittest.TestCase):
    def test_bare_hosts_get_https(self):
        self.assertEqual(fields.normalize_url("github.com"), "https://github.com")
        self.assertEqual(fields.normalize_url("//x.test"), "https://x.test")

    def test_web_urls_pass_through(self):
        self.assertEqual(fields.normalize_url("http://x.test/a"), "http://x.test/a")
        self.assertEqual(fields.normalize_url("https://x.test/a?b=c"), "https://x.test/a?b=c")

    def test_everything_else_is_refused(self):
        for bad in ["file:///etc/passwd", "javascript:alert(1)", "mailto:a@b.c",
                    "ftp://x.test", "   ", "", None]:
            self.assertIsNone(fields.normalize_url(bad), bad)


class TestTotpPeriod(unittest.TestCase):
    def test_read_from_the_uri(self):
        self.assertEqual(fields.totp_period_from_uri("otpauth://totp/A?secret=S&period=60"), 60)
        self.assertEqual(fields.totp_period_from_uri("otpauth://totp/A?period=15&secret=S"), 15)

    def test_bounded_and_defaulted(self):
        for uri in ["otpauth://totp/A?secret=S", "otpauth://totp/A?secret=S&period=0",
                    "otpauth://totp/A?secret=S&period=99999",
                    "otpauth://totp/A?secret=S&period=abc", "not-a-uri", "", None]:
            self.assertEqual(fields.totp_period_from_uri(uri), 30, uri)


if __name__ == "__main__":
    unittest.main()
