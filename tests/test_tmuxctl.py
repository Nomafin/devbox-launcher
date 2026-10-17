import subprocess

from launcher import tmuxctl


def test_default_run_treats_a_timeout_as_a_failed_command(monkeypatch):
    def hang(cmd, **kwargs):
        assert kwargs["timeout"] == tmuxctl.TIMEOUT
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
    monkeypatch.setattr(tmuxctl.subprocess, "run", hang)
    result = tmuxctl.default_run(tmuxctl.tmux_args(["list-sessions"]))
    assert result.returncode != 0
    assert result.stdout == ""
