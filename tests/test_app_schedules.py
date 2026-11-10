import pytest
from fastapi.testclient import TestClient

from launcher.agents import schedule, service
from launcher.app import create_app

from agents.conftest import FakeTmux, make_repo
from test_app import FakeAuth, FakeHost


@pytest.fixture
def world(tmp_path):
    clock = {"t": 1_000_000.0}
    base = tmp_path / "projects"
    make_repo(base / "notes")
    home = tmp_path / "home"
    ctx = service.Ctx(root=home / ".config" / "agents", base_dir=base, home=home,
                      run=FakeTmux(clock), now=lambda: clock["t"], sleep=lambda s: None,
                      trust=lambda p: None)

    def client(on=True):
        return TestClient(create_app(
            base_dir_fn=lambda: base, ctx_fn=lambda: ctx, auth=FakeAuth(),
            host=FakeHost(""), run_user=None, github_on=False, schedules_on=on),
            follow_redirects=False)
    client.ctx = ctx
    return client


FORM = {"project": "notes", "name": "nightly", "times": "6:00, 18:00",
        "prompt": "Do <b>it</b>", "mode": "auto", "model": ""}


def test_off_means_no_link_and_no_pages(world):
    c = world(on=False)
    assert "/schedules" not in c.get("/").text
    assert c.get("/schedules").status_code == 404
    assert c.post("/schedules", data=FORM).status_code == 404
    assert c.post("/schedules/notes/nightly/run").status_code == 404
    assert schedule.load(world.ctx.root) == []


def test_on_links_the_page(world):
    assert 'href="/schedules"' in world().get("/").text


def test_save_and_list_escaped(world):
    c = world()
    r = c.post("/schedules", data=FORM)
    assert r.status_code == 303
    html = c.get("/schedules").text
    assert "notes/nightly" in html and "06:00, 18:00" in html
    assert "<b>it</b>" not in html and "&lt;b&gt;it&lt;/b&gt;" in html
    assert "Has not run yet." in html


def test_bad_form_stays_on_the_page_with_what_was_typed(world):
    r = world().post("/schedules", data={**FORM, "times": "soon"})
    assert r.status_code == 200 and "not a time of day" in r.text
    assert 'value="nightly"' in r.text and schedule.load(world.ctx.root) == []


def test_main_is_refused_on_the_page(world):
    r = world().post("/schedules", data={**FORM, "name": "main"})
    assert r.status_code == 200 and "own" in r.text


def test_edit_fills_the_form(world):
    c = world()
    c.post("/schedules", data=FORM)
    html = c.get("/schedules", params={"edit": "notes/nightly"}).text
    assert 'value="nightly"' in html and 'value="06:00, 18:00"' in html


def test_run_now_starts_the_agent_and_says_so(world):
    c = world()
    c.post("/schedules", data=FORM)
    r = c.post("/schedules/notes/nightly/run")
    assert r.status_code == 200 and "started" in r.text
    assert "notes/nightly" in world.ctx.run.sessions


def test_pause_resume_and_delete(world):
    c = world()
    c.post("/schedules", data=FORM)
    c.post("/schedules/notes/nightly/pause")
    assert "paused" in c.get("/schedules").text
    c.post("/schedules/notes/nightly/resume")
    assert schedule.load(world.ctx.root)[0].enabled
    c.post("/schedules/notes/nightly/delete")
    assert schedule.load(world.ctx.root) == []


def test_unknown_action_is_not_found(world):
    c = world()
    c.post("/schedules", data=FORM)
    assert c.post("/schedules/notes/nightly/explode").status_code == 404


def test_write_from_another_site_is_refused(world):
    r = world().post("/schedules", data=FORM, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and schedule.load(world.ctx.root) == []
