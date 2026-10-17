"""The agent registry: one JSON file, written only under an exclusive flock.

`agentctl` (from a terminal, over SSH), the launcher (in-process, on several threads) and the
systemd timers all write it. flock locks belong to an open file description,
so two threads that each open the lock file exclude each other too.
"""

import fcntl
import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .model import Agent, AgentError

VERSION = 1
FILENAME = "registry.json"


def home(root=None) -> Path:
    if root is not None:
        return Path(root)
    env = os.environ.get("AGENTS_HOME")
    return Path(env) if env else Path.home() / ".config" / "agents"


def _parse(text: str) -> dict[str, Agent]:
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("agents"), list):
        raise ValueError("no agents list")
    agents = [Agent.from_dict(item) for item in data["agents"]]
    return {a.key: a for a in agents}


def load(root=None) -> dict[str, Agent]:
    """Read without locking: a reader sees the last complete write, because
    writes land by rename."""
    path = home(root) / FILENAME
    if not path.exists():
        return {}
    try:
        return _parse(path.read_text())
    except (ValueError, TypeError) as exc:
        raise AgentError(AgentError.INVALID,
                         f"{path} is unreadable ({exc}); the last good copy is "
                         f"{path.with_name(FILENAME + '.bak')}") from exc


def _save(root: Path, agents: dict[str, Agent]) -> None:
    path = root / FILENAME
    if path.exists():
        shutil.copy2(path, path.with_name(FILENAME + ".bak"))
    tmp = path.with_name(FILENAME + ".tmp")
    payload = {"version": VERSION,
               "agents": [a.to_dict() for a in sorted(agents.values(), key=lambda a: a.key)]}
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


@contextmanager
def transaction(root=None) -> Iterator[dict[str, Agent]]:
    """Read-modify-write under the lock. Saved on success and on AgentError
    (a failed start records its reason); any other exception is a bug and
    leaves the file as it was."""
    root = home(root)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "registry.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        agents = load(root)
        try:
            yield agents
        except AgentError:
            _save(root, agents)
            raise
        _save(root, agents)
