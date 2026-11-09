"""The Clone and New project pages. GitHub and the background clone are faked;
branch switching and `git init` run real git in the temp dir, since git's state
is the point there."""
import subprocess

import pytest
from fastapi.testclient import TestClient

from launcher.app import create_app
from launcher.github import GithubError, Repo
from launcher.projects import CloneStatus, default_run

from agents.conftest import make_repo
from test_app import FakeAuth, FakeGit, FakeHost, agents_ctx

ALPHA = Repo("alpha", "Hooace/alpha", "The first one", "2026-10-20T08:00:00Z",
             "https://github.com/Hooace/alpha.git")
BETA = Repo("beta", "bertborgren/beta", "Second <b>repo</b>", "2026-09-01T10:00:00Z",
            "https://github.com/bertborgren/beta.git")


class FakeGithub:
    def __init__(self, repos=(ALPHA, BETA), error=None, accepted=()):
        self.repo_list = list(repos)
        self.error = error
        self.accepted = list(accepted)
        self.invites_from = []
        self.created = []

    def repos(self, query=""):
        if self.error:
            raise self.error
        needle = query.lower()
        return [r for r in self.repo_list if needle in r.full_name.lower()]

    def accept_invitations(self, owner):
        self.invites_from.append(owner)
        return self.accepted

    def create_repo(self, name, path):
        if self.error:
            raise self.error
        self.created.append((name, path))


class FakeJobs:
    def __init__(self):
        self.started = []
        self.states = {}

    def start(self, base_dir, repo):
        self.started.append(repo.slug)
        self.states[repo.slug] = CloneStatus("running", None)

    def status(self, slug):
        return self.states.get(slug)


def git(*args, cwd=None):
    return subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                          cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def base_of(tmp_path):
    base = tmp_path / "projects"
    base.mkdir(exist_ok=True)
    return base


def with_identity(cmd, **kw):
    return default_run(cmd, env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}, **kw)


def client(tmp_path, on=True, gh=None, jobs=None, invites_from=None, projects_run=with_identity):
    base = base_of(tmp_path)
    return TestClient(create_app(
        base_dir_fn=lambda: base, ctx_fn=agents_ctx(base), auth=FakeAuth(), git=FakeGit(),
        host=FakeHost(""), run_user=None, github_on=on, invites_from=invites_from,
        gh=gh or FakeGithub(), jobs=jobs or FakeJobs(), projects_run=projects_run),
        follow_redirects=False)


def cloned(tmp_path, name="alpha"):
    src = tmp_path / "src"
    git("init", "-q", "-b", "main", str(src))
    git("commit", "-q", "--allow-empty", "-m", "init", cwd=src)
    git("branch", "feature", cwd=src)
    dest = base_of(tmp_path) / name
    git("clone", "-q", "-o", "github", str(src), str(dest))
    return dest


# --- off by default ----------------------------------------------------------

def test_off_means_no_links_and_no_pages(tmp_path):
    c = client(tmp_path, on=False)
    html = c.get("/").text
    assert "/clone" not in html and "/new-project" not in html
    assert c.get("/clone").status_code == 404
    assert c.get("/clone/alpha").status_code == 404
    assert c.post("/clone", data={"repo": "Hooace/alpha"}).status_code == 404
    assert c.post("/clone/alpha/branch", data={"branch": "x"}).status_code == 404
    assert c.get("/new-project").status_code == 404
    assert c.post("/new-project", data={"name": "x"}).status_code == 404
    assert not (base_of(tmp_path) / "x").exists()


def test_on_links_both_pages_from_the_header(tmp_path):
    html = client(tmp_path).get("/").text
    assert 'href="/clone"' in html and 'href="/new-project"' in html


# --- the list ----------------------------------------------------------------

def test_list_shows_repos_escaped_with_owner_and_date(tmp_path):
    html = client(tmp_path).get("/clone").text
    assert "Hooace/alpha" in html and "bertborgren/beta" in html and "2026-10-20" in html
    assert "<b>repo</b>" not in html and "&lt;b&gt;repo&lt;/b&gt;" in html
    assert 'name="repo" value="Hooace/alpha"' in html


def test_list_searches(tmp_path):
    html = client(tmp_path).get("/clone", params={"q": "alpha"}).text
    assert "Hooace/alpha" in html and "bertborgren/beta" not in html


def test_a_repo_already_here_links_to_its_branch_picker(tmp_path):
    cloned(tmp_path)
    html = client(tmp_path).get("/clone").text
    assert 'href="/clone/alpha">Cloned' in html
    assert 'value="Hooace/alpha"' not in html


def test_a_github_failure_stays_on_the_page(tmp_path):
    html = client(tmp_path, gh=FakeGithub(error=GithubError("GitHub: HTTP 401"))).get("/clone")
    assert html.status_code == 200 and "GitHub: HTTP 401" in html.text


def test_empty_list_says_so(tmp_path):
    assert "No repositories found." in client(tmp_path, gh=FakeGithub(repos=())).get("/clone").text


# --- invitations -------------------------------------------------------------

def test_opening_the_list_accepts_invitations_from_the_configured_owner(tmp_path):
    gh = FakeGithub(accepted=["Hooace/new"])
    html = client(tmp_path, gh=gh, invites_from="Hooace").get("/clone").text
    assert gh.invites_from == ["Hooace"] and "Hooace/new" in html


def test_without_an_owner_no_invitation_is_touched(tmp_path):
    gh = FakeGithub(accepted=["Hooace/new"])
    client(tmp_path, gh=gh).get("/clone")
    assert gh.invites_from == []


def test_a_failed_acceptance_does_not_cost_the_list(tmp_path):
    class Refusing(FakeGithub):
        def accept_invitations(self, owner):
            raise GithubError("GitHub: nope")
    html = client(tmp_path, gh=Refusing(), invites_from="Hooace").get("/clone").text
    assert "Hooace/alpha" in html and "GitHub: nope" in html


# --- cloning -----------------------------------------------------------------

def test_clone_starts_and_goes_to_its_status_page(tmp_path):
    jobs = FakeJobs()
    c = client(tmp_path, jobs=jobs)
    r = c.post("/clone", data={"repo": "Hooace/alpha"})
    assert r.status_code == 303 and r.headers["location"] == "/clone/alpha"
    assert jobs.started == ["alpha"]
    page = c.get("/clone/alpha").text
    assert "Cloning" in page and 'http-equiv="refresh"' in page


def test_only_a_repo_on_the_list_can_be_cloned(tmp_path):
    jobs = FakeJobs()
    r = client(tmp_path, jobs=jobs).post("/clone", data={"repo": "peanuts-group/client-pdg-api"})
    assert r.status_code == 200 and "No such repository" in r.text and jobs.started == []


def test_clone_from_another_site_is_refused(tmp_path):
    jobs = FakeJobs()
    r = client(tmp_path, jobs=jobs).post("/clone", data={"repo": "Hooace/alpha"},
                                         headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and jobs.started == []


def test_failed_clone_shows_why(tmp_path):
    jobs = FakeJobs()
    jobs.states["alpha"] = CloneStatus("failed", "Clone failed: <nope>")
    html = client(tmp_path, jobs=jobs).get("/clone/alpha").text
    assert "&lt;nope&gt;" in html and 'href="/clone"' in html


def test_unknown_clone_and_bad_name(tmp_path):
    c = client(tmp_path)
    assert "is in progress" in c.get("/clone/nothing").text
    assert "is in progress" in c.get("/clone/.hidden").text


# --- the branch --------------------------------------------------------------

def test_done_page_offers_the_branches(tmp_path):
    cloned(tmp_path)
    jobs = FakeJobs()
    jobs.states["alpha"] = CloneStatus("done", None)
    html = client(tmp_path, jobs=jobs).get("/clone/alpha").text
    assert '<option value="feature">' in html and '<option value="main" selected>' in html


def test_a_clone_found_after_a_restart_still_offers_the_branches(tmp_path):
    cloned(tmp_path)
    assert '<option value="feature">' in client(tmp_path).get("/clone/alpha").text


def test_switching_branch_goes_home_with_a_note(tmp_path):
    dest = cloned(tmp_path)
    c = client(tmp_path)
    r = c.post("/clone/alpha/branch", data={"branch": "feature", "new": ""})
    assert r.status_code == 303 and git("branch", "--show-current", cwd=dest) == "feature"
    assert "On branch feature" in c.get("/").text


def test_new_branch(tmp_path):
    dest = cloned(tmp_path)
    client(tmp_path).post("/clone/alpha/branch", data={"branch": "mine", "new": "1"})
    assert git("branch", "--show-current", cwd=dest) == "mine"


def test_bad_branch_stays_on_the_page(tmp_path):
    dest = cloned(tmp_path)
    r = client(tmp_path).post("/clone/alpha/branch", data={"branch": "nope", "new": ""})
    assert r.status_code == 200 and "No such branch" in r.text
    assert git("branch", "--show-current", cwd=dest) == "main"


def test_branch_of_a_folder_that_is_not_a_repo_changes_nothing(tmp_path):
    (base_of(tmp_path) / "plain").mkdir()
    r = client(tmp_path).post("/clone/plain/branch", data={"branch": "x", "new": "1"})
    assert r.status_code == 200 and "is in progress" in r.text


# --- new project -------------------------------------------------------------

def test_new_project_page_offers_the_repo(tmp_path):
    html = client(tmp_path).get("/new-project").text
    assert 'name="github"' in html and "private GitHub repo" in html


def test_new_project_with_repo(tmp_path):
    gh = FakeGithub()
    r = client(tmp_path, gh=gh).post("/new-project", data={"name": "kokeilu", "github": "1"})
    path = base_of(tmp_path) / "kokeilu"
    assert r.status_code == 303 and (path / ".git").is_dir()
    assert gh.created == [("kokeilu", path)]


def test_new_project_has_a_first_commit_to_branch_from(tmp_path):
    client(tmp_path).post("/new-project", data={"name": "kokeilu"})
    assert git("log", "--format=%s", cwd=base_of(tmp_path) / "kokeilu") == "Initial commit"


def test_without_a_first_commit_no_repo_is_made_and_the_card_says_why(tmp_path):
    def no_identity(cmd, **kw):
        if cmd[:2] == ["git", "commit"]:
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="who are you")
        return with_identity(cmd, **kw)
    gh = FakeGithub()
    c = client(tmp_path, gh=gh, projects_run=no_identity)
    c.post("/new-project", data={"name": "kokeilu", "github": "1"})
    assert gh.created == [] and "who are you" in c.get("/").text


def test_new_project_without_repo_stays_local(tmp_path):
    gh = FakeGithub()
    client(tmp_path, gh=gh).post("/new-project", data={"name": "kokeilu"})
    assert (base_of(tmp_path) / "kokeilu" / ".git").is_dir() and gh.created == []


def test_a_failed_repo_keeps_the_project_and_says_why_on_its_card(tmp_path):
    c = client(tmp_path, gh=FakeGithub(error=GithubError("GitHub: name taken")))
    c.post("/new-project", data={"name": "kokeilu", "github": "1"})
    assert (base_of(tmp_path) / "kokeilu" / ".git").is_dir()
    assert "GitHub: name taken" in c.get("/").text


@pytest.mark.parametrize("name,message", [("../evil", "Invalid"), ("taken", "already exists")])
def test_new_project_refusals_stay_on_the_page(tmp_path, name, message):
    make_repo(base_of(tmp_path) / "taken")
    gh = FakeGithub()
    r = client(tmp_path, gh=gh).post("/new-project", data={"name": name, "github": "1"})
    assert r.status_code == 200 and message in r.text and gh.created == []
