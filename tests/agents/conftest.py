import subprocess
from pathlib import Path

import pytest


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                           *args], check=True, capture_output=True, text=True).stdout


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "README").write_text("x\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "init")
    return path


@pytest.fixture
def base(tmp_path):
    return tmp_path / "projects"


import shlex
import subprocess as _sp

from launcher.agents import runtime


class FakeTmux:
    """tmux on the agents socket, as a dict. Fed the same argv runtime builds."""

    def __init__(self, clock):
        self.clock = clock
        self.sessions = {}      # name -> dict
        self.calls = []
        self.die_with = None    # pane text: the next session exits at once
        self.keys = []
        self.on_start = None    # called with the new session's dict

    def _ok(self, cmd, out=""):
        return _sp.CompletedProcess(cmd, 0, stdout=out, stderr="")

    def __call__(self, cmd, timeout=None):
        self.calls.append(cmd)
        assert cmd[:4] == ["tmux", "-u", "-L", runtime.SOCKET], cmd
        args = cmd[6:]          # after -f <conf>
        if "new-session" in args:
            i = args.index("new-session")
            rest = args[i + 1:]
            name = rest[rest.index("-s") + 1]
            if name in self.sessions:
                return _sp.CompletedProcess(cmd, 1, stdout="", stderr=f"duplicate session: {name}")
            env = {}
            for j, a in enumerate(rest):
                if a == "-e":
                    k, _, v = rest[j + 1].partition("=")
                    env[k] = v
            dead = self.die_with is not None
            self.sessions[name] = dict(
                created=int(self.clock["t"]), activity=int(self.clock["t"]), attached=False,
                dead=dead, cwd=rest[rest.index("-c") + 1], env=env, command=rest[-1],
                pane=self.die_with if dead else runtime.CONNECTED_SAMPLE)
            self.die_with = None
            if self.on_start:
                self.on_start(self.sessions[name])
            return self._ok(cmd)
        verb = args[0]
        if verb == "list-sessions":
            if not self.sessions:
                return _sp.CompletedProcess(cmd, 1, stdout="", stderr="no server running")
            return self._ok(cmd, "".join(
                f"{n}\t{s['created']}\t{s['activity']}\t{1 if s['attached'] else 0}"
                f"\t{s.get('window_activity', s['activity'])}\n"
                for n, s in self.sessions.items()))
        if verb == "list-panes":
            # pane_dead_status: empty while alive; a dead pane's exit
            # status ("exit_status", default 1: a crash).
            return self._ok(cmd, "".join(
                f"{n}\t{1 if s['dead'] else 0}\t{s.get('exit_status', 1) if s['dead'] else ''}\n"
                for n, s in self.sessions.items()))
        if verb == "kill-session":
            self.sessions.pop(self.resolve(args[args.index("-t") + 1]), None)
            return self._ok(cmd)
        if verb == "capture-pane":
            # With -S: the scrollback too ("pane"). Without: the visible
            # screen ("screen", which defaults to the same text).
            s = self.sessions.get(self.resolve(args[args.index("-t") + 1]))
            if not s:
                return self._ok(cmd, "")
            return self._ok(cmd, s["pane"] if "-S" in args else s.get("screen", s["pane"]))
        if verb == "send-keys":
            self.keys.append(args)
            return self._ok(cmd)
        raise AssertionError(f"unexpected tmux call {cmd}")

    @staticmethod
    def resolve(target):
        """Exact session names only: strip a leading "=" and a trailing ":".
        No prefix matching, unlike real tmux, so tests catch prefix targeting."""
        if target.startswith("="):
            target = target[1:]
        if target.endswith(":"):
            target = target[:-1]
        return target

    def argv(self, name):
        return shlex.split(self.sessions[name]["command"])


from types import SimpleNamespace

from launcher.agents import service


@pytest.fixture
def world(tmp_path):
    clock = {"t": 1_000_000.0}
    tmux = FakeTmux(clock)
    base = tmp_path / "projects"
    repo = make_repo(base / "notes")
    home = tmp_path / "home"
    ids = iter(f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 1000))
    trusted = []
    ctx = service.Ctx(root=home / ".config" / "agents", base_dir=base, home=home,
                      run=tmux, now=lambda: clock["t"], sleep=lambda s: None,
                      trust=trusted.append, new_id=lambda: next(ids))
    place = service.place_for(ctx, dir=repo)[0]
    return SimpleNamespace(ctx=ctx, tmux=tmux, clock=clock, base=base, repo=repo,
                           home=home, place=place, trusted=trusted)
