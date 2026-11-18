import json
import os
import shlex
import sys

import pytest

from launcher.agents import clearhook

NAME = "notes/main"


def _transcript(d, sid, mtime, title=NAME):
    f = d / f"{sid}.jsonl"
    f.write_text(json.dumps({"type": "custom-title", "customTitle": title, "sessionId": sid}) + "\n")
    os.utime(f, (mtime, mtime))
    return f


class Handoff:
    """Stands in for the headless claude run: records it, and writes the brief."""

    def __init__(self, text="# notes/main\n## State\nhanded off\n", ok=True):
        self.calls, self.text, self.ok = [], text, ok

    def __call__(self, argv, cwd, env):
        self.calls.append((argv, cwd, env))
        if self.ok:
            brief = argv[-1].split(" ", 1)[1]
            open(brief, "w").write(self.text)
        return self.ok


@pytest.fixture
def setup(tmp_path):
    d = tmp_path / ".claude" / "projects" / "-notes"
    d.mkdir(parents=True)
    brief = tmp_path / "briefs" / "notes" / "main.md"
    brief.parent.mkdir(parents=True)
    brief.write_text("# notes/main\n## State\nold\n")
    os.utime(brief, (1000.0, 1000.0))
    env = {"AGENT_NAME": NAME, "AGENT_BRIEF": str(brief)}
    new = _transcript(d, "new", 9000.0)
    event = {"hook_event_name": "SessionStart", "source": "clear", "session_id": "new",
             "transcript_path": str(new), "cwd": "/work"}
    return d, brief, env, event


def context(out):
    assert out is not None
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def test_only_a_clear_in_a_named_agent_does_anything(setup):
    d, brief, env, event = setup
    h = Handoff()
    assert clearhook.run({**event, "source": "startup"}, env, handoff=h) is None
    assert clearhook.run(event, {}, handoff=h) is None
    assert clearhook.run(event, {**env, clearhook.GUARD: "1"}, handoff=h) is None
    assert h.calls == []


def test_clear_hands_off_the_previous_conversation_then_loads_the_brief(setup):
    d, brief, env, event = setup
    _transcript(d, "older", 2000.0)
    _transcript(d, "prev", 5000.0)
    _transcript(d, "someone-else", 6000.0, title="notes/drafts")
    h = Handoff()
    ctx = context(clearhook.run(event, env, handoff=h))
    [(argv, cwd, henv)] = h.calls
    assert argv[0] == "claude" and "-p" in argv
    assert argv[argv.index("--resume") + 1] == "prev"
    assert "--no-session-persistence" in argv           # leaves no transcript behind
    assert argv[argv.index("--add-dir") + 1] == str(brief.parent)
    # --add-dir and --allowedTools take every word after them: the prompt
    # comes after "--", or claude reads it as a tool name.
    assert argv[-2:] == ["--", f"/handoff {brief}"]
    assert cwd == "/work" and henv[clearhook.GUARD] == "1"
    assert "handed off" in ctx and str(brief) in ctx and NAME in ctx


def test_a_current_brief_is_loaded_without_a_handoff(setup):
    d, brief, env, event = setup
    _transcript(d, "prev", 5000.0)
    os.utime(brief, (5050.0, 5050.0))                   # /handoff ran before the /clear
    h = Handoff()
    ctx = context(clearhook.run(event, env, handoff=h))
    assert h.calls == [] and "old" in ctx


def test_no_previous_conversation_loads_the_brief(setup):
    d, brief, env, event = setup
    h = Handoff()
    assert "old" in context(clearhook.run(event, env, handoff=h))
    assert h.calls == []


def test_a_failed_handoff_says_so(setup):
    d, brief, env, event = setup
    _transcript(d, "prev", 5000.0)
    ctx = context(clearhook.run(event, env, handoff=Handoff(ok=False)))
    assert "old" in ctx and "prev" in ctx and "may be out of date" in ctx


def test_settings_register_the_hook_for_clear(tmp_path):
    p = clearhook.write_settings(tmp_path)
    data = json.loads(p.read_text())
    [entry] = data["hooks"]["SessionStart"]
    assert entry["matcher"] == "clear"
    [hook] = entry["hooks"]
    assert hook["timeout"] >= clearhook.HANDOFF_TIMEOUT
    argv = shlex.split(hook["command"])
    assert argv == [sys.executable, "-m", "launcher.agents.cli", "hook", "session-start"]
