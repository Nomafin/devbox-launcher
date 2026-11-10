"""Getting a project onto the box: clone it, pick a branch, or start one fresh.

A clone can take minutes, longer than a request should, so it runs in the
background and the page polls `CloneJobs.status`. It lands in
`<base>/.cloning/<slug>` first and is renamed into place only when complete:
discovery never sees a half-cloned repo (`.cloning` itself has no `.git`), and
a failed clone leaves nothing behind.

Every name that comes from outside (repo slug, project name, branch) is
validated before it reaches a path or a command. Subprocess calls are
injected, so the tests choose between real git and a fake.
"""

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from launcher.github import REMOTE, Repo

Runner = Callable[..., subprocess.CompletedProcess]
Spawn = Callable[[Callable[[], None]], None]

CLONE_TIMEOUT = 600.0
CLONING_DIR = ".cloning"

_LOGIN_MARKERS = ("could not read Username", "Authentication failed", "terminal prompts disabled")
_LOGIN_MESSAGE = "git could not log in to GitHub. On the box, run: gh auth setup-git"
_SSH_LOGIN_MARKERS = ("Permission denied (publickey)", "Host key verification failed")
_SSH_LOGIN_MESSAGE = ("git could not log in to GitHub over SSH. On the box, check that "
                      "ssh -T git@github.com works.")

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


class ProjectError(Exception):
    """Something went wrong; the message is fit to show on the page."""


def default_run(
    cmd: list[str], *, cwd: Path | None = None, env: dict | None = None, timeout: float = 60.0,
) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **env} if env else None
    try:
        # errors="replace": a server hook's non-UTF-8 output must not raise.
        return subprocess.run(cmd, cwd=cwd, env=full_env, capture_output=True, text=True,
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        # Reads as a failed command, which every caller already handles.
        return subprocess.CompletedProcess(cmd, 124, stdout="", stderr=f"{cmd[0]} timed out")


def _thread_spawn(job: Callable[[], None]) -> None:
    threading.Thread(target=job, daemon=True).start()


def valid_name(name: str) -> bool:
    """A repo slug or project name that is safe as a single path component."""
    return bool(_NAME.fullmatch(name)) and not name.endswith(".git")


@dataclass(frozen=True)
class CloneStatus:
    state: str  # "running", "done" or "failed"
    error: str | None


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _clone_error(result: subprocess.CompletedProcess) -> str:
    stderr = result.stderr or ""
    if any(marker in stderr for marker in _LOGIN_MARKERS):
        return _LOGIN_MESSAGE
    if any(marker in stderr for marker in _SSH_LOGIN_MARKERS):
        return _SSH_LOGIN_MESSAGE
    last = _last_line(stderr)
    return f"Clone failed: {last}" if last else f"Clone failed (exit {result.returncode})."


class CloneJobs:
    """Background clones, one at a time per slug."""

    def __init__(self, run: Runner = default_run, spawn: Spawn = _thread_spawn):
        self._run = run
        self._spawn = spawn
        self._lock = threading.Lock()
        self._jobs: dict[str, CloneStatus] = {}

    def start(self, base_dir: Path, repo: Repo) -> None:
        slug = repo.slug
        if not valid_name(slug):
            raise ProjectError("Invalid repository name.")
        if (base_dir / slug).exists():
            raise ProjectError("Already cloned.")
        # Decided under the lock, so a double tap cannot start two clones.
        with self._lock:
            current = self._jobs.get(slug)
            if current is not None and current.state == "running":
                return
            self._jobs[slug] = CloneStatus("running", None)
        self._spawn(lambda: self._clone(base_dir, repo))

    def status(self, slug: str) -> CloneStatus | None:
        with self._lock:
            return self._jobs.get(slug)

    def _finish(self, slug: str, error: str | None) -> None:
        with self._lock:
            self._jobs[slug] = CloneStatus("failed" if error else "done", error)

    def _clone(self, base_dir: Path, repo: Repo) -> None:
        tmp = base_dir / CLONING_DIR / repo.slug
        dest = base_dir / repo.slug
        try:
            # Leftovers from a clone the launcher did not live to finish.
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.parent.mkdir(parents=True, exist_ok=True)
            result = self._run(
                ["git", "clone", "-o", REMOTE, "--", repo.clone_url, str(tmp)],
                # A prompt nobody can answer would hang until the timeout.
                env={"GIT_TERMINAL_PROMPT": "0"},
                timeout=CLONE_TIMEOUT,
            )
            if result.returncode != 0:
                error = _clone_error(result)
            elif dest.exists():
                # Something took the name while the clone ran.
                error = "Already cloned."
            else:
                os.rename(tmp, dest)
                error = None
        except Exception as e:
            # Anything, not just OSError: a job that dies here would leave the
            # slug "running" for good, and its page polling forever.
            error = f"Clone failed: {e}"
        if error:
            shutil.rmtree(tmp, ignore_errors=True)
        self._finish(repo.slug, error)


def remote_branches(path: Path, run: Runner) -> list[str]:
    """Branch names on the remotes, without the remote's name or `HEAD`."""
    prefix = "refs/remotes/"
    result = run(["git", "for-each-ref", "--format=%(refname)", prefix], cwd=path)
    if result.returncode != 0:
        return []
    names = set()
    for line in result.stdout.splitlines():
        _, _, name = line.strip()[len(prefix):].partition("/")
        if name and name != "HEAD":
            names.add(name)
    return sorted(names)


def _ref_exists(path: Path, ref: str, run: Runner) -> bool:
    return run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=path).returncode == 0


def checkout(path: Path, branch: str, new: bool, run: Runner) -> None:
    """Switch to `branch`: a new one from HEAD, or an existing one (local or on a remote)."""
    # A leading dash would read as an option to the commands below.
    if branch.startswith("-") or run(["git", "check-ref-format", "--branch", branch], cwd=path).returncode != 0:
        raise ProjectError("Invalid branch name.")
    local = _ref_exists(path, f"refs/heads/{branch}", run)
    remote = branch in remote_branches(path, run)
    if new:
        if local or remote:
            raise ProjectError("A branch with that name already exists.")
        cmd = ["git", "checkout", "-b", branch]
    else:
        if not (local or remote):
            raise ProjectError("No such branch.")
        # A branch only on a remote: git creates a local one tracking it.
        cmd = ["git", "checkout", branch, "--"]
    result = run(cmd, cwd=path)
    if result.returncode != 0:
        raise ProjectError(f"Checkout failed: {_last_line(result.stderr or '')}")


def init_project(base_dir: Path, name: str, run: Runner) -> Path:
    """A new, empty repo on `main`, for a project that does not exist anywhere yet."""
    if not valid_name(name):
        raise ProjectError("Invalid project name.")
    path = base_dir / name
    if path.exists():
        raise ProjectError("A project with that name already exists.")
    result = run(["git", "init", "-b", "main", str(path)])
    if result.returncode != 0:
        raise ProjectError(f"git init failed: {_last_line(result.stderr or '')}")
    return path


def first_commit(path: Path, run: Runner) -> None:
    """An empty commit, so the project has something to branch from: an agent's
    worktree cannot start in a repo with no commits, and a pull request needs a
    base on the remote."""
    result = run(["git", "commit", "--allow-empty", "-m", "Initial commit"], cwd=path)
    if result.returncode != 0:
        raise ProjectError(f"git could not make the first commit: {_last_line(result.stderr or '')}")


def current_branch(path: Path, run: Runner) -> str:
    """The checked-out branch; empty on a detached HEAD or a failure."""
    result = run(["git", "branch", "--show-current"], cwd=path)
    return result.stdout.strip() if result.returncode == 0 else ""
