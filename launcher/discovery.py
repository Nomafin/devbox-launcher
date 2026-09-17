import re
from dataclasses import dataclass
from pathlib import Path


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


def resolve_slug(base_dir: Path, slug: str) -> Project | None:
    for project in discover_projects(base_dir):
        if project.slug == slug:
            return project
    return None
