import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

from .discovery import Project

SOCKET = "devbox-launcher"
Runner = Callable[[list[str]], subprocess.CompletedProcess]

_URL = re.compile(r"https://claude\.ai/code[/?]\S+")
_CAPACITY = re.compile(r"Capacity:\s*(\d+)/")
# What a --session-id listener prints where a same-dir one prints Capacity:
# "Single session · exits when complete".
_SINGLE_SESSION = "Single session"

# Listener health, read off the pane. `tmux has-session` only proves the process
# is alive — a listener can sit registered-but-unusable (see pointer.py), so the
# UI asks for this instead.
CONNECTED = "connected"  # a session is live; reachable from the Claude app
READY = "ready"          # registered, no session yet
FAILED = "failed"        # session creation failed
STARTING = "starting"    # nothing on the pane yet
STUCK = "stuck"          # still nothing on the pane, long past when there should be
STOPPED = "stopped"      # no session at all

# How long a listener may show a blank pane before it is called stuck rather
# than starting. A healthy one prints its status block in about two seconds; a
# listener with nothing to say after this is not slow, it is wedged, and saying
# "Starting…" forever is the UI lying about it. Only STARTING is subject to
# this: READY is a *resting* state — a registered listener sits at 0/32 for as
# long as you leave it, and that is correct, not broken.
STARTING_GRACE = 30.0

# A tmux call that hangs (a wedged server) must not hang the poll with it.
TIMEOUT = 10.0

# The status word on its own line, so "Disconnected" is not read as connected.
_CONNECTED_WORD = re.compile(r"\bConnected\b")


# What `claude remote-control --spawn` accepts that the launcher uses: same-dir
# puts every session in the project dir; worktree gives each on-demand session
# its own git worktree, so parallel sessions do not edit the same checkout.
SPAWN_MODES = ("same-dir", "worktree")

# What `claude remote-control --permission-mode` accepts, in the order the row
# offers them. The CLI also takes bypassPermissions and dontAsk; neither is
# offered here — the first is refused outright for a cloud-reachable session,
# and both would have every listener on the box fail the same silent way if
# they were tapped by accident. Adding one is a line in this tuple and a label
# in ui._MODE_LABELS.
#
# The mode is fixed when the listener starts — there is no runtime key for it
# and the app has no control — so changing it means restarting the listener,
# which is what app.set_mode does.
PERMISSION_MODES = ("default", "acceptEdits", "auto", "plan")


class StartError(RuntimeError):
    """tmux refused to create the session; the message is tmux's own."""


def default_run(cmd: list[str], timeout: float = TIMEOUT) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Reads as a failed command, which every caller already handles.
        return subprocess.CompletedProcess(cmd, 124, stdout="", stderr="tmux timed out")


def tmux_args(args: list[str]) -> list[str]:
    return ["tmux", "-L", SOCKET, *args]


def session_name(slug: str) -> str:
    return f"devbox-{slug}"


def is_running(slug: str, run: Runner = default_run) -> bool:
    return run(tmux_args(["has-session", "-t", session_name(slug)])).returncode == 0


def start(project: Project, run: Runner = default_run,
          session_id: str | None = None, spawn: str = "same-dir",
          permission_mode: str | None = None) -> None:
    if spawn not in SPAWN_MODES:
        raise ValueError(f"unknown spawn mode: {spawn!r}")
    # None means "say nothing", which leaves the CLI's own default (and any
    # defaultMode in settings.json) in charge — not the same thing as passing
    # --permission-mode default, which overrides that setting.
    if permission_mode is not None and permission_mode not in PERMISSION_MODES:
        raise ValueError(f"unknown permission mode: {permission_mode!r}")
    name = session_name(project.slug)
    # With a recorded session, resume it so the conversation survives a reboot.
    # The two forms are mutually exclusive, not additive — the CLI rejects
    # "--session-id and --continue ... with --spawn, --capacity, or
    # --create-session-in-dir", and sending both kills the listener on every
    # start. Resuming also drops it into single-session mode; see health().
    # Quoted: the id comes from a file on disk (pointer.py validates it too),
    # and this string goes through `bash -lc`.
    mode = f"--session-id {shlex.quote(session_id)}" if session_id else f"--spawn {spawn}"
    # Passed in both forms: the CLI takes it alongside --session-id as well as
    # --spawn, so a resumed conversation comes back in the mode the row asks
    # for rather than the one it happened to be created in. The value is one
    # of PERMISSION_MODES, checked above, so it needs no quoting.
    permission = f" --permission-mode {permission_mode}" if permission_mode else ""
    command = f"claude remote-control --name {shlex.quote(name)} {mode}{permission}"
    # remain-on-exit keeps the pane once the command exits, so a listener that
    # dies on spawn leaves its error behind to read. Without it the session goes
    # with the command, capture-pane finds nothing, and the failure is
    # indistinguishable from a listener that is still starting up.
    #
    # Both commands go in a single tmux invocation: sent as two, a listener that
    # exits instantly can beat set-option and take its session down regardless.
    result = run(tmux_args(["set-option", "-g", "remain-on-exit", "on", ";",
                            "new-session", "-d", "-s", name, "-c", str(project.cwd),
                            "bash", "-lc", command]))
    # A refused new-session (duplicate name, missing cwd, no tmux) used to pass
    # silently, and the caller then sat in wait_for_health for a listener that
    # was never started.
    if result.returncode != 0:
        raise StartError((result.stderr or "").strip() or "tmux could not create the session")


def is_dead(slug: str, run: Runner = default_run) -> bool:
    """True when the session is still there but its command has exited."""
    out = run(tmux_args(["list-panes", "-t", session_name(slug), "-F", "#{pane_dead}"])).stdout or ""
    return any(line.strip() == "1" for line in out.splitlines())


def stop(
    slug: str,
    run: Runner = default_run,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 12,
    interval: float = 0.5,
) -> None:
    # Graceful stop: send Ctrl-C so `claude remote-control` shuts down cleanly and
    # disconnects the cloud bridge (the env then shows offline in the app promptly,
    # instead of looking active until its heartbeat lease times out). The process
    # exiting ends the tmux session; poll for that, and kill-session only as a
    # backstop if it hasn't exited within attempts*interval seconds.
    name = session_name(slug)
    if run(tmux_args(["has-session", "-t", name])).returncode != 0:
        return  # nothing running
    if is_dead(slug, run):
        # The process is already gone and only the pane is being held open for
        # its error text; C-c has nothing to signal, so skip straight to the
        # kill instead of burning the whole poll budget waiting for an exit
        # that already happened.
        run(tmux_args(["kill-session", "-t", name]))
        return
    run(tmux_args(["send-keys", "-t", name, "C-c"]))
    for _ in range(attempts):
        sleep(interval)
        if run(tmux_args(["has-session", "-t", name])).returncode != 0:
            return  # exited gracefully
        if is_dead(slug, run):
            # Exited gracefully too, but remain-on-exit is holding the emptied
            # session open. Nothing left to read on a listener the user stopped
            # on purpose, and leaving it would show up as a failure in the UI.
            run(tmux_args(["kill-session", "-t", name]))
            return
    run(tmux_args(["kill-session", "-t", name]))  # backstop


def _pane(slug: str, run: Runner) -> str:
    # -J joins wrapped lines: the connect URL is longer than the pane is wide, so
    # without it capture-pane returns the URL cut at the wrap point.
    return run(tmux_args(["capture-pane", "-J", "-t", session_name(slug), "-p"])).stdout or ""


def _scrollback(slug: str, run: Runner) -> str:
    """The whole pane, not just the visible region: a listener's error is
    printed first and the shell pads below it, so by the time anything asks, it
    has scrolled out of view."""
    return run(tmux_args(["capture-pane", "-J", "-t", session_name(slug), "-p",
                          "-S", "-"])).stdout or ""


def connect_url(slug: str, run: Runner = default_run) -> str | None:
    match = _URL.search(_pane(slug, run))
    return match.group(0) if match else None


def _url_from(pane: str) -> str | None:
    """The *last* link on the pane. A retried listener leaves the previous
    attempt's link in the scrollback, and the live session is the one printed
    last — taking the first would hand back a link to a dead environment."""
    matches = _URL.findall(pane)
    return matches[-1] if matches else None


def _reason_from(pane: str) -> str | None:
    lines = [line.strip() for line in pane.splitlines() if line.strip()]
    if not lines:
        return None
    return next((line for line in lines if line.startswith("Error:")), lines[0])


def _sessions_from(pane: str) -> int | None:
    """The live session count off the latest Capacity line. A resumed
    (single-session) listener prints none, so it has no count to report."""
    capacities = _CAPACITY.findall(pane)
    return int(capacities[-1]) if capacities else None


def _health_from(pane: str, dead: bool) -> str:
    """Health from the pane text plus whether the command has exited."""
    # A dead pane is conclusive and outranks the text: no Capacity line is ever
    # coming for a command that has already exited.
    if dead:
        return FAILED
    # Capacity is the honest signal. The status word says "Connected" as soon as
    # the cloud bridge is up, which happens seconds before (and regardless of)
    # a session existing — so only a live session counts as CONNECTED, and that
    # also outranks a "Session failed" line left over from an earlier attempt.
    capacities = _CAPACITY.findall(pane)   # last one wins: it is the current status block
    if capacities and int(capacities[-1]) >= 1:
        return CONNECTED
    # A resumed listener (--session-id) runs in classic single-session mode and
    # prints this INSTEAD of a Capacity line, so there is no count to read. The
    # session is the one being resumed, so the line appearing at all means it is
    # live — and a resume that fails exits the process, which the dead-pane
    # check above catches rather than leaving this text behind.
    if _SINGLE_SESSION in pane:
        return CONNECTED
    if "Session failed" in pane:
        return FAILED
    if capacities or "Ready" in pane or _CONNECTED_WORD.search(pane):
        return READY
    return STARTING


def failure_reason(slug: str, run: Runner = default_run) -> str | None:
    """The error a dead listener left on its pane, if it said anything.

    Reads the full scrollback (-S -): the error is printed first and the shell
    pads the pane below it, so by the time anything asks, it has scrolled out of
    the visible region.
    """
    # Prefer the explicit error line; a listener that dies for another reason
    # (a missing binary, say) only leaves whatever the shell printed first.
    return _reason_from(_scrollback(slug, run))


def health(slug: str, run: Runner = default_run) -> str:
    """One listener's health, asked directly. `snapshot()` answers the same
    question for the whole list at a fraction of the cost; this remains for
    `wait_for_health()`, which watches a single listener settle after a Start."""
    # A dead pane is conclusive and outranks the text: the command has exited,
    # so no Capacity line is ever coming.
    if is_dead(slug, run):
        return FAILED
    return _health_from(_pane(slug, run), dead=False)


def wait_for_health(
    slug: str,
    run: Runner = default_run,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 30,
    interval: float = 0.5,
    stable: int = 6,
) -> str:
    # Poll until the listener settles. FAILED is conclusive at once; CONNECTED is
    # not — a session doomed by a stale pointer holds a slot for about a second
    # before its child exits, so the pane reads 1/32 on its way to failing.
    # Require CONNECTED to survive `stable` polls before believing it.
    state = STARTING
    streak = 0
    for _ in range(attempts):
        # The caller has just started this session, so if it is gone the
        # listener died and took it with it (a session predating remain-on-exit,
        # or one killed underneath us). capture-pane would error and read as an
        # empty pane, i.e. STARTING, so without this the poll runs its full
        # budget and then reports a failure as if it were still coming up.
        if not is_running(slug, run):
            return FAILED
        state = health(slug, run)
        if state == FAILED:
            return FAILED
        streak = streak + 1 if state == CONNECTED else 0
        if streak >= stable:
            return CONNECTED
        sleep(interval)
    return state


def answer_enable_prompt_if_present(slug: str, run: Runner = default_run) -> bool:
    if "Enable Remote Control" in _pane(slug, run):
        run(tmux_args(["send-keys", "-t", session_name(slug), "y", "Enter"]))
        return True
    return False


# --- the batched snapshot ----------------------------------------------------
# The page asks about every project several times a minute, and asking tmux
# per project cost four subprocesses each (has-session, list-panes, and two
# capture-panes). Two whole-socket queries answer presence, age and deadness for
# the entire list at once; only a *running* session then costs a capture, and
# one capture answers every question asked of it.


@dataclass(frozen=True)
class Status:
    """Everything the page knows about one project's listener."""
    running: bool
    state: str
    age: int | None = None        # seconds since the session was created
    url: str | None = None
    reason: str | None = None
    sessions: int | None = None   # live sessions, where the pane counts them


def sessions(run: Runner = default_run) -> dict[str, int]:
    """session name -> creation time, for every session on the socket.

    A tmux with no server running exits non-zero; that is simply "nothing is
    running", not a failure worth propagating to the page.
    """
    result = run(tmux_args(["list-sessions", "-F", "#{session_name} #{session_created}"]))
    if result.returncode != 0:
        return {}
    out = {}
    for line in (result.stdout or "").splitlines():
        name, _, created = line.strip().partition(" ")
        if name and created.isdigit():
            out[name] = int(created)
    return out


def dead_sessions(run: Runner = default_run) -> set[str]:
    """Sessions holding a dead pane — the conclusive failure signal."""
    out = run(tmux_args(["list-panes", "-a", "-F", "#{session_name} #{pane_dead}"])).stdout or ""
    dead = set()
    for line in out.splitlines():
        name, _, flag = line.strip().partition(" ")
        if flag.strip() == "1":
            dead.add(name)
    return dead


def snapshot(slugs, run: Runner = default_run, now: Callable[[], float] = time.time) -> dict[str, Status]:
    live = sessions(run)
    dead = dead_sessions(run) if live else set()
    at = now()
    out: dict[str, Status] = {}
    for slug in slugs:
        name = session_name(slug)
        created = live.get(name)
        if created is None:
            out[slug] = Status(running=False, state=STOPPED)
            continue
        age = int(at - created)
        pane = _scrollback(slug, run)
        state = _health_from(pane, name in dead)
        # Blank for this long is not slow, it is wedged. Confined to STARTING:
        # a READY listener is resting, not failing.
        if state == STARTING and age > STARTING_GRACE:
            state = STUCK
        out[slug] = Status(
            running=True,
            state=state,
            age=age,
            # Only advertise a link that leads somewhere: a failed or
            # registered-only listener may still have one on its pane.
            url=_url_from(pane) if state == CONNECTED else None,
            reason=_reason_from(pane) if state == FAILED else None,
            sessions=_sessions_from(pane),
        )
    return out


def pane_tail(slug: str, run: Runner = default_run, lines: int = 20) -> list[str]:
    """The last few lines a session printed, for reading on a phone.

    Fetched only when someone opens the disclosure — never on the polling path,
    which is why it is not part of Status.
    """
    text = _scrollback(slug, run)
    return [line.rstrip() for line in text.splitlines() if line.strip()][-lines:]
