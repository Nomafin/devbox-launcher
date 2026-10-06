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


def permission_modes(path=None) -> dict[str, str]:
    """The permission mode each listener starts its sessions in, by slug.

    Absent means the launcher says nothing about it, so the CLI's own default
    (and any defaultMode in settings.json) applies — which is not the same as
    an explicit "default" entry.
    """
    return _modes(_read(path))


def _modes(data: dict) -> dict[str, str]:
    table = data.get("permissionMode")
    if not isinstance(table, dict):
        return {}
    return {slug: mode for slug, mode in table.items()
            if isinstance(slug, str) and isinstance(mode, str)}


def permission_mode(slug: str, path=None) -> str | None:
    return permission_modes(path).get(slug)


def set_permission_mode(slug: str, mode: str | None, path=None) -> None:
    """Record the mode for one slug, or forget it when `mode` is None."""
    with _lock:
        data = _read(path)
        table = _modes(data)
        if mode is None:
            if table.pop(slug, None) is None:
                return
        else:
            if table.get(slug) == mode:
                return
            table[slug] = mode
        data["permissionMode"] = table
        _write(data, path)


def instances(path=None) -> dict[str, list[str]]:
    """Instance names per project. Which of them should be *running* is in
    `running`, keyed by instance slug, exactly as for a project."""
    table = _read(path).get("instances")
    if not isinstance(table, dict):
        return {}
    return {slug: _strings(names) for slug, names in table.items() if isinstance(slug, str)}


def _update_instance(project_slug: str, name: str, present: bool, path=None) -> None:
    with _lock:
        data = _read(path)
        table = data.get("instances")
        table = dict(table) if isinstance(table, dict) else {}
        names = _strings(table.get(project_slug))
        if present == (name in names):
            return
        if present:
            names.append(name)
        else:
            names.remove(name)
        # A project with no instances left carries no key: the table then says
        # what it means, and nothing has to filter empties when reading it.
        if names:
            table[project_slug] = names
        else:
            table.pop(project_slug, None)
        data["instances"] = table
        _write(data, path)


def record_instance(project_slug: str, name: str, path=None) -> None:
    _update_instance(project_slug, name, True, path)


def forget_instance(project_slug: str, name: str, path=None) -> None:
    _update_instance(project_slug, name, False, path)
