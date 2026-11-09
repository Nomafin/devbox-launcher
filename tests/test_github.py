import json
import subprocess

import pytest

from launcher.github import Client, GithubError


class Gh:
    """Answers by the first matching piece of the command, and logs each one."""

    def __init__(self, **answers):
        self.answers = answers
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        joined = " ".join(cmd)
        for piece, (code, out, err) in self.answers.items():
            if piece.replace("_", " ") in joined or piece in joined:
                return subprocess.CompletedProcess(cmd, code, stdout=out, stderr=err)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def _repo(name, owner="Owner", description="desc"):
    return json.dumps({"name": name, "full_name": f"{owner}/{name}", "description": description,
                       "pushed_at": "2026-10-20T08:00:00Z",
                       "clone_url": f"https://github.com/{owner}/{name}.git"})


def test_repos_reads_one_json_object_per_line():
    run = Gh(user=(0, _repo("alpha") + "\n" + _repo("beta", description=None) + "\n", ""))
    repos = Client(run).repos()
    assert [r.full_name for r in repos] == ["Owner/alpha", "Owner/beta"]
    assert repos[0].slug == "alpha" and repos[0].updated_on.startswith("2026-10-20")
    assert repos[0].clone_url == "https://github.com/Owner/alpha.git"
    assert repos[1].description == ""


def test_repos_asks_for_everything_the_account_can_reach_and_skips_archived():
    run = Gh()
    Client(run).repos()
    joined = " ".join(run.calls[0])
    assert "affiliation=owner,collaborator,organization_member" in joined
    assert "select(.archived | not)" in joined and "--paginate" in joined


@pytest.mark.parametrize("query,found", [("ALPHA", ["alpha"]), ("owner/", ["alpha", "beta"]),
                                         ("second", ["beta"]), ("nope", [])])
def test_repos_filters_on_name_and_description(query, found):
    run = Gh(user=(0, _repo("alpha") + "\n" + _repo("beta", description="The second") + "\n", ""))
    assert [r.slug for r in Client(run).repos(query)] == found


def test_a_gh_failure_carries_its_last_line():
    run = Gh(user=(1, "", "some detail\nHTTP 401: Bad credentials\n"))
    with pytest.raises(GithubError, match="HTTP 401: Bad credentials"):
        Client(run).repos()


def test_a_missing_gh_is_an_error_for_the_page():
    def run(cmd):
        raise FileNotFoundError("gh")
    with pytest.raises(GithubError, match="could not run"):
        Client(run).repos()


def test_an_unreadable_answer_is_an_error_for_the_page():
    with pytest.raises(GithubError, match="could not be read"):
        Client(Gh(user=(0, "not json\n", ""))).repos()


def test_accepts_only_the_owners_invitations_to_the_owners_repos():
    run = Gh(invitations=(0, ("1\tOwner\tOwner/alpha\n"
                              "2\towner\tsome-org/their-repo\n"
                              "3\tstranger\tOwner/beta\n"
                              "4\tstranger\tstranger/repo\n"), ""))
    assert Client(run).accept_invitations("owner") == ["Owner/alpha"]
    patches = [c for c in run.calls if "PATCH" in c]
    assert patches == [["gh", "api", "-X", "PATCH", "user/repository_invitations/1", "--silent"]]


def test_create_repo_is_private_and_sets_origin(tmp_path):
    run = Gh()
    Client(run).create_repo("kokeilu", tmp_path)
    assert run.calls == [["gh", "repo", "create", "kokeilu", "--private", "--source",
                          str(tmp_path), "--remote", "origin", "--push"]]
