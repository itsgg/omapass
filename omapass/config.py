"""Tunable constants and the fixed vocabulary the helper matches against.

Kept apart from behaviour so the numbers that govern how long a secret lives
are in one place and can be read without reading the daemon.
"""

import hashlib
import pathlib
import re

# Bumped whenever the RPC surface changes. The daemon outlives a plugin
# update: the shell reloads the QML, but the old helper keeps serving, and a
# widget asking for an action it does not know about just gets "Unknown
# action". The version is checked on every connect so a stale daemon is
# replaced instead of quietly answering wrong.
#
# 4: edit_item takes generateField and a recipe, and edits with assignment
#    statements rather than a JSON template. A version 3 daemon left running
#    across the upgrade would still edit through a template, which op
#    documents as overwriting an item's passkey, so it has to be replaced.
# 5: ping carries a build id as well, and writes update the cached item list
#    before answering. A version 4 daemon answers a create without touching
#    that list, so the new item does not appear until a sync happens to land.
PROTOCOL_VERSION = 5


_BUILD_ID_CACHE = ""


def build_id() -> str:
    """A fingerprint of the helper code this process actually loaded.

    The version above documents deliberate changes to the wire contract, but
    it only replaces a running daemon if someone remembers to raise it, and
    forgetting is silent: the old daemon keeps answering with its old
    behaviour while the new code sits on disk unused. That has happened, so
    the source speaks for itself as well.

    Computed from the package directory plus the entry script, and not from
    the modules this process happens to have imported: the daemon reaches the
    code through cli.py and a caller need not, so an import-set fingerprint
    had the two disagree forever and no daemon was ever current.

    Cached, because in the daemon this must keep naming the code it started
    with even if the files on disk change underneath it.
    """
    global _BUILD_ID_CACHE
    if _BUILD_ID_CACHE:
        return _BUILD_ID_CACHE

    from . import paths

    package = pathlib.Path(__file__).resolve().parent
    sources = {path.resolve() for path in package.glob("*.py")}
    entry = paths.entry_script()
    if entry:
        sources.add(pathlib.Path(str(entry)).resolve())

    digest = hashlib.sha256()
    for path in sorted(sources):
        # The name, so moving code between modules is a change, but not the
        # directory, so the same code installed twice matches.
        digest.update(path.name.encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            # A source we cannot read is still part of the build, and
            # skipping it silently would let two different installs agree.
            digest.update(b"<unreadable>")
    _BUILD_ID_CACHE = digest.hexdigest()[:16]
    return _BUILD_ID_CACHE
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


# What `op` is asked to generate when creating a login. The password is made
# and stored by 1Password: it never passes through this helper.
DEFAULT_PASSWORD_RECIPE = "letters,digits,symbols,32"

# A recipe is passed to op as an argument, so it is checked against the shape
# op documents rather than forwarded blindly.
PASSWORD_RECIPE_RE = re.compile(
    r"^(letters|digits|symbols)(,(letters|digits|symbols))*(,\d{1,3})?$|^\d{1,3}$"
)
