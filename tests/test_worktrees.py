import subprocess
from pathlib import Path

import pytest

from launcher import worktrees


def responder(handler):
    def run(cmd):
        rc, out, err = handler(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr=err)
    return run


def recorder(handler=lambda cmd: (0, "", "")):
    calls = []
    def h(cmd):
        calls.append(cmd)
        return handler(cmd)
    return calls, responder(h)


def test_path_is_under_the_claude_worktrees_dir(tmp_path):
    assert worktrees.path(tmp_path, "fix-auth") == tmp_path / ".claude" / "worktrees" / "fix-auth"


def test_create_makes_a_new_branch_off_head(tmp_path):
    def handler(cmd):
        # rev-parse --verify: the branch does not exist yet
        return (1, "", "") if "rev-parse" in cmd else (0, "", "")
    calls, run = recorder(handler)
    assert worktrees.create(tmp_path, "fix-auth", run=run) is None
    add = next(c for c in calls if "add" in c)
    assert add[:3] == ["git", "-C", str(tmp_path)]
    assert add[3:6] == ["worktree", "add", "-b"]
    assert add[6] == "fix-auth"
    assert add[7] == str(worktrees.path(tmp_path, "fix-auth"))


def test_create_checks_out_a_branch_that_already_exists(tmp_path):
    calls, run = recorder(lambda cmd: (0, "", ""))     # rev-parse succeeds
    assert worktrees.create(tmp_path, "fix-auth", run=run) is None
    add = next(c for c in calls if "add" in c)
    assert "-b" not in add
    assert add[-2:] == [str(worktrees.path(tmp_path, "fix-auth")), "fix-auth"]


def test_create_returns_gits_own_error(tmp_path):
    def handler(cmd):
        if "rev-parse" in cmd:
            return (1, "", "")
        return (128, "", "fatal: 'fix-auth' is already checked out at '/x'\n")
    assert worktrees.create(tmp_path, "fix-auth", run=responder(handler)) == (
        "fatal: 'fix-auth' is already checked out at '/x'")


def test_create_has_a_fallback_message(tmp_path):
    def handler(cmd):
        return (1, "", "") if "rev-parse" in cmd else (1, "", "")
    assert worktrees.create(tmp_path, "x", run=responder(handler)) == (
        "git could not create the worktree.")


def test_remove_never_forces(tmp_path):
    calls, run = recorder()
    assert worktrees.remove(tmp_path, "fix-auth", run=run) is None
    assert calls[0][3:5] == ["worktree", "remove"]
    assert "--force" not in calls[0] and "-f" not in calls[0]


def test_remove_returns_gits_own_error(tmp_path):
    run = responder(lambda cmd: (1, "", "fatal: validation failed\n"))
    assert worktrees.remove(tmp_path, "x", run=run) == "fatal: validation failed"


def test_prune_forgets_a_worktree_deleted_behind_gits_back(tmp_path):
    calls, run = recorder()
    worktrees.prune(tmp_path, run=run)
    assert calls[0][3:5] == ["worktree", "prune"]


def test_exists_follows_the_directory(tmp_path):
    assert worktrees.exists(tmp_path, "fix-auth") is False
    worktrees.path(tmp_path, "fix-auth").mkdir(parents=True)
    assert worktrees.exists(tmp_path, "fix-auth") is True


def test_blockers_fails_closed_when_the_uniqueness_check_errors(tmp_path):
    # status is clean, but the log call that looks for worktree-only commits
    # errors — an unanswered question is not a "no", so this must refuse
    # rather than fall through to None.
    def handler(cmd):
        if "status" in cmd:
            return (0, "", "")
        if "rev-parse" in cmd:
            return (0, "wip\n", "")
        return (1, "", "fatal: bad revision\n")
    assert worktrees.blockers(tmp_path, run=responder(handler)) == "fatal: bad revision"


def test_blockers_fails_closed_when_the_branch_name_lookup_errors(tmp_path):
    # no branch name means the exclusion pattern for the log call cannot be
    # built, so this must refuse rather than run a check it knows is wrong.
    #
    # The `log` call is made to succeed here, unlike the abbrev-ref call: if
    # the abbrev-ref guard were the thing actually producing the refusal
    # below, removing it must change the answer. Giving both calls the same
    # fallback failure would let this test pass even with that guard deleted,
    # because the (still failing) log call would produce the identical
    # message on its own.
    def handler(cmd):
        if "status" in cmd or "--verify" in cmd:
            return (0, "", "")   # clean, and HEAD exists (not unborn)
        if "log" in cmd:
            return (0, "", "")   # would succeed if ever reached
        return (128, "", "fatal: not a git repository\n")
    assert worktrees.blockers(tmp_path, run=responder(handler)) == (
        "fatal: not a git repository")


# --- blockers, against a real repo ------------------------------------------
# This is the one place where being wrong loses the user's work, so it is
# tested against git itself rather than a fake.

def git(*args, cwd):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git("init", "-b", "main", cwd=root)
    git("config", "user.email", "t@example.com", cwd=root)
    git("config", "user.name", "T", cwd=root)
    (root / "README.md").write_text("hi\n")
    git("add", "README.md", cwd=root)
    git("commit", "-m", "first", cwd=root)
    return root


def test_blockers_is_none_for_a_clean_worktree_with_nothing_of_its_own(repo):
    # a worktree whose branch is at the same commit as main has nothing to
    # lose — and this repo has no remote at all, which must not by itself make
    # every commit look unique
    assert worktrees.create(repo, "wip") is None
    assert worktrees.blockers(worktrees.path(repo, "wip")) is None


def test_blockers_reports_uncommitted_changes(repo):
    worktrees.create(repo, "wip")
    (worktrees.path(repo, "wip") / "README.md").write_text("edited\n")
    assert "uncommitted" in worktrees.blockers(worktrees.path(repo, "wip")).lower()


def test_blockers_reports_an_untracked_file(repo):
    worktrees.create(repo, "wip")
    (worktrees.path(repo, "wip") / "notes.txt").write_text("x\n")
    assert "uncommitted" in worktrees.blockers(worktrees.path(repo, "wip")).lower()


def test_blockers_reports_a_commit_that_exists_only_here(repo):
    worktrees.create(repo, "wip")
    wt = worktrees.path(repo, "wip")
    (wt / "README.md").write_text("edited\n")
    git("add", "README.md", cwd=wt)
    git("commit", "-m", "work", cwd=wt)
    message = worktrees.blockers(wt)
    assert "1 commit" in message and "only in this worktree" in message


def test_blockers_counts_several_commits(repo):
    worktrees.create(repo, "wip")
    wt = worktrees.path(repo, "wip")
    for n in range(2):
        (wt / f"f{n}").write_text("x\n")
        git("add", "-A", cwd=wt)
        git("commit", "-m", f"c{n}", cwd=wt)
    assert "2 commits" in worktrees.blockers(wt)


def test_blockers_ignores_commits_another_branch_also_has(repo):
    # merged into main and still checked out: the commits survive the removal,
    # so they are not a reason to refuse it
    worktrees.create(repo, "wip")
    wt = worktrees.path(repo, "wip")
    (wt / "f").write_text("x\n")
    git("add", "-A", cwd=wt)
    git("commit", "-m", "work", cwd=wt)
    git("merge", "--ff-only", "wip", cwd=repo)
    assert worktrees.blockers(wt) is None


def test_a_clean_worktree_can_then_actually_be_removed(repo):
    worktrees.create(repo, "wip")
    assert worktrees.remove(repo, "wip") is None
    assert worktrees.exists(repo, "wip") is False


@pytest.fixture
def unborn_repo(tmp_path):
    # A fresh `git init` with no commit at all: HEAD is unborn. This is a real
    # case on this box, not a contrived one — a project that was never
    # committed to skips the GitHub-origin check entirely.
    root = tmp_path / "project"
    root.mkdir()
    git("init", "-b", "main", cwd=root)
    git("config", "user.email", "t@example.com", cwd=root)
    git("config", "user.name", "T", cwd=root)
    return root


def test_blockers_is_none_for_a_worktree_with_an_unborn_head(unborn_repo):
    # No commits means nothing of its own to lose — that is a safe answer,
    # not an unknown one, so this must not fall into the uniqueness check
    # (which would fail on a HEAD that does not exist yet) and refuse forever.
    assert worktrees.create(unborn_repo, "probe") is None
    assert worktrees.blockers(worktrees.path(unborn_repo, "probe")) is None


# --- blockers, ignored files at the worktree root --------------------------
# Verified against real git, not a fake: `git status --porcelain` says nothing
# about an ignored file, so a fake handler could hide exactly the gap this
# check closes.

def test_blockers_refuses_a_root_level_ignored_file(repo):
    (repo / ".gitignore").write_text("secrets.env\n")
    git("add", ".gitignore", cwd=repo)
    git("commit", "-m", "gitignore", cwd=repo)
    worktrees.create(repo, "wip")
    wt = worktrees.path(repo, "wip")
    (wt / "secrets.env").write_text("TOKEN=x\n")
    message = worktrees.blockers(wt)
    assert message is not None and "secrets.env" in message


def test_blockers_names_the_ignored_file_it_found(repo):
    (repo / ".gitignore").write_text("secrets.env\n")
    git("add", ".gitignore", cwd=repo)
    git("commit", "-m", "gitignore", cwd=repo)
    worktrees.create(repo, "wip")
    wt = worktrees.path(repo, "wip")
    (wt / "secrets.env").write_text("TOKEN=x\n")
    assert worktrees.blockers(wt) == "It has ignored files git would delete: secrets.env"


def test_blockers_does_not_refuse_an_ignored_directory(repo):
    # node_modules et al. are gitignored in almost every real worktree; a
    # blanket refusal on those would make Remove useless far more often than
    # it protects anything, so only a bare top-level *file* refuses.
    (repo / ".gitignore").write_text("node_modules/\n")
    git("add", ".gitignore", cwd=repo)
    git("commit", "-m", "gitignore", cwd=repo)
    worktrees.create(repo, "wip")
    wt = worktrees.path(repo, "wip")
    (wt / "node_modules").mkdir()
    (wt / "node_modules" / "pkg.js").write_text("x\n")
    assert worktrees.blockers(wt) is None


def test_blockers_still_reports_an_untracked_file_with_an_unborn_head(unborn_repo):
    # The unborn-HEAD shortcut only skips the uniqueness question; the
    # uncommitted-changes check still runs first, so work a user actually made
    # in this worktree is not waved through.
    worktrees.create(unborn_repo, "probe")
    (worktrees.path(unborn_repo, "probe") / "notes.txt").write_text("x\n")
    message = worktrees.blockers(worktrees.path(unborn_repo, "probe"))
    assert message is not None and "uncommitted" in message.lower()
