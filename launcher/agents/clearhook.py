"""/clear in a named agent: hand the old conversation off, then load the brief.

Claude Code runs `agentctl hook session-start` (registered through --settings
when the launcher starts an agent) as a conversation starts. After a /clear
the model has already lost the old context, so if the brief is older than the
previous conversation's last activity a headless claude resumes that
conversation and runs /handoff into the brief. It runs with
--no-session-persistence, so it leaves no transcript the launcher could take
for the agent's current conversation. The brief then goes into the new
conversation's context, which a /clear otherwise starts without.
"""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping

from . import briefs, runtime

GUARD = "AGENT_HANDOFF"         # set in the headless run, so it never recurses
HANDOFF_TIMEOUT = 240           # seconds; a long conversation takes about a minute
SETTINGS = "claude-settings.json"

CONTEXT = """You are agent `{name}`. The user just ran /clear, so this is a new
conversation for the same line of work. Your brief is at {brief} (also in
$AGENT_BRIEF); keep it current, and rewrite it fully on /handoff. It replaces
any copy of the brief earlier in your instructions.{note}

The brief now:

{text}"""

FAILED = """

The automatic handoff of the previous conversation ({previous}) failed, so the
brief may be out of date. Tell the user before relying on its State section."""

Handoff = Callable[[list, str, dict], bool]


def _headless(argv: list, cwd: str, env: dict) -> bool:
    try:
        return subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=HANDOFF_TIMEOUT).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def handoff_command(previous: str, brief: Path) -> list:
    return ["claude", "-p", "--resume", previous, "--no-session-persistence",
            "--add-dir", str(brief.parent), "--permission-mode", "acceptEdits",
            "--allowedTools", "Read,Write,Edit", f"/handoff {brief}"]


def run(event: dict, env: Mapping, handoff: Handoff = _headless) -> str | None:
    """The hook's stdout for this event, or None to say nothing."""
    name, brief = env.get("AGENT_NAME"), env.get("AGENT_BRIEF")
    if event.get("source") != "clear" or not name or not brief or env.get(GUARD):
        return None
    brief = Path(brief)
    note = ""
    transcript = event.get("transcript_path")
    found = (runtime.newest_titled(Path(transcript).parent, name,
                                   exclude=event.get("session_id"))
             if transcript else None)
    if found is not None:
        last_active, previous = found
        if briefs.is_stale(brief, last_active):
            ok = handoff(handoff_command(previous, brief), event.get("cwd") or os.getcwd(),
                         {**env, GUARD: "1"})
            if not ok:
                note = FAILED.format(previous=previous)
    try:
        text = brief.read_text()
    except OSError:
        text = "(no brief yet)"
    context = CONTEXT.format(name=name, brief=brief, note=note, text=text)
    return json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                              "additionalContext": context}})


def main(stdin=None) -> int:
    """Never fails the conversation's start: any problem means no output."""
    try:
        event = json.loads((stdin or sys.stdin).read())
        out = run(event if isinstance(event, dict) else {}, os.environ)
    except Exception:
        return 0
    if out:
        print(out)
    return 0


def write_settings(root) -> Path:
    """The --settings file that registers the hook for /clear."""
    command = shlex.join([sys.executable, "-m", "launcher.agents.cli", "hook", "session-start"])
    data = {"hooks": {"SessionStart": [{"matcher": "clear", "hooks": [
        {"type": "command", "command": command, "timeout": HANDOFF_TIMEOUT + 60}]}]}}
    p = Path(root) / SETTINGS
    text = json.dumps(data, indent=2) + "\n"
    if not p.exists() or p.read_text() != text:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(SETTINGS + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, p)
    return p
