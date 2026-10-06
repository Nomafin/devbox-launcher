import os
from pathlib import Path


def base_dir() -> Path:
    env = os.environ.get("LAUNCHER_BASE_DIR")
    return Path(env) if env else Path.home() / "projects"


def run_user() -> str | None:
    """The tailnet login allowed to run commands from the page. Unset means
    nobody: a shell on the box is not something to enable by default."""
    return os.environ.get("LAUNCHER_RUN_USER") or None
