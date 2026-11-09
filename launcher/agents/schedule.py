"""Scheduled runs: an agent started fresh with a prompt at set times of day.

A run is an ordinary agent, so it shows on the page and in the Claude app and
can be taken over from a phone. Only a named agent can have a schedule: it works
in a worktree on a branch of its own, so a run nobody is watching stays out of
the project's checkout, and what it did comes back as a pull request.

One job calls `tick` every few minutes, whatever the number of schedules, which
is also what catches up a run missed while the box was asleep. A run does not
interrupt: an agent someone is attached to, or one active a moment ago, keeps
going and that run is skipped.
"""

import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Iterator

from . import service
from .model import MAIN, AgentError

# A run this much late still happens; later than that it waits for its next time.
GRACE = 3600
# An agent with activity this recent is taken to be still working.
BUSY_WITHIN = 600
# The prompt travels on the tmux command line, whose message size is limited.
MAX_PROMPT = 8000

_TIME = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]*")

_BRIEF = """This is a scheduled run and nobody is watching it. Your branch is {branch}.

Rules for this run:
- Start with `git fetch`. If your branch's pull request has been merged, move this \
branch onto the default branch before starting anything new.
- Work and commit only on {branch}. Never push to the default branch, and never force-push.
- When you have something to show, push the branch and open a pull request with `gh pr create`, \
or update the one already open.
- If there is nothing to do, say so and stop. Do not make a commit just to record that.

The task:

"""


@dataclass
class Schedule:
    key: str                    # <project>/<name>
    times: list[str]            # "HH:MM", local time
    prompt: str
    mode: str | None = "auto"
    model: str | None = None
    enabled: bool = True
    created: float = 0.0
    last_fired: float = 0.0
    last_result: str | None = None


def _path(root) -> Path:
    return Path(root) / "schedules.json"


def load(root) -> list[Schedule]:
    try:
        data = json.loads(_path(root).read_text())
    except FileNotFoundError:
        return []
    except ValueError as exc:
        raise AgentError(AgentError.INVALID, f"{_path(root)} could not be read: {exc}") from exc
    known = {f.name for f in fields(Schedule)}
    return [Schedule(**{k: v for k, v in item.items() if k in known})
            for item in data.get("schedules", [])]


@contextmanager
def _transaction(root) -> Iterator[list[Schedule]]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "schedules.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        schedules = load(root)
        yield schedules
        tmp = _path(root).with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "schedules": [asdict(s) for s in schedules]},
                                  ensure_ascii=False, indent=1))
        os.replace(tmp, _path(root))


def parse_times(text: str) -> list[str]:
    """"6:00, 18:30" -> ["06:00", "18:30"], sorted and without repeats."""
    times = set()
    for part in re.split(r"[,\s]+", text.strip()):
        match = _TIME.fullmatch(part) if part else None
        if match is None:
            raise AgentError(AgentError.INVALID, f"not a time of day: {part!r} (use HH:MM)")
        times.add(f"{int(match[1]):02d}:{match[2]}")
    return sorted(times)


def _find(schedules: list[Schedule], key: str) -> Schedule:
    for s in schedules:
        if s.key == key:
            return s
    raise AgentError(AgentError.UNKNOWN, f"no schedule for {key}")


def add(ctx, key: str, *, times: str, prompt: str, mode: str | None = "auto",
        model: str | None = None) -> Schedule:
    """Add a schedule, or replace the one this agent has."""
    place, name = service.place_for(ctx, key=key)
    if name == MAIN:
        raise AgentError(AgentError.REFUSED,
                         "the main agent works in the project's own checkout; "
                         "give a schedule an agent name of its own")
    prompt = prompt.strip()
    if not prompt:
        raise AgentError(AgentError.INVALID, "the prompt is empty")
    if len(prompt) > MAX_PROMPT:
        raise AgentError(AgentError.INVALID, f"the prompt is over {MAX_PROMPT} characters; "
                         "put the details in a file in the repo and point to it")
    model = (model or "").strip() or None
    if model and not _MODEL.fullmatch(model):
        raise AgentError(AgentError.INVALID, f"not a model name: {model!r}")
    wanted = Schedule(key=f"{place.slug}/{name}", times=parse_times(times), prompt=prompt,
                      mode=service.check_mode(mode), model=model, created=ctx.now())
    with _transaction(ctx.root) as schedules:
        for i, s in enumerate(schedules):
            if s.key == wanted.key:
                # Editing it must not fire a time that has already passed.
                wanted.enabled, wanted.last_result = s.enabled, s.last_result
                schedules[i] = wanted
                return wanted
        schedules.append(wanted)
    return wanted


def remove(ctx, key: str) -> None:
    """The schedule only: the agent, its branch and its conversation stay."""
    with _transaction(ctx.root) as schedules:
        schedules.remove(_find(schedules, key))


def set_enabled(ctx, key: str, enabled: bool) -> Schedule:
    with _transaction(ctx.root) as schedules:
        s = _find(schedules, key)
        s.enabled = enabled
        # Back on means from now on, not whatever was missed while it was off.
        s.created = ctx.now()
        return s


def due(s: Schedule, now: float) -> float | None:
    """The time slot this schedule should run for now, or None."""
    if not s.enabled:
        return None
    slots = []
    for days_back in (0, 1):
        day = time.localtime(now - days_back * 86400)
        for hhmm in s.times:
            hour, minute = hhmm.split(":")
            slots.append(time.mktime((day.tm_year, day.tm_mon, day.tm_mday,
                                      int(hour), int(minute), 0, 0, 0, -1)))
    passed = [slot for slot in slots if slot <= now]
    if not passed:
        return None
    slot = max(passed)
    if now - slot > GRACE or slot <= max(s.last_fired, s.created):
        return None
    return slot


def _clock(ctx) -> str:
    return time.strftime("%d.%m. %H:%M", time.localtime(ctx.now()))


def _fire(ctx, s: Schedule) -> str:
    """Start the run, or say why not. Returns what to show as the last result."""
    try:
        place, name = service.place_for(ctx, key=s.key)
        view = next((v for v in service.views(ctx, project=place.slug, places=[place])
                     if v.agent.key == s.key), None)
        if view is not None and view.running:
            if view.attached:
                return f"{_clock(ctx)}: skipped, a terminal is attached"
            if ctx.now() - view.last_active < BUSY_WITHIN:
                return f"{_clock(ctx)}: skipped, the agent was active a moment ago"
        if view is None:
            service.new(ctx, place, name, s.mode)
        else:
            if view.running:
                service.stop(ctx, place, name)
            service.set_mode(ctx, place, name, s.mode)
        branch = service.lookup(ctx, place, name).branch
        extra = ["--model", s.model] if s.model else []
        service.start(ctx, place, name, fresh=True, force=True, repair=True,
                      extra=[*extra, _BRIEF.format(branch=branch) + s.prompt])
    except AgentError as exc:
        return f"{_clock(ctx)}: failed: {exc}"
    return f"{_clock(ctx)}: started"


def run_now(ctx, key: str) -> str:
    with _transaction(ctx.root) as schedules:
        s = _find(schedules, key)
        s.last_result = _fire(ctx, s)
        s.last_fired = ctx.now()
        return s.last_result


def tick(ctx) -> list[str]:
    """Start whatever is due. A skipped or failed run counts as that time's run:
    retrying every few minutes would only repeat the reason."""
    done = []
    now = ctx.now()
    if not _path(ctx.root).exists():
        return done
    with _transaction(ctx.root) as schedules:
        for s in schedules:
            if due(s, now) is None:
                continue
            s.last_result = _fire(ctx, s)
            s.last_fired = now
            done.append(f"{s.key}: {s.last_result}")
    return done
