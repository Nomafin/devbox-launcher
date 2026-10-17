"""What can be done to an agent. The only module that changes agent state.

Every operation is one registry transaction, so `agentctl` and the launcher can act
on the same agent at the same moment without stepping on each other. A start
holds the lock while the session settles (a few seconds), so a second start
waits and then finds it running.
"""

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import config, trust as _trust, worktrees
from . import briefs, places, registry, runtime
from .model import (ACTIVE, AGENT, ARCHIVED, MAIN, PARKED, SCRATCH, STOPPED, Agent,
                    AgentError, check_mode, check_name, split_key)
from .places import Place

PARK_AFTER = 3 * 86400
# tmux stamps a new session's activity with its creation time, and a resumed
# conversation redraws its history in the first seconds. Neither is anyone
# using the agent, so tmux activity this close to the start does not count.
START_GRACE = 60
SCRATCH_AFTER = 12 * 3600
SETTLE_POLLS = 10
SETTLE_INTERVAL = 0.5


@dataclass
class Ctx:
    root: Path
    base_dir: Path
    home: Path
    run: Callable = runtime.default_run
    git: Callable = worktrees.default_run
    now: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    trust: Callable = _trust.ensure_trusted
    new_id: Callable[[], str] = field(default=lambda: str(uuid.uuid4()))

    @classmethod
    def default(cls, base_dir=None) -> "Ctx":
        return cls(root=registry.home(), base_dir=Path(base_dir or config.base_dir()),
                   home=Path.home())


@dataclass
class View:
    agent: Agent
    running: bool
    dead: bool
    attached: bool
    last_active: float
    broken: bool
    mission: str | None
    uptime: int | None
    connected: bool | None = None
    exit_status: int | None = None

    def to_dict(self) -> dict:
        d = self.agent.to_dict()
        d.update(key=self.agent.key, display=self.agent.display, running=self.running,
                 dead=self.dead, attached=self.attached, last_active=self.last_active,
                 broken=self.broken, mission=self.mission, uptime=self.uptime,
                 connected=self.connected, exit_status=self.exit_status)
        return d


# --- addressing --------------------------------------------------------------

def place_for(ctx: Ctx, *, key: str | None = None, dir=None, name: str | None = None) -> tuple[Place, str]:
    if key:
        project, agent_name = split_key(key)
        place = places.by_slug(ctx.base_dir, project, registry.load(ctx.root))
        if place is None:
            raise AgentError(AgentError.UNKNOWN, f"no project {project!r}")
        return place, agent_name
    if dir is None:
        raise AgentError(AgentError.INVALID, "give <project>/<name> or --dir")
    place = places.for_dir(ctx.base_dir, dir, ctx.git)
    return place, places.agent_name(place, name)


def _main(ctx: Ctx, place: Place) -> Agent:
    return Agent(project=place.slug, name=MAIN, cwd=str(place.cwd), workdir=str(place.cwd),
                 created=ctx.now())


def _get(ctx: Ctx, agents: dict, place: Place, name: str) -> Agent:
    agent = agents.get(f"{place.slug}/{name}")
    if agent is not None and agent.state != ARCHIVED:
        return agent
    if name == MAIN:
        agent = _main(ctx, place)
        agents[agent.key] = agent
        return agent
    raise AgentError(AgentError.UNKNOWN, f"no agent {place.slug}/{name}")


def lookup(ctx: Ctx, place: Place, name: str) -> Agent:
    agents = dict(registry.load(ctx.root))
    return _get(ctx, agents, place, name)


# --- activity ----------------------------------------------------------------

def _tmux_activity(live: runtime.Session | None) -> float:
    """Client input (session_activity) or pane output (window_activity, which
    also moves when the agent is only used from the app, even after a /clear
    left the recorded transcript behind), once the start is over."""
    if live is None:
        return 0.0
    return float(max((t for t in (live.activity, live.output)
                      if t > live.created + START_GRACE), default=0))


def _activity(ctx: Ctx, agent: Agent, live: runtime.Session | None) -> float:
    """Real use, which a brief can have missed. A start is not: created is the
    floor, the rest is the agent being used."""
    return max(agent.created, agent.last_active, _tmux_activity(live),
               runtime.transcript_mtime(ctx.home, agent.session_id))


def _idle_since(ctx: Ctx, agent: Agent, live: runtime.Session | None) -> float:
    """Where the reaper's idle clock starts: use, or the last start, so an
    agent woken from parked is not parked again by the next reap."""
    return max(_activity(ctx, agent, live), agent.last_started)


def _clean_exit(s: runtime.Session | None) -> bool:
    """Closed from inside (/exit): stopped, not failed."""
    return bool(s and s.dead and s.exit_status == 0)


def _crashed(s: runtime.Session | None) -> bool:
    """Died after its start with a non-zero (or no) status. The corpse is kept
    for its output; restore resumes it like any agent that is not running."""
    return bool(s and s.dead and s.exit_status != 0)


# --- operations --------------------------------------------------------------

def _new(ctx: Ctx, agents: dict, place: Place, name: str, mode=None) -> Agent:
    check_name(name)
    mode = check_mode(mode)
    existing = agents.get(f"{place.slug}/{name}")
    if existing is not None and existing.state != ARCHIVED:
        raise AgentError(AgentError.EXISTS, f"agent {existing.key} already exists")
    branch = f"agent/{name}"
    error = worktrees.create(place.cwd, name, run=ctx.git, branch=branch)
    if error:
        raise AgentError(AgentError.REFUSED, error)
    agent = Agent(project=place.slug, name=name, cwd=str(place.cwd),
                  workdir=str(worktrees.path(place.cwd, name)), branch=branch, mode=mode,
                  created=ctx.now())
    agents[agent.key] = agent
    briefs.ensure(ctx.root, agent)
    return agent


def new(ctx: Ctx, place: Place, name: str, mode=None) -> Agent:
    with registry.transaction(ctx.root) as agents:
        return _new(ctx, agents, place, name, mode)


def _settle(ctx: Ctx, agent: Agent) -> str | None:
    """None once the session has survived the settle window, else why it died.

    The trust prompt is looked for on the visible screen only, and answered at
    most once: a resumed conversation redraws its history, which can hold the
    prompt's text, and Up Enter sent to a live agent resubmits its last prompt."""
    answered = False
    for _ in range(SETTLE_POLLS):
        ctx.sleep(SETTLE_INTERVAL)
        live = runtime.sessions(ctx.home, ctx.run).get(agent.display)
        if live is None or live.dead:
            text = runtime.pane(ctx.home, agent.display, ctx.run)
            return runtime.reason(text) or "the session exited without printing anything"
        if not answered and runtime.TRUST_PROMPT in runtime.screen(ctx.home, agent.display, ctx.run):
            runtime.answer_trust_prompt(ctx.home, agent.display, ctx.run)
            answered = True
    return None


def _start(ctx: Ctx, agents: dict, agent: Agent, *, fresh: bool, force: bool,
           repair: bool, extra=()) -> Agent:
    live = runtime.sessions(ctx.home, ctx.run).get(agent.display)
    if live is not None and not live.dead and not fresh:
        agent.state = ACTIVE
        return agent
    if (fresh and not force and agent.kind == AGENT and agent.session_id is not None
            and briefs.is_stale(briefs.path(ctx.root, agent), _activity(ctx, agent, live))):
        raise AgentError(AgentError.STALE_BRIEF,
                         f"the brief of {agent.key} has not been updated since its last "
                         "activity: run /handoff in it first, or start fresh anyway")
    if agent.has_worktree and not Path(agent.workdir).is_dir():
        if not repair:
            raise AgentError(AgentError.BROKEN,
                             f"the worktree {agent.workdir} is missing; it can be recreated "
                             f"from branch {agent.branch}")
        worktrees.prune(agent.cwd, run=ctx.git)
        error = worktrees.create(agent.cwd, agent.name, run=ctx.git, branch=agent.branch)
        if error:
            raise AgentError(AgentError.REFUSED, error)
    if live is not None:
        runtime.kill(ctx.home, agent.display, ctx.run)   # a corpse, or a fresh restart
    previous = agent.session_id
    resume = not fresh and agent.session_id is not None
    seed = None
    if not resume:
        agent.session_id = ctx.new_id()
        if agent.kind == AGENT:
            seed = briefs.write_seed(ctx.root, agent)
    ctx.trust(agent.workdir)
    env = {"AGENT_NAME": agent.key}
    if agent.kind == AGENT:
        env["AGENT_BRIEF"] = str(briefs.ensure(ctx.root, agent))
    command = runtime.claude_command(agent, fresh=not resume, seed=seed, extra=extra)
    try:
        runtime.start(ctx.home, agent.display, agent.workdir, command, env, ctx.run)
        why = _settle(ctx, agent)
    except Exception:
        agent.session_id = previous
        raise
    if why is not None:
        agent.session_id = previous      # that conversation never existed
        runtime.kill(ctx.home, agent.display, ctx.run)
        agent.state = STOPPED
        agent.last_error = why
        hint = ""
        if resume and runtime.RESUME_MISSING.search(why):
            hint = " Its conversation could not be resumed; start it fresh instead (--fresh)."
        raise AgentError(AgentError.START_FAILED, f"{agent.display} exited on start: {why}.{hint}")
    agent.state = ACTIVE
    agent.last_error = None
    agent.last_started = ctx.now()
    return agent


def start(ctx: Ctx, place: Place, name: str, *, fresh=False, force=False, create=False,
          repair=False, extra=()) -> Agent:
    with registry.transaction(ctx.root) as agents:
        key = f"{place.slug}/{name}"
        known = agents.get(key)
        if create and name != MAIN and (known is None or known.state == ARCHIVED):
            _new(ctx, agents, place, name)
        agent = _get(ctx, agents, place, name)
        return _start(ctx, agents, agent, fresh=fresh, force=force, repair=repair, extra=extra)


def _stop(ctx: Ctx, agent: Agent, state: str) -> None:
    runtime.kill(ctx.home, agent.display, ctx.run)
    agent.state = state


def stop(ctx: Ctx, place: Place, name: str) -> Agent | None:
    """Stop; a scratch session is deleted outright, since it never resumes."""
    with registry.transaction(ctx.root) as agents:
        agent = _get(ctx, agents, place, name)
        _stop(ctx, agent, STOPPED)
        if agent.kind == SCRATCH:
            del agents[agent.key]
            return None
        return agent


def park(ctx: Ctx, place: Place, name: str) -> Agent:
    with registry.transaction(ctx.root) as agents:
        agent = _get(ctx, agents, place, name)
        if agent.kind == SCRATCH:
            raise AgentError(AgentError.REFUSED, "scratch sessions are not parked; stop it")
        _stop(ctx, agent, PARKED)
        return agent


def archive(ctx: Ctx, place: Place, name: str) -> Agent:
    if name == MAIN:
        raise AgentError(AgentError.REFUSED, "the main agent cannot be archived")
    with registry.transaction(ctx.root) as agents:
        agent = _get(ctx, agents, place, name)
        if agent.kind == SCRATCH:
            raise AgentError(AgentError.REFUSED, "scratch sessions are not archived; stop it")
        if Path(agent.workdir).is_dir():
            why = worktrees.blockers(agent.workdir, run=ctx.git)
            if why:
                raise AgentError(AgentError.REFUSED, f"{agent.key} was not archived: {why}")
        _stop(ctx, agent, STOPPED)
        if Path(agent.workdir).is_dir():
            error = worktrees.remove(agent.cwd, agent.name, run=ctx.git)
            if error:
                raise AgentError(AgentError.REFUSED, error)
        worktrees.prune(agent.cwd, run=ctx.git)
        briefs.archive(ctx.root, agent, time.strftime("%Y%m%d", time.localtime(ctx.now())))
        agent.state = ARCHIVED
        return agent


def set_mode(ctx: Ctx, place: Place, name: str, mode: str | None) -> Agent:
    mode = check_mode(mode)
    with registry.transaction(ctx.root) as agents:
        agent = _get(ctx, agents, place, name)
        agent.mode = mode
        live = runtime.sessions(ctx.home, ctx.run).get(agent.display)
        if live is not None and not live.dead:
            runtime.kill(ctx.home, agent.display, ctx.run)
            _start(ctx, agents, agent, fresh=False, force=True, repair=False)
        return agent


def scratch(ctx: Ctx, place: Place) -> Agent:
    with registry.transaction(ctx.root) as agents:
        stamp = time.strftime("%m%d", time.localtime(ctx.now()))
        n = 1
        while f"{place.slug}/scratch-{stamp}-{n}" in agents:
            n += 1
        agent = Agent(project=place.slug, name=f"scratch-{stamp}-{n}", cwd=str(place.cwd),
                      workdir=str(place.cwd), kind=SCRATCH, created=ctx.now())
        agents[agent.key] = agent
        return _start(ctx, agents, agent, fresh=True, force=True, repair=False)


def brief_status(ctx: Ctx, place: Place, name: str) -> dict:
    agent = lookup(ctx, place, name)
    live = runtime.sessions(ctx.home, ctx.run).get(agent.display)
    p = briefs.path(ctx.root, agent)
    return {"brief": str(p), "stale": briefs.is_stale(p, _activity(ctx, agent, live))}


def _order(agent: Agent):
    return (agent.project, agent.name != MAIN, agent.kind == SCRATCH, agent.name)


def views(ctx: Ctx, *, project=None, include_archived=False, places=(),
          check_connected=False) -> list[View]:
    agents = dict(registry.load(ctx.root))
    for place in places:
        if f"{place.slug}/{MAIN}" not in agents:
            agents[f"{place.slug}/{MAIN}"] = _main(ctx, place)
    live = runtime.sessions(ctx.home, ctx.run)
    now = ctx.now()
    out = []
    for agent in sorted(agents.values(), key=_order):
        if project and agent.project != project:
            continue
        if agent.state == ARCHIVED and not include_archived:
            continue
        s = live.get(agent.display)
        running = s is not None and not s.dead
        brief = briefs.path(ctx.root, agent)
        connected = None
        if check_connected and running:
            connected = runtime.connected(runtime.screen(ctx.home, agent.display, ctx.run))
        out.append(View(
            agent=agent, running=running, dead=_crashed(s),
            attached=bool(s and s.attached), last_active=_activity(ctx, agent, s),
            broken=(agent.has_worktree and agent.state != ARCHIVED
                    and not Path(agent.workdir).is_dir()),
            mission=briefs.mission(brief.read_text()) if brief.exists() else None,
            uptime=int(now - s.created) if s else None, connected=connected,
            exit_status=s.exit_status if s and s.dead else None))
    return out


def _clear(ctx: Ctx, agents: dict, agent: Agent) -> None:
    """A conversation closed by itself (/exit). Stopped, so a reboot leaves it
    closed."""
    runtime.kill(ctx.home, agent.display, ctx.run)
    if agent.kind == SCRATCH:
        del agents[agent.key]
    else:
        agent.state = STOPPED


def reap(ctx: Ctx, park_after=PARK_AFTER, scratch_after=SCRATCH_AFTER) -> list[str]:
    done = []
    with registry.transaction(ctx.root) as agents:
        live = runtime.sessions(ctx.home, ctx.run)
        now = ctx.now()
        for agent in list(agents.values()):
            s = live.get(agent.display)
            agent.last_active = _activity(ctx, agent, s)
            if s is not None and s.attached:
                continue
            if _clean_exit(s):
                _clear(ctx, agents, agent)
                done.append(f"cleared {agent.display}")
                continue
            if _crashed(s):
                continue        # its output stays readable; restore resumes it
            idle = now - _idle_since(ctx, agent, s)
            if agent.kind == SCRATCH and idle >= scratch_after:
                runtime.kill(ctx.home, agent.display, ctx.run)
                del agents[agent.key]
                done.append(f"deleted {agent.display}")
            elif agent.kind == AGENT and agent.state in (ACTIVE, STOPPED) and idle >= park_after:
                _stop(ctx, agent, PARKED)
                done.append(f"parked {agent.display}")
    return done


def restore(ctx: Ctx) -> list[str]:
    done = []
    with registry.transaction(ctx.root) as agents:
        live = runtime.sessions(ctx.home, ctx.run)
        for agent in sorted(agents.values(), key=_order):
            if agent.kind != AGENT or agent.state != ACTIVE:
                continue
            s = live.get(agent.display)
            if s is not None and not s.dead:
                continue
            if _clean_exit(s):
                _clear(ctx, agents, agent)
                done.append(f"cleared {agent.display}")
                continue
            try:
                _start(ctx, agents, agent, fresh=False, force=True, repair=False)
                done.append(f"restored {agent.display}")
            except AgentError as exc:
                done.append(f"failed {agent.display}: {exc}")
    return done
