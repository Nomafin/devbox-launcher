import subprocess
from pathlib import Path

import pytest

from launcher.discovery import discover_projects
from launcher.github import Repo
from launcher.projects import (
    CloneJobs, ProjectError, checkout, current_branch, default_run, init_project,
    remote_branches, valid_name,
)

# Local identity and no signing, so the tests pass on a machine without
# a global git config.
GIT = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]


def git(*args, cwd=None):
    subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_source(tmp_path: Path) -> Path:
    """A bare repo with `main` and `dev`, standing in for GitHub."""
    bare = tmp_path / "src.git"
    git("init", "--bare", "-b", "main", str(bare))
    work = tmp_path / "work"
    git("init", "-b", "main", str(work))
    (work / "README").write_text("hi\n")
    git("add", "README", cwd=work)
    git("commit", "-m", "init", cwd=work)
    git("branch", "dev", cwd=work)
    git("remote", "add", "origin", str(bare), cwd=work)
    git("push", "origin", "main", "dev", cwd=work)
    return bare


def repo(url: str, slug: str = "thing") -> Repo:
    return Repo(slug=slug, full_name=f"Owner/{slug}", description="", updated_on="", clone_url=url)


def now(job):
    job()


@pytest.mark.parametrize("name", ["thing", "my-repo", "a.b_c", "X1"])
def test_valid_names(name):
    assert valid_name(name)


@pytest.mark.parametrize("name", ["", ".hidden", "-x", "a/b", "..", "a b", "x.git", "a" * 101])
def test_invalid_names(name):
    assert not valid_name(name)


def test_clone_lands_in_place_with_the_github_remote_and_can_be_listed(tmp_path):
    source = make_source(tmp_path)
    base = tmp_path / "projects"
    base.mkdir()
    jobs = CloneJobs(spawn=now)
    jobs.start(base, repo(str(source)))
    assert jobs.status("thing").state == "done"
    remotes = subprocess.run(["git", "remote"], cwd=base / "thing", capture_output=True, text=True)
    assert remotes.stdout.split() == ["github"]
    assert [p.slug for p in discover_projects(base)] == ["thing"]


def test_a_failed_clone_leaves_nothing_and_says_why(tmp_path):
    base = tmp_path / "projects"
    base.mkdir()
    jobs = CloneJobs(spawn=now)
    jobs.start(base, repo(str(tmp_path / "missing.git")))
    status = jobs.status("thing")
    assert status.state == "failed" and status.error.startswith("Clone failed")
    assert not (base / "thing").exists() and discover_projects(base) == []


def test_a_login_failure_says_what_to_run(tmp_path):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 128, stdout="", stderr="fatal: could not read Username for 'https://github.com'")
    jobs = CloneJobs(run=run, spawn=now)
    jobs.start(tmp_path, repo("https://github.com/Owner/thing.git"))
    assert "gh auth setup-git" in jobs.status("thing").error


def test_clone_never_waits_for_a_password(tmp_path):
    seen = {}

    def run(cmd, env=None, timeout=None):
        seen.update(cmd=cmd, env=env)
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no")
    CloneJobs(run=run, spawn=now).start(tmp_path, repo("u"))
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert seen["cmd"][:5] == ["git", "clone", "-o", "github", "--"]


def test_clone_that_raises_ends_failed_and_can_be_retried(tmp_path):
    source = make_source(tmp_path)
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return default_run(cmd, **kw)
    jobs = CloneJobs(run=run, spawn=now)
    jobs.start(tmp_path, repo(str(source)))
    assert jobs.status("thing").state == "failed"
    jobs.start(tmp_path, repo(str(source)))
    assert jobs.status("thing").state == "done"


def test_a_name_taken_during_the_clone_is_not_overwritten(tmp_path):
    source = make_source(tmp_path)
    base = tmp_path / "projects"
    base.mkdir()

    def run(cmd, **kw):
        result = default_run(cmd, **kw)
        (base / "thing").mkdir()
        (base / "thing" / "mine").write_text("keep")
        return result
    jobs = CloneJobs(run=run, spawn=now)
    jobs.start(base, repo(str(source)))
    assert jobs.status("thing").error == "Already cloned."
    assert (base / "thing" / "mine").read_text() == "keep"
    assert not (base / ".cloning" / "thing").exists()


def test_start_refuses_a_bad_name_and_an_existing_directory(tmp_path):
    jobs = CloneJobs(spawn=now)
    with pytest.raises(ProjectError, match="Invalid"):
        jobs.start(tmp_path, repo("u", slug="../evil"))
    (tmp_path / "thing").mkdir()
    with pytest.raises(ProjectError, match="Already cloned"):
        jobs.start(tmp_path, repo("u"))


def test_a_double_tap_starts_one_clone(tmp_path):
    started = []
    jobs = CloneJobs(spawn=started.append)
    jobs.start(tmp_path, repo("u"))
    jobs.start(tmp_path, repo("u"))
    assert len(started) == 1


def _cloned(tmp_path):
    source = make_source(tmp_path)
    base = tmp_path / "projects"
    base.mkdir()
    CloneJobs(spawn=now).start(base, repo(str(source)))
    return base / "thing"


def test_remote_branches_of_any_remote_without_head(tmp_path):
    assert remote_branches(_cloned(tmp_path), default_run) == ["dev", "main"]


def test_remote_branches_keep_slashes(tmp_path):
    path = _cloned(tmp_path)
    git("update-ref", "refs/remotes/github/feature/x", "HEAD", cwd=path)
    assert "feature/x" in remote_branches(path, default_run)


def test_checkout_existing_remote_branch_and_a_new_one(tmp_path):
    path = _cloned(tmp_path)
    checkout(path, "dev", False, default_run)
    assert current_branch(path, default_run) == "dev"
    checkout(path, "mine", True, default_run)
    assert current_branch(path, default_run) == "mine"


@pytest.mark.parametrize("branch,new,message", [
    ("dev", True, "already exists"), ("main", True, "already exists"),
    ("nope", False, "No such branch"), ("-x", False, "Invalid"), ("a..b", True, "Invalid"),
])
def test_checkout_refusals(tmp_path, branch, new, message):
    path = _cloned(tmp_path)
    with pytest.raises(ProjectError, match=message):
        checkout(path, branch, new, default_run)
    assert current_branch(path, default_run) == "main"


def test_init_project_is_an_empty_repo_on_main(tmp_path):
    path = init_project(tmp_path, "kokeilu", default_run)
    assert (path / ".git").is_dir() and current_branch(path, default_run) == "main"
    assert [p.slug for p in discover_projects(tmp_path)] == ["kokeilu"]


def test_init_project_refusals(tmp_path):
    with pytest.raises(ProjectError, match="Invalid"):
        init_project(tmp_path, "../evil", default_run)
    (tmp_path / "taken").mkdir()
    with pytest.raises(ProjectError, match="already exists"):
        init_project(tmp_path, "taken", default_run)
