"""Agents waking, creating, parking and stopping other agents (policy)."""

import json
import shlex

import pytest

from launcher.agents import briefs, cli, registry, runtime, service
from launcher.agents.model import MAIN, PARKED, STOPPED, AgentError

from .conftest import make_repo

PARENT = "notes/main"


def argv(w, display):
    return shlex.split(w.tmux.sessions[display]["command"])


def as_agent(w, key=PARENT):
    w.ctx.caller = key
    return w


def code(fn, *args, **kw) -> int:
    with pytest.raises(AgentError) as e:
        fn(*args, **kw)
    return e.value.code


def other_project(w):
    make_repo(w.base / "site")
    return service.place_for(w.ctx, key="site/main")[0]


# --- wake with a task --------------------------------------------------------

def test_prompt_is_the_first_message_of_a_fresh_start(world):
    service.new(world.ctx, world.place, "drafts")
    service.start(world.ctx, world.place, "drafts", prompt="  fix the build \n")
    assert argv(world, "notes/drafts")[-2:] == ["--", "fix the build"]


def test_prompt_wakes_a_parked_agent_in_its_conversation(world):
    a = service.start(world.ctx, world.place, "drafts", create=True)
    service.park(world.ctx, world.place, "drafts")
    service.start(world.ctx, world.place, "drafts", prompt="carry on with the review")
    cmd = argv(world, "notes/drafts")
    assert cmd[cmd.index("--resume") + 1] == a.session_id
    assert cmd[-2:] == ["--", "carry on with the review"]


def test_prompt_to_a_running_agent_is_refused(world):
    service.start(world.ctx, world.place, MAIN)
    assert code(service.start, world.ctx, world.place, MAIN, prompt="hi") == AgentError.RUNNING
    with pytest.raises(AgentError, match="SendMessage"):
        service.start(world.ctx, world.place, MAIN, prompt="hi")


def test_empty_or_huge_prompt_is_invalid(world):
    assert code(service.start, world.ctx, world.place, MAIN, prompt=" ") == AgentError.INVALID
    assert code(service.start, world.ctx, world.place, MAIN,
                prompt="x" * (runtime.MAX_PROMPT + 1)) == AgentError.INVALID
    assert world.tmux.sessions == {}


def test_cli_prompt_file(world, capsys, tmp_path):
    task = tmp_path / "task.md"
    task.write_text("read the brief and start\n")
    assert cli.main(["start", "notes/main", "--prompt-file", str(task)], ctx=world.ctx) == 0
    assert argv(world, "notes/main")[-2:] == ["--", "read the brief and start"]
    assert cli.main(["start", "notes/main", "--prompt", "again"], ctx=world.ctx) == AgentError.RUNNING
    assert "SendMessage" in capsys.readouterr().err


def test_waking_with_a_task_pushes_and_a_person_does_not(world):
    service.start(world.ctx, world.place, "drafts", create=True, prompt="go")
    assert world.pushes == []                       # a person did it
    service.park(world.ctx, world.place, "drafts")
    as_agent(world)
    service.start(world.ctx, world.place, "drafts", prompt="go on")
    assert world.pushes == ["notes/main woke notes/drafts"]


def test_waking_without_a_task_does_not_push(world):
    service.start(world.ctx, world.place, "drafts", create=True)
    service.park(world.ctx, world.place, "drafts")
    as_agent(world)
    service.start(world.ctx, world.place, "drafts")
    assert world.pushes == []


def test_agent_may_wake_an_agent_in_another_project(world):
    site = other_project(world)
    as_agent(world)
    service.start(world.ctx, site, MAIN, prompt="deploy is yours")
    assert "site/main" in world.tmux.sessions


# --- parentage ---------------------------------------------------------------

def test_agent_records_its_children_and_a_person_records_nothing(world):
    service.new(world.ctx, world.place, "by-hand")
    as_agent(world)
    service.new(world.ctx, world.place, "drafts")
    service.start(world.ctx, world.place, "tests", create=True)
    agents = registry.load(world.ctx.root)
    assert agents["notes/by-hand"].created_by is None
    assert agents["notes/drafts"].created_by == PARENT
    assert agents["notes/tests"].created_by == PARENT


def test_cli_reads_the_caller_from_agent_name(world, capsys, monkeypatch):
    monkeypatch.setenv("AGENT_NAME", PARENT)
    assert cli.main(["--json", "new", "notes/drafts"], ctx=world.ctx) == 0
    assert json.loads(capsys.readouterr().out)["created_by"] == PARENT


def test_creating_pushes_the_mission(world, tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text("# notes/drafts\n\n## Mission\nDraft the alumni letter.\n\n## State\n")
    as_agent(world)
    a = service.new(world.ctx, world.place, "drafts", brief=brief.read_text())
    assert briefs.path(world.ctx.root, a).read_text().startswith("# notes/drafts")
    assert world.pushes == ["notes/main created notes/drafts: Draft the alumni letter."]


def test_create_and_wake_in_one_call_pushes_once(world):
    as_agent(world)
    service.start(world.ctx, world.place, "drafts", create=True, prompt="go",
                  brief="## Mission\nTidy up.\n")
    assert world.pushes == ["notes/main created notes/drafts: Tidy up."]


# --- limits ------------------------------------------------------------------

def test_a_person_is_never_limited(world):
    world.memory["kb"] = 0
    for name in ("a", "b", "c", "d"):
        service.start(world.ctx, world.place, name, create=True)
    assert len(world.tmux.sessions) == 4


def test_low_memory_refuses_agents(world):
    service.new(world.ctx, world.place, "drafts")
    as_agent(world)
    world.memory["kb"] = 1024 * 1024        # 1 GB
    assert code(service.start, world.ctx, world.place, "drafts") == AgentError.LOW_MEMORY
    assert code(service.new, world.ctx, world.place, "other") == AgentError.LOW_MEMORY
    assert "notes/other" not in registry.load(world.ctx.root)


def test_too_many_running_refuses_agents(world, monkeypatch):
    monkeypatch.setattr(service, "MAX_RUNNING", 50)
    for i in range(10):
        service.start(world.ctx, world.place, f"a{i}", create=True)
    service.new(world.ctx, world.place, "drafts")
    as_agent(world)
    assert code(service.start, world.ctx, world.place, "drafts") == AgentError.TOO_MANY_RUNNING


def test_limits_are_configurable(world):
    world.ctx.root.mkdir(parents=True, exist_ok=True)
    (world.ctx.root / "limits.json").write_text(
        json.dumps({"min_available_mb": 100, "max_running": 1, "max_children": 1}))
    service.start(world.ctx, world.place, MAIN)
    as_agent(world)
    world.memory["kb"] = 200 * 1024
    assert code(service.start, world.ctx, world.place, "x", create=True) == AgentError.TOO_MANY_RUNNING
    world.ctx.caller = None
    service.stop(world.ctx, world.place, MAIN)
    as_agent(world)
    service.new(world.ctx, world.place, "one")
    assert code(service.new, world.ctx, world.place, "two") == AgentError.TOO_MANY_CHILDREN


def test_at_most_three_live_children(world):
    as_agent(world)
    for name in ("a", "b", "c"):
        service.new(world.ctx, world.place, name)
    assert code(service.new, world.ctx, world.place, "d") == AgentError.TOO_MANY_CHILDREN
    world.ctx.caller = None
    service.archive(world.ctx, world.place, "a")
    as_agent(world)
    service.new(world.ctx, world.place, "d")


def test_a_child_may_not_create(world):
    as_agent(world)
    service.new(world.ctx, world.place, "drafts")
    as_agent(world, "notes/drafts")
    assert code(service.new, world.ctx, world.place, "grandchild") == AgentError.NESTED
    assert code(service.start, world.ctx, world.place, "gc", create=True) == AgentError.NESTED


def test_agents_create_only_in_their_own_project(world):
    site = other_project(world)
    as_agent(world)
    assert code(service.new, world.ctx, site, "drafts") == AgentError.OTHER_PROJECT
    assert code(service.start, world.ctx, site, "drafts", create=True) == AgentError.OTHER_PROJECT


# --- park and stop -----------------------------------------------------------

def test_agent_parks_and_stops_its_children_only(world):
    service.start(world.ctx, world.place, "theirs", create=True)
    as_agent(world)
    service.start(world.ctx, world.place, "mine", create=True)
    assert service.park(world.ctx, world.place, "mine").state == PARKED
    assert code(service.park, world.ctx, world.place, "theirs") == AgentError.NOT_YOURS
    assert code(service.stop, world.ctx, world.place, "theirs") == AgentError.NOT_YOURS
    assert "notes/theirs" in world.tmux.sessions


def test_agent_may_not_restart_another_fresh(world):
    service.start(world.ctx, world.place, "theirs", create=True)
    as_agent(world)
    assert code(service.start, world.ctx, world.place, "theirs", fresh=True,
                force=True) == AgentError.NOT_YOURS


def test_agent_stopping_itself_is_saved_before_its_session_dies(world, monkeypatch):
    service.start(world.ctx, world.place, MAIN)
    as_agent(world)
    seen = []
    real_kill = runtime.kill

    def kill(home, name, run):
        seen.append(registry.load(world.ctx.root)["notes/main"].state)
        real_kill(home, name, run)
    monkeypatch.setattr(runtime, "kill", kill)
    service.stop(world.ctx, world.place, MAIN)
    assert seen == [STOPPED] and "notes/main" not in world.tmux.sessions


# --- no raising permissions --------------------------------------------------

def test_child_inherits_the_parents_mode(world):
    service.set_mode(world.ctx, world.place, MAIN, "acceptEdits")
    as_agent(world)
    assert service.new(world.ctx, world.place, "drafts").mode == "acceptEdits"
    assert service.new(world.ctx, world.place, "plans", "plan").mode == "plan"


def test_child_may_not_be_more_permissive(world):
    as_agent(world)                         # main runs in the default mode
    assert code(service.new, world.ctx, world.place, "drafts", "auto") == AgentError.MODE_RAISED
    assert code(service.new, world.ctx, world.place, "drafts", "acceptEdits") == AgentError.MODE_RAISED
    service.new(world.ctx, world.place, "drafts", "default")


@pytest.mark.parametrize("argv", [
    ["archive", "notes/drafts"],
    ["mode", "notes/drafts", "--set", "plan"],
    ["schedule", "add", "notes/drafts", "--at", "06:00", "--prompt", "x"],
    ["schedule", "rm", "notes/drafts"],
    ["schedule", "pause", "notes/drafts"],
    ["schedule", "resume", "notes/drafts"],
    ["scratch", "--project", "notes"],
])
def test_people_only_commands(world, capsys, argv):
    service.new(world.ctx, world.place, "drafts")
    as_agent(world)
    assert cli.main(argv, ctx=world.ctx) == AgentError.PEOPLE_ONLY
    assert "ask the user" in capsys.readouterr().err


def test_agents_pass_only_a_model_on_to_claude(world, capsys):
    as_agent(world)
    assert cli.main(["start", "notes/main", "--", "--dangerously-skip-permissions"],
                    ctx=world.ctx) == AgentError.PEOPLE_ONLY
    assert cli.main(["start", "notes/main", "--", "--model", "opus"], ctx=world.ctx) == 0


def test_schedule_ls_is_open_to_agents(world, capsys):
    as_agent(world)
    assert cli.main(["schedule", "ls"], ctx=world.ctx) == 0
