import shlex
import time

import pytest

from launcher.agents import cli, schedule, service
from launcher.agents.model import AgentError


def at(hhmm: str, day: int = 10) -> float:
    hour, minute = hhmm.split(":")
    return time.mktime((2026, 11, day, int(hour), int(minute), 0, 0, 0, -1))


@pytest.fixture
def sched(world):
    world.clock["t"] = at("05:00")
    return world


def add(world, key="notes/nightly", times="06:00, 18:00", prompt="Do the thing", **kw):
    return schedule.add(world.ctx, key, times=times, prompt=prompt, **kw)


def argv(world, key="notes/nightly"):
    return shlex.split(world.tmux.sessions[key]["command"])


# --- times -------------------------------------------------------------------

def test_times_are_normalised_sorted_and_unique():
    assert schedule.parse_times("18:30, 6:00 06:00") == ["06:00", "18:30"]


@pytest.mark.parametrize("text", ["", "25:00", "6", "06:60", "noon", "06:00;07:00"])
def test_bad_times_are_refused(text):
    with pytest.raises(AgentError):
        schedule.parse_times(text)


# --- adding ------------------------------------------------------------------

def test_main_cannot_have_a_schedule(sched):
    with pytest.raises(AgentError, match="own"):
        add(sched, key="notes/main")


def test_unknown_project_is_refused(sched):
    with pytest.raises(AgentError):
        add(sched, key="nothing/nightly")


@pytest.mark.parametrize("kw,message", [
    ({"prompt": "   "}, "empty"), ({"prompt": "x" * 8001}, "over"),
    ({"mode": "bypassPermissions"}, "bypassPermissions"), ({"model": "a b"}, "model"),
])
def test_bad_values_are_refused_and_nothing_is_saved(sched, kw, message):
    with pytest.raises(AgentError, match=message):
        add(sched, **kw)
    assert schedule.load(sched.ctx.root) == []


def test_adding_again_replaces_and_keeps_the_pause(sched):
    add(sched)
    schedule.set_enabled(sched.ctx, "notes/nightly", False)
    add(sched, times="07:00", prompt="New")
    [s] = schedule.load(sched.ctx.root)
    assert s.times == ["07:00"] and s.prompt == "New" and not s.enabled


# --- when --------------------------------------------------------------------

def test_not_due_before_its_time(sched):
    add(sched)
    assert schedule.tick(sched.ctx) == []


def test_due_at_its_time_and_only_once(sched):
    add(sched)
    sched.clock["t"] = at("06:03")
    assert len(schedule.tick(sched.ctx)) == 1
    sched.clock["t"] = at("06:08")
    assert schedule.tick(sched.ctx) == []


def test_a_time_before_the_schedule_existed_does_not_fire(sched):
    sched.clock["t"] = at("06:30")
    add(sched)
    assert schedule.tick(sched.ctx) == []


def test_a_run_missed_by_more_than_the_grace_waits_for_the_next(sched):
    add(sched)
    sched.clock["t"] = at("08:00")
    assert schedule.tick(sched.ctx) == []
    sched.clock["t"] = at("18:01")
    assert len(schedule.tick(sched.ctx)) == 1


def test_a_run_shortly_after_midnight_counts_yesterdays_late_time(sched):
    add(sched, times="23:50")
    sched.clock["t"] = at("00:10", day=11)
    assert len(schedule.tick(sched.ctx)) == 1


def test_paused_never_fires_and_resuming_does_not_catch_up(sched):
    add(sched)
    schedule.set_enabled(sched.ctx, "notes/nightly", False)
    sched.clock["t"] = at("06:01")
    assert schedule.tick(sched.ctx) == []
    schedule.set_enabled(sched.ctx, "notes/nightly", True)
    assert schedule.tick(sched.ctx) == []
    sched.clock["t"] = at("18:01")
    assert len(schedule.tick(sched.ctx)) == 1


def test_tick_without_any_schedule_writes_nothing(sched):
    assert schedule.tick(sched.ctx) == []
    assert not (sched.ctx.root / "schedules.json").exists()


# --- the run -----------------------------------------------------------------

def test_a_run_creates_the_agent_on_its_own_branch_with_the_prompt(sched):
    add(sched, model="sonnet")
    sched.clock["t"] = at("06:01")
    schedule.tick(sched.ctx)
    args = argv(sched)
    assert args[args.index("--permission-mode") + 1] == "auto"
    assert args[args.index("--model") + 1] == "sonnet"
    assert "--resume" not in args
    assert args[-1].endswith("Do the thing")
    assert "agent/nightly" in args[-1] and "pull request" in args[-1]
    assert sched.tmux.sessions["notes/nightly"]["cwd"] != str(sched.repo)
    [s] = schedule.load(sched.ctx.root)
    assert s.last_result.endswith("started") and s.last_fired == sched.clock["t"]


def test_an_idle_earlier_run_is_replaced_by_a_new_conversation(sched):
    add(sched)
    sched.clock["t"] = at("06:01")
    schedule.tick(sched.ctx)
    first = argv(sched)
    sched.clock["t"] = at("18:01")
    schedule.tick(sched.ctx)
    second = argv(sched)
    assert first[first.index("--session-id") + 1] != second[second.index("--session-id") + 1]


def test_a_recently_active_agent_is_not_interrupted(sched):
    add(sched)
    sched.clock["t"] = at("06:01")
    schedule.tick(sched.ctx)
    first = argv(sched)
    sched.tmux.sessions["notes/nightly"]["activity"] = int(at("17:58"))
    sched.clock["t"] = at("18:01")
    schedule.tick(sched.ctx)
    assert argv(sched) == first
    [s] = schedule.load(sched.ctx.root)
    assert "skipped" in s.last_result and s.last_fired == sched.clock["t"]


def test_an_attached_agent_is_not_interrupted(sched):
    add(sched)
    sched.clock["t"] = at("06:01")
    schedule.tick(sched.ctx)
    first = argv(sched)
    sched.tmux.sessions["notes/nightly"]["attached"] = True
    sched.clock["t"] = at("18:01")
    schedule.tick(sched.ctx)
    assert argv(sched) == first
    assert "attached" in schedule.load(sched.ctx.root)[0].last_result


def test_a_failed_start_is_recorded_and_other_schedules_still_run(sched):
    add(sched)
    add(sched, key="notes/other")
    sched.tmux.die_with = "Error: nope"
    sched.clock["t"] = at("06:01")
    schedule.tick(sched.ctx)
    results = {s.key: s.last_result for s in schedule.load(sched.ctx.root)}
    assert "failed" in results["notes/nightly"] and results["notes/other"].endswith("started")


def test_a_project_that_went_away_is_recorded_not_raised(sched):
    add(sched)
    data = (sched.ctx.root / "schedules.json").read_text().replace("notes/nightly", "gone/nightly")
    (sched.ctx.root / "schedules.json").write_text(data)
    sched.clock["t"] = at("06:01")
    assert "failed" in schedule.tick(sched.ctx)[0]


def test_removing_keeps_the_agent(sched):
    add(sched)
    schedule.run_now(sched.ctx, "notes/nightly")
    schedule.remove(sched.ctx, "notes/nightly")
    assert schedule.load(sched.ctx.root) == []
    place, name = service.place_for(sched.ctx, key="notes/nightly")
    assert service.lookup(sched.ctx, place, name).key == "notes/nightly"


# --- agentctl ----------------------------------------------------------------

def run(world, capsys, *args):
    code = cli.main(list(args), ctx=world.ctx)
    return (code, *capsys.readouterr())


def test_cli_add_ls_run_pause_rm(sched, capsys, tmp_path):
    prompt = tmp_path / "p.md"
    prompt.write_text("From a file")
    code, out, _ = run(sched, capsys, "schedule", "add", "notes/nightly", "--at", "6:00",
                       "--prompt-file", str(prompt))
    assert code == 0 and "notes/nightly at 06:00" in out
    assert "notes/nightly" in run(sched, capsys, "schedule", "ls")[1]
    assert "started" in run(sched, capsys, "schedule", "run", "notes/nightly")[1]
    assert argv(sched)[-1].endswith("From a file")
    assert run(sched, capsys, "schedule", "pause", "notes/nightly")[0] == 0
    assert "paused" in run(sched, capsys, "schedule", "ls")[1]
    assert run(sched, capsys, "schedule", "rm", "notes/nightly")[0] == 0
    assert run(sched, capsys, "schedule", "ls")[1].strip() == ""


def test_cli_needs_exactly_one_prompt(sched, capsys):
    code, _, err = run(sched, capsys, "schedule", "add", "notes/x", "--at", "6:00")
    assert code == AgentError.INVALID and "--prompt" in err


def test_cli_unknown_schedule_exit_code(sched, capsys):
    assert run(sched, capsys, "schedule", "run", "notes/none")[0] == AgentError.UNKNOWN
