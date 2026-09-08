// Tests for Model.js, the pure-JS half of the widget.
//
// Model.js is loaded as a QML JS library, not a module, so it is evaluated in
// a fresh context here rather than imported. That keeps the file free of any
// test-only export while still letting node run these in CI, where Qt is not
// available.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createContext, runInContext } from "node:vm";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const ctx = createContext({});
runInContext(readFileSync(join(root, "Model.js"), "utf8"), ctx);
const M = ctx;

// Values crossing back from the VM context carry that realm's prototypes, so
// deepEqual against a host literal fails on identity alone. Compare shape.
const plain = (v) => JSON.parse(JSON.stringify(v));

test("categoryIcon maps the categories the list actually shows", () => {
  assert.equal(M.categoryIcon("LOGIN"), M.categoryIcon("PASSWORD"));
  assert.equal(M.categoryIcon("CREDIT_CARD"), M.categoryIcon("BANK_ACCOUNT"));
  assert.notEqual(M.categoryIcon("SSH_KEY"), M.categoryIcon("LOGIN"));
  // Anything unknown still gets an icon rather than an empty cell.
  assert.ok(M.categoryIcon("SOMETHING_NEW").length > 0);
  assert.ok(M.categoryIcon(null).length > 0);
});

test("subtitleText prefers a username, then a bare host, then the vault", () => {
  assert.equal(M.subtitleText({ username: "octocat", url: "https://x.test" }), "octocat");
  assert.equal(M.subtitleText({ url: "https://www.example.com/a/b" }), "example.com");
  assert.equal(M.subtitleText({ url: "http://example.com" }), "example.com");
  assert.equal(M.subtitleText({ vault: "Personal" }), "Personal");
  assert.equal(M.subtitleText({ category: "LOGIN" }), "LOGIN");
  assert.equal(M.subtitleText(null), "");
});

test("quickFieldsFor gives each category actions it can actually perform", () => {
  const login = M.quickFieldsFor({ category: "LOGIN" });
  assert.deepEqual(plain(login), { primary: "password", identity: "username", extra: "otp" });

  // A card has no password, username or TOTP.
  const card = M.quickFieldsFor({ category: "CREDIT_CARD" });
  assert.equal(card.primary, "ccnum");
  assert.equal(card.extra, "cvv");
  assert.ok(!Object.values(plain(card)).includes("password"));

  // A bank account is not a card: no CVV.
  const bank = M.quickFieldsFor({ category: "BANK_ACCOUNT" });
  assert.equal(bank.primary, "accountNo");
  assert.ok(!Object.values(plain(bank)).includes("cvv"));

  const note = M.quickFieldsFor({ category: "SECURE_NOTE" });
  assert.equal(note.primary, "notes");
  assert.equal(note.identity, "");

  // An unknown category falls back to login-shaped actions.
  assert.equal(M.quickFieldsFor({ category: "WHATEVER" }).primary, "password");
  assert.equal(M.quickFieldsFor(null).primary, "password");
});

test("quickFieldList drops the slots a category does not use", () => {
  assert.deepEqual(plain(M.quickFieldList({ category: "SECURE_NOTE" })), ["notes"]);
  assert.deepEqual(plain(M.quickFieldList({ category: "LOGIN" })), ["password", "username", "otp"]);
  assert.equal(M.quickFieldList({ category: "SSH_KEY" }).length, 2);
});

test("maskText never reveals the length of what it hides", () => {
  const short = M.maskText("abc");
  const long = M.maskText("a-very-long-passphrase-indeed");
  assert.equal(short, long, "mask length must not track the value");
  assert.ok(short.length > 0);
  assert.equal(M.maskText(""), short);
});

test("formatExpiry turns 1Password's YYYYMM into something readable", () => {
  assert.equal(M.formatExpiry("202812"), "12/2028");
  assert.equal(M.formatExpiry("2812"), "12/28");
  // Anything unexpected is shown as-is rather than mangled.
  assert.equal(M.formatExpiry("whenever"), "whenever");
  assert.equal(M.formatExpiry(""), "");
});

test("formatCardNumber groups digits and leaves anything else alone", () => {
  assert.equal(M.formatCardNumber("4242424242424242"), "4242 4242 4242 4242");
  assert.equal(M.formatCardNumber("4242 4242 4242 4242"), "4242 4242 4242 4242");
  assert.equal(M.formatCardNumber("not-a-card"), "not-a-card");
  assert.equal(M.formatCardNumber("123"), "123");
});

test("displayValue formats by field type, never by guessing at the value", () => {
  assert.equal(M.displayValue({ id: "expiry", type: "MONTH_YEAR", value: "202812" }), "12/2028");
  assert.equal(
    M.displayValue({ id: "ccnum", type: "CREDIT_CARD_NUMBER", value: "4242424242424242" }),
    "4242 4242 4242 4242");
  // A password that merely looks like digits must not be regrouped.
  assert.equal(M.displayValue({ id: "password", type: "CONCEALED", value: "4242424242424242" }),
    "4242424242424242");
  assert.equal(M.displayValue(null), "");
});

test("fieldDisplayName expands 1Password's terse ids", () => {
  assert.equal(M.fieldDisplayName({ label: "ccnum" }), "Card Number");
  assert.equal(M.fieldDisplayName({ label: "cvv" }), "Security Code (CVV)");
  assert.equal(M.fieldDisplayName({ label: "expiry" }), "Expiry Date");
  assert.equal(M.fieldDisplayName({ label: "one_time_password" }), "One Time Password");
  assert.equal(M.fieldDisplayName(null), "");
});

test("fieldIcon distinguishes the fields a card actually has", () => {
  const cardholder = M.fieldIcon({ id: "cardholder", label: "cardholder" });
  const ccnum = M.fieldIcon({ id: "ccnum", type: "CREDIT_CARD_NUMBER", label: "number" });
  const cvv = M.fieldIcon({ id: "cvv", label: "cvv" });
  const expiry = M.fieldIcon({ id: "expiry", type: "MONTH_YEAR", label: "expiry" });
  // "cardholder" contains "card": it must not take the credit-card glyph.
  assert.notEqual(cardholder, ccnum);
  assert.notEqual(cvv, ccnum);
  assert.notEqual(expiry, ccnum);
});
