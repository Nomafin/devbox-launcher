#!/usr/bin/env python3
"""Serve the launcher UI against fabricated data, for documentation screenshots.

The page this serves is rendered by the real `launcher.ui` and `launcher.app`
code, and the agents on it are made by the real `launcher.agents.service`:
only tmux, the Claude CLI's login check, Run command and /proc are stubbed.
The projects are throwaway git repos in a temporary HOME, so the screenshots
in README.md stay honest about what the UI looks like without a live box, a
Claude login, or exposing whatever projects are on the machine taking them.

    python docs/demo_server.py [--port 8799] [--signed-out]

Then screenshot http://127.0.0.1:8799/ at a phone viewport.
"""
import argparse
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

# Path.home() reads $HOME, and the page shows paths relative to it, so the
# fabricated world has to be the home directory before anything asks.
WORLD = Path(tempfile.mkdtemp(prefix="devbox-launcher-demo-"))
os.environ["HOME"] = str(WORLD)

import uvicorn  # noqa: E402

from launcher.agents import briefs, registry, runtime, service  # noqa: E402
from launcher.agents.model import AgentError  # noqa: E402
from launcher.app import create_app  # noqa: E402
from launcher.runctl import RunStatus  # noqa: E402

BASE = WORLD / "projects"
CLOCK = {"t": time.time() - 10 * 86400}


def connected_pane(name: str, sid: str) -> str:
    return (f"╭─ Claude Code ─ {name}\n"
            f"{runtime.CONNECTED_SAMPLE} https://claude.ai/code/{sid}\n"
            "> \n")


class FakeTmux:
    """The agents socket as a dict, fed the same argv `runtime` builds."""

    def __init__(self):
        self.sessions = {}
        self.next_pane = None       # pane text for the next session to start
        self.next_dead = None       # exit status for the next session to start

    @staticmethod
    def _ok(cmd, out=""):
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    @staticmethod
    def _name(target):
        return target.lstrip("=").rstrip(":")

    def __call__(self, cmd, timeout=None):
        args = cmd[6:]                          # after tmux -u -L agents -f <conf>
        if "new-session" in args:
            rest = args[args.index("new-session") + 1:]
            name = rest[rest.index("-s") + 1]
            sid = shlex.split(rest[-1])[-1] if rest[-1] else "session"
            for flag in ("--resume", "--session-id"):
                parts = shlex.split(rest[-1])
                if flag in parts:
                    sid = parts[parts.index(flag) + 1]
            dead = self.next_dead is not None
            self.sessions[name] = dict(
                created=int(CLOCK["t"]), activity=int(CLOCK["t"]), dead=dead,
                status=self.next_dead,
                pane=self.next_pane or connected_pane(name, f"session_{sid[-12:]}"))
            self.next_pane = self.next_dead = None
            return self._ok(cmd)
        verb = args[0]
        if verb == "list-sessions":
            if not self.sessions:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no server running")
            return self._ok(cmd, "".join(
                f"{n}\t{s['created']}\t{s['activity']}\t0\t{s['activity']}\n"
                for n, s in self.sessions.items()))
        if verb == "list-panes":
            return self._ok(cmd, "".join(
                f"{n}\t{1 if s['dead'] else 0}\t{s['status'] if s['dead'] else ''}\n"
                for n, s in self.sessions.items()))
        if verb == "kill-session":
            self.sessions.pop(self._name(args[args.index("-t") + 1]), None)
            return self._ok(cmd)
        if verb == "capture-pane":
            s = self.sessions.get(self._name(args[args.index("-t") + 1]))
            return self._ok(cmd, s["pane"] if s else "")
        return self._ok(cmd)                    # send-keys and the like


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git = ["git", "-C", str(path), "-c", "user.email=demo@example.com", "-c", "user.name=demo"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "README.md").write_text(f"# {path.name}\n")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    return path


def set_mission(ctx, key: str, mission: str) -> None:
    agent = registry.load(ctx.root)[key]
    p = briefs.ensure(ctx.root, agent)
    p.write_text(f"# {agent.display}\n\n## Mission\n\n{mission}\n\n## State\n\n"
                 "## Next steps\n\n## Gotchas\n\n")


def build_world(tmux: FakeTmux) -> service.Ctx:
    """One project per state worth showing."""
    make_repo(BASE / "Acme" / "claude-project")       # a group: its agents run in claude-project/
    for name in ("blog", "demo", "notes", "webapp"):
        make_repo(BASE / name)
    ctx = service.Ctx(root=WORLD / ".config" / "agents", base_dir=BASE, home=WORLD,
                      run=tmux, now=lambda: CLOCK["t"], sleep=lambda s: None,
                      trust=lambda p: None)

    def place(slug):
        return service.place_for(ctx, key=f"{slug}/main")[0]

    def at(seconds_ago):
        CLOCK["t"] = NOW - seconds_ago

    # notes: a stopped main with a conversation to resume, and a parked agent.
    at(9 * 86400)
    service.new(ctx, place("notes"), "drafts")
    set_mission(ctx, "notes/drafts", "Turn the meeting notes into a weekly digest")
    service.start(ctx, place("notes"), "drafts")
    service.start(ctx, place("notes"), "main")
    at(4 * 86400)
    service.park(ctx, place("notes"), "drafts")
    service.stop(ctx, place("notes"), "main")

    # blog: an agent you left up over a week ago; the age is the point.
    at(8 * 86400)
    service.start(ctx, place("blog"), "main")
    set_mission(ctx, "blog/main", "Keep the blog building and the drafts moving")

    # Acme: main, and a named agent in its own worktree in Accept edits mode.
    at(3 * 3600)
    service.start(ctx, place("acme"), "main")
    service.new(ctx, place("acme"), "billing", mode="acceptEdits")
    set_mission(ctx, "acme/billing", "Move invoicing to the new payments API behind a flag")
    at(25 * 60)
    service.start(ctx, place("acme"), "billing")

    # webapp: the failure everyone hits first, carrying the CLI's own words,
    # and an agent whose Claude crashed after it had been running.
    at(2 * 3600)
    service.new(ctx, place("webapp"), "search")
    set_mission(ctx, "webapp/search", "Replace the search box with full-text search")
    service.start(ctx, place("webapp"), "search")
    tmux.sessions["webapp/search"].update(
        dead=True, status=1,
        pane=tmux.sessions["webapp/search"]["pane"] + "\nError: ENOSPC: no space left on device\n")
    at(60)
    tmux.next_pane = "Error: You must be logged in to use Remote Control.\n"
    tmux.next_dead = 1
    try:
        service.start(ctx, place("webapp"), "main")
    except AgentError:
        pass

    # demo: a Quick chat, still coming up.
    at(10)
    service.scratch(ctx, place("demo"))
    for s in tmux.sessions.values():
        if s["created"] == int(CLOCK["t"]):
            s["pane"] = "Starting…\n"
    at(0)
    return ctx


NOW = CLOCK["t"] + 10 * 86400


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
        return {"command": "git pull origin main", "cwd": str(BASE / "blog")}

    def start(self, slug, command, cwd):
        pass

    def send_input(self, slug, text):
        pass

    def interrupt(self, slug):
        pass

    def close(self, slug):
        pass


class FakeGit:
    def blocks_remote_control(self, repo):
        return False

    def rename_origin(self, repo):
        return None


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--signed-out", action="store_true",
                        help="render the signed-out banner over the agent list")
    args = parser.parse_args()

    ctx = build_world(FakeTmux())
    app = create_app(
        base_dir_fn=lambda: BASE, ctx_fn=lambda: ctx,
        git=FakeGit(), host=FakeHost(), auth=FakeAuth(signed_in=not args.signed_out),
        # Run command is shown when a run user is configured, as on a real box
        # that has opted in; the stub never runs anything.
        runner=FakeRunner(), run_user="you@example.com",
    )
    print(f"demo world in {WORLD}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
