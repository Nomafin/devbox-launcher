import subprocess

from launcher import authctl


def responder(handler):
    """Runner dispatching on the command: handler(cmd) -> (returncode, stdout)."""
    def run(cmd):
        rc, out = handler(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
    return run


def pane(text, rc=0):
    return responder(lambda cmd: (rc, text))


# Captured off the devbox. The redirect goes to a hosted callback rather than
# localhost, which is the whole reason this flow can be driven from a phone.
PANE_AWAITING_CODE = """Opening browser to sign in…
If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&scope=org%3Acreate_api_key+user%3Aprofile&code_challenge=k7Qm2XpLvNb9RsTz4Hy6Wd1CjFg8aUe&code_challenge_method=S256&state=pR4vKm9TzXqNb2Ld7Hs5Wy3CjFa8Ge1
Paste code here if prompted >
"""
AUTHORIZE_URL = (
    "https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    "&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback"
    "&scope=org%3Acreate_api_key+user%3Aprofile&code_challenge=k7Qm2XpLvNb9RsTz4Hy6Wd1CjFg8aUe"
    "&code_challenge_method=S256&state=pR4vKm9TzXqNb2Ld7Hs5Wy3CjFa8Ge1"
)
PANE_REJECTED = PANE_AWAITING_CODE.rstrip("\n") + (
    " Login failed: Request failed with status code 400\n"
    "Pane is dead (status 1, Fri Aug 14 22:23:37 2026)\n"
)
# A half-copied code. The CLI stays at the prompt for another try rather than
# exiting, so the pane keeps accumulating these.
PANE_MALFORMED = PANE_AWAITING_CODE.rstrip("\n") + (
    " Invalid code. Please make sure the full code was copied.\n"
)


# --- status -----------------------------------------------------------------

def test_status_reads_the_json_the_cli_prints():
    assert authctl.status(run=pane('{"loggedIn": true, "authMethod": "claudeai"}')) is True
    assert authctl.status(run=pane('{"loggedIn": false, "authMethod": "none"}')) is False


def test_status_ignores_the_exit_code():
    # `claude auth status` exits 1 when logged out, so the return code says
    # nothing about whether the call worked — only the JSON does
    assert authctl.status(run=pane('{"loggedIn": false}', rc=1)) is False
    assert authctl.status(run=pane('{"loggedIn": true}', rc=1)) is True


def test_status_is_false_when_the_output_is_not_json():
    # a CLI that failed to run at all must read as logged out, not crash the page
    assert authctl.status(run=pane("bash: claude: command not found", rc=127)) is False
    assert authctl.status(run=pane("")) is False


def test_status_survives_the_cli_being_missing_entirely():
    # status() is called on every render of the main page, so an unrunnable
    # `claude` must read as logged out, not take the whole UI down with a 500
    def explode(_cmd):
        raise FileNotFoundError("No such file or directory: 'claude'")
    assert authctl.status(run=explode) is False


def test_status_asks_the_cli_not_the_credentials_file():
    calls = []
    authctl.status(run=responder(lambda cmd: (calls.append(cmd), (0, "{}"))[1]))
    assert calls[0] == ["claude", "auth", "status"]


# --- starting the flow ------------------------------------------------------

def creation_call(calls):
    return next(c for c in calls if "new-session" in c)


def test_start_login_keeps_the_pane_so_a_failure_stays_readable():
    calls = []
    def handler(cmd):
        calls.append(cmd)
        return (1, "")          # nothing to clear
    authctl.start_login(run=responder(handler))
    cmd = creation_call(calls)
    assert cmd[:3] == ["tmux", "-L", "devbox-launcher"]
    assert cmd[3:8] == ["set-option", "-g", "remain-on-exit", "on", ";"]
    assert cmd[-3:] == ["claude", "auth", "login"]


def test_start_login_uses_a_session_name_no_project_can_collide_with():
    # listeners are `devbox-<slug>`, so a project directory named "login" would
    # otherwise share this session
    assert authctl.SESSION == "launcher-login"
    assert not authctl.SESSION.startswith("devbox-")


def test_start_login_gives_the_pane_room_for_the_url():
    calls = []
    authctl.start_login(run=responder(lambda cmd: (calls.append(cmd), (1, ""))[1]))
    cmd = creation_call(calls)
    assert cmd[cmd.index("-x") + 1] == "200"


def test_is_running_reflects_the_session():
    assert authctl.is_running(run=responder(lambda cmd: (0, ""))) is True
    assert authctl.is_running(run=responder(lambda cmd: (1, ""))) is False


def test_is_active_is_false_once_the_flow_has_exited():
    # remain-on-exit keeps the session standing after `claude auth login` exits,
    # so has-session still says yes. The link that session was showing is spent,
    # which is the difference the UI has to act on.
    def handler(cmd):
        if "list-panes" in cmd:
            return (0, "1")
        return (0, "")
    assert authctl.is_running(run=responder(handler)) is True
    assert authctl.is_active(run=responder(handler)) is False


def test_is_active_is_true_while_the_flow_is_waiting():
    def handler(cmd):
        if "list-panes" in cmd:
            return (0, "0")
        return (0, "")
    assert authctl.is_active(run=responder(handler)) is True


def test_is_active_is_false_when_there_is_no_session_at_all():
    assert authctl.is_active(run=responder(lambda cmd: (1, ""))) is False


def test_start_login_clears_a_spent_session_first():
    # tmux refuses a duplicate name, so a dead login session left by an earlier
    # attempt would make every later sign-in silently do nothing
    calls = []
    def handler(cmd):
        calls.append(cmd)
        return (0, "1") if "list-panes" in cmd else (0, "")
    authctl.start_login(run=responder(handler))
    killed = [i for i, c in enumerate(calls) if "kill-session" in c]
    created = [i for i, c in enumerate(calls) if "new-session" in c]
    assert killed and created and killed[0] < created[0]


def test_start_login_does_not_kill_when_there_is_nothing_to_clear():
    calls = []
    def handler(cmd):
        calls.append(cmd)
        return (1, "")          # no session at all
    authctl.start_login(run=responder(handler))
    assert not any("kill-session" in c for c in calls)


# --- reading the flow -------------------------------------------------------

def test_authorize_url_is_scraped_off_the_pane():
    assert authctl.authorize_url(run=pane(PANE_AWAITING_CODE)) == AUTHORIZE_URL


def test_authorize_url_is_none_before_the_cli_prints_it():
    assert authctl.authorize_url(run=pane("Opening browser to sign in…\n")) is None


def test_authorize_url_reads_the_joined_scrollback():
    # the URL is far longer than the pane is wide and is printed first, so
    # without -J and -S - it comes back truncated or gone
    calls = []
    authctl.authorize_url(run=responder(lambda cmd: (calls.append(cmd), (0, ""))[1]))
    assert "-J" in calls[0] and calls[0][-2:] == ["-S", "-"]


def test_waiting_for_code_detects_the_prompt():
    assert authctl.waiting_for_code(run=pane(PANE_AWAITING_CODE)) is True
    assert authctl.waiting_for_code(run=pane("Opening browser to sign in…\n")) is False


def test_error_is_read_off_a_rejected_login():
    assert authctl.error(run=pane(PANE_REJECTED)) == (
        "Login failed: Request failed with status code 400")


def test_error_is_none_while_the_flow_is_healthy():
    assert authctl.error(run=pane(PANE_AWAITING_CODE)) is None


def test_error_reads_a_malformed_code_too():
    # The CLI rejects a malformed paste differently from a well-formed but
    # invalid one: it says "Invalid code", stays alive and lets you paste again,
    # rather than reporting "Login failed" and exiting. Missing this leaves the
    # likeliest phone mistake — a half-copied code — with no message at all.
    assert authctl.error(run=pane(PANE_MALFORMED)) == (
        "Invalid code. Please make sure the full code was copied.")


def test_errors_counts_each_rejection():
    assert authctl.errors(run=pane(PANE_AWAITING_CODE)) == []
    assert len(authctl.errors(run=pane(PANE_MALFORMED))) == 1
    assert len(authctl.errors(run=pane(PANE_MALFORMED + PANE_MALFORMED))) == 2


def test_wait_for_login_is_not_fooled_by_an_earlier_rejection():
    # The pane keeps its history, so the previous attempt's "Invalid code" is
    # still sitting there when the next code is submitted. Counting from a
    # baseline is what stops a good second paste being read as a failure.
    calls = {"n": 0}
    def handler(cmd):
        if cmd[:2] == ["claude", "auth"]:
            calls["n"] += 1
            # the exchange is still in flight on the first poll
            return (0, '{"loggedIn": %s}' % ("false" if calls["n"] < 2 else "true"))
        return (0, PANE_MALFORMED)          # the stale rejection never goes away
    assert authctl.wait_for_login(
        run=responder(handler), sleep=lambda _s: None, baseline=1) is True


def test_wait_for_login_fails_on_a_rejection_newer_than_the_baseline():
    def handler(cmd):
        if cmd[:2] == ["claude", "auth"]:
            return (1, '{"loggedIn": false}')
        return (0, PANE_MALFORMED + PANE_MALFORMED)
    assert authctl.wait_for_login(
        run=responder(handler), sleep=lambda _s: None, baseline=1) is False


# --- submitting the code ----------------------------------------------------

def test_submit_code_sends_the_code_literally_then_enter():
    # -l stops tmux reading a character of the code as a key name; Enter has to
    # be a separate call because -l would send the word "Enter" as text
    calls = []
    authctl.submit_code("abc#def", run=responder(lambda cmd: (calls.append(cmd), (0, ""))[1]))
    assert calls[0] == ["tmux", "-L", "devbox-launcher", "send-keys", "-t",
                        "launcher-login", "-l", "abc#def"]
    assert calls[1] == ["tmux", "-L", "devbox-launcher", "send-keys", "-t",
                        "launcher-login", "Enter"]


def test_wait_for_login_succeeds_once_the_cli_reports_logged_in():
    states = iter(['{"loggedIn": false}', '{"loggedIn": true}'])
    def handler(cmd):
        if cmd[:2] == ["claude", "auth"]:
            return (0, next(states, '{"loggedIn": true}'))
        return (0, PANE_AWAITING_CODE)
    assert authctl.wait_for_login(run=responder(handler), sleep=lambda _s: None) is True


def test_wait_for_login_gives_up_as_soon_as_the_cli_rejects_the_code():
    # the flow exits on a bad code, so polling out the full budget would leave
    # the user staring at a spinner for something already decided
    polls = []
    def handler(cmd):
        if cmd[:2] == ["claude", "auth"]:
            polls.append(cmd)
            return (1, '{"loggedIn": false}')
        return (0, PANE_REJECTED)
    assert authctl.wait_for_login(run=responder(handler), sleep=lambda _s: None) is False
    assert len(polls) == 1


def test_wait_for_url_polls_until_the_cli_prints_it():
    panes = iter(["", "Opening browser to sign in…\n", PANE_AWAITING_CODE])
    run = responder(lambda cmd: (0, next(panes, PANE_AWAITING_CODE)))
    assert authctl.wait_for_url(run=run, sleep=lambda _s: None) == AUTHORIZE_URL


def test_wait_for_url_gives_up_rather_than_hanging_the_page():
    assert authctl.wait_for_url(
        run=pane(""), sleep=lambda _s: None, attempts=3) is None


def test_cancel_kills_the_login_session():
    calls = []
    authctl.cancel(run=responder(lambda cmd: (calls.append(cmd), (0, ""))[1]))
    assert calls[0] == ["tmux", "-L", "devbox-launcher", "kill-session", "-t", "launcher-login"]


def test_status_gives_the_cli_a_longer_timeout_than_tmux(monkeypatch):
    # It talks to the network, but it is on the polling path so it is bounded;
    # a hung CLI reads as logged out rather than a request that never answers.
    seen = {}
    def hang(cmd, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
    monkeypatch.setattr(authctl.subprocess, "run", hang)
    assert authctl.status() is False
    assert seen["timeout"] == authctl.STATUS_TIMEOUT
