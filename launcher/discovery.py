import re
from dataclasses import dataclass
from pathlib import Path

from .worktrees import path as worktree_path


@dataclass(frozen=True)
class Project:
    slug: str
    name: str
    path: Path
    launch_path: Path | None = None

    @property
    def cwd(self) -> Path:
        return self.launch_path or self.path


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


def discover_projects(base_dir: Path) -> list[Project]:
    if not base_dir.is_dir():
        return []
    children = [
        child
        for child in sorted(base_dir.iterdir(), key=lambda p: p.name.lower())
        if child.is_dir()
    ]
    projects: list[Project] = []
    counts: dict[str, int] = {}
    for child in children:
        if (child / ".git").exists():
            launch_path = None
        elif (child / "claude-project").is_dir():
            launch_path = child / "claude-project"
        else:
            continue
        base_slug = slugify(child.name)
        n = counts.get(base_slug, 0)
        slug = base_slug if n == 0 else f"{base_slug}-{n + 1}"
        counts[base_slug] = n + 1
        projects.append(Project(slug=slug, name=child.name, path=child, launch_path=launch_path))
    return projects


def instance_of(project: Project, name: str) -> Project:
    """The `Project` for one instance of `project`.

    The single construction site, so `resolve_slug` and a caller that already
    holds the parent (the page's `snapshot()`, which just walked the tree once
    to find it) build the exact same thing rather than two copies that could
    drift apart.
    """
    return Project(slug=f"{project.slug}--{name}", name=name,
                   path=project.path, launch_path=worktree_path(project.cwd, name))


def resolve_slug(base_dir: Path, slug: str, instances: dict | None = None) -> Project | None:
    """The project — or instance — a slug names.

    Instances come from what the launcher recorded, never from walking the
    tree: a worktree the Claude app created for one of its own sessions must
    not turn into a row.
    """
    for project in discover_projects(base_dir):
        if project.slug == slug:
            return project
    if not instances:
        return None
    # Not `instances.split`: that module imports slugify from here.
    project_slug, sep, name = slug.partition("--")
    if not sep or not project_slug or not name:
        return None
    if name not in instances.get(project_slug, []):
        return None
    parent = resolve_slug(base_dir, project_slug)
    if parent is None:
        return None
    return instance_of(parent, name)
