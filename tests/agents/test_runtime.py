import os
import shlex
import subprocess

import pytest

from launcher.agents import runtime
from launcher.agents.model import Agent, AgentError

from .conftest import FakeTmux


@pytest.fixture
def tmux():
    return FakeTmux({"t": 1000.0})


def agent(**kw):
    a = Agent(project="notes-site", name="drafts", cwd="/p", workdir="/p/wt", session_id="sid-1")
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def test_claude_command_quotes_display_name(tmp_path):
    argv = shlex.split(runtime.claude_command(agent(), fresh=True, seed=tmp_path / "s.md"))
    assert argv[:5] == ["claude", "--remote-control", "notes-site·drafts", "-n", "notes-site·drafts"]
    assert argv[argv.index("--session-id") + 1] == "sid-1"
    assert argv[argv.index("--append-system-prompt-file") + 1] == str(tmp_path / "s.md")
    assert "--resume" not in argv and "--permission-mode" not in argv


def test_claude_command_resume_with_mode():
    argv = shlex.split(runtime.claude_command(agent(mode="plan"), fresh=False, seed=None,
                                              extra=["--model", "x"]))
    assert argv[argv.index("--resume") + 1] == "sid-1"
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert "--model" not in argv          # extras apply to a new conversation only


def test_claude_command_extra_on_fresh():
    argv = shlex.split(runtime.claude_command(agent(), fresh=True, seed=None, extra=["--model", "x"]))
    assert argv[-2:] == ["--model", "x"]


def test_start_and_sessions(tmp_path, tmux):
    runtime.start(tmp_path, "notes·drafts", "/p/wt", "claude", {"AGENT_NAME": "notes/drafts"}, tmux)
    s = runtime.sessions(tmp_path, tmux)["notes·drafts"]
    assert (s.created, s.attached, s.dead) == (1000, False, False)
    assert tmux.sessions["notes·drafts"]["env"] == {"AGENT_NAME": "notes/drafts"}
    new = next(c for c in tmux.calls if "new-session" in c)
    assert new[:6] == ["tmux", "-u", "-L", "agents", "-f",
                       str(tmp_path / ".config" / "agents" / "tmux.conf")]
    assert "remain-on-exit" in new


def test_start_refused(tmp_path, tmux):
    runtime.start(tmp_path, "x", "/", "claude", {}, tmux)
    with pytest.raises(AgentError) as e:
        runtime.start(tmp_path, "x", "/", "claude", {}, tmux)
    assert e.value.code == AgentError.START_FAILED


def test_no_server_means_no_sessions(tmp_path, tmux):
    assert runtime.sessions(tmp_path, tmux) == {}


def test_no_server_stderr_means_no_sessions(tmp_path):
    def run(cmd, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no server running on /tmp/x")
    assert runtime.sessions(tmp_path, run) == {}


def test_timeout_is_not_no_sessions(tmp_path):
    def run(cmd):
        return subprocess.CompletedProcess(cmd, 124, stdout="", stderr="tmux timed out")
    with pytest.raises(AgentError) as e:
        runtime.sessions(tmp_path, run)
    assert e.value.code == AgentError.START_FAILED
    assert "did not answer" in str(e.value)


def test_failing_list_panes_raises(tmp_path, tmux):
    runtime.start(tmp_path, "x", "/", "claude", {}, tmux)

    def run(cmd, timeout=None):
        if "list-panes" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        return tmux(cmd)
    with pytest.raises(AgentError) as e:
        runtime.sessions(tmp_path, run)
    assert e.value.code == AgentError.START_FAILED


def test_dead_pane_is_dead(tmp_path, tmux):
    tmux.die_with = "Error: boom"
    runtime.start(tmp_path, "x", "/", "claude", {}, tmux)
    runtime.start(tmp_path, "y", "/", "claude", {}, tmux)
    tmux.sessions["x"]["exit_status"] = 3
    got = runtime.sessions(tmp_path, tmux)
    assert (got["x"].dead, got["x"].exit_status) == (True, 3)
    assert (got["y"].dead, got["y"].exit_status) == (False, None)
    panes = [c for c in tmux.calls if "list-panes" in c][-1]
    assert "#{pane_dead_status}" in panes[-1]


def test_pane_targets_exact_session_only(tmp_path, tmux):
    runtime.start(tmp_path, "x·drafts2", "/", "claude", {}, tmux)
    assert runtime.pane(tmp_path, "x·drafts", tmux) == ""
    cap = [c for c in tmux.calls if "capture-pane" in c][-1]
    assert cap[cap.index("-t") + 1] == "=x·drafts:"
    assert runtime.pane(tmp_path, "x·drafts2", tmux) == runtime.CONNECTED_SAMPLE


def test_trust_prompt_targets_exact_pane(tmp_path, tmux):
    runtime.start(tmp_path, "notes·drafts", "/", "claude", {}, tmux)
    runtime.answer_trust_prompt(tmp_path, "notes·drafts", tmux)
    args = tmux.keys[-1]
    assert args[args.index("-t") + 1] == "=notes·drafts:"
    assert args[-2:] == ["Up", "Enter"]


def test_signals():
    assert runtime.connected(runtime.CONNECTED_SAMPLE)
    assert not runtime.connected("Welcome to Claude Code")
    assert runtime.reason("\n\nError: No conversation found\n\n") == "Error: No conversation found"
    assert runtime.reason("") is None
    assert runtime.url("x https://claude.ai/code/session_abc y") == "https://claude.ai/code/session_abc"


def test_transcript_mtime(tmp_path):
    d = tmp_path / ".claude" / "projects" / "-p-wt"
    d.mkdir(parents=True)
    f = d / "sid-1.jsonl"
    f.write_text("{}")
    os.utime(f, (5000.0, 5000.0))
    assert runtime.transcript_mtime(tmp_path, "sid-1") == 5000.0
    assert runtime.transcript_mtime(tmp_path, "other") == 0.0
    assert runtime.transcript_mtime(tmp_path, None) == 0.0


def test_screen_is_the_visible_screen_only(tmp_path, tmux):
    runtime.start(tmp_path, "x", "/", "claude", {}, tmux)
    tmux.sessions["x"].update(pane="old history\nnow", screen="now")
    assert runtime.screen(tmp_path, "x", tmux) == "now"
    cap = [c for c in tmux.calls if "capture-pane" in c][-1]
    assert "-S" not in cap and cap[cap.index("-t") + 1] == "=x:"
    assert runtime.pane(tmp_path, "x", tmux) == "old history\nnow"


def test_sessions_read_window_activity(tmp_path, tmux):
    runtime.start(tmp_path, "x", "/", "claude", {}, tmux)
    tmux.sessions["x"]["window_activity"] = 4242
    s = runtime.sessions(tmp_path, tmux)["x"]
    assert (s.activity, s.output) == (1000, 4242)
