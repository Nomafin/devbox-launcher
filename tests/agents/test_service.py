import os
import shlex
from pathlib import Path

import pytest

from launcher.agents import briefs, registry, runtime, service
from launcher.agents.model import (ACTIVE, ARCHIVED, MAIN, PARKED, SCRATCH, STOPPED,
                                   AgentError)

from .conftest import git

DAY = 86400


def argv(w, display):
    return shlex.split(w.tmux.sessions[display]["command"])


def test_start_main_in_checkout(world):
    a = service.start(world.ctx, world.place, MAIN)
    assert a.state == ACTIVE and a.display == "notes/main"
    s = world.tmux.sessions["notes/main"]
    assert s["cwd"] == str(world.repo)
    assert s["env"]["AGENT_NAME"] == "notes/main"
    assert "--session-id" in argv(world, "notes/main")
    assert world.trusted == [str(world.repo)]
    assert registry.load(world.ctx.root)["notes/main"].session_id == a.session_id


def test_start_twice_is_a_noop(world):
    service.start(world.ctx, world.place, MAIN)
    calls = len([c for c in world.tmux.calls if "new-session" in c])
    service.start(world.ctx, world.place, MAIN)
    assert len([c for c in world.tmux.calls if "new-session" in c]) == calls


def test_new_creates_worktree_on_agent_branch(world):
    a = service.new(world.ctx, world.place, "drafts")
    wt = world.repo / ".claude" / "worktrees" / "drafts"
    assert a.workdir == str(wt) and wt.is_dir()
    assert git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "agent/drafts"
    assert briefs.path(world.ctx.root, a).exists()


def test_new_refuses_duplicate_and_reserved(world):
    service.new(world.ctx, world.place, "drafts")
    with pytest.raises(AgentError) as e:
        service.new(world.ctx, world.place, "drafts")
    assert e.value.code == AgentError.EXISTS
    with pytest.raises(AgentError):
        service.new(world.ctx, world.place, "main")


def test_unknown_agent_and_create(world):
    with pytest.raises(AgentError) as e:
        service.start(world.ctx, world.place, "drafts")
    assert e.value.code == AgentError.UNKNOWN
    a = service.start(world.ctx, world.place, "drafts", create=True)
    assert world.tmux.sessions["notes/drafts"]["cwd"] == a.workdir


def test_stop_then_start_resumes(world):
    a = service.start(world.ctx, world.place, MAIN)
    service.stop(world.ctx, world.place, MAIN)
    assert "notes/main" not in world.tmux.sessions
    assert registry.load(world.ctx.root)["notes/main"].state == STOPPED
    service.start(world.ctx, world.place, MAIN)
    cmd = argv(world, "notes/main")
    assert cmd[cmd.index("--resume") + 1] == a.session_id


def test_fresh_refuses_stale_brief_then_force(world):
    a = service.start(world.ctx, world.place, MAIN)
    os.utime(briefs.path(world.ctx.root, a), (world.clock["t"], world.clock["t"]))
    world.clock["t"] += 3600
    world.tmux.sessions["notes/main"]["activity"] = int(world.clock["t"])
    with pytest.raises(AgentError) as e:
        service.start(world.ctx, world.place, MAIN, fresh=True)
    assert e.value.code == AgentError.STALE_BRIEF
    b = service.start(world.ctx, world.place, MAIN, fresh=True, force=True)
    assert b.session_id != a.session_id
    cmd = argv(world, "notes/main")
    assert "--append-system-prompt-file" in cmd


def test_fresh_after_handoff_is_not_stale(world):
    a = service.start(world.ctx, world.place, MAIN)
    world.clock["t"] += 3600
    p = briefs.path(world.ctx.root, a)
    os.utime(p, (world.clock["t"], world.clock["t"]))             # /handoff wrote it
    world.tmux.sessions["notes/main"]["activity"] = int(world.clock["t"] + 30)  # its reply
    service.start(world.ctx, world.place, MAIN, fresh=True)


def test_start_failure_records_error_and_hints_fresh(world):
    service.start(world.ctx, world.place, MAIN)
    service.stop(world.ctx, world.place, MAIN)
    world.tmux.die_with = "Error: No conversation found with session ID: x\n"
    with pytest.raises(AgentError) as e:
        service.start(world.ctx, world.place, MAIN)
    assert e.value.code == AgentError.START_FAILED
    assert "--fresh" in str(e.value)
    a = registry.load(world.ctx.root)["notes/main"]
    assert a.state == STOPPED and "No conversation found" in a.last_error
    assert "notes/main" not in world.tmux.sessions


def test_dead_corpse_is_cleared_on_start(world):
    service.start(world.ctx, world.place, MAIN)
    world.tmux.sessions["notes/main"]["dead"] = True
    service.start(world.ctx, world.place, MAIN)
    assert world.tmux.sessions["notes/main"]["dead"] is False


def test_archive_refuses_uncommitted_then_succeeds(world):
    a = service.new(world.ctx, world.place, "drafts")
    (Path(a.workdir) / "README").write_text("changed\n")
    with pytest.raises(AgentError) as e:
        service.archive(world.ctx, world.place, "drafts")
    assert e.value.code == AgentError.REFUSED and Path(a.workdir).is_dir()
    git(a.workdir, "checkout", "README")
    git(world.repo, "merge", "-q", "agent/drafts")
    b = service.archive(world.ctx, world.place, "drafts")
    assert b.state == ARCHIVED and not Path(a.workdir).exists()
    assert not briefs.path(world.ctx.root, a).exists()
    assert list((world.ctx.root / "archive" / "notes").glob("drafts-*.md"))


def test_main_cannot_be_archived(world):
    with pytest.raises(AgentError) as e:
        service.archive(world.ctx, world.place, MAIN)
    assert e.value.code == AgentError.REFUSED


def test_broken_worktree_and_repair(world):
    a = service.new(world.ctx, world.place, "drafts")
    import shutil
    shutil.rmtree(a.workdir)
    v = [v for v in service.views(world.ctx) if v.agent.name == "drafts"][0]
    assert v.broken
    with pytest.raises(AgentError) as e:
        service.start(world.ctx, world.place, "drafts")
    assert e.value.code == AgentError.BROKEN
    service.start(world.ctx, world.place, "drafts", repair=True)
    assert Path(a.workdir).is_dir()


def test_set_mode_restarts_running_with_resume(world):
    a = service.start(world.ctx, world.place, MAIN)
    service.set_mode(world.ctx, world.place, MAIN, "plan")
    cmd = argv(world, "notes/main")
    assert cmd[cmd.index("--permission-mode") + 1] == "plan"
    assert cmd[cmd.index("--resume") + 1] == a.session_id
    service.set_mode(world.ctx, world.place, MAIN, "default")
    assert registry.load(world.ctx.root)["notes/main"].mode is None


def test_scratch_names_and_kind(world):
    a = service.scratch(world.ctx, world.place)
    b = service.scratch(world.ctx, world.place)
    assert a.kind == SCRATCH and a.workdir == str(world.repo)
    assert (a.name[:8], a.name[-2:], b.name[-2:]) == ("scratch-", "-1", "-2")
    assert "AGENT_BRIEF" not in world.tmux.sessions[a.display]["env"]


def test_reap_parks_idle_agents(world):
    service.start(world.ctx, world.place, MAIN)
    world.clock["t"] += 3 * DAY + 1
    done = service.reap(world.ctx)
    assert done == ["parked notes/main"]
    assert registry.load(world.ctx.root)["notes/main"].state == PARKED
    assert "notes/main" not in world.tmux.sessions


def test_reap_never_touches_attached(world):
    service.start(world.ctx, world.place, MAIN)
    world.tmux.sessions["notes/main"]["attached"] = True
    world.clock["t"] += 10 * DAY
    assert service.reap(world.ctx) == []


def test_reap_counts_transcript_activity(world):
    a = service.start(world.ctx, world.place, MAIN)
    world.clock["t"] += 3 * DAY + 1
    d = world.home / ".claude" / "projects" / "-x"
    d.mkdir(parents=True)
    f = d / f"{a.session_id}.jsonl"
    f.write_text("{}")
    os.utime(f, (world.clock["t"] - 60, world.clock["t"] - 60))
    assert service.reap(world.ctx) == []


def test_reap_deletes_idle_scratch(world):
    a = service.scratch(world.ctx, world.place)
    world.clock["t"] += 12 * 3600 + 1
    assert service.reap(world.ctx) == [f"deleted {a.display}"]
    assert a.key not in registry.load(world.ctx.root)


def test_restore_restarts_active_agents(world):
    a = service.start(world.ctx, world.place, MAIN)
    world.tmux.sessions.clear()                       # reboot
    assert service.restore(world.ctx) == ["restored notes/main"]
    cmd = argv(world, "notes/main")
    assert cmd[cmd.index("--resume") + 1] == a.session_id


def test_views_synthesize_main(world):
    vs = service.views(world.ctx, places=[world.place])
    assert [(v.agent.key, v.running) for v in vs] == [("notes/main", False)]


def test_place_for_dir_alias(world):
    place, name = service.place_for(world.ctx, dir=world.repo, name="notes")
    assert (place.slug, name) == ("notes", MAIN)


def test_failed_first_start_keeps_no_session_id(world):
    world.tmux.die_with = "boom\n"
    with pytest.raises(AgentError):
        service.start(world.ctx, world.place, MAIN)
    assert registry.load(world.ctx.root)["notes/main"].session_id is None
    world.tmux.die_with = None
    service.start(world.ctx, world.place, MAIN)
    cmd = argv(world, "notes/main")
    assert "--session-id" in cmd and "--resume" not in cmd


def test_failed_fresh_start_keeps_old_session_id(world):
    a = service.start(world.ctx, world.place, MAIN)
    service.stop(world.ctx, world.place, MAIN)
    world.tmux.die_with = "boom\n"
    with pytest.raises(AgentError):
        service.start(world.ctx, world.place, MAIN, fresh=True, force=True)
    assert registry.load(world.ctx.root)["notes/main"].session_id == a.session_id
    world.tmux.die_with = None
    service.start(world.ctx, world.place, MAIN)
    cmd = argv(world, "notes/main")
    assert cmd[cmd.index("--resume") + 1] == a.session_id


def test_fresh_on_never_started_agent_skips_stale_check(world):
    a = service.start(world.ctx, world.place, MAIN, fresh=True)
    assert a.state == ACTIVE


# --- the visible screen, not the scrollback ----------------------------------

TRUST = f"Quick safety check: {runtime.TRUST_PROMPT}?\n❯ No, exit\n  Yes, I trust this folder\n"


def test_resume_with_trust_prompt_only_in_scrollback_sends_no_keys(world):
    service.start(world.ctx, world.place, MAIN)
    service.stop(world.ctx, world.place, MAIN)
    world.tmux.on_start = lambda s: s.update(pane=TRUST + runtime.CONNECTED_SAMPLE,
                                             screen=runtime.CONNECTED_SAMPLE)
    service.start(world.ctx, world.place, MAIN)
    assert world.tmux.keys == []


def test_trust_prompt_on_screen_is_answered_once(world):
    world.tmux.on_start = lambda s: s.update(pane=TRUST, screen=TRUST)
    service.start(world.ctx, world.place, MAIN)
    assert [k[-2:] for k in world.tmux.keys] == [["Up", "Enter"]]


def test_connected_reads_the_screen_not_the_scrollback(world):
    service.start(world.ctx, world.place, MAIN)
    world.tmux.sessions["notes/main"].update(pane=runtime.CONNECTED_SAMPLE + "\nreconnecting",
                                             screen="reconnecting")
    v = [v for v in service.views(world.ctx, check_connected=True) if v.agent.name == MAIN][0]
    assert v.connected is False


def test_dead_on_start_reason_reads_the_scrollback(world):
    world.tmux.die_with = "Error: boom\n\n\n"
    world.tmux.on_start = lambda s: s.update(screen="\n\n")
    with pytest.raises(AgentError) as e:
        service.start(world.ctx, world.place, MAIN)
    assert "Error: boom" in str(e.value)


# --- activity ----------------------------------------------------------------

def test_reap_counts_pane_output(world):
    # After /clear the recorded transcript goes quiet; the pane still moves.
    service.start(world.ctx, world.place, MAIN)
    world.clock["t"] += 3 * DAY + 1
    world.tmux.sessions["notes/main"]["window_activity"] = int(world.clock["t"] - 60)
    assert service.reap(world.ctx) == []
    assert registry.load(world.ctx.root)["notes/main"].state == ACTIVE


def test_resume_does_not_make_a_handed_off_brief_stale(world):
    a = service.start(world.ctx, world.place, MAIN)
    world.clock["t"] += 3600
    p = briefs.path(world.ctx.root, a)
    os.utime(p, (world.clock["t"], world.clock["t"]))             # /handoff wrote it
    service.stop(world.ctx, world.place, MAIN)
    world.clock["t"] += 3600
    service.start(world.ctx, world.place, MAIN)                   # Resume, nothing typed
    world.clock["t"] += 300
    assert service.brief_status(world.ctx, world.place, MAIN)["stale"] is False


# --- a woken agent ------------------------------------------------------------

def test_woken_agent_is_not_re_parked_by_the_next_reap(world):
    service.start(world.ctx, world.place, MAIN)
    world.clock["t"] += 4 * DAY
    assert service.reap(world.ctx) == ["parked notes/main"]
    service.start(world.ctx, world.place, MAIN)                   # Start wakes it
    world.clock["t"] += 600
    assert service.reap(world.ctx) == []
    a = registry.load(world.ctx.root)["notes/main"]
    assert a.state == ACTIVE and a.last_started == world.clock["t"] - 600


def test_reap_does_not_turn_a_start_into_brief_activity(world):
    a = service.start(world.ctx, world.place, MAIN)
    p = briefs.path(world.ctx.root, a)
    os.utime(p, (world.clock["t"], world.clock["t"]))
    service.stop(world.ctx, world.place, MAIN)
    world.clock["t"] += 3600
    service.start(world.ctx, world.place, MAIN)
    world.clock["t"] += 600
    service.reap(world.ctx)                                       # stores last_active
    assert service.brief_status(world.ctx, world.place, MAIN)["stale"] is False


# --- a pane that exited by itself: /exit (status 0) or a crash ----------------

def _exit(world, status):
    world.tmux.sessions["notes/main"].update(dead=True, exit_status=status,
                                             pane="Error: API went away\n")


def _view(world):
    return [v for v in service.views(world.ctx) if v.agent.name == MAIN][0]


def test_clean_exit_reads_as_stopped(world):
    service.start(world.ctx, world.place, MAIN)
    _exit(world, 0)
    v = _view(world)
    assert (v.running, v.dead, v.exit_status) == (False, False, 0)


def test_crash_reads_as_crashed(world):
    service.start(world.ctx, world.place, MAIN)
    _exit(world, 3)
    v = _view(world)
    assert (v.running, v.dead, v.exit_status) == (False, True, 3)


def test_reap_clears_a_clean_exit_so_restore_skips_it(world):
    service.start(world.ctx, world.place, MAIN)
    _exit(world, 0)
    assert service.reap(world.ctx) == ["cleared notes/main"]
    assert "notes/main" not in world.tmux.sessions
    assert registry.load(world.ctx.root)["notes/main"].state == STOPPED
    assert service.restore(world.ctx) == []


def test_reap_leaves_a_crash_in_place(world):
    service.start(world.ctx, world.place, MAIN)
    _exit(world, 3)
    world.clock["t"] += 10 * DAY
    assert service.reap(world.ctx) == []
    assert world.tmux.sessions["notes/main"]["dead"] is True
    assert registry.load(world.ctx.root)["notes/main"].state == ACTIVE


def test_restore_resumes_a_crashed_agent(world):
    a = service.start(world.ctx, world.place, MAIN)
    _exit(world, 3)
    assert service.restore(world.ctx) == ["restored notes/main"]
    assert world.tmux.sessions["notes/main"]["dead"] is False
    cmd = argv(world, "notes/main")
    assert cmd[cmd.index("--resume") + 1] == a.session_id


def test_restore_clears_a_clean_exit_instead_of_reopening_it(world):
    service.start(world.ctx, world.place, MAIN)
    _exit(world, 0)
    assert service.restore(world.ctx) == ["cleared notes/main"]
    assert "notes/main" not in world.tmux.sessions
    assert registry.load(world.ctx.root)["notes/main"].state == STOPPED
