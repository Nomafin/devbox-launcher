"""The `bridge-pointer.json` that `claude remote-control` keeps per project dir.

It records the environment/session to resume on the next start. When the server
has archived that session, resuming it fails hard: the bridge adopts the dead
work item, the server answers `end_session reason=archived`, the child exits and
the listener parks at `Capacity: 0/32` — registered, but invisible in the app.
Clearing the pointer makes the next start register a fresh environment.
"""

import json
import logging
import re
from pathlib import Path

FILENAME = "bridge-pointer.json"

log = logging.getLogger("launcher")

# What a cloud session id looks like. The id ends up on a shell command line
# (tmuxctl.start), so anything outside this set is not a session id at all —
# it is a pointer file that has been tampered with or corrupted.
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def pointer_path(cwd: Path, home: Path | None = None) -> Path:
    # Claude names the per-project dir after the cwd with `/` turned into `-`.
    encoded = str(cwd).replace("/", "-")
    return (home or Path.home()) / ".claude" / "projects" / encoded / FILENAME


def session_id(cwd: Path, home: Path | None = None) -> str | None:
    """The cloud session (`session_…`) the bridge last registered for this dir.

    This is what `claude remote-control --session-id` wants: it resolves the id
    server-side, so the local transcript uuid is not usable there. Passing it
    resumes that conversation instead of opening a fresh one.

    A missing, truncated or session-less pointer means "start fresh" rather than
    an error — a half-written pointer must not be able to block Start.
    """
    try:
        data = json.loads(pointer_path(cwd, home).read_text())
    except (OSError, ValueError):
        return None
    sid = data.get("sessionId") if isinstance(data, dict) else None
    if not sid:
        return None
    if not isinstance(sid, str) or not _SESSION_ID.match(sid):
        log.warning("pointer: ignoring malformed sessionId in %s", pointer_path(cwd, home))
        return None
    return sid


def clear(cwd: Path, home: Path | None = None) -> bool:
    path = pointer_path(cwd, home)
    if not path.exists():
        return False
    path.unlink()
    return True
