import pytest

from launcher.agents import places
from launcher.agents.model import MAIN, AgentError

from .conftest import make_repo


def test_repo_root_is_the_project(base):
    make_repo(base / "quiz")
    p = places.for_dir(base, base / "quiz")
    assert (p.slug, p.cwd) == ("quiz", base / "quiz")


def test_subdirectory_of_repo_is_the_project(base):
    make_repo(base / "wiki")
    (base / "wiki" / "src").mkdir()
    assert places.for_dir(base, base / "wiki" / "src").slug == "wiki"


def test_group_root_launches_in_claude_project(base):
    (base / "Notes" / "claude-project").mkdir(parents=True)
    p = places.for_dir(base, base / "Notes" / "claude-project")
    assert (p.slug, p.cwd) == ("notes", base / "Notes" / "claude-project")


def test_nested_repo_in_group_is_its_own_project(base):
    (base / "Notes" / "claude-project").mkdir(parents=True)
    make_repo(base / "Notes" / "site")
    p = places.for_dir(base, base / "Notes" / "site")
    assert (p.slug, p.name, p.cwd) == ("notes-site", "Notes/site", base / "Notes" / "site")


def test_worktree_dir_maps_to_its_repo(base):
    repo = make_repo(base / "wiki")
    wt = repo / ".claude" / "worktrees" / "live"
    wt.mkdir(parents=True)
    assert places.for_dir(base, wt).slug == "wiki"


def test_outside_any_project(base, tmp_path):
    base.mkdir()
    with pytest.raises(AgentError) as e:
        places.for_dir(base, tmp_path)
    assert e.value.code == AgentError.INVALID


def test_project_name_means_main(base):
    (base / "Notes" / "claude-project").mkdir(parents=True)
    make_repo(base / "Notes" / "site")
    make_repo(base / "quiz")
    quiz = places.for_dir(base, base / "quiz")
    site = places.for_dir(base, base / "Notes" / "site")
    assert places.agent_name(quiz, "quiz") == MAIN
    assert places.agent_name(quiz, None) == MAIN
    assert places.agent_name(site, "site") == MAIN
    assert places.agent_name(site, "notes-site") == MAIN
    assert places.agent_name(site, "drafts") == "drafts"


def test_by_slug_finds_registry_only_projects(base):
    (base / "Notes" / "claude-project").mkdir(parents=True)
    from launcher.agents.model import Agent
    agents = {"notes-site/drafts": Agent(project="notes-site", name="drafts",
                                          cwd=str(base / "Notes" / "site"), workdir="x")}
    assert places.by_slug(base, "notes", agents).cwd == base / "Notes" / "claude-project"
    assert places.by_slug(base, "notes-site", agents).cwd == base / "Notes" / "site"
    assert places.by_slug(base, "nope", agents) is None
