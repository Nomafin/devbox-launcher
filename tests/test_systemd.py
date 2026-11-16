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


# --- memory fence (LAUNCHER_AGENTS_MEMORY_MAX) --------------------------------

def test_the_agents_tmux_server_unit_runs_in_the_agents_slice():
    service = _unit("agents-tmux.service.in")["Service"]
    assert service["Slice"] == "agents.slice"
    assert service["Type"] == "forking"
    assert "start-server" in service["ExecStart"]


def test_stopping_the_tmux_server_unit_never_kills_the_agents():
    # Its main process is the tmux server itself, so KillMode=process would
    # kill every agent on a stop or restart; only none leaves them running.
    assert _unit("agents-tmux.service.in")["Service"]["KillMode"] == "none"


def test_the_agents_slice_takes_its_limits_from_install():
    sl = _unit("agents.slice.in")["Slice"]
    assert sl["MemoryMax"] == "@AGENTS_MEMORY_MAX@"
    assert sl["MemorySwapMax"] == "@AGENTS_SWAP_MAX@"


def test_restore_comes_after_the_tmux_server_unit():
    unit = _unit("agents-restore.service.in")["Unit"]
    assert "agents-tmux.service" in unit["After"]
    assert unit["Wants"] == "agents-tmux.service"


def test_the_tmux_server_survives_its_last_session():
    # Otherwise the next start creates a new server outside the slice.
    assert "set -s exit-empty off" in (ROOT / "agents" / "tmux.conf").read_text()


def test_install_sh_installs_the_fence_only_when_asked():
    script = (ROOT / "install.sh").read_text()
    assert "LAUNCHER_AGENTS_MEMORY_MAX" in script
    assert "systemctl --user enable --now agents-tmux.service" in script
    assert "rm -f \"$UNIT_DIR/agents-tmux.service\" \"$UNIT_DIR/agents.slice\"" in script
