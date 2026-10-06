"""The git side of project instances.

An instance works in a git worktree under the project, so several sessions can
edit the same repo at once without fighting over one checkout. Every git call
the launcher makes for instances lives here; nothing else shells out to git.

Removal never forces. The launcher is used from a phone, where a mis-tap is
easy and uncommitted work is not recoverable, so a worktree with anything of
its own in it is refused with the reason rather than deleted.
"""

import subprocess
from pathlib import Path
from typing import Callable

Runner = Callable[[list[str]], subprocess.CompletedProcess]

# Longer than the tmux calls: `worktree add` writes a whole checkout, and a
# large repo on a small box is not instant.
TIMEOUT = 60.0


def default_run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        # Reads as a failed command, which every caller already handles.
        return subprocess.CompletedProcess(cmd, 124, stdout="", stderr="git timed out")


def path(project_cwd, name: str) -> Path:
    # Where the Claude CLI puts its own worktrees, so there is one such place
    # in a repo rather than two.
    return Path(project_cwd) / ".claude" / "worktrees" / name


def exists(project_cwd, name: str) -> bool:
    return path(project_cwd, name).is_dir()


def _error(result, fallback: str) -> str:
    return (result.stderr or "").strip() or fallback


def _has_branch(project_cwd, name: str, run: Runner) -> bool:
    return run(["git", "-C", str(project_cwd), "rev-parse", "--verify", "--quiet",
                f"refs/heads/{name}"]).returncode == 0


def create(project_cwd, name: str, run: Runner = default_run) -> str | None:
    """Add the worktree on branch `name`. None on success, else git's reason.

    The branch is created off whatever the project has checked out, unless it
    already exists — asking for an instance named after an existing branch
    means working on that branch, not refusing.
    """
    target = str(path(project_cwd, name))
    args = ["git", "-C", str(project_cwd), "worktree", "add"]
    args += [target, name] if _has_branch(project_cwd, name, run) else ["-b", name, target]
    result = run(args)
    if result.returncode != 0:
        return _error(result, "git could not create the worktree.")
    return None


def _ignored_root_names(stdout: str) -> list[str]:
    """The bare top-level names out of a `--ignored=matching` listing.

    `--ignored=matching` lists each ignored path git actually found, rather
    than collapsing a whole ignored directory into its own name — so a file
    inside one comes back with a `/` in it. Only a bare top-level name (no
    `/`) is one worth reporting: a directory that is itself ignored
    (`node_modules/`) is reported the same collapsed way `--ignored` always
    reports it — as a single entry ending in `/` — which also has a `/` in
    it and so is filtered out here, same as anything nested.
    """
    names = []
    for line in (stdout or "").splitlines():
        if not line.startswith("!! "):
            continue
        name = line[3:].strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1]
        if name and "/" not in name:
            names.append(name)
    return names


def blockers(worktree_path, run: Runner = default_run) -> str | None:
    """Why this worktree must not be removed, or None when it is safe.

    Three questions, because they lose different things: changes that were
    never committed, ignored files at the worktree's top level that `git
    worktree remove` deletes anyway (`git status` never mentions those — a
    `.env` or a scratch note dropped by a session is exactly the kind of
    unrecoverable work this whole function exists to protect), and commits
    that exist nowhere else.

    An ignored *directory* (`node_modules/`, `.venv/`, `target/`) is not a
    reason to refuse: almost every real worktree has one, and a blanket
    refusal on those would make Remove useless far more often than it
    protects anything.
    """
    wt = str(worktree_path)
    dirty = run(["git", "-C", wt, "status", "--porcelain"])
    if dirty.returncode != 0:
        return _error(dirty, "git could not read the worktree.")
    if (dirty.stdout or "").strip():
        return "It has uncommitted changes."
    # Fails closed like every other question here: this function is the only
    # thing standing between a phone tap and unrecoverable work.
    ignored = run(["git", "-C", wt, "status", "--porcelain", "--ignored=matching"])
    if ignored.returncode != 0:
        return _error(ignored, "git could not check for ignored files.")
    root_ignored = _ignored_root_names(ignored.stdout)
    if root_ignored:
        return f"It has ignored files git would delete: {', '.join(root_ignored[:3])}"
    # An unborn HEAD (no commits at all) is a real case here — a fresh
    # `git init` skips the GitHub check entirely, so such projects exist on
    # this box — and it has no commits of its own to lose by definition.
    # That is a safe answer, not an unknown one: skip the uniqueness question
    # rather than let it fail on a HEAD that does not exist yet, which would
    # otherwise refuse forever. A Remove that can never succeed is worse than
    # the failure the fail-closed rule below defends against.
    if run(["git", "-C", wt, "rev-parse", "--verify", "--quiet", "HEAD"]).returncode != 0:
        return None
    # What exists ONLY here: HEAD minus every remote and every other local
    # branch. Asking only `--not --remotes` would call every commit unique in a
    # repo with no remote at all — and those exist (a fresh `git init` skips
    # the GitHub check entirely), which would make such an instance
    # unremovable forever.
    # `--exclude` matches against the short name `--branches` itself prints
    # (e.g. `wip`), not `heads/wip` or the full `refs/heads/wip` — either of
    # those silently fails to match, which would make even a clean instance
    # look unremovable.
    #
    # Both calls below fail closed: this function is the only thing standing
    # between a phone tap and unrecoverable work, so a check that could not
    # run is a reason to refuse, not a quiet "nothing found."
    branch_result = run(["git", "-C", wt, "rev-parse", "--abbrev-ref", "HEAD"])
    if branch_result.returncode != 0:
        return _error(branch_result, "git could not read the worktree's branch.")
    branch = branch_result.stdout.strip()
    unique = run(["git", "-C", wt, "log", "--oneline", "HEAD", "--not",
                  f"--exclude={branch}", "--branches", "--remotes"])
    if unique.returncode != 0:
        return _error(unique, "git could not check for commits unique to this worktree.")
    lines = [line for line in (unique.stdout or "").splitlines() if line.strip()]
    if lines:
        plural = "" if len(lines) == 1 else "s"
        return f"It has {len(lines)} commit{plural} that exist only in this worktree."
    return None


def remove(project_cwd, name: str, run: Runner = default_run) -> str | None:
    result = run(["git", "-C", str(project_cwd), "worktree", "remove",
                  str(path(project_cwd, name))])
    if result.returncode != 0:
        return _error(result, "git could not remove the worktree.")
    return None


def prune(project_cwd, run: Runner = default_run) -> None:
    """Forget worktrees whose directory has gone. Best effort: this runs when
    the launcher is already cleaning up after something that went missing."""
    run(["git", "-C", str(project_cwd), "worktree", "prune"])
