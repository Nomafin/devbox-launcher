import logging
import time
import types
from pathlib import Path
from html import escape
from fastapi.testclient import TestClient
from launcher import app as app_module
from launcher.app import create_app
from launcher import discovery
from launcher.tmuxctl import CONNECTED, FAILED, READY, STARTING, STOPPED, STUCK, Status


class FakeTmux:
    CONNECTED, FAILED, READY, STARTING = CONNECTED, FAILED, READY, STARTING
    STUCK, STOPPED = STUCK, STOPPED

    def __init__(self, settles=None, state=CONNECTED):
        self.started, self.stopped, self.running = [], [], set()
        self.resumed = []                    # session_id passed to each start()
        self.settles = list(settles or [])   # what each wait_for_health() returns
        self.state = state                   # what health() reports to render()
        self.url = None
        self.reason = None                   # what a dead pane left behind
        self.dead = set()                    # sessions held open by remain-on-exit
        self.age = None                      # seconds since the session started
        self.tail = []                        # what pane_tail() hands back
        self.spawns = []                      # spawn mode passed to each start()
        self.sessions = None                  # live sessions the pane reports
    def snapshot(self, slugs):
        # The real one answers for the whole list in two tmux calls; the fake
        # just projects its own fields through the same shape.
        out = {}
        for slug in slugs:
            if not self.is_running(slug):
                out[slug] = Status(running=False, state=STOPPED)
                continue
            state = self.health(slug)
            out[slug] = Status(
                running=True, state=state, age=self.age,
                url=self.url if state == CONNECTED else None,
                reason=self.reason if state == FAILED else None,
                sessions=self.sessions)
        return out
    def pane_tail(self, slug, lines=20):
        return list(self.tail)
    def is_running(self, slug):
        if slug in getattr(self, "explode_on", ()):
            raise RuntimeError("boom")
        return slug in self.running
    def start(self, project, session_id=None, spawn="same-dir"):
        if project.slug in getattr(self, "fail_on", ()):
            raise RuntimeError("boom")
        self.started.append(project.slug); self.resumed.append(session_id)
        self.spawns.append(spawn)
        self.running.add(project.slug)
    def stop(self, slug):
        self.stopped.append(slug); self.running.discard(slug); self.dead.discard(slug)
    def is_dead(self, slug):
        return slug in self.dead
    def connect_url(self, slug):
        return self.url
    def health(self, slug):
        return self.state
    def failure_reason(self, slug):
        return self.reason
    def wait_for_health(self, slug):
        return self.settles.pop(0) if len(self.settles) > 1 else (
            self.settles[0] if self.settles else CONNECTED)


class FakeTrust:
    def __init__(self):
        self.seeded = []
    def ensure_trusted(self, path, config_path=None):
        self.seeded.append(str(path)); return True


class FakePointer:
    def __init__(self, sid=None):
        self.cleared = []
        self.sid = sid                       # cloud session recorded by the bridge
    def clear(self, cwd, home=None):
        # Clearing the file removes the session id with it, which is what makes
        # the retry register fresh instead of re-adopting the dead session.
        self.cleared.append(str(cwd)); self.sid = None; return True
    def session_id(self, cwd, home=None):
        return self.sid


class FakeState:
    def __init__(self, running=None, parallel=None):
        self.running = list(running or [])
        self.parallel_slugs = list(parallel or [])
    def parallel(self):
        return list(self.parallel_slugs)
    def set_parallel(self, slug, on):
        if on and slug not in self.parallel_slugs:
            self.parallel_slugs.append(slug)
        if not on and slug in self.parallel_slugs:
            self.parallel_slugs.remove(slug)
    def desired(self):
        return list(self.running)
    def record_started(self, slug):
        if slug not in self.running:
            self.running.append(slug)
    def record_stopped(self, slug):
        if slug in self.running:
            self.running.remove(slug)


class FakeAuth:
    def __init__(self, logged_in=True, url=None, err=None, accepts=False):
        self.logged_in = logged_in
        self.url = url                 # what the CLI has printed so far
        self.err = err                 # a rejected code, if any
        self.accepts = accepts         # whether a submitted code will be taken
        self.started, self.cancelled = 0, 0
        self.baseline = None           # what /login/code passed to wait_for_login
        self.codes = []
        self.running = False
    def status(self):
        return self.logged_in
    def is_active(self):
        return self.running
    def start_login(self):
        self.started += 1; self.running = True
    def authorize_url(self):
        return self.url
    def wait_for_url(self):
        return self.url
    def error(self):
        return self.err
    def errors(self):
        return [self.err] if self.err else []
    def submit_code(self, code):
        self.codes.append(code)
    def wait_for_login(self, baseline=0):
        self.baseline = baseline
        self.logged_in = self.accepts
        return self.accepts
    def cancel(self):
        self.cancelled += 1; self.running = False


class FakeHost:
    def __init__(self, text="4.2 of 12 GB", disk_text=""):
        self.text = text
        self.disk_text = disk_text
    def memory(self):
        return self.text
    def disk(self):
        return self.disk_text


class FakeGit:
    def __init__(self, blocks=False, error=None):
        self.blocks_it = blocks
        self.error = error
        self.renamed = []
    def blocks_remote_control(self, path):
        return self.blocks_it
    def rename_origin(self, path):
        self.renamed.append(str(path))
        self.blocks_it = False
        return self.error


def _mkrepo(base: Path, name: str) -> None:
    (base / name / ".git").mkdir(parents=True)


def make_app(tmp_path, tmx, trust, ptr=None, st=None, auth=None, git=None, host=None):
    _mkrepo(tmp_path, "demo")
    return create_app(base_dir_fn=lambda: tmp_path, tmx=tmx, trust=trust,
                      disc=discovery, ptr=ptr or FakePointer(), st=st or FakeState(),
                      auth=auth or FakeAuth(), git=git or FakeGit(),
                      host=host or FakeHost(""))


def make_client(tmp_path, tmx, trust, ptr=None, st=None, auth=None, git=None, host=None):
    return TestClient(make_app(tmp_path, tmx, trust, ptr, st, auth, git, host))


def test_index_lists_projects_with_status(tmp_path):
    client = make_client(tmp_path, FakeTmux(), FakeTrust())
    body = client.get("/").text
    assert "demo" in body
    assert "Start" in body


def test_start_seeds_trust_then_starts_and_redirects(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    client = make_client(tmp_path, tmx, trust)
    r = client.post("/start/demo", follow_redirects=False)
    assert r.status_code == 303
    assert tmx.started == ["demo"]
    assert trust.seeded and trust.seeded[0].endswith("/demo")


def test_start_seeds_trust_for_group_cwd(tmp_path):
    (tmp_path / "Acme" / "claude-project" / ".git").mkdir(parents=True)
    tmx, trust = FakeTmux(), FakeTrust()
    # go through make_client like every other test here, so this app also gets
    # a FakePointer/FakeState rather than silently touching real host state
    client = make_client(tmp_path, tmx, trust)
    r = client.post("/start/acme", follow_redirects=False)
    assert r.status_code == 303
    assert tmx.started == ["acme"]
    assert trust.seeded and trust.seeded[0].endswith("Acme/claude-project")


def test_start_unknown_slug_does_nothing(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    client = make_client(tmp_path, tmx, trust)
    r = client.post("/start/ghost", follow_redirects=False)
    assert r.status_code == 303
    assert tmx.started == [] and trust.seeded == []


def test_stop_calls_tmux(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    tmx.running.add("demo")
    client = make_client(tmp_path, tmx, trust)
    client.post("/stop/demo", follow_redirects=False)
    assert tmx.stopped == ["demo"]


def test_running_project_shows_stop_and_session_name(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    tmx.running.add("demo")
    client = make_client(tmp_path, tmx, trust)
    body = client.get("/").text
    assert "Stop" in body
    assert "devbox-demo" in body


def test_start_clears_the_pointer_and_retries_when_the_session_fails(tmp_path):
    # archived-environment case: first listener fails, so drop the stale resume
    # pointer and start again — the retry registers a fresh environment
    tmx, trust, ptr = FakeTmux(settles=[FAILED, CONNECTED]), FakeTrust(), FakePointer()
    client = make_client(tmp_path, tmx, trust, ptr)
    client.post("/start/demo", follow_redirects=False)
    assert tmx.started == ["demo", "demo"]
    assert tmx.stopped == ["demo"]
    assert ptr.cleared and ptr.cleared[0].endswith("/demo")


def test_start_keeps_the_pointer_when_the_listener_connects(tmp_path):
    # healthy Stop -> Start must still resume the retained environment
    tmx, trust, ptr = FakeTmux(settles=[CONNECTED]), FakeTrust(), FakePointer()
    client = make_client(tmp_path, tmx, trust, ptr)
    client.post("/start/demo", follow_redirects=False)
    assert tmx.started == ["demo"]
    assert ptr.cleared == []


def test_start_resumes_the_conversation_the_pointer_names(tmp_path):
    # The whole point: after a reboot, Start picks the conversation back up
    # rather than opening a fresh one.
    tmx, trust = FakeTmux(settles=[CONNECTED]), FakeTrust()
    ptr = FakePointer(sid="session_01Xk7P")
    client = make_client(tmp_path, tmx, trust, ptr)
    client.post("/start/demo", follow_redirects=False)
    assert tmx.resumed == ["session_01Xk7P"]


def test_start_registers_fresh_when_no_conversation_is_recorded(tmp_path):
    # First ever start for a project: nothing to resume, so no --session-id.
    tmx, trust, ptr = FakeTmux(settles=[CONNECTED]), FakeTrust(), FakePointer()
    client = make_client(tmp_path, tmx, trust, ptr)
    client.post("/start/demo", follow_redirects=False)
    assert tmx.resumed == [None]


def test_retry_after_a_failed_resume_starts_fresh(tmp_path):
    # Archived-session case: resuming session_01OLD fails, the pointer is
    # dropped, and the retry must NOT try to resume it again — otherwise the
    # retry dies identically and the listener never comes up.
    tmx, trust = FakeTmux(settles=[FAILED, CONNECTED]), FakeTrust()
    ptr = FakePointer(sid="session_01OLD")
    client = make_client(tmp_path, tmx, trust, ptr)
    client.post("/start/demo", follow_redirects=False)
    assert tmx.resumed == ["session_01OLD", None]


def test_start_retries_only_once(tmp_path):
    tmx, trust, ptr = FakeTmux(settles=[FAILED]), FakeTrust(), FakePointer()
    client = make_client(tmp_path, tmx, trust, ptr)
    client.post("/start/demo", follow_redirects=False)
    assert tmx.started == ["demo", "demo"]
    assert len(ptr.cleared) == 1


def test_start_logs_why_the_listener_would_not_come_up(tmp_path, caplog):
    # the failure used to leave no trace at all: journalctl showed a plain
    # "POST /start/demo 303" and nothing else, for 15s of doing nothing
    tmx, trust = FakeTmux(settles=[FAILED]), FakeTrust()
    tmx.reason = "Error: You must be logged in to use Remote Control."
    client = make_client(tmp_path, tmx, trust)
    with caplog.at_level(logging.WARNING):
        client.post("/start/demo", follow_redirects=False)
    assert "demo" in caplog.text
    assert "You must be logged in" in caplog.text


def test_start_does_not_log_a_listener_that_comes_up(tmp_path, caplog):
    tmx, trust = FakeTmux(settles=[CONNECTED]), FakeTrust()
    client = make_client(tmp_path, tmx, trust)
    with caplog.at_level(logging.WARNING):
        client.post("/start/demo", follow_redirects=False)
    assert caplog.text == ""


def test_connected_listener_shows_healthy_with_its_connect_url(tmp_path):
    tmx, trust = FakeTmux(state=CONNECTED), FakeTrust()
    tmx.running.add("demo")
    tmx.url = "https://claude.ai/code?environment=env_01ABC"
    client = make_client(tmp_path, tmx, trust)
    body = client.get("/").text
    assert 'data-slug="demo" data-state="connected"' in body
    # the link is the point of a live row: it has to be tappable, not printed
    assert 'href="https://claude.ai/code?environment=env_01ABC"' in body


def test_failed_listener_is_not_shown_as_healthy(tmp_path):
    tmx, trust = FakeTmux(state=FAILED), FakeTrust()
    tmx.running.add("demo")
    client = make_client(tmp_path, tmx, trust)
    body = client.get("/").text
    assert 'data-slug="demo" data-state="connected"' not in body
    assert 'data-slug="demo" data-state="failed"' in body
    assert "Start failed" in body


def test_failed_listener_shows_why_it_failed(tmp_path):
    # the whole point of keeping the dead pane: "Stop, then Start to retry" is
    # useless advice when the real problem is that the box is logged out
    tmx, trust = FakeTmux(state=FAILED), FakeTrust()
    tmx.running.add("demo")
    tmx.reason = "Error: You must be logged in to use Remote Control."
    client = make_client(tmp_path, tmx, trust)
    body = client.get("/").text
    assert "You must be logged in" in body


def test_failed_listener_without_a_reason_still_renders(tmp_path):
    tmx, trust = FakeTmux(state=FAILED), FakeTrust()
    tmx.running.add("demo")
    client = make_client(tmp_path, tmx, trust)
    body = client.get("/").text
    assert "Start failed" in body
    assert "None" not in body


def test_failure_reason_is_escaped(tmp_path):
    tmx, trust = FakeTmux(state=FAILED), FakeTrust()
    tmx.running.add("demo")
    tmx.reason = "Error: <script>alert(1)</script>"
    client = make_client(tmp_path, tmx, trust)
    body = client.get("/").text
    # the page carries its own <script> now, so pin the assertion to the reason
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_registered_listener_without_a_session_is_flagged(tmp_path):
    tmx, trust = FakeTmux(state=READY), FakeTrust()
    tmx.running.add("demo")
    client = make_client(tmp_path, tmx, trust)
    body = client.get("/").text
    assert 'data-slug="demo" data-state="connected"' not in body
    assert "No session yet" in body


def test_starting_listener_says_so(tmp_path):
    tmx, trust = FakeTmux(state=STARTING), FakeTrust()
    tmx.running.add("demo")
    client = make_client(tmp_path, tmx, trust)
    body = client.get("/").text
    assert "starting" in body.lower()


def test_start_records_that_the_project_should_be_running(tmp_path):
    tmx, trust, st = FakeTmux(), FakeTrust(), FakeState()
    client = make_client(tmp_path, tmx, trust, st=st)
    client.post("/start/demo", follow_redirects=False)
    assert st.desired() == ["demo"]


def test_start_records_intent_even_when_the_listener_fails(tmp_path):
    # a project that failed to start is still one the user wants running
    tmx, trust, st = FakeTmux(settles=[FAILED]), FakeTrust(), FakeState()
    client = make_client(tmp_path, tmx, trust, st=st)
    client.post("/start/demo", follow_redirects=False)
    assert st.desired() == ["demo"]


def test_stop_records_that_the_project_should_not_be_running(tmp_path):
    tmx, trust, st = FakeTmux(), FakeTrust(), FakeState(["demo"])
    tmx.running.add("demo")
    client = make_client(tmp_path, tmx, trust, st=st)
    client.post("/stop/demo", follow_redirects=False)
    assert st.desired() == []


def test_start_of_an_unknown_slug_records_nothing(tmp_path):
    tmx, trust, st = FakeTmux(), FakeTrust(), FakeState()
    client = make_client(tmp_path, tmx, trust, st=st)
    client.post("/start/ghost", follow_redirects=False)
    assert st.desired() == []


def test_reconcile_starts_a_desired_listener_that_is_not_running(tmp_path):
    tmx, trust, st = FakeTmux(), FakeTrust(), FakeState(["demo"])
    app = make_app(tmp_path, tmx, trust, st=st)
    app.state.reconcile()
    assert tmx.started == ["demo"]


def test_reconcile_skips_a_listener_that_is_already_running(tmp_path):
    # normal case after a redeploy: listeners survive, nothing to do
    tmx, trust, st = FakeTmux(), FakeTrust(), FakeState(["demo"])
    tmx.running.add("demo")
    app = make_app(tmp_path, tmx, trust, st=st)
    app.state.reconcile()
    assert tmx.started == []


def test_reconcile_replaces_a_listener_whose_session_is_dead(tmp_path):
    # remain-on-exit means a failed listener leaves its session behind, so
    # has-session alone would report it as running and restore would skip it
    # for good. The service outlives the tmux server (KillMode=process), so a
    # redeploy is exactly when this pass has to notice.
    tmx, trust, st = FakeTmux(), FakeTrust(), FakeState(["demo"])
    tmx.running.add("demo")
    tmx.dead.add("demo")
    app = make_app(tmp_path, tmx, trust, st=st)
    app.state.reconcile()
    assert tmx.started == ["demo"]


def test_launch_clears_a_dead_session_before_starting(tmp_path):
    # tmux refuses a duplicate session name, so the corpse has to go first or
    # the new listener never starts
    tmx, trust = FakeTmux(), FakeTrust()
    tmx.running.add("demo")
    tmx.dead.add("demo")
    client = make_client(tmp_path, tmx, trust)
    client.post("/start/demo", follow_redirects=False)
    assert tmx.stopped == ["demo"]
    assert tmx.started == ["demo"]


def test_reconcile_prunes_a_project_that_no_longer_exists(tmp_path):
    tmx, trust, st = FakeTmux(), FakeTrust(), FakeState(["ghost"])
    app = make_app(tmp_path, tmx, trust, st=st)
    app.state.reconcile()
    assert tmx.started == []
    assert st.desired() == []


def test_reconcile_continues_after_one_project_fails(tmp_path):
    (tmp_path / "other" / ".git").mkdir(parents=True)
    tmx, trust, st = FakeTmux(), FakeTrust(), FakeState(["demo", "other"])
    tmx.fail_on = {"demo"}
    app = make_app(tmp_path, tmx, trust, st=st)
    app.state.reconcile()
    assert tmx.started == ["other"]
    assert st.desired() == ["demo", "other"]   # a failure does not clear intent


def test_reconcile_continues_after_one_project_raises_before_launch(tmp_path):
    # a raise from resolve_slug/is_running (e.g. a permission error walking the
    # projects tree) must not abort the pass any more than a launch() failure does
    (tmp_path / "other" / ".git").mkdir(parents=True)
    tmx, trust, st = FakeTmux(), FakeTrust(), FakeState(["demo", "other"])
    tmx.explode_on = {"demo"}
    app = make_app(tmp_path, tmx, trust, st=st)
    app.state.reconcile()
    assert tmx.started == ["other"]


def test_lifespan_survives_the_reconcile_thread_failing_to_start(tmp_path, monkeypatch):
    # Thread.start() can raise under boot-time resource pressure (e.g. "can't
    # start new thread"). Restore is a best-effort convenience — it must never
    # be able to take the whole web app down with it, or systemd's restart
    # limit can leave the unit permanently dead with no remote way to recover
    # the box.
    class ExplodingThread:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(app_module, "threading",
                         types.SimpleNamespace(Thread=ExplodingThread))

    tmx, trust, st = FakeTmux(), FakeTrust(), FakeState(["demo"])
    app = make_app(tmp_path, tmx, trust, st=st)
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
    # and the listener that would have been restored was never even attempted
    assert tmx.started == []


AUTH_URL = "https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c&state=jYMi"


def test_signed_out_box_says_so_on_the_main_page(tmp_path):
    # the failure this exists for: every Start fails identically and the page
    # gave no hint why. One glance should now answer it.
    auth = FakeAuth(logged_in=False)
    client = make_client(tmp_path, FakeTmux(), FakeTrust(), auth=auth)
    body = client.get("/").text
    assert "signed out" in body.lower()
    assert 'action="/login/start"' in body


def test_signed_in_box_shows_no_banner(tmp_path):
    client = make_client(tmp_path, FakeTmux(), FakeTrust(), auth=FakeAuth(logged_in=True))
    body = client.get("/").text
    assert "signed out" not in body.lower()
    assert "/login/start" not in body


def test_login_start_launches_the_flow_and_shows_the_page(tmp_path):
    auth = FakeAuth(logged_in=False, url=AUTH_URL)
    client = make_client(tmp_path, FakeTmux(), FakeTrust(), auth=auth)
    r = client.post("/login/start", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    assert auth.started == 1


def test_login_start_does_not_restart_a_flow_already_in_progress(tmp_path):
    # restarting throws away the challenge behind the URL the user is already
    # looking at on their phone
    auth = FakeAuth(logged_in=False, url=AUTH_URL)
    auth.running = True
    client = make_client(tmp_path, FakeTmux(), FakeTrust(), auth=auth)
    client.post("/login/start", follow_redirects=False)
    assert auth.started == 0


def test_login_page_offers_the_url_and_somewhere_to_paste_the_code(tmp_path):
    auth = FakeAuth(logged_in=False, url=AUTH_URL)
    client = make_client(tmp_path, FakeTmux(), FakeTrust(), auth=auth)
    body = client.get("/login").text
    assert escape(AUTH_URL) in body
    assert 'action="/login/code"' in body
    assert 'name="code"' in body


def test_login_page_waits_before_the_url_appears(tmp_path):
    auth = FakeAuth(logged_in=False, url=None)
    client = make_client(tmp_path, FakeTmux(), FakeTrust(), auth=auth)
    body = client.get("/login").text
    assert "starting" in body.lower()


def test_login_page_offers_a_fresh_link_when_the_flow_has_exited(tmp_path):
    # "Login failed" ends the process, so the link on the pane is spent
    auth = FakeAuth(logged_in=False, url=AUTH_URL,
                    err="Login failed: Request failed with status code 400")
    auth.running = False
    client = make_client(tmp_path, FakeTmux(), FakeTrust(), auth=auth)
    body = client.get("/login").text
    assert "status code 400" in body
    assert 'action="/login/start"' in body        # and a way to try again
    assert 'name="code"' not in body              # pasting again would go nowhere


def test_login_page_lets_you_paste_again_after_a_malformed_code(tmp_path):
    # "Invalid code" leaves the CLI sitting at the prompt, so the link is still
    # good — sending the user back to square one would throw away a live flow
    auth = FakeAuth(logged_in=False, url=AUTH_URL,
                    err="Invalid code. Please make sure the full code was copied.")
    auth.running = True
    client = make_client(tmp_path, FakeTmux(), FakeTrust(), auth=auth)
    body = client.get("/login").text
    assert "Invalid code" in body
    assert 'name="code"' in body                  # paste box still there
    assert escape(AUTH_URL) in body               # and the same link


def test_submitting_a_code_ignores_an_earlier_rejection(tmp_path):
    # without a baseline the stale "Invalid code" would read as this code failing
    auth = FakeAuth(logged_in=False, url=AUTH_URL, accepts=True,
                    err="Invalid code. Please make sure the full code was copied.")
    auth.running = True
    client = make_client(tmp_path, FakeTmux(), FakeTrust(), auth=auth)
    client.post("/login/code", data={"code": "good"}, follow_redirects=False)
    assert auth.baseline == 1


def test_login_page_says_when_the_box_is_already_signed_in(tmp_path):
    client = make_client(tmp_path, FakeTmux(), FakeTrust(), auth=FakeAuth(logged_in=True))
    body = client.get("/login").text
    assert "signed in" in body.lower()


def test_submitting_a_good_code_signs_in_and_clears_the_flow(tmp_path):
    auth = FakeAuth(logged_in=False, url=AUTH_URL, accepts=True)
    client = make_client(tmp_path, FakeTmux(), FakeTrust(), auth=auth)
    r = client.post("/login/code", data={"code": " abc#def "}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert auth.codes == ["abc#def"]              # trimmed: phones love a stray space
    assert auth.cancelled == 1                    # no dead login session left behind


def test_submitting_a_bad_code_returns_to_the_login_page(tmp_path):
    auth = FakeAuth(logged_in=False, url=AUTH_URL, accepts=False)
    client = make_client(tmp_path, FakeTmux(), FakeTrust(), auth=auth)
    r = client.post("/login/code", data={"code": "wrong"}, follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert auth.cancelled == 0                    # keep the pane: it holds the error


def test_signing_in_restores_the_listeners_that_should_be_running(tmp_path):
    # the point of doing this from a phone: one tap, then walk away
    tmx, trust, st = FakeTmux(), FakeTrust(), FakeState(["demo"])
    auth = FakeAuth(logged_in=False, url=AUTH_URL, accepts=True)
    client = make_client(tmp_path, tmx, trust, st=st, auth=auth)
    client.post("/login/code", data={"code": "good"}, follow_redirects=False)
    deadline = time.monotonic() + 2
    while tmx.started != ["demo"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert tmx.started == ["demo"]


def test_a_failed_login_does_not_restore_anything(tmp_path):
    tmx, trust, st = FakeTmux(), FakeTrust(), FakeState(["demo"])
    auth = FakeAuth(logged_in=False, url=AUTH_URL, accepts=False)
    client = make_client(tmp_path, tmx, trust, st=st, auth=auth)
    client.post("/login/code", data={"code": "wrong"}, follow_redirects=False)
    time.sleep(0.1)
    assert tmx.started == []


def test_lifespan_triggers_reconcile_on_startup(tmp_path):
    # the lifespan hook IS the feature: entering TestClient as a context manager
    # runs FastAPI's real startup event, which must fire the reconcile thread
    # without the test calling app.state.reconcile() itself
    tmx, trust, st = FakeTmux(), FakeTrust(), FakeState(["demo"])
    app = make_app(tmp_path, tmx, trust, st=st)
    with TestClient(app):
        deadline = time.monotonic() + 2
        while tmx.started != ["demo"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert tmx.started == ["demo"]


# --- the JSON API behind the buttons -----------------------------------------
# The forms are the fallback; these routes are what a tap actually hits, so
# they have to do the same work and hand back enough to redraw the row.

def test_api_projects_returns_a_rendered_row_per_project(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    client = make_client(tmp_path, tmx, trust)
    data = client.get("/api/projects").json()
    assert [p["slug"] for p in data["projects"]] == ["demo"]
    assert 'data-slug="demo"' in data["projects"][0]["html"]
    assert data["count"] == "0 of 1 running"
    assert data["signedIn"] is True


def test_api_start_starts_the_listener_and_reports_it_live(tmp_path):
    tmx, trust = FakeTmux(state=CONNECTED), FakeTrust()
    tmx.url = "https://claude.ai/code?environment=env_01ABC"
    client = make_client(tmp_path, tmx, trust)
    data = client.post("/api/start/demo").json()
    assert tmx.started == ["demo"] and trust.seeded
    assert data["count"] == "1 of 1 running"
    assert 'data-slug="demo" data-state="connected"' in data["projects"][0]["html"]
    assert "env_01ABC" in data["projects"][0]["html"]


def test_api_stop_stops_the_listener_and_reports_it_stopped(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    tmx.running.add("demo")
    client = make_client(tmp_path, tmx, trust)
    data = client.post("/api/stop/demo").json()
    assert tmx.stopped == ["demo"]
    assert data["count"] == "0 of 1 running"
    assert 'data-state="stopped"' in data["projects"][0]["html"]


def test_api_start_records_intent_like_the_form_route(tmp_path):
    tmx, trust, st = FakeTmux(), FakeTrust(), FakeState()
    client = make_client(tmp_path, tmx, trust, st=st)
    client.post("/api/start/demo")
    assert st.desired() == ["demo"]
    client.post("/api/stop/demo")
    assert st.desired() == []


def test_api_start_of_an_unknown_slug_changes_nothing(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    client = make_client(tmp_path, tmx, trust)
    data = client.post("/api/start/nope").json()
    assert tmx.started == []
    assert data["count"] == "0 of 1 running"


def test_api_reports_a_signed_out_box(tmp_path):
    # the client reloads on this rather than patching the row, because a
    # signed-out box changes what the whole page means
    tmx, trust = FakeTmux(), FakeTrust()
    client = make_client(tmp_path, tmx, trust, auth=FakeAuth(logged_in=False))
    assert client.get("/api/projects").json()["signedIn"] is False


# --- what makes it usable on a phone -----------------------------------------

def test_a_live_row_offers_the_connect_url_as_a_link(tmp_path):
    # printed as bare text (what it used to be) it is unusable on a phone
    tmx, trust = FakeTmux(state=CONNECTED), FakeTrust()
    tmx.running.add("demo")
    tmx.url = "https://claude.ai/code?environment=env_01ABC"
    body = make_client(tmp_path, tmx, trust).get("/").text
    assert '<a class="open" href="https://claude.ai/code?environment=env_01ABC"' in body


def test_a_stopped_row_shows_its_path_relative_to_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    tmx, trust = FakeTmux(), FakeTrust()
    body = make_client(tmp_path, tmx, trust).get("/").text
    assert "~/demo" in body
    assert str(tmp_path / "demo") not in body


def test_a_path_outside_home_is_shown_in_full(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "elsewhere"))
    tmx, trust = FakeTmux(), FakeTrust()
    body = make_client(tmp_path, tmx, trust).get("/").text
    assert str(tmp_path / "demo") in body


def test_every_row_keeps_a_working_form_for_a_browser_without_javascript(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    body = make_client(tmp_path, tmx, trust).get("/").text
    assert 'method="post" action="/start/demo"' in body
    assert 'data-api="/api/start/demo"' in body


def test_the_empty_state_says_what_to_do(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    app = create_app(base_dir_fn=lambda: empty, tmx=FakeTmux(), trust=FakeTrust(),
                     disc=discovery, ptr=FakePointer(), st=FakeState(), auth=FakeAuth())
    body = TestClient(app).get("/").text
    assert "Clone a repo into" in body


# --- stuck listeners ---------------------------------------------------------

def test_a_stuck_listener_says_so_and_says_what_to_do(tmp_path):
    tmx, trust = FakeTmux(state=STUCK), FakeTrust()
    tmx.running.add("demo")
    body = make_client(tmp_path, tmx, trust).get("/").text
    assert 'data-slug="demo" data-state="stuck"' in body
    assert "Stuck" in body
    assert "Stop, then Start to retry" in body


def test_a_stuck_listener_is_not_offered_a_connect_link(tmp_path):
    # it never got a session; a link from an earlier attempt leads nowhere
    tmx, trust = FakeTmux(state=STUCK), FakeTrust()
    tmx.running.add("demo")
    tmx.url = "https://claude.ai/code?environment=env_01ABC"
    body = make_client(tmp_path, tmx, trust).get("/").text
    assert "env_01ABC" not in body


# --- session age -------------------------------------------------------------

def test_a_live_row_shows_how_long_it_has_been_up(tmp_path):
    tmx, trust = FakeTmux(state=CONNECTED), FakeTrust()
    tmx.running.add("demo")
    tmx.age = 9 * 86400
    body = make_client(tmp_path, tmx, trust).get("/").text
    assert '<span class="age">9d</span>' in body


def test_age_is_formatted_by_the_unit_that_matters():
    assert app_module._age(5) == "5s"
    assert app_module._age(90) == "1m"
    assert app_module._age(3 * 3600 + 5) == "3h"
    assert app_module._age(50 * 3600) == "2d"
    assert app_module._age(None) == ""


def test_a_stopped_row_has_no_age(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    body = make_client(tmp_path, tmx, trust).get("/").text
    assert 'class="age"' not in body


# --- the memory line ---------------------------------------------------------

def test_the_header_says_how_much_room_is_left(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    client = make_client(tmp_path, tmx, trust,
                         host=FakeHost("4.2 of 12 GB", "6.0 of 20 GB"))
    data = client.get("/api/projects").json()
    # the count is what fits beside the title; usage gets its own line
    assert data["count"] == "0 of 1 running"
    assert data["usage"] == "RAM 4.2 of 12 GB · Disk 6.0 of 20 GB"


def test_a_box_that_reports_only_memory_says_only_that(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    client = make_client(tmp_path, tmx, trust, host=FakeHost("4.2 of 12 GB", ""))
    assert client.get("/api/projects").json()["usage"] == "RAM 4.2 of 12 GB"


def test_a_box_that_cannot_report_anything_still_shows_the_count(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    client = make_client(tmp_path, tmx, trust, host=FakeHost("", ""))
    data = client.get("/api/projects").json()
    assert data["count"] == "0 of 1 running"
    assert data["usage"] == ""


# --- the origin gotcha -------------------------------------------------------

def test_a_repo_with_a_github_origin_is_flagged_before_you_tap_start(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    body = make_client(tmp_path, tmx, trust, git=FakeGit(blocks=True)).get("/").text
    assert "origin points at GitHub" in body
    assert 'action="/fix-remote/demo"' in body


def test_a_clean_repo_is_not_nagged(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    body = make_client(tmp_path, tmx, trust, git=FakeGit(blocks=False)).get("/").text
    assert "origin points at GitHub" not in body


def test_a_running_listener_is_flagged_too(tmp_path):
    # the listener comes up green and no session ever appears, which is exactly
    # the confusing case this warning exists for
    tmx, trust = FakeTmux(state=CONNECTED), FakeTrust()
    tmx.running.add("demo")
    body = make_client(tmp_path, tmx, trust, git=FakeGit(blocks=True)).get("/").text
    assert "origin points at GitHub" in body


def test_the_fix_renames_the_remote_and_clears_the_warning(tmp_path):
    tmx, trust, git = FakeTmux(), FakeTrust(), FakeGit(blocks=True)
    client = make_client(tmp_path, tmx, trust, git=git)
    data = client.post("/api/fix-remote/demo").json()
    assert git.renamed == [str(tmp_path / "demo")]
    assert "origin points at GitHub" not in data["projects"][0]["html"]


def test_the_fix_of_an_unknown_slug_touches_nothing(tmp_path):
    tmx, trust, git = FakeTmux(), FakeTrust(), FakeGit(blocks=True)
    client = make_client(tmp_path, tmx, trust, git=git)
    client.post("/api/fix-remote/nope")
    assert git.renamed == []


def test_a_refused_fix_says_why_in_the_journal(tmp_path, caplog):
    git = FakeGit(blocks=True, error="This repo already has a remote named github.")
    tmx, trust = FakeTmux(), FakeTrust()
    client = make_client(tmp_path, tmx, trust, git=git)
    with caplog.at_level(logging.WARNING, logger="launcher"):
        client.post("/api/fix-remote/demo")
    assert "already has a remote named github" in caplog.text


def test_the_form_fallback_redirects_like_the_others(tmp_path):
    tmx, trust, git = FakeTmux(), FakeTrust(), FakeGit(blocks=True)
    client = make_client(tmp_path, tmx, trust, git=git)
    response = client.post("/fix-remote/demo", follow_redirects=False)
    assert response.status_code == 303
    assert git.renamed == [str(tmp_path / "demo")]


# --- the pane tail -----------------------------------------------------------

def test_a_running_row_offers_its_output(tmp_path):
    tmx, trust = FakeTmux(state=CONNECTED), FakeTrust()
    tmx.running.add("demo")
    body = make_client(tmp_path, tmx, trust).get("/").text
    assert '<details class="out" data-slug="demo">' in body


def test_a_stopped_row_offers_nothing_to_read(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    body = make_client(tmp_path, tmx, trust).get("/").text
    assert "details class=\"out\"" not in body


def test_the_pane_route_returns_the_tail(tmp_path):
    tmx, trust = FakeTmux(state=CONNECTED), FakeTrust()
    tmx.running.add("demo")
    tmx.tail = ["Capacity: 1/32", "waiting"]
    client = make_client(tmp_path, tmx, trust)
    assert client.get("/api/pane/demo").json() == {"lines": ["Capacity: 1/32", "waiting"]}


def test_the_pane_route_of_an_unknown_slug_is_empty(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    tmx.tail = ["should not be reachable"]
    client = make_client(tmp_path, tmx, trust)
    assert client.get("/api/pane/nope").json() == {"lines": []}


def test_the_pane_is_not_read_while_polling(tmp_path):
    # the whole point of the disclosure: the snapshot must not pay for output
    # nobody has asked to see
    class Counting(FakeTmux):
        def __init__(self):
            super().__init__(state=CONNECTED)
            self.tails = 0
        def pane_tail(self, slug, lines=20):
            self.tails += 1
            return []

    tmx, trust = Counting(), FakeTrust()
    tmx.running.add("demo")
    client = make_client(tmp_path, tmx, trust)
    client.get("/api/projects")
    client.get("/")
    assert tmx.tails == 0


# --- Retry -------------------------------------------------------------------
# Stop, wait, Start, wait was the recovery for every broken listener. One
# control does both, and drops the resume pointer on the way.

def test_a_failed_row_offers_retry(tmp_path):
    tmx, trust = FakeTmux(state=FAILED), FakeTrust()
    tmx.running.add("demo")
    body = make_client(tmp_path, tmx, trust).get("/").text
    assert 'action="/retry/demo"' in body


def test_a_stuck_row_offers_retry(tmp_path):
    tmx, trust = FakeTmux(state=STUCK), FakeTrust()
    tmx.running.add("demo")
    body = make_client(tmp_path, tmx, trust).get("/").text
    assert 'action="/retry/demo"' in body


def test_a_healthy_row_does_not_offer_retry(tmp_path):
    tmx, trust = FakeTmux(state=CONNECTED), FakeTrust()
    tmx.running.add("demo")
    body = make_client(tmp_path, tmx, trust).get("/").text
    assert "/retry/demo" not in body


def test_retry_stops_clears_the_pointer_and_starts_again(tmp_path):
    tmx, trust, ptr = FakeTmux(state=FAILED), FakeTrust(), FakePointer(sid="session_01OLD")
    tmx.running.add("demo")
    client = make_client(tmp_path, tmx, trust, ptr=ptr)
    client.post("/api/retry/demo")
    assert tmx.stopped == ["demo"]
    assert ptr.cleared == [str(tmp_path / "demo")]
    assert tmx.started == ["demo"]
    # the recorded session is the prime suspect for the failure, so the restart
    # must register fresh rather than re-adopt it
    assert tmx.resumed == [None]


def test_retry_keeps_the_project_desired(tmp_path):
    tmx, trust, st = FakeTmux(state=FAILED), FakeTrust(), FakeState()
    tmx.running.add("demo")
    client = make_client(tmp_path, tmx, trust, st=st)
    client.post("/api/retry/demo")
    assert st.desired() == ["demo"]


def test_retry_of_an_unknown_slug_changes_nothing(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    client = make_client(tmp_path, tmx, trust)
    client.post("/api/retry/nope")
    assert tmx.started == [] and tmx.stopped == []


# --- Start fresh -------------------------------------------------------------

def test_a_stopped_row_with_a_recorded_session_offers_a_fresh_start(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    body = make_client(tmp_path, tmx, trust, ptr=FakePointer(sid="session_01ABC")).get("/").text
    assert 'action="/start-fresh/demo"' in body


def test_without_a_recorded_session_there_is_nothing_to_start_fresh_from(tmp_path):
    # Start already registers fresh, so the control would do nothing
    tmx, trust = FakeTmux(), FakeTrust()
    body = make_client(tmp_path, tmx, trust, ptr=FakePointer(sid=None)).get("/").text
    assert "/start-fresh/demo" not in body


def test_a_running_row_never_offers_a_fresh_start(tmp_path):
    tmx, trust = FakeTmux(state=CONNECTED), FakeTrust()
    tmx.running.add("demo")
    body = make_client(tmp_path, tmx, trust, ptr=FakePointer(sid="session_01ABC")).get("/").text
    assert "/start-fresh/demo" not in body


def test_start_fresh_discards_the_pointer_before_starting(tmp_path):
    tmx, trust, ptr = FakeTmux(), FakeTrust(), FakePointer(sid="session_01ABC")
    client = make_client(tmp_path, tmx, trust, ptr=ptr)
    client.post("/api/start-fresh/demo")
    assert ptr.cleared == [str(tmp_path / "demo")]
    assert tmx.resumed == [None]


def test_plain_start_still_resumes(tmp_path):
    # the whole point of the pair: Start keeps the conversation, this does not
    tmx, trust, ptr = FakeTmux(), FakeTrust(), FakePointer(sid="session_01ABC")
    client = make_client(tmp_path, tmx, trust, ptr=ptr)
    client.post("/api/start/demo")
    assert ptr.cleared == []
    assert tmx.resumed == ["session_01ABC"]


# --- parallel sessions -------------------------------------------------------

def test_a_parallel_project_starts_in_worktree_mode(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    client = make_client(tmp_path, tmx, trust, st=FakeState(parallel=["demo"]))
    client.post("/api/start/demo")
    assert tmx.spawns == ["worktree"]


def test_a_parallel_project_never_resumes(tmp_path):
    # the CLI refuses --session-id together with --spawn, and a resumed
    # listener is single-session — the opposite of what the toggle asks for
    tmx, trust, ptr = FakeTmux(), FakeTrust(), FakePointer(sid="session_01ABC")
    client = make_client(tmp_path, tmx, trust, ptr=ptr, st=FakeState(parallel=["demo"]))
    client.post("/api/start/demo")
    assert tmx.resumed == [None]


def test_a_plain_project_keeps_same_dir_mode(tmp_path):
    tmx, trust = FakeTmux(), FakeTrust()
    make_client(tmp_path, tmx, trust).post("/api/start/demo")
    assert tmx.spawns == ["same-dir"]


def test_the_toggle_turns_parallel_on_and_off(tmp_path):
    st = FakeState()
    client = make_client(tmp_path, FakeTmux(), FakeTrust(), st=st)
    body = client.post("/api/parallel/demo", data={"on": "1"}).json()
    assert st.parallel() == ["demo"]
    assert 'data-parallel="on"' in body["projects"][0]["html"]
    client.post("/api/parallel/demo", data={"on": "0"})
    assert st.parallel() == []


def test_the_toggle_form_works_without_javascript(tmp_path):
    st = FakeState()
    client = make_client(tmp_path, FakeTmux(), FakeTrust(), st=st)
    r = client.post("/parallel/demo", data={"on": "1"}, follow_redirects=False)
    assert r.status_code == 303 and st.parallel() == ["demo"]


def test_the_toggle_ignores_an_unknown_slug(tmp_path):
    st = FakeState()
    make_client(tmp_path, FakeTmux(), FakeTrust(), st=st).post("/api/parallel/ghost", data={"on": "1"})
    assert st.parallel() == []


def test_a_stopped_row_offers_the_toggle(tmp_path):
    body = make_client(tmp_path, FakeTmux(), FakeTrust()).get("/").text
    assert 'action="/parallel/demo"' in body
    assert 'data-parallel="off"' in body


def test_a_running_row_does_not_offer_the_toggle(tmp_path):
    # the mode is fixed when the listener starts; flipping it mid-run would
    # say something about the listener that is not true
    tmx = FakeTmux(state=CONNECTED)
    tmx.running.add("demo")
    body = make_client(tmp_path, tmx, FakeTrust(), st=FakeState(parallel=["demo"])).get("/").text
    assert "/parallel/demo" not in body
    assert "Parallel" in body


def test_a_parallel_row_has_no_fresh_start(tmp_path):
    # a parallel listener always starts fresh, so the control would do nothing
    body = make_client(tmp_path, FakeTmux(), FakeTrust(), ptr=FakePointer(sid="session_01ABC"),
                       st=FakeState(parallel=["demo"])).get("/").text
    assert "/start-fresh/demo" not in body


def test_a_running_row_counts_its_sessions(tmp_path):
    tmx = FakeTmux(state=CONNECTED)
    tmx.running.add("demo")
    tmx.sessions = 3
    body = make_client(tmp_path, tmx, FakeTrust()).get("/").text
    assert "3 sessions" in body


def test_a_single_session_is_not_counted(tmp_path):
    tmx = FakeTmux(state=CONNECTED)
    tmx.running.add("demo")
    tmx.sessions = 1
    body = make_client(tmp_path, tmx, FakeTrust()).get("/").text
    assert "sessions" not in body.split('data-slug="demo"')[1].split("</div>")[0]


# --- running first -----------------------------------------------------------

def _mkrepos(base, *names):
    for name in names:
        (base / name / ".git").mkdir(parents=True)


def test_running_projects_are_listed_first(tmp_path):
    # eleven projects is more than one phone screen; the live ones are the ones
    # you opened the page for
    _mkrepos(tmp_path, "alpha", "beta", "gamma", "delta")
    tmx, trust = FakeTmux(state=CONNECTED), FakeTrust()
    tmx.running.update({"beta", "delta"})
    app = create_app(base_dir_fn=lambda: tmp_path, tmx=tmx, trust=trust, disc=discovery,
                     ptr=FakePointer(), st=FakeState(), auth=FakeAuth(),
                     git=FakeGit(), host=FakeHost(""))
    data = TestClient(app).get("/api/projects").json()
    assert [p["slug"] for p in data["projects"]] == ["beta", "delta", "alpha", "gamma"]


def test_alphabetical_order_survives_within_each_group(tmp_path):
    _mkrepos(tmp_path, "zulu", "alpha", "mike")
    tmx, trust = FakeTmux(), FakeTrust()
    app = create_app(base_dir_fn=lambda: tmp_path, tmx=tmx, trust=trust, disc=discovery,
                     ptr=FakePointer(), st=FakeState(), auth=FakeAuth(),
                     git=FakeGit(), host=FakeHost(""))
    data = TestClient(app).get("/api/projects").json()
    assert [p["slug"] for p in data["projects"]] == ["alpha", "mike", "zulu"]


def test_the_fresh_start_control_does_not_borrow_the_login_links_styling(tmp_path):
    # `.link` is the login page's authorize URL, which is monospace; a button
    # carrying that class inherits the family and renders as code
    tmx, trust = FakeTmux(), FakeTrust()
    body = make_client(tmp_path, tmx, trust, ptr=FakePointer(sid="session_01ABC")).get("/").text
    assert 'class="btn link"' not in body


# --- hardening ---------------------------------------------------------------

def test_signed_in_is_cached_between_polls(tmp_path):
    # `claude auth status` is a subprocess on every poll otherwise.
    class CountingAuth(FakeAuth):
        def __init__(self):
            super().__init__(logged_in=True); self.asked = 0
        def status(self):
            self.asked += 1; return self.logged_in
    auth = CountingAuth()
    client = make_client(tmp_path, FakeTmux(), FakeTrust(), auth=auth)
    for _ in range(3):
        assert client.get("/api/projects").json()["signedIn"] is True
    assert auth.asked == 1


def test_signing_in_drops_the_cached_answer(tmp_path):
    auth = FakeAuth(logged_in=False, url=AUTH_URL, accepts=True)
    client = make_client(tmp_path, FakeTmux(), FakeTrust(), auth=auth)
    assert client.get("/api/projects").json()["signedIn"] is False
    client.post("/login/code", data={"code": "abc"}, follow_redirects=False)
    assert client.get("/api/projects").json()["signedIn"] is True


def test_a_start_refused_by_tmux_is_logged_not_raised(tmp_path, caplog):
    class RefusingTmux(FakeTmux):
        def start(self, project, session_id=None):
            raise app_module._tmuxctl.StartError("duplicate session: devbox-demo")
    tmx = RefusingTmux()
    client = make_client(tmp_path, tmx, FakeTrust())
    with caplog.at_level(logging.WARNING, logger="launcher"):
        r = client.post("/start/demo", follow_redirects=False)
    assert r.status_code == 303
    assert "duplicate session" in caplog.text


def test_a_stop_during_the_settle_wait_is_not_healed_back_up(tmp_path):
    # The row is tappable while Start blocks in wait_for_health. A Stop there
    # kills the listener, which reads as FAILED — but it was asked for, so the
    # stale-pointer retry must not start it again.
    st = FakeState()
    class StoppedMidWait(FakeTmux):
        def wait_for_health(self, slug):
            st.record_stopped(slug); self.stop(slug)
            return FAILED
    tmx = StoppedMidWait()
    client = make_client(tmp_path, tmx, FakeTrust(), st=st)
    client.post("/start/demo", follow_redirects=False)
    assert tmx.started == ["demo"]
    assert st.desired() == []
