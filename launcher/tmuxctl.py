"""The launcher's own tmux socket, used by sign-in and Run command.

Agents run on a socket of their own (see agents/runtime.py); nothing here
starts or reads one.
"""

import subprocess
from typing import Callable

SOCKET = "devbox-launcher"
Runner = Callable[[list[str]], subprocess.CompletedProcess]

# A tmux call that hangs (a wedged server) must not hang the page with it.
TIMEOUT = 10.0


class StartError(RuntimeError):
    """tmux refused to create the session; the message is tmux's own."""


def default_run(cmd: list[str], timeout: float = TIMEOUT) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Reads as a failed command, which every caller already handles.
        return subprocess.CompletedProcess(cmd, 124, stdout="", stderr="tmux timed out")


def tmux_args(args: list[str]) -> list[str]:
    return ["tmux", "-L", SOCKET, *args]
