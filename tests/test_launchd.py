import plistlib
from pathlib import Path

import pytest

LAUNCHD = Path(__file__).parent.parent / "launchd"
LABELS = ["devbox.launcher", "devbox.agents-reap", "devbox.agents-restore"]


def _plist(label: str) -> dict:
    return plistlib.loads((LAUNCHD / f"{label}.plist.in").read_bytes())


@pytest.mark.parametrize("label", LABELS)
def test_template_is_a_plist_named_after_its_label(label):
    assert _plist(label)["Label"] == label


@pytest.mark.parametrize("label", LABELS)
def test_path_finds_homebrew_tmux_and_claude(label):
    # launchd's own PATH has neither.
    path = _plist(label)["EnvironmentVariables"]["PATH"].split(":")
    assert "@CLAUDE_BIN_DIR@" in path and "/opt/homebrew/bin" in path


@pytest.mark.parametrize("label", ["devbox.launcher", "devbox.agents-restore"])
def test_agents_outlive_the_job_that_started_them(label):
    assert _plist(label)["AbandonProcessGroup"] is True


def test_launcher_restarts_only_after_a_crash():
    plist = _plist("devbox.launcher")
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}


def test_restore_runs_once_at_load_and_is_not_kept_alive():
    plist = _plist("devbox.agents-restore")
    assert plist["RunAtLoad"] is True and "KeepAlive" not in plist
    assert plist["ProgramArguments"][-1] == "@VENV@/bin/agentctl"
    assert 'exec "$0" restore' in plist["ProgramArguments"][2]


def test_reaper_runs_every_half_hour_and_not_at_load():
    plist = _plist("devbox.agents-reap")
    assert plist["StartInterval"] == 1800 and "RunAtLoad" not in plist


def test_installer_does_not_load_the_restore_job():
    # Loading it would start every agent marked active, during an install.
    script = (LAUNCHD.parent / "install.sh").read_text()
    assert 'launchd_load "devbox.launcher"' in script
    assert 'launchd_load "devbox.agents-reap"' in script
    assert 'launchd_load "devbox.agents-restore"' not in script
