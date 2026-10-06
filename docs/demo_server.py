#!/usr/bin/env python3
"""Serve the launcher UI against fabricated data, for documentation screenshots.

The page this serves is rendered by the real `launcher.ui` and `launcher.app`
code — only the boundaries that would touch tmux, git, the Claude CLI and
/proc are stubbed. That keeps the screenshots in README.md honest about what
the UI looks like without requiring a live box, a Claude login, or exposing
whatever projects happen to be on the machine taking the picture.

    python docs/demo_server.py [--port 8799] [--signed-out]

Then screenshot http://127.0.0.1:8799/ at a phone viewport.
"""
import argparse
from pathlib import Path

import uvicorn

from launcher import discovery
from launcher.app import create_app
from launcher.discovery import Project
from launcher.runctl import RunStatus
from launcher.tmuxctl import CONNECTED, FAILED, READY, STOPPED, Status

BASE = Path.home() / "projects"

# One project per state worth showing, in the order the real page sorts them:
# running first, alphabetical within each group.
PROJECTS = [
    Project(slug="acme", name="Acme", path=BASE / "Acme",
            launch_path=BASE / "Acme" / "claude-project"),
    Project(slug="blog", name="blog", path=BASE / "blog"),
    Project(slug="demo", name="demo", path=BASE / "demo"),
    Project(slug="webapp", name="webapp", path=BASE / "webapp"),
    Project(slug="notes", name="notes", path=BASE / "notes"),
    Project(slug="sandbox", name="sandbox", path=BASE / "sandbox"),
]

STATUSES = {
    # A group project running several sessions in worktree mode.
    "acme": Status(running=True, state=CONNECTED, age=10_800, sessions=3,
                   url="https://claude.ai/code?environment=env_01Rb8TnKqW3xMzJ5vHd7Ls2Y"),
    # An instance of acme: its own worktree and branch, one live session.
    "acme--spike": Status(running=True, state=CONNECTED, age=1_500, sessions=1,
                          url="https://claude.ai/code?environment=env_01Tz6KpLmQr8WvXn3Hd4Jc9F"),
    # A listener you left up over a week ago — the age is the point.
    "blog": Status(running=True, state=CONNECTED, age=777_600, sessions=1,
                   url="https://claude.ai/code?environment=env_01Qw4NmPvXs2LtRk9Bd6Hy3C"),
    # Registered and waiting for the app: a resting state, not a fault.
    "demo": Status(running=True, state=READY, age=7_200),
    # The failure everyone hits first, carrying the CLI's own words.
    "webapp": Status(running=True, state=FAILED, age=45,
                     reason="Error: You must be logged in to use Remote Control."),
    "notes": Status(running=False, state=STOPPED),
    "sandbox": Status(running=False, state=STOPPED),
}

PANE_TAIL = [
    "·✔︎· Connected · Acme · main",
    "    Capacity: 3/32 · New sessions will be created in a git worktree",
    "    devbox-acme",
    "Continue coding in the Claude mobile app or https://claude.ai/code",
]


class FakeTmux:
    def snapshot(self, slugs):
        return {slug: STATUSES[slug] for slug in slugs}

    def pane_tail(self, slug, lines=20):
        return list(PANE_TAIL)

    def is_running(self, slug):
        return STATUSES[slug].running


class FakeState:
    def parallel(self):
        return ["acme"]          # drives the "Parallel" badge on the acme row

    def desired(self):
        return []                # nothing to restore: keeps boot reconcile quiet

    def instances(self):
        return {"acme": ["spike"]}   # one instance row, indented under acme

    def permission_modes(self):
        # Everything else shows the default chip ("Ask").
        return {"acme": "auto", "acme--spike": "acceptEdits", "demo": "plan"}


class FakeRunner:
    """Run command: one finished command on blog, nothing elsewhere."""
    RUNS = {"blog": RunStatus(exists=True, running=False, exit_code=0)}

    def statuses(self):
        return dict(self.RUNS)

    def status(self, slug):
        return self.RUNS.get(slug, RunStatus(False, False, None))

    def output(self, slug):
        return ("From github.com:you/blog\n * branch            main       -> FETCH_HEAD\n"
                "Already up to date.\n")

    def last(self, slug):
        return {"command": "git pull github main", "cwd": str(BASE / "blog")}

    def start(self, slug, command, cwd):
        pass

    def send_input(self, slug, text):
        pass

    def interrupt(self, slug):
        pass

    def close(self, slug):
        pass


class FakePointer:
    def session_id(self, cwd, home=None):
        # Only `notes` has a recorded conversation, so only it offers
        # "Start fresh — new conversation".
        return "session_01Xk7PqMwTn4RsLv9Bd2Hy6C" if cwd.name == "notes" else None


class FakeGit:
    def blocks_remote_control(self, repo):
        return repo.name == "sandbox"     # shows the origin warning + its button


class FakeHost:
    def memory(self):
        return "1.2 of 12 GB"

    def disk(self):
        return "24.2 of 39.4 GB"


class FakeAuth:
    def __init__(self, signed_in=True):
        self.signed_in = signed_in

    def status(self):
        return self.signed_in

    def is_active(self):
        return False

    def errors(self):
        return []


class FakeDiscovery:
    def discover_projects(self, base_dir):
        return list(PROJECTS)

    # Pure functions of their inputs; the real ones are what the page uses.
    instance_of = staticmethod(discovery.instance_of)

    def resolve_slug(self, base_dir, slug, instances=None):
        for project in PROJECTS:
            if project.slug == slug:
                return project
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--signed-out", action="store_true",
                        help="render the signed-out banner instead of the project list state")
    args = parser.parse_args()

    app = create_app(
        base_dir_fn=lambda: BASE,
        tmx=FakeTmux(), disc=FakeDiscovery(), st=FakeState(), ptr=FakePointer(),
        git=FakeGit(), host=FakeHost(), auth=FakeAuth(signed_in=not args.signed_out),
        # Run command is shown when a run user is configured, as on a real box
        # that has opted in; the stub never runs anything.
        runner=FakeRunner(), run_user="you@example.com",
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
