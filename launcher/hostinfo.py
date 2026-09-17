"""How much room the box has left.

Only one question is worth answering on the page — is there space for another
listener — so this reports exactly that and nothing else. Inside the devbox LXC
lxcfs makes /proc/meminfo show the *container's* limit rather than the
hypervisor's, which is the number that constrains a Start.

Disk earns its place beside memory because on a box that builds Docker images
and node_modules into a 20 GB rootfs, filling the disk is likelier than filling
the RAM.
"""

import os
from pathlib import Path

MEMINFO = Path("/proc/meminfo")
ROOT = Path("/")

_GIB = 1024 * 1024  # /proc/meminfo is in kB


def _fields(path: Path) -> dict[str, int]:
    try:
        text = path.read_text()
    except OSError:
        return {}
    out = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        value = rest.strip().split(" ")[0]
        if value.isdigit():
            out[key] = int(value)
    return out


def _gb(kb: int) -> str:
    """`12` for a whole number, `7.2` otherwise: the total is a constant of the
    box, and a decimal on it only competes with the number that moves."""
    value = round(kb / _GIB, 1)
    return str(int(value)) if value == int(value) else str(value)


def memory(path: Path = MEMINFO) -> str:
    """"4.2 of 12 GB", or "" when it cannot be read.

    The header is decoration: a box that does not answer must not be the reason
    the page fails to render.
    """
    fields = _fields(path)
    total, available = fields.get("MemTotal"), fields.get("MemAvailable")
    if not total or available is None:
        return ""
    used = max(total - available, 0)
    return f"{round(used / _GIB, 1)} of {_gb(total)} GB"


def disk(path: Path = ROOT) -> str:
    """"6.0 of 20 GB", or "" when it cannot be read.

    Uses the blocks *available* rather than free: the root-reserved remainder is
    not space you can fill, so counting it would overstate the room left.
    """
    try:
        stat = os.statvfs(path)
    except OSError:
        return ""
    total = stat.f_blocks * stat.f_frsize
    used = total - stat.f_bavail * stat.f_frsize
    if not total:
        return ""
    gib = 1024 ** 3
    return f"{round(used / gib, 1)} of {_gb(total // 1024)} GB"
