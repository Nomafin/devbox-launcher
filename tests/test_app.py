import logging
import subprocess
from html import escape
from pathlib import Path

from fastapi.testclient import TestClient

from launcher import app as app_module
from launcher.agents import runtime, service
from launcher.app import create_app


class FakeAgentsTmux:
    """tmux on the agents socket with nothing running: these tests are about
    the page around the agents, not the agents themselves."""
    def __init__(self):
        self.calls = []
    def __call__(self, cmd, timeout=None):
        assert cmd[:4] == ["tmux", "-u", "-L", runtime.SOCKET], cmd
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no server running")


def agents_ctx(tmp_path):
    """A service.Ctx over a temp dir. Its home sits beside the projects, in a
    directory discovery skips (no .git, no claude-project/)."""
    home = tmp_path / ".agents-home"
    ctx = service.Ctx(root=home / ".config" / "agents", base_dir=tmp_path, home=home,
                      run=FakeAgentsTmux(), sleep=lambda s: None, trust=lambda p: None)
    return lambda: ctx


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


def make_app(tmp_path, auth=None, git=None, host=None):
    _mkrepo(tmp_path, "demo")
    return create_app(base_dir_fn=lambda: tmp_path, ctx_fn=agents_ctx(tmp_path),
                      auth=auth or FakeAuth(), git=git or FakeGit(),
                      host=host or FakeHost(""), run_user=None)


def make_client(tmp_path, auth=None, git=None, host=None):
    return TestClient(make_app(tmp_path, auth, git, host))


# --- the page ----------------------------------------------------------------

def test_signed_out_box_says_so_on_the_main_page(tmp_path):
    # the failure this exists for: every Start fails identically and the page
    # gave no hint why. One glance should now answer it.
    auth = FakeAuth(logged_in=False)
    client = make_client(tmp_path, auth=auth)
    body = client.get("/").text
    assert "signed out" in body.lower()
    assert 'action="/login/start"' in body


def test_signed_in_box_shows_no_banner(tmp_path):
    client = make_client(tmp_path, auth=FakeAuth(logged_in=True))
    body = client.get("/").text
    assert "signed out" not in body.lower()
    assert "/login/start" not in body


AUTH_URL = "https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c&state=jYMi"


# --- signing in --------------------------------------------------------------

def test_login_start_launches_the_flow_and_shows_the_page(tmp_path):
    auth = FakeAuth(logged_in=False, url=AUTH_URL)
    client = make_client(tmp_path, auth=auth)
    r = client.post("/login/start", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    assert auth.started == 1


def test_login_start_does_not_restart_a_flow_already_in_progress(tmp_path):
    # restarting throws away the challenge behind the URL the user is already
    # looking at on their phone
    auth = FakeAuth(logged_in=False, url=AUTH_URL)
    auth.running = True
    client = make_client(tmp_path, auth=auth)
    client.post("/login/start", follow_redirects=False)
    assert auth.started == 0


def test_login_page_offers_the_url_and_somewhere_to_paste_the_code(tmp_path):
    auth = FakeAuth(logged_in=False, url=AUTH_URL)
    client = make_client(tmp_path, auth=auth)
    body = client.get("/login").text
    assert escape(AUTH_URL) in body
    assert 'action="/login/code"' in body
    assert 'name="code"' in body


def test_login_page_waits_before_the_url_appears(tmp_path):
    auth = FakeAuth(logged_in=False, url=None)
    client = make_client(tmp_path, auth=auth)
    body = client.get("/login").text
    assert "starting" in body.lower()


def test_login_page_offers_a_fresh_link_when_the_flow_has_exited(tmp_path):
    # "Login failed" ends the process, so the link on the pane is spent
    auth = FakeAuth(logged_in=False, url=AUTH_URL,
                    err="Login failed: Request failed with status code 400")
    auth.running = False
    client = make_client(tmp_path, auth=auth)
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
    client = make_client(tmp_path, auth=auth)
    body = client.get("/login").text
    assert "Invalid code" in body
    assert 'name="code"' in body                  # paste box still there
    assert escape(AUTH_URL) in body               # and the same link


def test_submitting_a_code_ignores_an_earlier_rejection(tmp_path):
    # without a baseline the stale "Invalid code" would read as this code failing
    auth = FakeAuth(logged_in=False, url=AUTH_URL, accepts=True,
                    err="Invalid code. Please make sure the full code was copied.")
    auth.running = True
    client = make_client(tmp_path, auth=auth)
    client.post("/login/code", data={"code": "good"}, follow_redirects=False)
    assert auth.baseline == 1


def test_login_page_says_when_the_box_is_already_signed_in(tmp_path):
    client = make_client(tmp_path, auth=FakeAuth(logged_in=True))
    body = client.get("/login").text
    assert "signed in" in body.lower()


def test_submitting_a_good_code_signs_in_and_clears_the_flow(tmp_path):
    auth = FakeAuth(logged_in=False, url=AUTH_URL, accepts=True)
    client = make_client(tmp_path, auth=auth)
    r = client.post("/login/code", data={"code": " abc#def "}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert auth.codes == ["abc#def"]              # trimmed: phones love a stray space
    assert auth.cancelled == 1                    # no dead login session left behind


def test_submitting_a_bad_code_returns_to_the_login_page(tmp_path):
    auth = FakeAuth(logged_in=False, url=AUTH_URL, accepts=False)
    client = make_client(tmp_path, auth=auth)
    r = client.post("/login/code", data={"code": "wrong"}, follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert auth.cancelled == 0                    # keep the pane: it holds the error


def test_signing_in_starts_no_agents(tmp_path):
    # Agents come back through agents-restore.service at boot, not through the
    # web app; a sign-in only clears the banner.
    auth = FakeAuth(logged_in=False, url=AUTH_URL, accepts=True)
    client = make_client(tmp_path, auth=auth)
    client.post("/login/code", data={"code": "good"}, follow_redirects=False)
    data = client.get("/api/projects").json()
    assert data["count"] == "0 agents running"


# --- rows and header ---------------------------------------------------------

def test_api_reports_a_signed_out_box(tmp_path):
    # the client reloads on this rather than patching the row, because a
    # signed-out box changes what the whole page means
    client = make_client(tmp_path, auth=FakeAuth(logged_in=False))
    assert client.get("/api/projects").json()["signedIn"] is False


def test_a_stopped_row_shows_its_path_relative_to_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    body = make_client(tmp_path).get("/").text
    assert "~/demo" in body
    assert str(tmp_path / "demo") not in body


def test_a_path_outside_home_is_shown_in_full(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "elsewhere"))
    body = make_client(tmp_path).get("/").text
    assert str(tmp_path / "demo") in body


def test_the_empty_state_says_what_to_do(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    app = create_app(base_dir_fn=lambda: empty, ctx_fn=agents_ctx(empty), auth=FakeAuth(),
                     git=FakeGit(), host=FakeHost(""), run_user=None)
    body = TestClient(app).get("/").text
    assert "Clone a repo into" in body


def test_age_is_formatted_by_the_unit_that_matters():
    assert app_module._age(5) == "5s"
    assert app_module._age(90) == "1m"
    assert app_module._age(3 * 3600 + 5) == "3h"
    assert app_module._age(50 * 3600) == "2d"
    assert app_module._age(None) == ""


def test_the_header_says_how_much_room_is_left(tmp_path):
    client = make_client(tmp_path, host=FakeHost("4.2 of 12 GB", "6.0 of 20 GB"))
    data = client.get("/api/projects").json()
    # the count is what fits beside the title; usage gets its own line
    assert data["count"] == "0 agents running"
    assert data["usage"] == "RAM 4.2 of 12 GB · Disk 6.0 of 20 GB"


def test_a_box_that_reports_only_memory_says_only_that(tmp_path):
    client = make_client(tmp_path, host=FakeHost("4.2 of 12 GB", ""))
    assert client.get("/api/projects").json()["usage"] == "RAM 4.2 of 12 GB"


def test_a_box_that_cannot_report_anything_still_shows_the_count(tmp_path):
    client = make_client(tmp_path, host=FakeHost("", ""))
    data = client.get("/api/projects").json()
    assert data["count"] == "0 agents running"
    assert data["usage"] == ""


# --- the origin gotcha -------------------------------------------------------

def test_a_repo_with_a_github_origin_is_flagged_on_its_project_row(tmp_path):
    body = make_client(tmp_path, git=FakeGit(blocks=True)).get("/").text
    assert "origin points at GitHub" in body
    assert 'action="/fix-remote/demo"' in body


def test_a_clean_repo_is_not_nagged(tmp_path):
    body = make_client(tmp_path, git=FakeGit(blocks=False)).get("/").text
    assert "origin points at GitHub" not in body


def test_the_fix_renames_the_remote_and_clears_the_warning(tmp_path):
    git = FakeGit(blocks=True)
    client = make_client(tmp_path, git=git)
    data = client.post("/api/fix-remote/demo").json()
    assert git.renamed == [str(tmp_path / "demo")]
    assert "origin points at GitHub" not in data["projects"][0]["html"]


def test_the_fix_of_an_unknown_slug_touches_nothing(tmp_path):
    git = FakeGit(blocks=True)
    client = make_client(tmp_path, git=git)
    client.post("/api/fix-remote/nope")
    assert git.renamed == []


def test_a_refused_fix_says_why_in_the_journal(tmp_path, caplog):
    git = FakeGit(blocks=True, error="This repo already has a remote named github.")
    client = make_client(tmp_path, git=git)
    with caplog.at_level(logging.WARNING, logger="launcher"):
        client.post("/api/fix-remote/demo")
    assert "already has a remote named github" in caplog.text


def test_the_form_fallback_redirects_like_the_others(tmp_path):
    git = FakeGit(blocks=True)
    client = make_client(tmp_path, git=git)
    response = client.post("/fix-remote/demo", follow_redirects=False)
    assert response.status_code == 303
    assert git.renamed == [str(tmp_path / "demo")]


# --- hardening ---------------------------------------------------------------

def test_signed_in_is_cached_between_polls(tmp_path):
    # `claude auth status` is a subprocess on every poll otherwise.
    class CountingAuth(FakeAuth):
        def __init__(self):
            super().__init__(logged_in=True); self.asked = 0
        def status(self):
            self.asked += 1; return self.logged_in
    auth = CountingAuth()
    client = make_client(tmp_path, auth=auth)
    for _ in range(3):
        assert client.get("/api/projects").json()["signedIn"] is True
    assert auth.asked == 1


def test_signing_in_drops_the_cached_answer(tmp_path):
    auth = FakeAuth(logged_in=False, url=AUTH_URL, accepts=True)
    client = make_client(tmp_path, auth=auth)
    assert client.get("/api/projects").json()["signedIn"] is False
    client.post("/login/code", data={"code": "abc"}, follow_redirects=False)
    assert client.get("/api/projects").json()["signedIn"] is True


# --- running a command -------------------------------------------------------

from launcher.runctl import NONE as NO_RUN, RunStatus, StartError as RunStartError

ME = "dev@example.com"
AS_ME = {"Tailscale-User-Login": ME, "Sec-Fetch-Site": "same-origin"}


class FakeRun:
    """Per-slug commands, projected through the same shape as runctl."""
    def __init__(self, **by_slug):
        self.st = dict(by_slug)                       # slug -> RunStatus
        self.text, self.records = {}, {}
        self.started, self.inputs = [], []
        self.interrupts, self.closes = [], []
        self.refuse = False
    def status(self, slug):
        return self.st.get(slug, NO_RUN)
    def statuses(self):
        return {s: v for s, v in self.st.items() if v.exists}
    def start(self, slug, command, cwd):
        if self.refuse:
            raise RunStartError("no tmux")
        self.started.append((slug, command, str(cwd)))
        self.records[slug] = {"command": command, "cwd": str(cwd)}
        self.st[slug] = RunStatus(True, True, None)
    def last(self, slug):
        return self.records.get(slug, {})
    def output(self, slug):
        return self.text.get(slug, "")
    def send_input(self, slug, text):
        self.inputs.append((slug, text))
    def interrupt(self, slug):
        self.interrupts.append(slug)
    def close(self, slug):
        self.closes.append(slug)
        self.st[slug] = NO_RUN


RUNNING = RunStatus(True, True, None)


def finished(code):
    return RunStatus(True, False, code)


def run_client(tmp_path, runner=None, user=ME):
    _mkrepo(tmp_path, "demo")
    return TestClient(create_app(
        base_dir_fn=lambda: tmp_path, ctx_fn=agents_ctx(tmp_path), auth=FakeAuth(),
        git=FakeGit(), host=FakeHost(""), runner=runner or FakeRun(), run_user=user))


def post(client, path, data=None, headers=AS_ME):
    return client.post(path, data=data or {}, headers=headers, follow_redirects=False)


def test_every_row_offers_run_command(tmp_path):
    body = run_client(tmp_path).get("/").text
    assert 'href="/run/demo"' in body
    assert 'data-run="demo"' in body              # the script opens the modal from this
    assert 'id="runbox"' in body                  # and the modal itself


def test_rows_offer_no_run_when_it_is_disabled(tmp_path):
    body = run_client(tmp_path, user=None).get("/").text
    assert "/run/demo" not in body
    assert 'id="runbox"' not in body


def test_row_says_a_command_is_running(tmp_path):
    body = run_client(tmp_path, FakeRun(demo=RUNNING)).get("/").text
    assert "Command running" in body


def test_row_says_a_command_finished_and_how(tmp_path):
    body = run_client(tmp_path, FakeRun(demo=finished(3))).get("/").text
    assert "Command finished · exit 3" in body


def test_api_projects_rows_carry_the_run_state_too(tmp_path):
    data = run_client(tmp_path, FakeRun(demo=RUNNING)).get("/api/projects").json()
    assert "Command running" in data["projects"][0]["html"]


# access


def test_run_refuses_without_the_tailnet_identity(tmp_path):
    # Tailscale Serve sets this header; anything else on the tailnet that can
    # reach the page must not get a shell on the box
    client = run_client(tmp_path)
    assert client.get("/run/demo").status_code == 403
    assert client.get("/run/demo", headers={"Tailscale-User-Login": "eve@example.com"}).status_code == 403
    assert client.get("/api/run/demo").status_code == 403


def test_run_is_off_entirely_when_no_user_is_configured(tmp_path):
    client = run_client(tmp_path, user=None)
    assert client.get("/run/demo", headers=AS_ME).status_code == 403


def test_run_refuses_a_cross_site_post(tmp_path):
    # the phone's browser adds the identity header to *any* request to the box,
    # including one a hostile page makes it send
    runner = FakeRun()
    client = run_client(tmp_path, runner)
    data = {"command": "ls"}
    assert post(client, "/run/demo", data, {"Tailscale-User-Login": ME,
                                            "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert post(client, "/api/run/demo", data, {"Tailscale-User-Login": ME,
                                                "Origin": "https://evil.example"}).status_code == 403
    assert runner.started == []


def test_run_accepts_a_same_origin_post_without_fetch_metadata(tmp_path):
    runner = FakeRun()
    post(run_client(tmp_path, runner), "/run/demo", {"command": "ls"},
         {"Tailscale-User-Login": ME, "Origin": "http://testserver"})
    assert len(runner.started) == 1


def test_driving_routes_need_the_identity_too(tmp_path):
    runner = FakeRun(demo=RUNNING)
    client = run_client(tmp_path, runner)
    for path in ("/run/demo/input", "/run/demo/interrupt", "/run/demo/close",
                 "/api/run/demo/input", "/api/run/demo/interrupt", "/api/run/demo/close"):
        assert post(client, path, {"text": "y"}, headers={}).status_code == 403
    assert runner.inputs == [] and runner.interrupts == [] and runner.closes == []


def test_run_routes_404_for_an_unknown_project(tmp_path):
    client = run_client(tmp_path)
    assert client.get("/run/ghost", headers=AS_ME).status_code == 404
    assert client.get("/api/run/ghost", headers=AS_ME).status_code == 404
    assert post(client, "/run/ghost", {"command": "ls"}).status_code == 404


# starting


def test_running_a_command_starts_it_in_the_project(tmp_path):
    runner = FakeRun()
    r = post(run_client(tmp_path, runner), "/run/demo", {"command": " gcloud deploy \n"})
    assert r.status_code == 303 and r.headers["location"] == "/run/demo"
    assert runner.started == [("demo", "gcloud deploy", str(tmp_path / "demo"))]


def test_run_is_logged(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="launcher")
    post(run_client(tmp_path), "/run/demo", {"command": "rm -rf build"})
    assert "rm -rf build" in caplog.text and ME in caplog.text and "demo" in caplog.text


def test_run_refuses_an_empty_command(tmp_path):
    runner = FakeRun()
    post(run_client(tmp_path, runner), "/run/demo", {"command": "   "})
    assert runner.started == []


def test_run_does_not_replace_a_command_still_running(tmp_path):
    runner = FakeRun(demo=RUNNING)
    post(run_client(tmp_path, runner), "/run/demo", {"command": "ls"})
    assert runner.started == [] and runner.closes == []


def test_a_refused_start_is_logged_not_raised(tmp_path, caplog):
    runner = FakeRun()
    runner.refuse = True
    r = post(run_client(tmp_path, runner), "/run/demo", {"command": "ls"})
    assert r.status_code == 303
    assert "no tmux" in caplog.text


# the panel


def test_run_page_offers_a_form_when_nothing_has_run(tmp_path):
    body = run_client(tmp_path).get("/run/demo", headers=AS_ME).text
    assert 'action="/run/demo"' in body
    assert 'data-api="/api/run/demo"' in body
    assert 'name="command"' in body
    assert "demo" in body


def test_run_page_shows_a_running_command_with_its_controls(tmp_path):
    runner = FakeRun(demo=RUNNING)
    runner.text["demo"] = "Continue? (y/n) <&>"
    runner.records["demo"] = {"command": "deploy <prod>", "cwd": str(tmp_path / "demo")}
    body = run_client(tmp_path, runner).get("/run/demo", headers=AS_ME).text
    assert escape("Continue? (y/n) <&>") in body
    assert escape("deploy <prod>") in body
    assert 'action="/run/demo/input"' in body
    assert 'action="/run/demo/interrupt"' in body
    assert 'name="command"' not in body


def test_run_page_shows_the_exit_code_once_finished(tmp_path):
    runner = FakeRun(demo=finished(2))
    body = run_client(tmp_path, runner).get("/run/demo", headers=AS_ME).text
    assert "exit code 2" in body.lower()
    assert 'action="/run/demo/input"' not in body
    assert 'action="/run/demo/close"' in body


def test_run_page_shows_home_as_a_tilde(tmp_path):
    runner = FakeRun(demo=RUNNING)
    runner.records["demo"] = {"command": "ls", "cwd": str(Path.home())}
    body = run_client(tmp_path, runner).get("/run/demo", headers=AS_ME).text
    assert '<p class="where">~</p>' in body


def test_api_run_reports_state_output_and_the_panel(tmp_path):
    runner = FakeRun(demo=finished(1))
    runner.text["demo"] = "out"
    data = run_client(tmp_path, runner).get("/api/run/demo", headers=AS_ME).json()
    assert {k: data[k] for k in ("exists", "running", "exitCode", "output")} == {
        "exists": True, "running": False, "exitCode": 1, "output": "out"}
    assert 'action="/run/demo/close"' in data["html"]


def test_api_start_returns_the_new_state(tmp_path):
    runner = FakeRun()
    data = post(run_client(tmp_path, runner), "/api/run/demo", {"command": "ls"}).json()
    assert data["running"] is True
    assert runner.started


# driving


def test_input_goes_to_a_running_command(tmp_path):
    runner = FakeRun(demo=RUNNING)
    r = post(run_client(tmp_path, runner), "/run/demo/input", {"text": " y "})
    assert r.headers["location"] == "/run/demo"
    assert runner.inputs == [("demo", "y")]


def test_input_is_dropped_once_the_command_has_exited(tmp_path):
    runner = FakeRun(demo=finished(0))
    post(run_client(tmp_path, runner), "/api/run/demo/input", {"text": "y"})
    assert runner.inputs == []


def test_interrupt_and_close(tmp_path):
    runner = FakeRun(demo=RUNNING)
    client = run_client(tmp_path, runner)
    post(client, "/run/demo/interrupt")
    data = post(client, "/api/run/demo/close").json()
    assert runner.interrupts == ["demo"] and runner.closes == ["demo"]
    assert data["exists"] is False


def test_page_scripts_parse(tmp_path):
    # A "\n" inside the Python string once became a real newline inside a JS
    # string literal, and the whole script silently failed to load.
    import shutil, subprocess as sp
    node = shutil.which("node")
    if node is None:
        import pytest
        pytest.skip("node not installed")
    from launcher import ui
    for script in (ui._RUN_JS + ui._JS,):
        path = tmp_path / "page.js"
        path.write_text(script)
        result = sp.run([node, "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
