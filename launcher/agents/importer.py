"""One-time migration: launcher instances (and named sessions on a `dbx` tmux
socket, from an earlier terminal wrapper) → agents.

Registers agents only. Nothing old is stopped: the old sessions keep running on
their own sockets until their owner stops them, which is when /handoff into the
new agent's brief is worth doing.

Three phases. Reading and asking happen without the registry lock, so a prompt
never blocks the launcher, agentctl or the timers. Only the final apply takes the
lock, and it re-checks every key it writes.
"""

import json
import subprocess
from pathlib import Path

from .. import discovery, worktrees
from . import briefs, places, registry, runtime
from .model import AGENT, Agent, AgentError
from .service import _new


def _ask(question: str) -> bool:
    try:
        with open("/dev/tty") as tty:
            print(f"{question} [y/N] ", end="", flush=True)
            return tty.readline().strip().lower().startswith("y")
    except OSError:
        return False


def _branch(workdir, git) -> str | None:
    r = git(["git", "-C", str(workdir), "rev-parse", "--abbrev-ref", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else None


def _instances(path: Path) -> dict[str, list[str]]:
    try:
        table = json.loads(path.read_text()).get("instances", {})
    except (OSError, ValueError, AttributeError):
        return {}
    return {k: [n for n in v if isinstance(n, str)] for k, v in table.items() if isinstance(v, list)}


def _dbx_sessions(run) -> list[tuple[str, str]]:
    try:
        r = run(["tmux", "-L", "dbx", "list-panes", "-a", "-F", "#{session_name}\t#{pane_current_path}"])
    except OSError:                           # tmux not installed: no dbx sessions
        return []
    if r.returncode != 0:
        return []
    out = []
    for line in (r.stdout or "").splitlines():
        name, _, path = line.partition("\t")
        if name.startswith("cc-") and path:
            out.append((name, path))
    return out


def run(ctx, dry_run=False, ask=_ask, desired_path=None, dbx_run=None) -> list[str]:
    desired_path = Path(desired_path or Path.home() / ".local" / "state" / "devbox-launcher"
                        / "desired.json")
    dbx_run = dbx_run or runtime.default_run

    # Phase 1: read and ask, without the lock. Items are output lines, or
    # ("adopt", agent, line) / ("create", place, name, key) to apply in phase 3.
    agents = registry.load(ctx.root)
    plan: list = []
    planned: set[str] = set()

    def adopt_plan(place, name, workdir):
        key = f"{place.slug}/{name}"
        if key in agents or key in planned:
            plan.append(f"skip {key}: already an agent")
            return
        planned.add(key)
        agent = Agent(project=place.slug, name=name, cwd=str(place.cwd), workdir=str(workdir),
                      branch=_branch(workdir, ctx.git), kind=AGENT, created=ctx.now())
        line = (f"{'would adopt' if dry_run else 'adopted'} {key} "
                f"(worktree {workdir}, branch {agent.branch})")
        plan.append(("adopt", agent, line))

    for slug, names in _instances(desired_path).items():
        place = places.by_slug(ctx.base_dir, slug, agents)
        if place is None:
            plan.append(f"skip launcher instances of {slug}: project is gone")
            continue
        for name in names:
            if worktrees.exists(place.cwd, name):
                adopt_plan(place, name, worktrees.path(place.cwd, name))

    for session, path in _dbx_sessions(dbx_run):
        if "_" not in session:
            continue                          # cc-<slug>: the project's default session
        try:
            place = places.for_dir(ctx.base_dir, path, ctx.git)
        except AgentError:
            plan.append(f"skip {session}: {path} is not in a project")
            continue
        name = places.agent_name(place, discovery.slugify(session.split("_", 1)[1]))
        if name == "main":
            continue
        marker = "/.claude/worktrees/"
        if marker in path:
            wt_name = path.split(marker, 1)[1].split("/", 1)[0]
            if wt_name != discovery.slugify(wt_name):
                plan.append(f"skip {session}: worktree name {wt_name!r} is not a valid agent name")
                continue
            adopt_plan(place, wt_name, worktrees.path(place.cwd, wt_name))
            continue
        key = f"{place.slug}/{name}"
        if key in agents or key in planned:
            continue
        if dry_run:
            plan.append(f"would ask: make {key} a worktree agent (branch agent/{name})")
            continue
        if ask(f"{session} works in the shared checkout. Make {key} a worktree agent?"):
            planned.add(key)
            plan.append(("create", place, name, key))

    # Phase 2: apply under one transaction, re-checking every key.
    done: list[str] = []
    if dry_run or not any(not isinstance(item, str) for item in plan):
        for item in plan:
            done.append(item if isinstance(item, str) else item[2])
    else:
        with registry.transaction(ctx.root) as agents:
            for item in plan:
                if isinstance(item, str):
                    done.append(item)
                elif item[0] == "adopt":
                    _, agent, line = item
                    if agent.key in agents:
                        done.append(f"skip {agent.key}: already an agent")
                        continue
                    agents[agent.key] = agent
                    done.append(line)
                    done.append(f"  brief: {briefs.ensure(ctx.root, agent)}")
                else:
                    _, place, name, key = item
                    if key in agents:
                        done.append(f"skip {key}: already an agent")
                        continue
                    try:
                        agent = _new(ctx, agents, place, name)
                        done.append(f"created {key}  brief: {briefs.path(ctx.root, agent)}")
                    except AgentError as exc:
                        done.append(f"skip {key}: {exc}")
    if not dry_run and done:
        done.append("Old sessions keep running. Before stopping one, run "
                    "`/handoff <brief path>` in it so its agent starts with what it knew.")
    return done
