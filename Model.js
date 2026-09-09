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
  if (raw.length === 4) {
    // Four digits are ambiguous: op stores a month and year as six, so these
    // come from somewhere else and could be MMYY or YYMM. Whichever half is
    // a real month decides it, and if both could be, the leading pair wins,
    // because a person typing an expiry types the month first.
    var lead = parseInt(raw.substring(0, 2), 10);
    var tail = parseInt(raw.substring(2, 4), 10);
    if (lead >= 1 && lead <= 12) return raw.substring(0, 2) + "/" + raw.substring(2, 4);
    if (tail >= 1 && tail <= 12) return raw.substring(2, 4) + "/" + raw.substring(0, 2);
    return String(val || "");
  }
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

// ---------------------------------------------------------------- Create form
//
// The form is driven by a spec rather than hand-built QML, so adding a
// category means adding an entry here. Each field names the widget to render
// by 1Password's own field type, which is the vocabulary op already speaks:
// https://www.1password.dev/cli/item-fields/
//
// These are curated, not a dump of `op item template get`. A Login template
// carries a dozen mostly-empty fields, and a form with twelve blanks is worse
// than one with four.
var CREATE_SPECS = {
  LOGIN: {
    label: "Login",
    icon: "\u{f0306}",
    generates: "password",
    fields: [
      { id: "title",    label: "Title",    type: "STRING",    required: true,
        placeholder: "GitHub" },
      { id: "username", label: "Username", type: "STRING",
        placeholder: "you@example.com" },
      { id: "password", label: "Password", type: "CONCEALED", generate: true },
      { id: "url",      label: "Website",  type: "URL",
        placeholder: "github.com" }
    ]
  },
  PASSWORD: {
    label: "Password",
    icon: "\u{f0306}",
    generates: "password",
    fields: [
      { id: "title",    label: "Title",    type: "STRING",    required: true,
        placeholder: "Router admin" },
      { id: "password", label: "Password", type: "CONCEALED", generate: true },
      { id: "url",      label: "Website",  type: "URL", placeholder: "192.168.1.1" }
    ]
  },
  CREDIT_CARD: {
    label: "Credit card",
    icon: "\u{f092f}",
    fields: [
      { id: "title",      label: "Title",       type: "STRING", required: true,
        placeholder: "Acme Bank Visa" },
      { id: "cardholder", label: "Cardholder",  type: "STRING",
        placeholder: "A N Other" },
      { id: "ccnum",      label: "Card number", type: "CONCEALED",
        placeholder: "4242 4242 4242 4242" },
      { id: "expiry",     label: "Expiry",      type: "MONTH_YEAR",
        placeholder: "202812" },
      { id: "cvv",        label: "Security code (CVV)", type: "CONCEALED",
        placeholder: "123" }
    ]
  },
  SECURE_NOTE: {
    label: "Secure note",
    icon: "\u{f039e}",
    fields: [
      { id: "title",     label: "Title", type: "STRING", required: true,
        placeholder: "Recovery codes" },
      { id: "notesPlain", label: "Note", type: "MULTILINE",
        placeholder: "Anything you want kept" }
    ]
  }
};

// The order the category chooser offers them in.
var CREATE_CATEGORIES = ["LOGIN", "PASSWORD", "CREDIT_CARD", "SECURE_NOTE"];

function createCategories() {
  var out = [];
  for (var i = 0; i < CREATE_CATEGORIES.length; i++) {
    var id = CREATE_CATEGORIES[i];
    out.push({ id: id, label: CREATE_SPECS[id].label, icon: CREATE_SPECS[id].icon });
  }
  return out;
}

// Only some categories have a field 1Password can generate for us.
function generatedFieldFor(category) {
  var spec = createSpec(category);
  return spec.generates || "";
}

function createSpec(category) {
  return CREATE_SPECS[String(category || "LOGIN").toUpperCase()] || CREATE_SPECS.LOGIN;
}

// The widget a field type maps to. Unknown types fall back to plain text
// rather than vanishing from the form.
function inputKindFor(type) {
  switch (String(type || "").toUpperCase()) {
  case "CONCEALED": return "secret";
  case "URL":       return "url";
  case "EMAIL":     return "email";
  case "MONTH_YEAR": return "monthYear";
  case "MULTILINE": return "multiline";
  case "MENU":      return "select";
  default:          return "text";
  }
}

// Validation runs before the helper is called, so a bad form never becomes a
// failed op invocation. Returns a map of field id to message; empty means ok.
function validateCreate(spec, values) {
  var errors = {};
  for (var i = 0; i < spec.fields.length; i++) {
    var f = spec.fields[i];
    var raw = values[f.id] === undefined || values[f.id] === null ? "" : String(values[f.id]);
    var v = raw.trim();

    if (f.required && v.length === 0) {
      errors[f.id] = f.label + " is required";
      continue;
    }
    if (v.length === 0) continue;

    if (f.type === "URL" && !looksLikeWebUrl(v)) {
      errors[f.id] = "Enter a web address, or leave it blank";
    } else if (f.type === "EMAIL" && v.indexOf("@") < 1) {
      errors[f.id] = "That does not look like an email address";
    } else if (f.type === "MONTH_YEAR" && !/^\d{4}[-/]?(0[1-9]|1[0-2])$/.test(v)) {
      // The month itself, not just its length: 202899 counted as valid here
      // and was refused by op after the form had already been submitted.
      errors[f.id] = "Use YYYYMM, with a month from 01 to 12";
    }
  }
  return errors;
}

// The fields a submit should actually send.
//
// Pulled out of the form so it can be tested: getting this wrong is how an
// edit loses data. Three rules, and each one is a defect that happened.
//
//   - A field op is generating is omitted entirely. Sending a value alongside
//     --generate-password races it, and sending the box the Generate toggle
//     cleared sets the password to nothing.
//   - An edit sends only what changed. Sending the whole form pushes the
//     copy loaded minutes ago back over a password rotated since.
//   - A create sends everything, because there is nothing to diff against.
//
// `originals` is empty for a create. Returns a map of field id to
// { value, type }, which is what the helper's `changes` is built from.
function submitFields(spec, values, originals, generateField, editing) {
  var out = {};
  var orig = originals || {};
  for (var i = 0; i < spec.fields.length; i++) {
    var f = spec.fields[i];
    if (f.id === "title" || f.id === "url") continue;
    if (generateField && f.id === generateField) continue;
    var val = values[f.id] === undefined || values[f.id] === null ? "" : String(values[f.id]);
    if (editing) {
      var was = orig[f.id] === undefined || orig[f.id] === null ? "" : String(orig[f.id]);
      if (val === was) continue;
    }
    out[f.id] = { value: val, type: f.type };
  }
  return out;
}

// The title or website a submit should send.
//
// Empty means "not supplied" to the helper, so an edit that did not touch
// these sends nothing for them. Sending the form's copy regardless would
// write a title minutes old back over one changed elsewhere since, which is
// the same overwrite `submitFields` avoids for ordinary fields.
function submitText(values, originals, key, editing) {
  var now = values[key] === undefined || values[key] === null ? "" : String(values[key]);
  if (!editing) return now;
  var raw = (originals || {})[key];
  var was = raw === undefined || raw === null ? "" : String(raw);
  return now === was ? "" : now;
}

// op's --url can set a website but not remove one, so blanking the box would
// do nothing at all. The form says so rather than swallowing it.
function urlClearedByEdit(values, originals, editing) {
  if (!editing) return false;
  var was = (originals || {}).url;
  var now = values.url;
  return String(was || "").trim().length > 0 && String(now || "").trim().length === 0;
}

function hasErrors(errors) {
  for (var k in errors) { if (errors.hasOwnProperty(k)) return true; }
  return false;
}

// Mirrors the helper's own rule: a bare host is fine and becomes https, but a
// non-web scheme is refused rather than quietly stored.
function looksLikeWebUrl(value) {
  var v = String(value || "").trim();
  if (v.length === 0) return false;
  if (v.indexOf("://") === -1) {
    if (v.indexOf("//") === 0) return true;
    var head = v.split("/")[0];
    return head.indexOf(":") === -1 && head.indexOf(".") > 0;
  }
  var scheme = v.split("://")[0].toLowerCase();
  return scheme === "http" || scheme === "https";
}
