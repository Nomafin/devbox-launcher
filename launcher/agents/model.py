"""What an agent is, and what it is called.

An agent is one durable line of work on a project: a name, the directory it
works in, and the conversation it is in. `main` works in the project's own
checkout; every other agent has a git worktree of its own. A scratch session
is a throwaway in the main checkout that never comes back.
"""

from dataclasses import asdict, dataclass, fields

from ..discovery import slugify

SEP = "/"
MAIN = "main"
AGENT = "agent"
SCRATCH = "scratch"

ACTIVE, STOPPED, PARKED, ARCHIVED = "active", "stopped", "parked", "archived"
STATES = (ACTIVE, STOPPED, PARKED, ARCHIVED)

# What `claude --permission-mode` accepts that the launcher offers. The CLI
# also takes bypassPermissions and dontAsk; neither is offered (see the
# README, "Permission mode").
PERMISSION_MODES = ("default", "acceptEdits", "auto", "plan")


class AgentError(Exception):
    INVALID = 2
    UNKNOWN = 3
    STALE_BRIEF = 4
    START_FAILED = 5
    REFUSED = 6
    EXISTS = 7
    BROKEN = 8

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Agent:
    project: str
    name: str
    cwd: str          # the project's checkout (launch dir)
    workdir: str      # where claude runs: cwd, or the agent's worktree
    branch: str | None = None
    kind: str = AGENT
    state: str = STOPPED
    session_id: str | None = None
    mode: str | None = None
    created: float = 0.0
    last_active: float = 0.0
    # The last successful start. The reaper's idle clock starts from it, so a
    # woken agent is not parked again at once; brief staleness ignores it,
    # since a start is not work the brief has missed.
    last_started: float = 0.0
    last_error: str | None = None

    @property
    def key(self) -> str:
        return f"{self.project}/{self.name}"

    @property
    def display(self) -> str:
        return f"{self.project}{SEP}{self.name}"

    @property
    def is_main(self) -> bool:
        return self.name == MAIN

    @property
    def has_worktree(self) -> bool:
        return self.kind == AGENT and not self.is_main

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Agent":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def check_name(name: str) -> str:
    """A name a person may give a new agent."""
    if not name or slugify(name) != name or "--" in name:
        raise AgentError(AgentError.INVALID,
                         f"{name!r} is not a valid agent name: use lowercase letters, "
                         "digits and single dashes")
    if name == MAIN or name.startswith(SCRATCH + "-"):
        raise AgentError(AgentError.INVALID, f"{name!r} is reserved")
    return name


def check_mode(mode: str | None) -> str | None:
    """The mode to record. "default" is recorded as nothing, so a defaultMode
    in settings.json stays in charge rather than being overridden."""
    if mode is None or mode == "default":
        return None
    if mode not in PERMISSION_MODES:
        raise AgentError(AgentError.INVALID, f"unknown permission mode {mode!r}")
    return mode


def split_key(key: str) -> tuple[str, str]:
    project, sep, name = (key or "").partition("/")
    if not sep or not project or not name:
        raise AgentError(AgentError.INVALID, f"{key!r} is not <project>/<name>")
    return project, name
