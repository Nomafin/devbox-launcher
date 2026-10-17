import pytest

from launcher.agents.model import (AGENT, MAIN, SCRATCH, Agent, AgentError,
                                   check_mode, check_name, split_key)


def make(**kw):
    base = dict(project="notes-site", name="drafts", cwd="/p/notes", workdir="/p/notes/.claude/worktrees/drafts")
    base.update(kw)
    return Agent(**base)


def test_names():
    a = make()
    assert a.key == "notes-site/drafts"
    assert a.display == "notes-site/drafts"
    assert a.has_worktree and not a.is_main


def test_main_and_scratch_have_no_worktree():
    assert not make(name=MAIN).has_worktree
    assert not make(name="scratch-1016-1", kind=SCRATCH).has_worktree


def test_round_trip_ignores_unknown_keys():
    d = make(session_id="abc").to_dict()
    d["future_field"] = 1
    assert Agent.from_dict(d) == make(session_id="abc")


@pytest.mark.parametrize("bad", ["", "Drafts", "ti tu", "main", "scratch-1", "a--b", "-a"])
def test_check_name_rejects(bad):
    with pytest.raises(AgentError) as e:
        check_name(bad)
    assert e.value.code == AgentError.INVALID


def test_check_name_accepts():
    assert check_name("alumni-stars") == "alumni-stars"


def test_check_mode():
    assert check_mode(None) is None
    assert check_mode("default") is None
    assert check_mode("plan") == "plan"
    with pytest.raises(AgentError):
        check_mode("bypassPermissions")


def test_split_key():
    assert split_key("wiki/live") == ("wiki", "live")
    with pytest.raises(AgentError):
        split_key("wiki")
