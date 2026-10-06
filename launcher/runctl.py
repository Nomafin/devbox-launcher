"""Shell commands typed on the phone and run on the box, one per project.

Claude sometimes needs a human to run a command: one that changes production
(its own permission check rightly refuses those) or one that waits for input.
From a phone, over Remote Control, there was no way to do either. This runs
the command in a tmux session the page can read and type into, the same way
the sign-in flow drives `claude auth login`.

The person runs the command, not Claude: a session on the box must never call
this itself, since that would sidestep the very check it exists to respect.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .state import state_path
from .tmuxctl import Runner, StartError, default_run, tmux_args

# Deliberately not `devbox-<slug>`: that is the project's listener.
PREFIX = "launcher-run-"

# Wide, so a deploy's progress lines are not wrapped into a mess on capture.
_WIDTH, _HEIGHT = "200", "50"

# What tmux prints into a pane held open by remain-on-exit. The exit code is
# shown on its own, so this line is noise.
_DEAD_NOTICE = re.compile(r"^Pane is dead \(")


@dataclass(frozen=True)
class RunStatus:
    exists: bool                # a session is there, running or finished
    running: bool               # its command has not exited yet
    exit_code: int | None       # set once it has


NONE = RunStatus(exists=False, running=False, exit_code=None)


def session_name(slug: str) -> str:
    return f"{PREFIX}{slug}"


def record_path(slug: str, directory=None) -> Path:
    base = Path(directory) if directory is not None else state_path().parent
    return base / f"run-{slug}.json"


def last(slug: str, directory=None) -> dict:
    """The command most recently started for this project, and where — kept on
    disk so the page can still say what it is showing after a restart."""
    try:
        data = json.loads(record_path(slug, directory).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _remember(slug: str, command: str, cwd, directory=None) -> None:
    target = record_path(slug, directory)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps({"command": command, "cwd": str(cwd)}))
    os.replace(tmp, target)


def _parse(dead: str, code: str) -> RunStatus:
    if dead != "1":
        return RunStatus(exists=True, running=True, exit_code=None)
    code = code.strip()
    return RunStatus(exists=True, running=False,
                     exit_code=int(code) if code.lstrip("-").isdigit() else None)


def status(slug: str, run: Runner = default_run) -> RunStatus:
    result = run(tmux_args(["list-panes", "-t", session_name(slug), "-F",
                            "#{pane_dead} #{pane_dead_status}"]))
    if result.returncode != 0:
        return NONE
    dead, _, code = (result.stdout or "").strip().partition(" ")
    return _parse(dead, code)


def statuses(run: Runner = default_run) -> dict[str, RunStatus]:
    """slug -> status for every project with a command, in one tmux call: the
    main page asks on every poll, and one call per project would add up."""
    result = run(tmux_args(["list-panes", "-a", "-F",
                            "#{session_name} #{pane_dead} #{pane_dead_status}"]))
    if result.returncode != 0:
        return {}
    out = {}
    for line in (result.stdout or "").splitlines():
        name, _, rest = line.strip().partition(" ")
        if not name.startswith(PREFIX):
            continue
        dead, _, code = rest.partition(" ")
        out[name[len(PREFIX):]] = _parse(dead, code)
    return out


def start(slug: str, command: str, cwd, run: Runner = default_run, directory=None) -> None:
    name = session_name(slug)
    # A finished run is still holding its session open for reading; tmux
    # refuses a duplicate name, so clear it. The caller refuses to replace one
    # that is still running.
    if run(tmux_args(["has-session", "-t", name])).returncode == 0:
        close(slug, run)
    result = run(tmux_args(["set-option", "-g", "remain-on-exit", "on", ";",
                            "new-session", "-d", "-s", name,
                            "-x", _WIDTH, "-y", _HEIGHT, "-c", str(cwd),
                            "bash", "-lc", command]))
    if result.returncode != 0:
        raise StartError((result.stderr or "").strip() or "tmux could not create the session")
    _remember(slug, command, cwd, directory)


def output(slug: str, run: Runner = default_run) -> str:
    # -J rejoins wrapped lines, -S - takes the whole scrollback: a long deploy
    # scrolls its first lines out of the visible region.
    text = run(tmux_args(["capture-pane", "-J", "-t", session_name(slug),
                          "-p", "-S", "-"])).stdout or ""
    lines = [line.rstrip() for line in text.splitlines() if not _DEAD_NOTICE.match(line)]
    return "\n".join(lines).rstrip("\n")


def send_input(slug: str, text: str, run: Runner = default_run) -> None:
    # -l sends the text literally, so nothing in it is read as a key name.
    # tmux rejects -l with nothing to send, and a bare Enter is a real answer.
    if text:
        run(tmux_args(["send-keys", "-t", session_name(slug), "-l", text]))
    run(tmux_args(["send-keys", "-t", session_name(slug), "Enter"]))


def interrupt(slug: str, run: Runner = default_run) -> None:
    run(tmux_args(["send-keys", "-t", session_name(slug), "C-c"]))


def close(slug: str, run: Runner = default_run) -> None:
    run(tmux_args(["kill-session", "-t", session_name(slug)]))
