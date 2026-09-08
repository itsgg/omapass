"""Tunable constants and the fixed vocabulary the helper matches against.

Kept apart from behaviour so the numbers that govern how long a secret lives
are in one place and can be read without reading the daemon.
"""

PLUGIN_ID = "gg.omapass"
DEFAULT_CLIPBOARD_TIMEOUT = 30
MAX_CLIPBOARD_TIMEOUT = 3600

# How long a decrypted item stays in the daemon's memory before it is refetched.
# Short on purpose: it bounds how long plaintext secrets live in RAM and keeps
# TOTP codes from going stale (a code is only valid for a 30s window).
DETAILS_CACHE_TTL = 90.0

# How long a cached "vault is unlocked" verdict survives a daemon restart.
# The on-disk cache lives in the tmpfs runtime dir, so it never outlives a
# login, but 1Password locks itself on idle long before that.
UNLOCK_CACHE_TTL = 8 * 3600.0

# Largest single RPC request the daemon will buffer, so a local client cannot
# grow the daemon's memory without bound.
MAX_REQUEST_BYTES = 1 << 20

# Substrings 1Password uses when the CLI is not authorized to read the vault.
AUTH_ERROR_MARKERS = (
    "not currently signed in",
    "not signed in",
    "session expired",
    "authorization prompt",
    "no account found",
    "you are not authorized",
    "is locked",
    "vault is locked",
    "connect to the 1password app",
)

# Backoff between clipboard-clear attempts, in seconds. The trailing 0 is the
# final attempt, after which the user is warned rather than left unaware that a
# credential is still on the clipboard.
WIPE_RETRY_DELAYS = (0.5, 2.0, 5.0, 15.0, 0)

# TOTP window in seconds when the item does not say otherwise. Most issuers
# use 30, but the period is per-token and some use 60.
DEFAULT_TOTP_PERIOD = 30

# Schemes an item URL may be opened with. Everything else (file:, javascript:,
# and friends) is refused rather than handed to xdg-open.
ALLOWED_URL_SCHEMES = ("http", "https")
