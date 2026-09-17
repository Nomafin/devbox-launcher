"""The `origin` gotcha, detected before it costs you a failed Start.

A repo whose `origin` points at GitHub makes `claude remote-control` fail to
create a session — the listener comes up, the row goes green, and no session
ever appears in the app. The fix is to rename the remote so remote-control skips
its check. That was documented in the runbook, which is no use to someone
holding a phone, so the launcher checks for it and offers the rename.

The config file is parsed directly rather than shelling out to `git`: the page
asks about every project on every poll, and reading one small file is far
cheaper than a subprocess per repo. The answer is cached against the config's
mtime, so a repo is re-read only after something actually changes it.
"""

import configparser
import subprocess
from pathlib import Path
from typing import Callable

Runner = Callable[[list[str]], subprocess.CompletedProcess]

# A git that hangs (a stuck lock file, say) must not hang the request with it.
TIMEOUT = 10.0

# path -> (config mtime, blocks?)
_cache: dict[Path, tuple[float, bool]] = {}


def default_run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        # Reads as a failed command, which every caller already handles.
        return subprocess.CompletedProcess(cmd, 124, stdout="", stderr="git timed out")


def clear_cache() -> None:
    _cache.clear()


def _config_path(repo: Path) -> Path | None:
    """`.git/config`, when there is one to read.

    `.git` is a *file* for a worktree or submodule; those have no config here
    and are not what this check is about, so they read as "nothing to flag".
    """
    git = repo / ".git"
    if not git.is_dir():
        return None
    config = git / "config"
    return config if config.is_file() else None


def _remotes(config: Path) -> dict[str, str]:
    # No interpolation: a URL with a `%` in it (a percent-encoded password) is
    # data, not a `%(name)s` reference, and the default parser raises on it
    # from the lookup below rather than from read().
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(config)
    except (configparser.Error, OSError):
        return {}
    out = {}
    for section in parser.sections():
        # git writes these as: [remote "origin"]
        if section.startswith('remote "') and section.endswith('"'):
            out[section[len('remote "'):-1]] = parser[section].get("url", "")
    return out


def _blocks(config: Path) -> bool:
    return "github.com" in _remotes(config).get("origin", "")


def blocks_remote_control(repo: Path) -> bool:
    """True when this repo's `origin` will stop a listener creating a session."""
    config = _config_path(repo)
    if config is None:
        return False
    try:
        mtime = config.stat().st_mtime
    except OSError:
        return False
    cached = _cache.get(repo)
    if cached and cached[0] == mtime:
        return cached[1]
    answer = _blocks(config)
    _cache[repo] = (mtime, answer)
    return answer


def rename_origin(repo: Path, run: Runner = default_run) -> str | None:
    """`git remote rename origin github`. Returns None on success, else why not.

    This is the launcher's only write into a repo, so it refuses anything it was
    not asked to do: no GitHub origin, or a `github` remote already present, and
    it does nothing rather than guess.
    """
    config = _config_path(repo)
    if config is None or not _blocks(config):
        return "This repo has no GitHub origin to rename."
    if "github" in _remotes(config):
        return "This repo already has a remote named github."
    result = run(["git", "-C", str(repo), "remote", "rename", "origin", "github"])
    # Drop the cached answer either way: on success it is now wrong, and on
    # failure git may still have rewritten part of the config.
    _cache.pop(repo, None)
    if result.returncode != 0:
        return (result.stderr or "").strip() or "git could not rename the remote."
    return None
