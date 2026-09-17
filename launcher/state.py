"""Which listeners the user wants running, and in which mode.

tmux sessions die with the host, so after a reboot the launcher has no idea what
was up. This records the intent behind each Start/Stop tap, and `app.reconcile()`
replays it at boot.
"""

import json
import os
import threading
from pathlib import Path

FILENAME = "desired.json"

# Guards the record_started/record_stopped read-modify-write: once the reconcile
# thread is running, the app is already serving, so a Start/Stop click during
# boot-time reconcile can otherwise race with reconcile's own writes and lose
# an update to desired.json.
_lock = threading.Lock()


def state_path(path=None) -> Path:
    if path is not None:
        return Path(path)
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "devbox-launcher" / FILENAME


def _strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [slug for slug in value if isinstance(slug, str)]


def _read(path=None) -> dict:
    try:
        data = json.loads(state_path(path).read_text())
    except (OSError, ValueError):
        return {}          # missing, unreadable or malformed — start from nothing
    return data if isinstance(data, dict) else {}


def desired(path=None) -> list[str]:
    return _strings(_read(path).get("running"))


def parallel(path=None) -> list[str]:
    """Projects whose listener spawns each session in its own git worktree."""
    return _strings(_read(path).get("parallel"))


def _write(data: dict, path=None) -> None:
    target = state_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, target)


def _update(key: str, slug: str, present: bool, path=None) -> None:
    """Add or remove one slug under `key`, leaving every other key alone."""
    with _lock:
        data = _read(path)
        slugs = _strings(data.get(key))
        if present == (slug in slugs):
            return
        if present:
            slugs.append(slug)
        else:
            slugs.remove(slug)
        data[key] = slugs
        _write(data, path)


def record_started(slug: str, path=None) -> None:
    _update("running", slug, True, path)


def record_stopped(slug: str, path=None) -> None:
    _update("running", slug, False, path)


def set_parallel(slug: str, on: bool, path=None) -> None:
    _update("parallel", slug, on, path)
