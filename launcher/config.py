import os
from pathlib import Path


def base_dir() -> Path:
    env = os.environ.get("LAUNCHER_BASE_DIR")
    return Path(env) if env else Path.home() / "projects"
