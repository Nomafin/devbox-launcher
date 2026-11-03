"""GitHub for the Clone and New project pages, through the `gh` CLI.

`gh` already holds the box's login, so the launcher stores no token of its own
and can see exactly what that account can see. Which repos that is, is decided
on GitHub: a repo not shared with the account is not on the list, and an agent
on this box cannot reach it either.

Every failure is a GithubError whose message is fit to show on the page.
"""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

Runner = Callable[[list[str]], subprocess.CompletedProcess]

# The remote a repo from here gets. Not `origin`: see gitremote.py.
REMOTE = "github"

_REPOS = ("user/repos?per_page=100&sort=pushed"
          "&affiliation=owner,collaborator,organization_member")
_REPO_FIELDS = ".[] | select(.archived | not) | {name, full_name, description, pushed_at, clone_url}"
_INVITATIONS = "user/repository_invitations"
_INVITATION_FIELDS = ".[] | [.id, .inviter.login, .repository.full_name] | @tsv"


class GithubError(Exception):
    """Something went wrong; the message is fit to show on the page."""


@dataclass(frozen=True)
class Repo:
    slug: str           # the name alone, which is also its directory here
    full_name: str      # owner/name
    description: str
    updated_on: str
    clone_url: str


def default_run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=60)
    except subprocess.TimeoutExpired:
        # Reads as a failed command, which every caller already handles.
        return subprocess.CompletedProcess(cmd, 124, stdout="", stderr="gh timed out")


class Client:
    def __init__(self, run: Runner = default_run):
        self._run = run

    def _gh(self, *args: str) -> str:
        try:
            result = self._run(["gh", *args])
        except OSError as exc:
            raise GithubError(f"The GitHub CLI (gh) could not run: {exc}") from exc
        if result.returncode != 0:
            lines = [line.strip() for line in (result.stderr or "").splitlines() if line.strip()]
            raise GithubError(f"GitHub: {lines[-1]}" if lines else "GitHub did not answer.")
        return result.stdout or ""

    def repos(self, query: str = "") -> list[Repo]:
        """What this box's account can reach, most recently pushed first."""
        found = []
        for line in self._gh("api", "--paginate", _REPOS, "--jq", _REPO_FIELDS).splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                found.append(Repo(slug=r["name"], full_name=r["full_name"],
                                  description=r.get("description") or "",
                                  updated_on=r.get("pushed_at") or "", clone_url=r["clone_url"]))
            except (ValueError, KeyError, TypeError) as exc:
                raise GithubError("GitHub's answer could not be read.") from exc
        needle = query.strip().lower()
        return [r for r in found
                if needle in r.full_name.lower() or needle in r.description.lower()]

    def accept_invitations(self, owner: str) -> list[str]:
        """Accept what `owner` invited this account to in `owner`'s own repos,
        and nothing else: an invitation into an organization's repo stays open
        whoever sent it. Returns the repos accepted."""
        wanted = owner.casefold()
        accepted = []
        for line in self._gh("api", "--paginate", _INVITATIONS,
                             "--jq", _INVITATION_FIELDS).splitlines():
            fields = line.split("\t")
            if len(fields) != 3:
                continue
            invitation, inviter, repo = fields
            if inviter.casefold() != wanted or repo.split("/")[0].casefold() != wanted:
                continue
            self._gh("api", "-X", "PATCH", f"{_INVITATIONS}/{invitation}", "--silent")
            accepted.append(repo)
        return accepted

    def create_repo(self, name: str, path: Path) -> None:
        """A private repo on this box's account, as the `github` remote of `path`."""
        self._gh("repo", "create", name, "--private", "--source", str(path), "--remote", REMOTE)
