"""Where the helper keeps its runtime state, and how it opens those files.

Everything here lives in $XDG_RUNTIME_DIR, has a predictable name, and is
readable only by its owner. The directory is validated rather than trusted:
predictable names in a shared directory would let another user pre-create our
files and read what lands in them.
"""

import os
import pathlib
import stat

def _is_private_dir(path: pathlib.Path) -> bool:
    """True when path is a real directory owned by us and closed to others."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return (
        stat.S_ISDIR(st.st_mode)
        and st.st_uid == os.getuid()
        and not (st.st_mode & 0o077)
    )


def runtime_dir() -> pathlib.Path:
    """Returns secure user runtime directory.

    XDG_RUNTIME_DIR is checked rather than trusted: everything here has a
    predictable name, so a world-writable or foreign-owned directory would let
    another user pre-create our files and read what lands in them.
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg and _is_private_dir(pathlib.Path(xdg)):
        return pathlib.Path(xdg)

    fallback = pathlib.Path(f"/tmp/omapass-{os.getuid()}")
    fallback.mkdir(mode=0o700, parents=True, exist_ok=True)
    # A pre-existing /tmp path could be a symlink or another user's directory.
    st = os.lstat(fallback)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise RuntimeError(f"{fallback} is not a directory owned by this user")
    if st.st_mode & 0o077:
        os.chmod(fallback, 0o700)
        st = os.lstat(fallback)
    if not _is_private_dir(fallback):
        raise RuntimeError(f"{fallback} could not be made private")
    return fallback


def write_private(path: pathlib.Path, text: str) -> None:
    """Writes text to path, 0600 from the moment the file exists.

    O_NOFOLLOW so a symlink left in our place is refused rather than followed.
    """
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def open_private(path: pathlib.Path):
    """Opens (creating if needed) a 0600 lock file for flock."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    return os.fdopen(fd, "w")


def is_our_wipe_process(pid: int) -> bool:
    """True when pid is one of our own clipboard-wipe children.

    Guards against PID reuse: without this a recycled pid means we SIGTERM an
    unrelated process of the user's.

    Matched on shape, not on substrings. Asking whether the command line
    merely contains "omapass" and "_wipe" also says yes to an editor with
    this file open, to a grep for it, and to the test suite run with
    `-k _wipe`, all of which would then be signalled.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = f.read().decode("utf-8", "replace").split("\0")
    except OSError:
        return False
    if not argv or not pathlib.Path(argv[0]).name.startswith("python"):
        return False
    wanted = pathlib.Path(str(entry_script())).name
    for i, arg in enumerate(argv[:-1]):
        if arg and pathlib.Path(arg).name == wanted and argv[i + 1] == "_wipe":
            return True
    return False


def socket_path() -> pathlib.Path:
    return runtime_dir() / "omapass.sock"


def cache_path() -> pathlib.Path:
    return runtime_dir() / "omapass-cache.json"


def daemon_lock_path() -> pathlib.Path:
    return runtime_dir() / "omapass-daemon.lock"


def daemon_pid_path() -> pathlib.Path:
    return runtime_dir() / "omapass-daemon.pid"


def startup_lock_path() -> pathlib.Path:
    return runtime_dir() / "omapass-startup.lock"


def clipboard_lock_path() -> pathlib.Path:
    return runtime_dir() / "omapass-clipboard.lock"


def wipe_pid_path() -> pathlib.Path:
    return runtime_dir() / "omapass-wipe.pid"


def token_path() -> pathlib.Path:
    return runtime_dir() / "omapass-clipboard.token"


# Set by the entry script. The daemon respawns itself by path, and a module
# file inside the package is not something python can run as `serve`.
_ENTRY_SCRIPT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent / "omapass-agent.py"


def set_entry_script(path) -> None:
    global _ENTRY_SCRIPT
    _ENTRY_SCRIPT = pathlib.Path(path).resolve()


def entry_script() -> pathlib.Path:
    return _ENTRY_SCRIPT
