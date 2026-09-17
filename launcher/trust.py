import json
import os
import threading
from pathlib import Path

# Guards the read-modify-write below: the reconcile thread and POST /start can
# now both call ensure_trusted concurrently (reconcile runs throughout the boot
# window while the anyio threadpool can service a Start at the same time), and
# both would otherwise write the same fixed temp path, so os.replace could
# publish interleaved bytes and corrupt ~/.claude.json.
_lock = threading.Lock()


def claude_config_path() -> Path:
    return Path.home() / ".claude.json"


def ensure_trusted(project_path, config_path=None) -> bool:
    config_path = Path(config_path) if config_path else claude_config_path()
    key = str(project_path)

    with _lock:
        data = {}
        if config_path.exists():
            text = config_path.read_text().strip()
            data = json.loads(text) if text else {}

        changed = False
        projects = data.setdefault("projects", {})
        project = projects.setdefault(key, {})
        if project.get("hasTrustDialogAccepted") is not True:
            project["hasTrustDialogAccepted"] = True
            changed = True
        if data.get("remoteDialogSeen") is not True:
            data["remoteDialogSeen"] = True
            changed = True

        if changed:
            mode = (config_path.stat().st_mode & 0o777) if config_path.exists() else 0o600
            tmp = config_path.with_name(config_path.name + ".launcher.tmp")
            tmp.write_text(json.dumps(data))
            os.chmod(tmp, mode)
            os.replace(tmp, config_path)
        return changed
