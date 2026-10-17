import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from launcher.agents import registry, service
from launcher.app import create_app

from agents.conftest import FakeTmux, make_repo


class Host:
    def memory(self): return ""
    def disk(self): return ""


class Auth:
    def status(self): return True


class Git:
    def blocks_remote_control(self, cwd): return False
    def rename_origin(self, cwd): return None


@pytest.fixture
def app_world(tmp_path):
    clock = {"t": 1_000_000.0}
    tmux = FakeTmux(clock)
    base = tmp_path / "projects"
    repo = make_repo(base / "notes")
    make_repo(base / "wiki")
    home = tmp_path / "home"
    ids = iter(f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 1000))
    ctx = service.Ctx(root=home / ".config" / "agents", base_dir=base, home=home, run=tmux,
                      now=lambda: clock["t"], sleep=lambda s: None, trust=lambda p: None,
                      new_id=lambda: next(ids))
    app = create_app(base_dir_fn=lambda: base, ctx_fn=lambda: ctx, auth=Auth(), git=Git(),
                     host=Host(), run_user=None)
    return types.SimpleNamespace(client=TestClient(app), ctx=ctx, tmux=tmux, repo=repo, clock=clock)


def rows(w):
    return {r["slug"]: r["html"] for r in w.client.get("/api/projects").json()["projects"]}


def test_page_lists_projects_with_main_agents(app_world):
    r = rows(app_world)
    assert set(r) == {"notes", "notes/main", "wiki", "wiki/main"}
    assert 'data-state="stopped"' in r["notes/main"]


def test_start_and_stop_agent(app_world):
    data = app_world.client.post("/api/agents/notes/main/start").json()
    html = {p["slug"]: p["html"] for p in data["projects"]}["notes/main"]
    assert 'data-state="connected"' in html
    assert "notes·main" in app_world.tmux.sessions
    app_world.client.post("/api/agents/notes/main/stop")
    assert "notes·main" not in app_world.tmux.sessions


def test_new_agent_and_scratch(app_world):
    app_world.client.post("/api/projects/notes/new", data={"name": "drafts"})
    assert "notes/drafts" in rows(app_world)
    app_world.client.post("/api/projects/notes/scratch")
    assert any(k.startswith("notes/scratch-") for k in rows(app_world))


def test_bad_name_shows_note(app_world):
    r = app_world.client.post("/api/projects/notes/new", data={"name": "Bad Name"}).json()
    html = {p["slug"]: p["html"] for p in r["projects"]}["notes"]
    assert "not a valid agent name" in html


def test_fresh_with_stale_brief_asks_again(app_world):
    import os
    from launcher.agents import briefs
    app_world.client.post("/api/agents/notes/main/start")
    a = registry.load(app_world.ctx.root)["notes/main"]
    os.utime(briefs.path(app_world.ctx.root, a), (1, 1))
    app_world.tmux.sessions["notes·main"]["activity"] = int(app_world.clock["t"])
    r = app_world.client.post("/api/agents/notes/main/fresh").json()
    html = {p["slug"]: p["html"] for p in r["projects"]}["notes/main"]
    assert "Fresh anyway" in html
    old = a.session_id
    app_world.client.post("/api/agents/notes/main/fresh", data={"force": "1"})
    assert registry.load(app_world.ctx.root)["notes/main"].session_id != old


def test_parked_rows_are_marked_and_counted(app_world):
    app_world.client.post("/api/agents/notes/main/start")
    app_world.client.post("/api/agents/notes/main/park")
    data = app_world.client.get("/api/projects").json()
    assert data["parked"] == 1
    html = {p["slug"]: p["html"] for p in data["projects"]}["notes/main"]
    assert 'class="row agent parked"' in html


def test_mode_chip(app_world):
    app_world.client.post("/api/agents/notes/main/mode", data={"mode": "plan"})
    assert registry.load(app_world.ctx.root)["notes/main"].mode == "plan"


def test_pane(app_world):
    app_world.client.post("/api/agents/notes/main/start")
    lines = app_world.client.get("/api/pane/notes/main").json()["lines"]
    from launcher.agents import runtime
    assert lines[-1] == runtime.CONNECTED_SAMPLE


def test_unknown_project_is_harmless(app_world):
    assert app_world.client.post("/api/agents/nope/main/start").status_code == 200


def test_tmux_that_does_not_answer_still_renders_the_page(app_world):
    # A wedged agents server raises from runtime.sessions(); the page must
    # still list the projects and say what went wrong, not answer 500.
    real = app_world.tmux

    def wedged(cmd, timeout=None):
        if "list-sessions" in cmd:
            import subprocess
            return subprocess.CompletedProcess(cmd, 124, stdout="", stderr="tmux timed out")
        return real(cmd, timeout)

    app_world.ctx.run = wedged
    resp = app_world.client.get("/api/projects")
    assert resp.status_code == 200
    data = resp.json()
    r = {p["slug"]: p["html"] for p in data["projects"]}
    assert set(r) == {"~agents", "notes", "wiki"}
    assert "tmux timed out" in r["~agents"]
    assert data["projects"][0]["slug"] == "~agents"
    page = app_world.client.get("/")
    assert page.status_code == 200 and "tmux timed out" in page.text


def test_stale_marker_expires_with_its_note(app_world):
    import os
    from launcher.agents import briefs
    app_world.client.post("/api/agents/notes/main/start")
    a = registry.load(app_world.ctx.root)["notes/main"]
    os.utime(briefs.path(app_world.ctx.root, a), (1, 1))
    r = app_world.client.post("/api/agents/notes/main/fresh").json()
    assert "Fresh anyway" in {p["slug"]: p["html"] for p in r["projects"]}["notes/main"]
    html = rows(app_world)["notes/main"]
    assert "Fresh anyway" not in html and "Fresh conversation" in html


def test_corrupt_registry_still_renders_the_page(app_world):
    root = app_world.ctx.root
    root.mkdir(parents=True, exist_ok=True)
    (root / registry.FILENAME).write_text("{not json")
    resp = app_world.client.get("/api/projects")
    assert resp.status_code == 200
    r = {p["slug"]: p["html"] for p in resp.json()["projects"]}
    assert ".bak" in r["~agents"] and {"notes", "wiki"} <= set(r)
    assert app_world.client.get("/").status_code == 200


def test_clean_exit_row_is_stopped_not_failed(app_world):
    app_world.client.post("/api/agents/notes/main/start")
    app_world.tmux.sessions["notes·main"].update(dead=True, exit_status=0)
    html = rows(app_world)["notes/main"]
    assert 'data-state="stopped"' in html and "Start failed" not in html
    assert "Crashed" not in html and "Show output" not in html


def test_crash_row_is_red_with_exit_code_and_output(app_world):
    app_world.client.post("/api/agents/notes/main/start")
    app_world.tmux.sessions["notes·main"].update(dead=True, exit_status=3,
                                                 pane="Error: API went away\n")
    html = rows(app_world)["notes/main"]
    assert 'data-state="failed"' in html and "Crashed (exit 3)" in html
    assert "Show output" in html
    lines = app_world.client.get("/api/pane/notes/main").json()["lines"]
    assert lines == ["Error: API went away"]
