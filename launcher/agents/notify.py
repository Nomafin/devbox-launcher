"""Push notifications (ntfy) when an agent creates or wakes another.

The topic URL comes from LAUNCHER_NTFY_URL, or else from what install.sh
recorded (~/.config/devbox-launcher/ntfy-url), since agentctl runs in an
agent's shell where the units' environment is not set. Neither set means no
pushes. A push that fails is dropped: it must never fail the command.
"""

import os
import urllib.request
from pathlib import Path

TIMEOUT = 5.0


def url(home) -> str | None:
    env = os.environ.get("LAUNCHER_NTFY_URL")
    if env:
        return env.strip() or None
    try:
        recorded = (Path(home) / ".config" / "devbox-launcher" / "ntfy-url").read_text().strip()
    except OSError:
        return None
    return recorded or None


def send(home, message: str) -> None:
    target = url(home)
    if not target:
        return
    request = urllib.request.Request(target, data=message.encode(), method="POST",
                                     headers={"Title": "agents", "Tags": "robot"})
    try:
        urllib.request.urlopen(request, timeout=TIMEOUT).close()
    except Exception:
        pass
