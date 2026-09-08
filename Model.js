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

function maskText(val, length) {
  var len = length || (val ? String(val).length : 12);
  var dots = "";
  for (var i = 0; i < Math.min(len, 16); i++) {
    dots += "•";
  }
  return dots;
}
