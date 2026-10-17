import json
import threading
import time

import pytest

from launcher.agents import registry
from launcher.agents.model import Agent, AgentError


def agent(name="drafts"):
    return Agent(project="notes", name=name, cwd="/p", workdir=f"/p/.claude/worktrees/{name}")


def test_empty_when_missing(tmp_path):
    assert registry.load(tmp_path) == {}


def test_transaction_saves_and_backs_up(tmp_path):
    with registry.transaction(tmp_path) as agents:
        agents["notes/drafts"] = agent()
    with registry.transaction(tmp_path) as agents:
        agents["notes/sso"] = agent("sso")
    assert set(registry.load(tmp_path)) == {"notes/drafts", "notes/sso"}
    backup = json.loads((tmp_path / "registry.json.bak").read_text())
    assert [a["name"] for a in backup["agents"]] == ["drafts"]


def test_agent_error_still_saves(tmp_path):
    with pytest.raises(AgentError):
        with registry.transaction(tmp_path) as agents:
            agents["notes/drafts"] = agent()
            raise AgentError(AgentError.START_FAILED, "boom")
    assert "notes/drafts" in registry.load(tmp_path)


def test_other_errors_do_not_save(tmp_path):
    with pytest.raises(RuntimeError):
        with registry.transaction(tmp_path) as agents:
            agents["notes/drafts"] = agent()
            raise RuntimeError("bug")
    assert registry.load(tmp_path) == {}


def test_corrupt_registry_refuses_and_names_backup(tmp_path):
    (tmp_path / "registry.json").write_text("{nope")
    with pytest.raises(AgentError) as e:
        with registry.transaction(tmp_path):
            pass
    assert "registry.json.bak" in str(e.value)
    assert (tmp_path / "registry.json").read_text() == "{nope"


def test_transaction_serialises_writers(tmp_path):
    inside = threading.Event()

    def slow_writer():
        with registry.transaction(tmp_path) as agents:
            inside.set()
            time.sleep(0.3)
            agents["notes/drafts"] = agent()

    t = threading.Thread(target=slow_writer)
    t.start()
    inside.wait()
    with registry.transaction(tmp_path) as agents:
        seen = set(agents)
    t.join()
    assert seen == {"notes/drafts"}


def test_home_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path))
    assert registry.home() == tmp_path
