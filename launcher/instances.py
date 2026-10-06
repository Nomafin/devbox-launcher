"""What an instance of a project is called.

An instance is an ordinary listener in a git worktree of its own, so it needs
a slug that cannot be confused with a project's. `--` is safe because
`slugify` collapses every run of non-alphanumerics to a single `-`: no folder
under ~/projects can ever produce one.
"""

from .discovery import slugify

SEP = "--"


def slug(project_slug: str, name: str) -> str:
    return f"{project_slug}{SEP}{name}"


def split(slug: str) -> tuple[str, str] | None:
    """(project slug, instance name), or None for a plain project slug."""
    project, sep, name = slug.partition(SEP)
    if not sep or not project or not name:
        return None
    return project, name


def valid(name: str) -> bool:
    """A name usable unchanged as a slug, a branch and a directory at once."""
    return bool(name) and slugify(name) == name
