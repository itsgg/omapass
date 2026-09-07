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
