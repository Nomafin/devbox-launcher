import os
from pathlib import Path


def base_dir() -> Path:
    env = os.environ.get("LAUNCHER_BASE_DIR")
    return Path(env) if env else Path.home() / "projects"


def run_user() -> str | None:
    """The tailnet login allowed to run commands from the page. Unset means
    nobody: a shell on the box is not something to enable by default."""
    return os.environ.get("LAUNCHER_RUN_USER") or None


def github() -> bool:
    """Whether the Clone and New project pages are on. Off keeps the UI as it
    was: this box may have no `gh`, or no wish to show what it can reach."""
    return os.environ.get("LAUNCHER_GITHUB") == "1"


def github_invites_from() -> str | None:
    """The account whose repo invitations the Clone page accepts. Unset means
    invitations are left for you to answer on GitHub."""
    return os.environ.get("LAUNCHER_GITHUB_INVITES_FROM") or None


def schedules() -> bool:
    """Whether the Schedules page is on. install.sh sets it along with the job
    that runs the schedules, since one is no use without the other."""
    return os.environ.get("LAUNCHER_SCHEDULES") == "1"
