// Model utilities and constants for OmaPass

var CATEGORIES = [
  { id: "ALL", label: "All", icon: "󰌆" },
  { id: "LOGINS", label: "Logins", icon: "󰌆" },
  { id: "CARDS", label: "Cards", icon: "󰤯" },
  { id: "NOTES", label: "Notes", icon: "󰎞" },
  { id: "FAVORITES", label: "Favorites", icon: "󰓎" }
];

function categoryIcon(category) {
  var cat = String(category || "").toUpperCase();
  if (cat === "LOGIN" || cat === "PASSWORD") return "󰌆";
  if (cat === "CREDIT_CARD" || cat === "BANK_ACCOUNT") return "󰤯";
  if (cat === "SECURE_NOTE" || cat === "DOCUMENT") return "󰎞";
  if (cat === "SERVER" || cat === "DATABASE") return "󰒋";
  if (cat === "SSH_KEY") return "󰌌";
  return "󰌆";
}

function subtitleText(item) {
  if (!item) return "";
  if (item.username && item.username.length > 0) return item.username;
  if (item.url && item.url.length > 0) {
    try {
      var u = item.url.replace(/^https?:\/\//i, "").replace(/^www\./i, "");
      return u.split("/")[0];
    } catch (e) {
      return item.url;
    }
  }
  if (item.vault) return item.vault;
  return item.category || "";
}

function fieldIcon(field) {
  if (!field) return "󰈔";
  var fid = String(field.id || "").toLowerCase();
  var purpose = String(field.purpose || "").toUpperCase();
  var type = String(field.type || "").toUpperCase();
  var label = String(field.label || "").toLowerCase();

  if (purpose === "PASSWORD" || fid.indexOf("password") !== -1 || fid.indexOf("pin") !== -1 || label.indexOf("password") !== -1 || label.indexOf("pin") !== -1) return "󰌆";
  if (purpose === "USERNAME" || fid.indexOf("user") !== -1 || label.indexOf("user") !== -1 || fid.indexOf("email") !== -1 || label.indexOf("email") !== -1) return "󰋽";
  if (type === "OTP" || fid.indexOf("otp") !== -1 || fid.indexOf("totp") !== -1 || label.indexOf("one-time") !== -1) return "󰄬";
  // Ordered before the generic card branch: "cardholder" contains "card", so
  // the name on the card was picking up a credit-card glyph.
  if (fid.indexOf("cardholder") !== -1 || label.indexOf("cardholder") !== -1 || label.indexOf("name on card") !== -1) return "󰋽";
  if (fid === "cvv" || fid.indexOf("cvv") !== -1 || label.indexOf("verification") !== -1 || label.indexOf("security code") !== -1) return "";
  if (type === "MONTH_YEAR" || fid.indexOf("expiry") !== -1 || label.indexOf("expiry") !== -1 || label.indexOf("expiration") !== -1) return "󰃭";
  if (type === "CREDIT_CARD_NUMBER" || fid.indexOf("ccnum") !== -1 || fid.indexOf("card") !== -1 || label.indexOf("card") !== -1 || fid === "cvv" || label.indexOf("verification") !== -1) return "󰤯";
  if (type === "URL" || fid.indexOf("url") !== -1 || fid.indexOf("website") !== -1 || label.indexOf("website") !== -1) return "󰖟";
  if (purpose === "NOTES" || fid.indexOf("note") !== -1) return "󰎞";
  return "󰈔";
}

function fieldDisplayName(field) {
  if (!field) return "";
  var label = String(field.label || field.id || "");
  if (label === "ccnum") return "Card Number";
  if (label === "cvv") return "Security Code (CVV)";
  if (label === "expiry") return "Expiry Date";
  if (label === "cardholder") return "Cardholder Name";
  if (label === "validFrom") return "Valid From";
  if (label === "pin") return "PIN";
  // Capitalize words
  return label.replace(/([a-z])([A-Z])/g, "$1 $2")
              .replace(/[-_]/g, " ")
              .replace(/\b\w/g, function(c) { return c.toUpperCase(); });
}

// A fixed run of dots, deliberately not proportional to the value: matching
// the length told a shoulder-surfer how long a password is, and told them a
// CVV is exactly three digits.
function maskText(val, length) {
  return "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022";
}

// Quick actions that make sense for an item, keyed off its category.
//
// Offering "copy password" on a credit card is not merely noise: the helper
// has nothing to match, so the click fails with an error the user never asked
// for. This is category-level, not field-level: `op item list` returns no
// field data, so a login is offered TOTP whether or not it has one, and the
// copy reports "no one-time password on this item" if it does not.
// Three slots, in the order the keyboard binds them:
//   primary   the secret itself  (Shift+Enter)
//   identity  who it belongs to
//   extra     second factor or security code  (Ctrl+Enter)
function quickFieldsFor(item) {
  var cat = String((item && item.category) || "").toUpperCase();
  if (cat === "CREDIT_CARD") {
    return { primary: "ccnum", identity: "cardholder", extra: "cvv" };
  }
  if (cat === "BANK_ACCOUNT") {
    // A bank account is not a card: it has an account number and a PIN, and
    // offering it a CVV was as wrong as offering a card a password.
    return { primary: "accountno", identity: "owner", extra: "pin" };
  }
  if (cat === "SECURE_NOTE" || cat === "DOCUMENT") {
    return { primary: "notes", identity: "", extra: "" };
  }
  if (cat === "SSH_KEY") {
    return { primary: "password", identity: "username", extra: "" };
  }
  return { primary: "password", identity: "username", extra: "otp" };
}

// Ordered list of the fields above that this item actually has, so a row
// renders one button per real action and no dead ones.
function quickFieldList(item) {
  var f = quickFieldsFor(item);
  var out = [];
  if (f.primary) out.push(f.primary);
  if (f.identity) out.push(f.identity);
  if (f.extra) out.push(f.extra);
  return out;
}

var FIELD_META = {
  password:   { icon: "\u{f0306}", label: "Password" },
  username:   { icon: "\u{f02fd}", label: "Username" },
  otp:        { icon: "\u{f012c}", label: "TOTP" },
  ccnum:      { icon: "\u{f092f}", label: "Card number" },
  accountno:  { icon: "\u{f092f}", label: "Account number" },
  owner:      { icon: "\u{f02fd}", label: "Account holder" },
  pin:        { icon: "\uf023", label: "PIN" },
  cvv:        { icon: "\uf023", label: "CVV" },
  cardholder: { icon: "\u{f02fd}", label: "Cardholder" },
  expiry:     { icon: "\u{f00ed}", label: "Expiry" },
  notes:      { icon: "\u{f039e}", label: "Note" }
};

function fieldMeta(field) {
  return FIELD_META[field] || { icon: "\u{f0214}", label: field || "Value" };
}

function fieldLabelFor(field) { return fieldMeta(field).label; }
function fieldIconFor(field) { return fieldMeta(field).icon; }

// 1Password stores a card expiry as YYYYMM; "202812" on screen reads as a
// meaningless number.
function formatExpiry(val) {
  var raw = String(val || "").replace(/\D/g, "");
  if (raw.length === 6) return raw.substring(4, 6) + "/" + raw.substring(0, 4);
  if (raw.length === 4) return raw.substring(2, 4) + "/" + raw.substring(0, 2);
  return String(val || "");
}

// A 16-digit run is unreadable without grouping.
function formatCardNumber(val) {
  var raw = String(val || "").replace(/\s+/g, "");
  if (!/^[0-9]{12,19}$/.test(raw)) return String(val || "");
  return raw.replace(/([0-9]{4})/g, "$1 ").trim();
}

// What the details view should print for a field. Never used for copying:
// the clipboard always gets the value verbatim.
function displayValue(field) {
  if (!field) return "";
  var id = String(field.id || "").toLowerCase();
  var type = String(field.type || "").toUpperCase();
  var val = field.value === undefined || field.value === null ? "" : String(field.value);
  if (id.indexOf("expiry") !== -1 || type === "MONTH_YEAR") return formatExpiry(val);
  if (type === "CREDIT_CARD_NUMBER" || id.indexOf("ccnum") !== -1) return formatCardNumber(val);
  return val;
}
