"""Which project a directory, or a slug, belongs to.

A project is what the launcher lists under ~/projects (a repo, or a group's
claude-project/), plus one thing it does not list: a git repo nested inside a
project (Notes/site), which the terminal side treats as a project of its
own, `<parent>-<path>`. Those exist only once an agent has been made for one,
so `by_slug` also looks in the registry.
"""

from dataclasses import dataclass
from pathlib import Path

from .. import discovery, worktrees
from .model import MAIN, AgentError

_WORKTREES = "/.claude/worktrees/"


@dataclass(frozen=True)
class Place:
    slug: str
    name: str
    cwd: Path


def discovered(base_dir) -> list[Place]:
    return [Place(p.slug, p.name, p.cwd) for p in discovery.discover_projects(Path(base_dir))]


def _toplevel(d: Path, git) -> Path | None:
    result = git(["git", "-C", str(d), "rev-parse", "--show-toplevel"])
    out = (result.stdout or "").strip()
    if result.returncode != 0 or not out:
        return None
    # A worktree's toplevel is the worktree; the repo is what it belongs to.
    if _WORKTREES in out + "/":
        out = out.split(_WORKTREES)[0]
    return Path(out)


def for_dir(base_dir, d, git=worktrees.default_run) -> Place:
    d = Path(d).resolve()
    for project in discovery.discover_projects(Path(base_dir)):
        root = project.path.resolve()
        if d != root and root not in d.parents:
            continue
        top = _toplevel(d, git)
        if (top is None or top == root or top == project.cwd.resolve()
                or root not in top.parents):
            return Place(project.slug, project.name, project.cwd)
        rel = top.relative_to(root)
        return Place(f"{project.slug}-{discovery.slugify(str(rel))}", f"{project.name}/{rel}", top)
    raise AgentError(AgentError.INVALID, f"{d} is not inside a project under {base_dir}")


def by_slug(base_dir, slug: str, agents: dict) -> Place | None:
    for place in discovered(base_dir):
        if place.slug == slug:
            return place
    for agent in agents.values():
        if agent.project == slug:
            cwd = Path(agent.cwd)
            return Place(slug, cwd.name, cwd)
    return None


def agent_name(place: Place, name: str | None) -> str:
    """The agent a typed name means. Typing the project's own name opens
    main: asking for `notes` inside notes is how people ask for "the" session."""
    if not name:
        return MAIN
    aliases = {place.slug, discovery.slugify(Path(place.name).name)}
    return MAIN if name in aliases else name
