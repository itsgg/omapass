"""Turning a 1Password item into the value the user actually asked for.

1Password lets a user rename any field, so a login's password can be labelled
"admin key" and an unrelated field can carry a word that merely looks right.
Matching is therefore scored rather than first-hit: an exact id outranks a
purpose, which outranks a substring, which outranks a bare type.
"""

import re
import urllib.parse
from typing import Any, Dict, Optional

from .config import ALLOWED_URL_SCHEMES, DEFAULT_TOTP_PERIOD

# Scoring rather than first-match matters: an item with a
# "verification_url" field used to beat its real "cvv", and a "validFrom"
# of type MONTH_YEAR used to beat the actual expiry.
FIELD_RULES: Dict[str, Dict[str, Any]] = {
    "password": {"ids": {"password"}, "purposes": {"PASSWORD"}, "weak": ("password", "passphrase", "pin")},
    "username": {"ids": {"username"}, "purposes": {"USERNAME"}, "weak": ("username", "login name", "email")},
    "ccnum": {"ids": {"ccnum"}, "types": {"CREDIT_CARD_NUMBER"}, "weak": ("card number", "cardnumber")},
    "cvv": {"ids": {"cvv"}, "weak": ("cvv", "security code", "verification code", "card verification")},
    "cardholder": {"ids": {"cardholder"}, "weak": ("cardholder", "name on card")},
    "expiry": {"ids": {"expiry", "expires"}, "weak": ("expiry", "expiration"), "types": {"MONTH_YEAR"}},
    "accountNo": {"ids": {"accountno", "accountnumber"}, "weak": ("account number", "account no")},
    "owner": {"ids": {"owner", "accountholder"}, "weak": ("account holder", "owner")},
    "pin": {"ids": {"pin"}, "weak": ("pin",)},
}

def field_score(f: Dict[str, Any], field: str) -> int:
    """Ranks one item field as a candidate for `field`. 0 means no match."""
    rule = FIELD_RULES.get(field)
    fid = str(f.get("id", "")).lower()
    label = str(f.get("label", "")).lower()
    purpose = str(f.get("purpose", "")).upper()
    ftype = str(f.get("type", "")).upper()

    if rule is None:
        # An exact id beats a label that merely reads like one: an item can
        # carry a field labelled "recovery_code" alongside the real field
        # whose id is recovery_code, and the wrong one was winning on order.
        if fid == field:
            return 4
        return 2 if label == field else 0

    # Every signal is scored and the best wins. Returning on the first
    # hit let a weak signal shadow a stronger one: two MONTH_YEAR fields
    # both scored 1, so "valid from" could answer a request for "expiry".
    hay = label + " " + fid
    score = 0
    if fid in rule.get("ids", ()) or label in rule.get("ids", ()):
        score = max(score, 4)
    if purpose in rule.get("purposes", ()):
        score = max(score, 3)
    if any(token in hay for token in rule.get("weak", ())):
        score = max(score, 2)
    if ftype in rule.get("types", ()):
        # A type alone is the weakest evidence: MONTH_YEAR is worn by
        # validFrom as well as expiry.
        score = max(score, 1)
    return score

def match_field_entry(item_data: Dict[str, Any], field: str) -> Optional[Dict[str, Any]]:
    """Returns the best-scoring field dict for `field`, or None."""
    best_score = 0
    best: Optional[Dict[str, Any]] = None
    for f in item_data.get("fields", []):
        score = field_score(f, field)
        if score > best_score and str(f.get("value", "")):
            best_score = score
            best = f
    return best

def match_field(item_data: Dict[str, Any], field: str) -> Optional[str]:
    """Picks a named credential out of parsed item details.

    Matching is by score, not by position: 1Password lets the user rename
    any field, so a login's password can be labelled "admin key", while an
    unrelated field can carry a word that merely looks right.
    """
    if field == "notes":
        return str(item_data.get("notes") or "") or None

    best_score = 0
    best_value: Optional[str] = None
    for f in item_data.get("fields", []):
        score = field_score(f, field)
        if score > best_score:
            value = str(f.get("value", ""))
            if value:
                best_score = score
                best_value = value
    return best_value

# Fields the widget can ask for. `otp` is always fetched live; the rest
# come from the parsed item, because `op item get --fields label=X` only
# finds fields the user happened to label exactly that.
SUPPORTED_FIELDS = (
    "password", "username", "otp", "ccnum", "cvv", "cardholder", "expiry", "notes",
    "accountNo", "owner", "pin",
)

# A field may also be named by its own id, so the details view can copy a
# custom field without shipping its plaintext back to us. Bounded and
# conservative: this only ever selects a field of an item we already hold.
FIELD_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")



def subsequence_match(query: str, haystack: str) -> bool:
    """True when every character of query appears in haystack, in order.

    This is what makes "gthb" find "GitHub" - substring matching alone
    cannot, and the README promises fuzzy search.
    """
    if not query or not haystack:
        return False
    pos = 0
    for ch in query:
        pos = haystack.find(ch, pos)
        if pos < 0:
            return False
        pos += 1
    return True



def normalize_url(url: str) -> Optional[str]:
    """Returns a safe http(s) URL, or None if it cannot be one.

    1Password items routinely store bare hosts ("github.com"), which
    xdg-open cannot resolve; anything with a scheme we do not allow
    (file:, javascript:, ...) is refused rather than launched.
    """
    candidate = (url or "").strip()
    if not candidate:
        return None
    if "://" not in candidate:
        if candidate.startswith("//"):
            candidate = "https:" + candidate
        elif ":" in candidate.split("/")[0]:
            # A scheme-like prefix with no "//" (javascript:, mailto:, ...)
            return None
        else:
            candidate = "https://" + candidate
    scheme = candidate.split("://", 1)[0].lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        return None
    return candidate


def totp_period_from_uri(uri: Any) -> int:
    """Reads `period` out of an otpauth:// URI, in seconds.

    The countdown is wrong for the whole life of the token if this is assumed:
    a 60-second code shown with a 30-second timer looks expired while it is
    still valid, and valid once it is not.
    """
    try:
        text = str(uri or "")
        if not text.lower().startswith("otpauth://"):
            return DEFAULT_TOTP_PERIOD
        values = urllib.parse.parse_qs(urllib.parse.urlparse(text).query).get("period")
        if not values:
            return DEFAULT_TOTP_PERIOD
        period = int(values[0])
        # Bounded: a nonsense period would make the countdown lie rather than
        # simply be unknown.
        if 5 <= period <= 300:
            return period
    except (ValueError, TypeError):
        pass
    return DEFAULT_TOTP_PERIOD
