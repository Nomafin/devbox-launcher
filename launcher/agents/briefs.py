"""An agent's brief: what the next conversation needs to know.

Kept outside the repo, so main's brief never dirties the checkout and briefs
outlive worktrees. The agent writes it (told to by the seed preamble and by
/handoff); agentctl only creates it, reads its Mission line, and archives it.
"""

from pathlib import Path

from .model import Agent

SECTIONS = ("Mission", "State", "Next steps", "Gotchas")

# The reply to /handoff is written seconds after the brief, and counts as
# activity. Anything later than this is real work the brief does not cover.
STALE_GRACE = 120.0

PREAMBLE = """You are agent `{display}`, one long-running line of work on this project.
Your brief is at {brief} (also in $AGENT_BRIEF). It is how the next
conversation for this agent will know what you know, so keep it current:
update it when you finish a meaningful piece of work, and rewrite it fully
when asked to hand off (/handoff). Keep its four sections: Mission, State,
Next steps, Gotchas. A new conversation for this agent is started with Fresh
(the launcher's Fresh conversation, or `agentctl start <project>/<name> --fresh`), not /clear: if
the user wants to start over, hand off and point them there.

The brief as of the start of this conversation:

{text}"""


def template(agent: Agent) -> str:
    return f"# {agent.display}\n\n" + "".join(f"## {s}\n\n" for s in SECTIONS)


def path(root, agent: Agent) -> Path:
    return Path(root) / "briefs" / agent.project / f"{agent.name}.md"


def ensure(root, agent: Agent) -> Path:
    p = path(root, agent)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(template(agent))
    return p


def mission(text: str) -> str | None:
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                return None
            inside = line[3:].strip() == "Mission"
            continue
        if inside and line.strip():
            return line.strip()
    return None


def is_stale(p: Path, last_active: float) -> bool:
    if last_active <= 0:
        return False
    try:
        mtime = Path(p).stat().st_mtime
    except FileNotFoundError:
        return True
    return last_active - mtime > STALE_GRACE


def write_seed(root, agent: Agent) -> Path:
    brief = ensure(root, agent)
    seed = Path(root) / "seeds" / agent.project / f"{agent.name}.md"
    seed.parent.mkdir(parents=True, exist_ok=True)
    seed.write_text(PREAMBLE.format(display=agent.display, brief=brief, text=brief.read_text()))
    return seed


def archive(root, agent: Agent, day: str) -> Path | None:
    p = path(root, agent)
    if not p.exists():
        return None
    dest = Path(root) / "archive" / agent.project / f"{agent.name}-{day}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    p.replace(dest)
    return dest
