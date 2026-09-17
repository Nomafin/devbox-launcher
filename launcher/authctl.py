"""The devbox's own Claude login, driven from the web UI.

`claude remote-control` needs the box signed in, and the refresh token lapses on
its own schedule. When it does, every listener fails identically and the only
fix was an SSH session — useless from a phone, which is the one place the
launcher exists to be used from.

The OAuth flow makes this tractable: it redirects to a *hosted* callback
(`platform.claude.com/oauth/code/callback`), not to localhost, so the browser
leg happens entirely on the phone and nothing has to route back to the box. All
the launcher does is show the URL the CLI printed and hand the resulting code
back to it on stdin.
"""

import json
import re
import subprocess
import time
from typing import Callable

from .tmuxctl import Runner, default_run, tmux_args

# Deliberately not `devbox-<something>`: listeners are named that way, so a
# project directory called "login" would otherwise share this session.
SESSION = "launcher-login"

# Wide enough that the authorize URL is not wrapped into oblivion. capture-pane
# -J rejoins wrapped lines, but a roomy pane keeps the scrollback honest.
_WIDTH, _HEIGHT = "200", "50"

_URL = re.compile(r"https://claude\.com/\S*oauth\S+")
# Two distinct rejections, and they mean different things to the caller:
# "Invalid code" is a malformed paste — the CLI stays at the prompt for another
# try — while "Login failed" is a well-formed code the server refused, which
# ends the process and needs a fresh link.
_ERROR = re.compile(r"(Login failed:[^\n]*|Invalid code\.[^\n]*)")
_PROMPT = "Paste code here if prompted"

# `claude auth status` talks to the network to check the token, so it gets
# longer than a tmux call — but not forever: it is on the polling path.
STATUS_TIMEOUT = 20.0


def _status_run(cmd: list[str]) -> subprocess.CompletedProcess:
    return default_run(cmd, timeout=STATUS_TIMEOUT)


def status(run: Runner = _status_run) -> bool:
    """Whether the box is signed in to Claude.

    Asks the CLI rather than reading ~/.claude/.credentials.json: the file has
    held expired-but-present tokens and empty-string tokens, and only the CLI
    knows which of those still counts. Note it exits non-zero when logged out,
    so the return code carries no information — the JSON is the answer.
    """
    try:
        out = run(["claude", "auth", "status"]).stdout or ""
    except OSError:
        return False    # no `claude` on PATH at all — every listener would fail too
    try:
        return json.loads(out).get("loggedIn") is True
    except (ValueError, AttributeError):
        return False    # not JSON at all: a CLI that could not run is logged out


def is_running(run: Runner = default_run) -> bool:
    """Whether a login session exists at all — including a spent one."""
    return run(tmux_args(["has-session", "-t", SESSION])).returncode == 0


def is_dead(run: Runner = default_run) -> bool:
    out = run(tmux_args(["list-panes", "-t", SESSION, "-F", "#{pane_dead}"])).stdout or ""
    return any(line.strip() == "1" for line in out.splitlines())


def is_active(run: Runner = default_run) -> bool:
    """Whether a login flow is still usable.

    remain-on-exit keeps the session standing after the CLI exits, so
    has-session alone cannot tell a flow waiting for a code from one whose link
    is already spent — and those need opposite things from the UI.
    """
    return is_running(run) and not is_dead(run)


def start_login(run: Runner = default_run) -> None:
    # Clear a spent session first: tmux refuses a duplicate name, so a corpse
    # left by an earlier attempt would make every later sign-in do nothing.
    if is_running(run):
        cancel(run)
    # remain-on-exit for the same reason listeners set it: a rejected code exits
    # the flow, and the error has to outlive the process to be worth showing.
    run(tmux_args(["set-option", "-g", "remain-on-exit", "on", ";",
                   "new-session", "-d", "-s", SESSION,
                   "-x", _WIDTH, "-y", _HEIGHT,
                   "claude", "auth", "login"]))


def _pane(run: Runner) -> str:
    # -S - for the whole scrollback: the URL is printed first and scrolls out of
    # the visible region as the flow prints its prompt beneath it.
    return run(tmux_args(["capture-pane", "-J", "-t", SESSION, "-p", "-S", "-"])).stdout or ""


def authorize_url(run: Runner = default_run) -> str | None:
    match = _URL.search(_pane(run))
    return match.group(0) if match else None


def waiting_for_code(run: Runner = default_run) -> bool:
    return _PROMPT in _pane(run)


def errors(run: Runner = default_run) -> list[str]:
    """Every rejection on the pane, oldest first.

    The count matters, not just the presence: a "Invalid code" from an earlier
    attempt stays on the pane forever, so only a *new* one says anything about
    the code just submitted.
    """
    return [match.strip() for match in _ERROR.findall(_pane(run))]


def error(run: Runner = default_run) -> str | None:
    found = errors(run)
    return found[-1] if found else None


def submit_code(code: str, run: Runner = default_run) -> None:
    # -l sends the code literally, so a character that happens to spell a tmux
    # key name is not interpreted as one. Enter needs its own call: under -l it
    # would arrive as the five letters "Enter".
    run(tmux_args(["send-keys", "-t", SESSION, "-l", code]))
    run(tmux_args(["send-keys", "-t", SESSION, "Enter"]))


def cancel(run: Runner = default_run) -> None:
    run(tmux_args(["kill-session", "-t", SESSION]))


def wait_for_url(
    run: Runner = default_run,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 20,
    interval: float = 0.5,
) -> str | None:
    """Poll until the CLI has printed the authorize URL, or give up.

    Giving up beats blocking: the page can render a "starting…" state and let
    the user retry, which is far better than a request that never answers.
    """
    for _ in range(attempts):
        url = authorize_url(run)
        if url:
            return url
        sleep(interval)
    return None


def wait_for_login(
    run: Runner = default_run,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 20,
    interval: float = 0.5,
    baseline: int = 0,
) -> bool:
    """Poll until the submitted code is accepted, or conclusively isn't.

    `baseline` is how many rejections were already on the pane before this code
    was submitted; only a rejection beyond that count belongs to this attempt.
    Status is checked first each round so a slow-but-successful exchange is
    never mistaken for the previous attempt's failure.
    """
    for _ in range(attempts):
        if status(run):
            return True
        if len(errors(run)) > baseline:
            return False
        sleep(interval)
    return False
