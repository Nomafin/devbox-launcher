"""How much room the box has left.

Only one question is worth answering on the page — is there space for another
listener — so this reports exactly that and nothing else. Inside the devbox LXC
lxcfs makes /proc/meminfo show the *container's* limit rather than the
hypervisor's, which is the number that constrains a Start.

Disk earns its place beside memory because on a box that builds Docker images
and node_modules into a 20 GB rootfs, filling the disk is likelier than filling
the RAM.

A Mac has no /proc: there the same two numbers come from vm_stat and sysconf.
"""

import os
import re
import subprocess
import sys
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


def _default_run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=5)


def _darwin_fields(run=_default_run) -> dict[str, int]:
    """/proc/meminfo's two fields, in kB. Inactive and speculative pages count
    as available, as they do in Linux's MemAvailable: the system takes them
    back when something needs the room."""
    try:
        result = run(["/usr/bin/vm_stat"])
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    text = result.stdout or ""
    size = re.search(r"page size of (\d+) bytes", text)
    pages = dict(re.findall(r"^Pages (free|inactive|speculative):\s+(\d+)\.", text, re.M))
    if not size or len(pages) != 3:
        return {}
    available = sum(int(n) for n in pages.values()) * int(size.group(1)) // 1024
    return {"MemTotal": total, "MemAvailable": available}


def _gb(kb: int) -> str:
    """`12` for a whole number, `7.2` otherwise: the total is a constant of the
    box, and a decimal on it only competes with the number that moves."""
    value = round(kb / _GIB, 1)
    return str(int(value)) if value == int(value) else str(value)


def memory(path: Path | None = None) -> str:
    """"4.2 of 12 GB", or "" when it cannot be read.

    The header is decoration: a box that does not answer must not be the reason
    the page fails to render.
    """
    fields = _fields(path or MEMINFO)
    if not fields and path is None and sys.platform == "darwin":
        fields = _darwin_fields()
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
