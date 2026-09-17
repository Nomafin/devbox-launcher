import subprocess

from launcher import gitremote


def _repo(base, name, config: str | None = None):
    path = base / name
    git = path / ".git"
    git.mkdir(parents=True)
    if config is not None:
        (git / "config").write_text(config)
    return path


_GITHUB = """[core]
	repositoryformatversion = 0
[remote "origin"]
	url = git@github.com:dev/thing.git
	fetch = +refs/heads/*:refs/remotes/origin/*
"""

_GITHUB_HTTPS = """[remote "origin"]
	url = https://github.com/dev/thing.git
"""

_RENAMED = """[remote "github"]
	url = git@github.com:dev/thing.git
"""

_ELSEWHERE = """[remote "origin"]
	url = git@gitlab.com:dev/thing.git
"""


class FakeRun:
    def __init__(self, rc=0, stderr=""):
        self.calls = []
        self.rc, self.stderr = rc, stderr

    def __call__(self, cmd):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.rc, stdout="", stderr=self.stderr)


# --- detection ---------------------------------------------------------------

def test_a_github_origin_is_flagged(tmp_path):
    assert gitremote.blocks_remote_control(_repo(tmp_path, "a", _GITHUB))


def test_an_https_github_origin_is_flagged_too(tmp_path):
    assert gitremote.blocks_remote_control(_repo(tmp_path, "b", _GITHUB_HTTPS))


def test_an_origin_somewhere_else_is_fine(tmp_path):
    assert not gitremote.blocks_remote_control(_repo(tmp_path, "c", _ELSEWHERE))


def test_a_repo_with_no_origin_is_fine(tmp_path):
    assert not gitremote.blocks_remote_control(_repo(tmp_path, "d", _RENAMED))


def test_a_repo_with_no_config_is_fine(tmp_path):
    assert not gitremote.blocks_remote_control(_repo(tmp_path, "e"))


def test_a_directory_that_is_not_a_repo_is_fine(tmp_path):
    (tmp_path / "plain").mkdir()
    assert not gitremote.blocks_remote_control(tmp_path / "plain")


def test_only_the_origin_remotes_url_counts(tmp_path):
    # a github *upstream* does not block anything; only origin is checked
    path = _repo(tmp_path, "f", """[remote "origin"]
	url = git@gitlab.com:dev/thing.git
[remote "upstream"]
	url = git@github.com:someone/thing.git
""")
    assert not gitremote.blocks_remote_control(path)


def test_a_worktree_style_git_file_is_not_treated_as_a_repo(tmp_path):
    # a group's claude-project/ may be a plain dir; .git as a *file* (worktree
    # or submodule) has no config to read and must not raise
    path = tmp_path / "g"
    path.mkdir()
    (path / ".git").write_text("gitdir: /somewhere/else\n")
    assert not gitremote.blocks_remote_control(path)


# --- caching -----------------------------------------------------------------

def test_the_answer_is_cached_until_the_config_changes(tmp_path):
    path = _repo(tmp_path, "h", _GITHUB)
    gitremote.clear_cache()
    assert gitremote.blocks_remote_control(path)
    # rewrite with a newer mtime and a different answer
    config = path / ".git" / "config"
    config.write_text(_RENAMED)
    import os
    stat = config.stat()
    os.utime(config, (stat.st_atime + 10, stat.st_mtime + 10))
    assert not gitremote.blocks_remote_control(path)


# --- the fix -----------------------------------------------------------------

def test_rename_runs_the_git_command_in_the_repo(tmp_path):
    path = _repo(tmp_path, "i", _GITHUB)
    run = FakeRun()
    assert gitremote.rename_origin(path, run=run) is None
    assert run.calls == [["git", "-C", str(path), "remote", "rename", "origin", "github"]]


def test_rename_refuses_when_there_is_no_github_origin(tmp_path):
    path = _repo(tmp_path, "j", _ELSEWHERE)
    run = FakeRun()
    assert gitremote.rename_origin(path, run=run) == "This repo has no GitHub origin to rename."
    assert run.calls == []


def test_rename_refuses_when_a_github_remote_already_exists(tmp_path):
    path = _repo(tmp_path, "k", _GITHUB + '[remote "github"]\n\turl = x\n')
    run = FakeRun()
    assert gitremote.rename_origin(path, run=run) == "This repo already has a remote named github."
    assert run.calls == []


def test_rename_reports_what_git_said_when_it_fails(tmp_path):
    path = _repo(tmp_path, "l", _GITHUB)
    run = FakeRun(rc=1, stderr="fatal: could not lock config file\n")
    assert gitremote.rename_origin(path, run=run) == "fatal: could not lock config file"


def test_rename_invalidates_the_cache(tmp_path):
    path = _repo(tmp_path, "m", _GITHUB)
    gitremote.clear_cache()
    assert gitremote.blocks_remote_control(path)

    def run(cmd):
        (path / ".git" / "config").write_text(_RENAMED)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    assert gitremote.rename_origin(path, run=run) is None
    # without invalidation the mtime may land in the same tick and keep the
    # stale True, leaving a warning on a row that has just been fixed
    assert not gitremote.blocks_remote_control(path)


# --- parsing -----------------------------------------------------------------

_PERCENT = """[remote "origin"]
	url = https://user:p%40ss@github.com/x/y.git
"""


def test_a_url_with_a_percent_in_it_still_parses(tmp_path):
    # A percent-encoded password is data, not a %(name)s interpolation; the
    # default ConfigParser raised on it from the url lookup, which was outside
    # the try and took the whole page down.
    assert gitremote.blocks_remote_control(_repo(tmp_path, "p", _PERCENT))


def test_default_run_treats_a_timeout_as_a_failed_command(monkeypatch):
    def hang(cmd, **kwargs):
        assert kwargs["timeout"] == gitremote.TIMEOUT
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
    monkeypatch.setattr(gitremote.subprocess, "run", hang)
    result = gitremote.default_run(["git", "status"])
    assert result.returncode != 0
    assert result.stderr
