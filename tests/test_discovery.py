from pathlib import Path
from launcher.discovery import Project, slugify, discover_projects, resolve_slug, instance_of


def _mkrepo(base: Path, name: str) -> None:
    (base / name / ".git").mkdir(parents=True)


def test_slugify_lowercases_and_dashes():
    assert slugify("My Cool_Repo") == "my-cool-repo"
    assert slugify("demo-f1226") == "demo-f1226"
    assert slugify("!!!") == "project"


def test_discovers_only_git_repos_sorted(tmp_path):
    _mkrepo(tmp_path, "beta")
    _mkrepo(tmp_path, "Alpha")
    (tmp_path / "not-a-repo").mkdir()
    (tmp_path / "file.txt").write_text("x")
    projects = discover_projects(tmp_path)
    assert [p.name for p in projects] == ["Alpha", "beta"]
    assert [p.slug for p in projects] == ["alpha", "beta"]


def test_slug_collisions_are_disambiguated(tmp_path):
    _mkrepo(tmp_path, "My Repo")
    _mkrepo(tmp_path, "my-repo")
    projects = discover_projects(tmp_path)
    assert sorted(p.slug for p in projects) == ["my-repo", "my-repo-2"]


def test_missing_base_dir_returns_empty(tmp_path):
    assert discover_projects(tmp_path / "nope") == []


def test_resolve_slug_roundtrips(tmp_path):
    _mkrepo(tmp_path, "homelab-project")
    p = resolve_slug(tmp_path, "homelab-project")
    assert isinstance(p, Project) and p.name == "homelab-project"
    assert resolve_slug(tmp_path, "ghost") is None


def test_group_folder_with_claude_project_is_discovered(tmp_path):
    (tmp_path / "Acme" / "claude-project" / ".git").mkdir(parents=True)
    projects = discover_projects(tmp_path)
    assert len(projects) == 1
    p = projects[0]
    assert p.name == "Acme"
    assert p.slug == "acme"
    assert p.path == tmp_path / "Acme"
    assert p.cwd == tmp_path / "Acme" / "claude-project"


def test_group_claude_project_need_not_be_a_repo(tmp_path):
    (tmp_path / "Acme" / "claude-project").mkdir(parents=True)
    projects = discover_projects(tmp_path)
    assert len(projects) == 1
    assert projects[0].cwd == tmp_path / "Acme" / "claude-project"


def test_plain_repo_cwd_equals_path(tmp_path):
    _mkrepo(tmp_path, "homelab")
    projects = discover_projects(tmp_path)
    assert len(projects) == 1
    p = projects[0]
    assert p.cwd == p.path == tmp_path / "homelab"


def test_repo_with_claude_project_subdir_is_treated_as_plain_repo(tmp_path):
    _mkrepo(tmp_path, "Umb")
    (tmp_path / "Umb" / "claude-project" / ".git").mkdir(parents=True)
    projects = discover_projects(tmp_path)
    assert len(projects) == 1
    p = projects[0]
    assert p.name == "Umb"
    assert p.cwd == tmp_path / "Umb"


def test_folder_that_is_neither_repo_nor_group_is_skipped(tmp_path):
    (tmp_path / "random").mkdir()
    assert discover_projects(tmp_path) == []


def test_resolve_slug_finds_a_recorded_instance(tmp_path):
    _mkrepo(tmp_path, "homelab")
    project = resolve_slug(tmp_path, "homelab--fix-auth", {"homelab": ["fix-auth"]})
    assert project is not None
    assert project.slug == "homelab--fix-auth"
    assert project.name == "fix-auth"          # the row is labelled with the instance
    assert project.path == tmp_path / "homelab"
    assert project.cwd == tmp_path / "homelab" / ".claude" / "worktrees" / "fix-auth"


def test_resolve_slug_ignores_an_unrecorded_instance(tmp_path):
    # a worktree the Claude app made for its own session is not an instance
    _mkrepo(tmp_path, "homelab")
    assert resolve_slug(tmp_path, "homelab--stray", {"homelab": ["fix-auth"]}) is None
    assert resolve_slug(tmp_path, "homelab--fix-auth") is None      # nothing recorded


def test_resolve_slug_of_an_instance_whose_project_is_gone(tmp_path):
    assert resolve_slug(tmp_path, "ghost--wip", {"ghost": ["wip"]}) is None


def test_resolve_slug_of_a_group_instance_hangs_off_the_launch_dir(tmp_path):
    # a group launches in claude-project/, so its instances live under that
    (tmp_path / "Acme" / "claude-project").mkdir(parents=True)
    project = resolve_slug(tmp_path, "acme--wip", {"acme": ["wip"]})
    assert project.cwd == (tmp_path / "Acme" / "claude-project"
                           / ".claude" / "worktrees" / "wip")


def test_resolve_slug_still_finds_plain_projects(tmp_path):
    _mkrepo(tmp_path, "homelab")
    assert resolve_slug(tmp_path, "homelab").slug == "homelab"
    assert resolve_slug(tmp_path, "nope") is None


def test_instance_of_matches_what_resolve_slug_builds(tmp_path):
    # One construction site: a caller already holding the parent (snapshot())
    # and one resolving from a bare slug (resolve_slug) must never drift apart.
    _mkrepo(tmp_path, "homelab")
    parent = resolve_slug(tmp_path, "homelab")
    assert instance_of(parent, "fix-auth") == resolve_slug(
        tmp_path, "homelab--fix-auth", {"homelab": ["fix-auth"]})
