"""What an agent may do to other agents through agentctl. People are not limited.

The caller is the agent whose session runs the command: the launcher starts
every session with AGENT_NAME=<project>/<name> in its environment, and
agentctl reads it from there. No AGENT_NAME means a person (a terminal, the
launcher, a timer). This keeps well-meaning agents inside agreed limits; it
is not a security boundary, since an agent's shell can unset the variable.
The permission rules in Claude's settings are the other half.

An agent may:
- wake any agent, in any project, with or without a task;
- create agents in its own project only, at most `max_children` live ones,
  never more permissive than itself, and only if no agent created it;
- park or stop itself and its own children, nothing else.
It may not archive, change modes or change schedules. Starting or creating
anything is refused while memory is short or too many agents run.

The numbers can be changed in limits.json beside the registry.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .model import ARCHIVED, Agent, AgentError, mode_rank, split_key

LIMITS = "limits.json"
DEFAULTS = {"min_available_mb": 1536, "max_running": 10, "max_children": 3}

# agentctl commands (and schedule actions) only a person may run.
PEOPLE_ONLY = {"archive", "mode", "scratch", "import",
               "schedule add", "schedule rm", "schedule pause", "schedule resume"}


def caller(env: Mapping) -> str | None:
    key = (env.get("AGENT_NAME") or "").strip()
    if not key:
        return None
    split_key(key)          # a malformed name is an error, not a person
    return key


def limits(root) -> dict:
    out = dict(DEFAULTS)
    try:
        data = json.loads((Path(root) / LIMITS).read_text())
    except FileNotFoundError:
        return out
    except ValueError as exc:
        raise AgentError(AgentError.INVALID, f"{Path(root) / LIMITS} could not be read: {exc}") from exc
    for key in DEFAULTS:
        if isinstance(data.get(key), (int, float)) and not isinstance(data.get(key), bool):
            out[key] = data[key]
    return out


@dataclass
class Caller:
    key: str
    project: str
    mode: str | None
    created_by: str | None


def who(agents: dict, key: str) -> Caller:
    """The calling agent as the registry knows it. main may not be recorded
    yet; it was then created by nobody and runs in the default mode."""
    a = agents.get(key)
    project = split_key(key)[0]
    if a is None or a.state == ARCHIVED:
        return Caller(key, project, None, None)
    return Caller(key, project, a.mode, a.created_by)


def command(caller_key: str | None, cmd: str) -> None:
    if caller_key and cmd in PEOPLE_ONLY:
        raise AgentError(AgentError.PEOPLE_ONLY,
                         f"agents may not run `agentctl {cmd}`; ask the user to do it")


# Words an agent may put after `--` (passed on to claude): a model, nothing
# that could change its permissions.
EXTRA_ALLOWED = ("--model",)


def extra(caller_key: str | None, args) -> None:
    args = list(args)
    if not caller_key or not args:
        return
    ok = len(args) % 2 == 0 and all(args[i] in EXTRA_ALLOWED for i in range(0, len(args), 2))
    if not ok:
        raise AgentError(AgentError.PEOPLE_ONLY,
                         "agents may pass only --model on to claude; give the task with --prompt")


def create(agents: dict, caller_key: str, project: str, mode: str | None, root) -> str | None:
    """Check that the caller may create an agent in `project`. Returns the
    child's mode: the one asked for, or else the parent's own."""
    me = who(agents, caller_key)
    if me.created_by:
        raise AgentError(AgentError.NESTED,
                         f"{me.key} was created by {me.created_by}, and an agent another agent "
                         "created may not create agents; ask your parent or the user")
    if project != me.project:
        raise AgentError(AgentError.OTHER_PROJECT,
                         f"agents create agents only in their own project ({me.project}); "
                         f"to get work done in {project}, wake one of its agents with a task")
    cap = limits(root)["max_children"]
    children = sorted(a.key for a in agents.values()
                      if a.created_by == caller_key and a.state != ARCHIVED)
    if len(children) >= cap:
        raise AgentError(AgentError.TOO_MANY_CHILDREN,
                         f"{me.key} already has {len(children)} agents of its own "
                         f"({', '.join(children)}); the limit is {cap}. Ask the user to "
                         "archive one that is done")
    if mode is None:
        return me.mode
    if mode_rank(mode) > mode_rank(me.mode):
        raise AgentError(AgentError.MODE_RAISED,
                         f"a new agent may not be more permissive than its parent: "
                         f"{mode} is above {me.key}'s {me.mode or 'default'}")
    return mode


def room(root, running: int, available_kb: int | None) -> None:
    """Refuse to start anything more when the box is full."""
    lim = limits(root)
    if available_kb is not None and available_kb < lim["min_available_mb"] * 1024:
        raise AgentError(AgentError.LOW_MEMORY,
                         f"only {available_kb // 1024} MB of memory is available (the limit is "
                         f"{lim['min_available_mb']} MB); park an agent of your own or ask the user")
    if running >= lim["max_running"]:
        raise AgentError(AgentError.TOO_MANY_RUNNING,
                         f"{running} agents are running (the limit is {lim['max_running']}); "
                         "park an agent of your own or ask the user")


def stop(caller_key: str | None, agent: Agent, verb: str) -> None:
    if caller_key is None or agent.key == caller_key or agent.created_by == caller_key:
        return
    raise AgentError(AgentError.NOT_YOURS,
                     f"agents may {verb} only themselves and agents they created; "
                     f"{agent.key} is neither. Message it, or ask the user")
