import configparser
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


def _unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(ROOT / name)
    return parser


@pytest.mark.parametrize("name", ["devbox-launcher.service.in", "agents-restore.service.in",
                                  "agents-schedule.service.in"])
def test_agents_outlive_the_unit_that_started_their_tmux_server(name):
    assert _unit(name)["Service"]["KillMode"] == "process"


def test_schedule_tick_runs_agentctl_with_the_projects_and_claude_on_path():
    service = _unit("agents-schedule.service.in")["Service"]
    assert service["Type"] == "oneshot"
    assert service["ExecStart"] == "@VENV@/bin/agentctl schedule tick"
    env = (ROOT / "agents-schedule.service.in").read_text()
    assert "LAUNCHER_BASE_DIR=@BASE_DIR@" in env and "PATH=@CLAUDE_BIN_DIR@:" in env


def test_schedule_timer_ticks_every_five_minutes():
    timer = _unit("agents-schedule.timer")["Timer"]
    assert timer["OnUnitActiveSec"] == "5min"


def test_the_launcher_unit_passes_the_schedules_setting():
    assert "Environment=LAUNCHER_SCHEDULES=@SCHEDULES@" in (
        ROOT / "devbox-launcher.service.in").read_text()


def test_install_sh_installs_the_timer_on_linux():
    script = (ROOT / "install.sh").read_text()
    assert "systemctl --user enable --now agents-schedule.timer" in script
    assert "only works on macOS" not in script
