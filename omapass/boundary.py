"""The process boundary: which interpreter runs the helper, where the programs
it calls come from, and what environment its children see.

Every process this helper starts receives credentials, either on stdin or
through the clipboard it writes, so the question of *which* program starts is
a security question. Three rules, each closing one way of putting someone
else's code in front of the vault:

- The interpreter is one fixed path, run in isolated mode (`-I`). Isolated
  mode ignores PYTHONPATH, PYTHONSTARTUP and the rest of the PYTHON* family,
  skips the user site directory, and keeps the script's directory off
  sys.path; the entry script adds its own package directory back explicitly.
- A program is looked up in a fixed list of directories, never in an
  inherited PATH, and the file found has to be a regular executable that
  neither it nor its directory lets other users rewrite.
- A child's environment is built from an allowlist. Nothing that steers a
  loader or an interpreter (LD_*, PYTHON*, PATH) survives; the desktop and
  session variables the tools need are named one by one.

Two more bound every process run() starts, since a program that hangs or
never stops talking is the other way to take the helper down: a hard
deadline, after which the whole process group is killed and reaped, and a
live cap on stdout and stderr, enforced as the bytes arrive rather than
after the process has finished filling memory.

The widget applies the same rules on its side (a fixed command, a cleared
environment, the same allowlist, a deadline per request), so the request
helper is already running under them by the time this module builds the
daemon's environment.
"""

import os
import signal
import stat
import subprocess
import threading
import time
from typing import Dict, List, Optional

# The one interpreter the helper runs under. A fixed path rather than
# whichever python3 is first on PATH: the widget names this same path.
PYTHON = "/usr/bin/python3"

# Where a program may come from. Searched in this order, and the PATH every
# child receives.
TRUSTED_BIN_DIRS = ("/usr/local/bin", "/usr/bin", "/bin")

# What a child may inherit from this process, by exact name. Kept in step with
# the widget's helperEnvironmentNames by a test, so the two allowlists cannot
# drift apart: the daemon sees only what the widget let through, and this list
# is what it passes on to op, the clipboard and the browser.
#
# Why each group is here:
#   HOME, USER, LOGNAME, LANG, LC_*   who is running, and how text is encoded
#   XDG_*                             the runtime directory op's socket and
#                                     our own files live in, the config and
#                                     data homes, and what xdg-open needs to
#                                     find the default browser
#   WAYLAND_DISPLAY, DISPLAY          the compositor wl-copy and wtype talk to
#   XAUTHORITY                        the X cookie an XWayland window needs
#   DBUS_SESSION_BUS_ADDRESS          notify-send, and the desktop app
#   HYPRLAND_INSTANCE_SIGNATURE       Hyprland's own socket
#   *CURSOR*, GDK_SCALE, *OZONE*      the 1Password window, when this helper
#   ELECTRON_OZONE_PLATFORM_HINT      has to start the app itself
#   OP_ACCOUNT                        which account op should use; a selector,
#                                     not a credential
SESSION_VARS = (
    "HOME", "USER", "LOGNAME", "LANG",
    "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "LC_TIME", "LC_NUMERIC", "LC_COLLATE",
    "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
    "XDG_STATE_HOME", "XDG_DATA_DIRS", "XDG_CONFIG_DIRS",
    "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "XDG_SESSION_ID",
    "XDG_SESSION_DESKTOP", "XDG_SEAT", "XDG_VTNR",
    "WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
    "HYPRLAND_INSTANCE_SIGNATURE",
    "XCURSOR_SIZE", "XCURSOR_THEME", "HYPRCURSOR_SIZE", "HYPRCURSOR_THEME",
    "GDK_SCALE", "OZONE_PLATFORM", "ELECTRON_OZONE_PLATFORM_HINT",
    "OP_ACCOUNT",
)


def child_environment(**extra: str) -> Dict[str, str]:
    """The whole environment a child gets: the allowlist, a fixed PATH, extra.

    `extra` is for values that must travel in the environment rather than
    argv, such as the clipboard wipe token. It wins over an inherited name.
    """
    env: Dict[str, str] = {}
    for name in SESSION_VARS:
        value = os.environ.get(name)
        if value:
            env[name] = value
    env["PATH"] = ":".join(TRUSTED_BIN_DIRS)
    env.update(extra)
    return env


def _trusted_executable(path: str) -> bool:
    """True when path is a regular executable nobody else can rewrite.

    Follows symlinks: /usr/bin/python3 is one. Both the file and the
    directory it was named through are checked, since a world-writable
    directory lets anyone replace the file without ever writing to it.
    """
    try:
        st = os.stat(path)
        dst = os.stat(os.path.dirname(path) or "/")
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode) or not os.access(path, os.X_OK):
        return False
    if st.st_uid not in (0, os.getuid()) or st.st_mode & stat.S_IWOTH:
        return False
    if dst.st_uid not in (0, os.getuid()) or dst.st_mode & stat.S_IWOTH:
        return False
    return True


def tool(name: str) -> Optional[str]:
    """The path a program runs from, or None when no trusted copy exists.

    A bare name is searched for in TRUSTED_BIN_DIRS. An absolute path is
    accepted only if it passes the same checks. Anything else, such as a
    relative path, is refused: there is no directory it would be trusted in.
    """
    if not name:
        return None
    if os.path.isabs(name):
        return name if _trusted_executable(name) else None
    if os.sep in name:
        return None
    for directory in TRUSTED_BIN_DIRS:
        candidate = os.path.join(directory, name)
        if _trusted_executable(candidate):
            return candidate
    return None


def interpreter() -> List[str]:
    """How to start another copy of this helper: the fixed python, isolated.

    Raises rather than falling back to sys.executable. The point of a fixed
    interpreter is that no environment can choose a different one, and a
    silent fallback would be exactly that.
    """
    if not _trusted_executable(PYTHON):
        raise FileNotFoundError(f"{PYTHON} is missing or not trustworthy")
    return [PYTHON, "-I"]


# What run() allows a process that was given no explicit deadline, and how
# much output it may produce before it is killed. The cap is far above any
# real answer (a 5000-item vault lists in about 3 MB) and exists so that a
# runaway cannot grow the helper's memory without bound.
DEFAULT_DEADLINE = 30.0
OUTPUT_CAP = 32 * 1024 * 1024


class OutputTooLarge(subprocess.SubprocessError):
    """A process was killed for producing more than the cap allows."""

    def __init__(self, argv, cap: int):
        super().__init__(f"{argv[0]} produced more than {cap} bytes and was stopped")
        self.cmd = argv
        self.cap = cap


def _prepare(argv: List[str], kwargs: dict) -> None:
    """Resolves argv[0] to its trusted path and closes the environment.

    argv[0] stays as written, so the child sees its own name and the tests
    see the command they expect; `executable` is what actually runs. An
    explicit `env` is taken as already closed: the callers that pass one
    built it with child_environment().
    """
    if not argv:
        raise ValueError("empty command")
    path = tool(argv[0])
    if path is None:
        raise FileNotFoundError(f"{argv[0]} is not installed in a trusted location")
    kwargs.setdefault("executable", path)
    kwargs.setdefault("env", child_environment())


def _drain(stream, sink: List[bytes], cap: int, overflow: threading.Event) -> None:
    """Reads a pipe as bytes arrive, stopping the moment the cap is passed.

    os.read on the descriptor, not stream.read(n): the latter waits for n
    bytes or EOF, which is not "live". Once over the cap this stops reading;
    the pipe fills, the child blocks on its next write, and the caller kills
    the group. Nothing past the cap is kept.
    """
    fd = stream.fileno()
    total = 0
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            return
        if not chunk:
            return
        total += len(chunk)
        if total > cap:
            overflow.set()
            return
        sink.append(chunk)


def kill_group(proc: subprocess.Popen) -> None:
    """Kills everything the process started, then reaps the process itself.

    The child was started in a new session, so its pid is its process group,
    and SIGKILL to the group reaches anything it spawned that did not leave
    the group on purpose. Killing only the child would leave a grandchild
    holding the pipe, and the secret it was handed, alive: wl-copy forks
    exactly such a child to hold the clipboard.
    """
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        pass


def run(argv: List[str], *, stdout=None, stderr=None, text: bool = False,
        timeout: Optional[float] = None, check: bool = False, input=None,
        cap: int = OUTPUT_CAP, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run with the boundary applied, and bounded.

    Every process gets a deadline (`timeout`, or DEFAULT_DEADLINE) and an
    output cap. Past either, the whole process group is killed and reaped
    and the caller gets subprocess.TimeoutExpired or OutputTooLarge, the
    same exceptions subprocess.run's callers already handle. Output is read
    live on a thread per pipe, so a noisy process is stopped while it is
    talking rather than after its output has been buffered whole.
    """
    _prepare(argv, kwargs)
    deadline = DEFAULT_DEADLINE if timeout is None else float(timeout)
    kwargs.setdefault("start_new_session", True)
    stdin = subprocess.PIPE if input is not None else kwargs.pop("stdin", subprocess.DEVNULL)
    proc = subprocess.Popen(argv, stdin=stdin, stdout=stdout, stderr=stderr, **kwargs)

    sinks = {"out": [], "err": []}
    overflow = threading.Event()
    readers = []
    for name, stream in (("out", proc.stdout), ("err", proc.stderr)):
        if stream is not None:
            t = threading.Thread(target=_drain, args=(stream, sinks[name], cap, overflow), daemon=True)
            t.start()
            readers.append(t)

    if input is not None:
        # On a thread: a child that never reads its stdin would otherwise
        # block this write past the pipe's buffer, and the deadline with it.
        data = input.encode("utf-8") if isinstance(input, str) else input

        def feed():
            try:
                proc.stdin.write(data)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        threading.Thread(target=feed, daemon=True).start()

    started = time.monotonic()
    timed_out = False
    while proc.poll() is None:
        if overflow.is_set():
            kill_group(proc)
            break
        if time.monotonic() - started > deadline:
            timed_out = True
            kill_group(proc)
            break
        time.sleep(0.02)
    for t, stream in zip(readers, [s for s in (proc.stdout, proc.stderr) if s is not None]):
        # After a group kill every writer is gone and EOF is immediate. A
        # writer that escaped the group (a setsid grandchild) keeps the pipe
        # open and the reader blocked; that reader keeps its descriptor
        # too. Closing it here would free the number for the next Popen,
        # and the blocked read would then wake on that process's output.
        t.join(timeout=2.0)
        if not t.is_alive():
            try:
                stream.close()
            except OSError:
                pass

    out = b"".join(sinks["out"]) if proc.stdout is not None else None
    err = b"".join(sinks["err"]) if proc.stderr is not None else None
    if text:
        out = out.decode("utf-8", "replace") if out is not None else None
        err = err.decode("utf-8", "replace") if err is not None else None
    if overflow.is_set():
        raise OutputTooLarge(argv, cap)
    if timed_out:
        raise subprocess.TimeoutExpired(argv, deadline, output=out, stderr=err)
    result = subprocess.CompletedProcess(argv, proc.returncode, out, err)
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, argv, out, err)
    return result


def popen(argv: List[str], **kwargs):
    """subprocess.Popen with the boundary applied.

    For the processes that are handed a secret on stdin and then waited on
    (wl-copy, wtype), or started and left (the daemon, the app, a browser).
    Each gets its own session, so a caller that must stop one can stop its
    whole group, and one that is meant to outlive the helper does.
    """
    _prepare(argv, kwargs)
    kwargs.setdefault("start_new_session", True)
    return subprocess.Popen(argv, **kwargs)
