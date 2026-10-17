"""Agents' processes: interactive Claude sessions in tmux, socket `agents`.

One session per agent, named by its display name. remain-on-exit keeps a
session that died on start, so its error can be read; everything that starts
one clears such a corpse first.
"""

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .model import Agent, AgentError

SOCKET = "agents"
TIMEOUT = 10.0
Runner = Callable[[list[str]], subprocess.CompletedProcess]

# Measured against Claude Code 2.1.301.
CONNECTED_SAMPLE = "/remote-control is active · Continue here, on your phone, or at"
CONNECTED = re.compile(r"/remote-control is active")
# Folder trust prompt. ensure_trusted() should prevent it; if it shows anyway,
# its default is "No, exit", so it is answered rather than left to kill the
# session. "Up Enter" selects "Yes, I trust this folder".
TRUST_PROMPT = "Is this a project you created or one you trust"
RESUME_MISSING = re.compile(r"No conversation found", re.IGNORECASE)
URL = re.compile(r"https://claude\.ai/code[/?]\S+")


def default_run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, stdout="", stderr="tmux timed out")


def conf(home) -> Path:
    return Path(home) / ".config" / "agents" / "tmux.conf"


def tmux(home, *args: str) -> list[str]:
    # -f only matters when this call starts the server; passing it every time
    # means whichever call comes first starts it configured.
    # -u: the box's shells run with LANG=C; names are ASCII now, but agents'
    # panes print UTF-8, which tmux would otherwise mangle.
    return ["tmux", "-u", "-L", SOCKET, "-f", str(conf(home)), *args]


@dataclass(frozen=True)
class Session:
    name: str
    created: int
    activity: int       # session_activity: the last client input
    attached: bool
    dead: bool
    output: int = 0     # window_activity: the last pane output, e.g. app-only use
    exit_status: int | None = None   # a dead pane's: 0 is /exit, else a crash


# stderr of tmux when the server is simply not running. Any other failure is
# not "nothing running": callers act on an empty answer, so it must not be one.
NO_SERVER = ("no server running", "error connecting to", "No such file or directory")


def _failed(result: subprocess.CompletedProcess) -> AgentError:
    detail = (result.stderr or "").strip() or f"rc {result.returncode}"
    return AgentError(AgentError.START_FAILED,
                      f"tmux on the agents socket did not answer: {detail}")


def sessions(home, run: Runner) -> dict[str, Session]:
    listed = run(tmux(home, "list-sessions", "-F",
                      "#{session_name}\t#{session_created}\t#{session_activity}\t#{session_attached}"
                      "\t#{window_activity}"))
    if listed.returncode != 0:
        if any(s in (listed.stderr or "") for s in NO_SERVER):
            return {}   # genuinely no server: nothing running
        raise _failed(listed)
    dead: dict[str, int | None] = {}
    panes = run(tmux(home, "list-panes", "-a", "-F",
                     "#{session_name}\t#{pane_dead}\t#{pane_dead_status}"))
    if panes.returncode != 0:
        raise _failed(panes)    # never report a dead session as alive
    for line in (panes.stdout or "").splitlines():
        name, _, rest = line.partition("\t")
        flag, _, status = rest.partition("\t")
        if flag.strip() == "1":
            status = status.strip()
            # No status (killed by a signal, say) is no clean exit either.
            dead[name] = int(status) if status.lstrip("-").isdigit() else None
    out = {}
    for line in (listed.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) != 5 or not parts[1].isdigit() or not parts[2].isdigit():
            continue
        name = parts[0]
        output = parts[4].strip()
        out[name] = Session(name, int(parts[1]), int(parts[2]),
                            parts[3].strip() not in ("", "0"), name in dead,
                            int(output) if output.isdigit() else 0, dead.get(name))
    return out


def start(home, name: str, workdir, command: str, env: dict, run: Runner) -> None:
    args = ["set-option", "-g", "remain-on-exit", "on", ";",
            "new-session", "-d", "-s", name, "-c", str(workdir)]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    args += ["bash", "-lc", command]
    result = run(tmux(home, *args))
    if result.returncode != 0:
        raise AgentError(AgentError.START_FAILED,
                         (result.stderr or "").strip() or "tmux could not create the session")


def kill(home, name: str, run: Runner) -> None:
    run(tmux(home, "kill-session", "-t", f"={name}"))


def pane(home, name: str, run: Runner) -> str:
    """The pane with all its scrollback. Only for what has to look back: why a
    session died on start (its pane is short then) and the output tail. A
    resumed conversation redraws its old history, so anything that decides on
    the agent's current state reads screen() instead."""
    # "=name:" is the exact session's current pane; a bare name would fall back
    # to prefix matching, so `notes/drafts` could read `notes/drafts2`.
    return run(tmux(home, "capture-pane", "-J", "-p", "-S", "-", "-t", f"={name}:")).stdout or ""


def screen(home, name: str, run: Runner) -> str:
    """What the pane shows right now, without scrollback."""
    return run(tmux(home, "capture-pane", "-J", "-p", "-t", f"={name}:")).stdout or ""


def tail(home, name: str, run: Runner, lines: int = 20) -> list[str]:
    return [l.rstrip() for l in pane(home, name, run).splitlines() if l.strip()][-lines:]


def connected(text: str) -> bool:
    return bool(CONNECTED.search(text or ""))


def url(text: str) -> str | None:
    found = URL.findall(text or "")
    return found[-1] if found else None


def reason(text: str) -> str | None:
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return None
    return next((l for l in lines if l.lower().startswith("error")), lines[-1])


def answer_trust_prompt(home, name: str, run: Runner) -> None:
    run(tmux(home, "send-keys", "-t", f"={name}:", "Up", "Enter"))


def claude_command(agent: Agent, *, fresh: bool, seed: Path | None, extra=()) -> str:
    parts = ["claude", "--remote-control", agent.display, "-n", agent.display]
    if fresh:
        parts += ["--session-id", agent.session_id]
        if seed is not None:
            parts += ["--append-system-prompt-file", str(seed)]
    else:
        parts += ["--resume", agent.session_id]
    if agent.mode:
        parts += ["--permission-mode", agent.mode]
    if fresh:
        parts += list(extra)
    return shlex.join(parts)


def transcript_mtime(home, session_id: str | None) -> float:
    if not session_id:
        return 0.0
    best = 0.0
    for f in (Path(home) / ".claude" / "projects").glob(f"*/{session_id}.jsonl"):
        try:
            best = max(best, f.stat().st_mtime)
        except OSError:
            pass
    return best
