import subprocess
from pathlib import Path
from launcher import tmuxctl
from launcher.discovery import Project


def recorder(returncode=0, stdout=""):
    calls = []
    def run(cmd):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")
    return calls, run


def test_session_name():
    assert tmuxctl.session_name("demo") == "devbox-demo"


def test_is_running_reflects_returncode():
    _, run = recorder(returncode=0)
    assert tmuxctl.is_running("demo", run=run) is True
    _, run = recorder(returncode=1)
    assert tmuxctl.is_running("demo", run=run) is False


def responder(handler):
    """Runner dispatching on the tmux subcommand: handler(cmd) -> (returncode, stdout)."""
    def run(cmd):
        rc, out = handler(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
    return run


def test_start_uses_socket_dir_and_named_command():
    calls, run = recorder()
    tmuxctl.start(Project(slug="demo", name="Demo", path=Path("/home/dev/projects/Demo")), run=run)
    cmd = calls[0]
    assert cmd[:3] == ["tmux", "-L", "devbox-launcher"]
    assert "new-session" in cmd and "-d" in cmd
    assert "devbox-demo" in cmd
    assert "/home/dev/projects/Demo" in cmd
    assert cmd[-1] == "claude remote-control --name devbox-demo --spawn same-dir"


def test_start_can_spawn_each_session_in_a_worktree():
    calls, run = recorder()
    tmuxctl.start(Project(slug="demo", name="Demo", path=Path("/home/dev/projects/Demo")),
                  run=run, spawn="worktree")
    assert calls[0][-1] == "claude remote-control --name devbox-demo --spawn worktree"


def test_start_rejects_an_unknown_spawn_mode():
    _, run = recorder()
    try:
        tmuxctl.start(Project(slug="demo", name="Demo", path=Path("/x")), run=run, spawn="x; rm")
    except ValueError:
        return
    raise AssertionError("an unknown spawn mode reached the command line")


def test_start_resumes_by_session_id_when_one_is_known():
    # Resuming keeps the conversation across a reboot instead of registering a
    # fresh one. The id must be the cloud session_… from bridge-pointer.json.
    calls, run = recorder()
    tmuxctl.start(Project(slug="demo", name="Demo", path=Path("/home/dev/projects/Demo")),
                  run=run, session_id="session_01Xk7P")
    assert calls[0][-1] == "claude remote-control --name devbox-demo --session-id session_01Xk7P"


def test_start_never_combines_session_id_with_spawn():
    # The CLI rejects them together:
    #   "Error: --session-id and --continue cannot be used with --spawn,
    #    --capacity, or --create-session-in-dir."
    # Sending both means the listener dies instantly on every Start.
    calls, run = recorder()
    tmuxctl.start(Project(slug="demo", name="Demo", path=Path("/home/dev/projects/Demo")),
                  run=run, session_id="session_01Xk7P")
    assert "--spawn" not in calls[0][-1]


def test_start_keeps_the_pane_when_the_listener_dies_on_spawn():
    # Without remain-on-exit the session dies with the command, so capture-pane
    # has nothing left to read and the listener's error is lost. That is what
    # made a logged-out `claude` indistinguishable from a listener still coming
    # up. Both tmux commands go in one invocation: sent separately, a listener
    # that exits instantly can beat set-option and vanish anyway.
    calls, run = recorder()
    tmuxctl.start(Project(slug="demo", name="Demo", path=Path("/home/dev/projects/Demo")), run=run)
    cmd = calls[0]
    assert cmd[3:8] == ["set-option", "-g", "remain-on-exit", "on", ";"]
    assert cmd.index("set-option") < cmd.index("new-session")
    assert len(calls) == 1


def test_start_uses_launch_path_for_group():
    calls, run = recorder()
    project = Project(
        slug="dk",
        name="Acme",
        path=Path("/home/dev/projects/Acme"),
        launch_path=Path("/home/dev/projects/Acme/claude-project"),
    )
    tmuxctl.start(project, run=run)
    cmd = calls[0]
    assert cmd[cmd.index("-c") + 1] == "/home/dev/projects/Acme/claude-project"


def test_stop_noop_when_not_running():
    # has-session returns non-zero -> nothing to stop, no signals sent
    calls = []
    def run(cmd):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
    tmuxctl.stop("demo", run=run, sleep=lambda _s: None)
    assert calls == [["tmux", "-L", "devbox-launcher", "has-session", "-t", "devbox-demo"]]


def test_stop_graceful_then_returns_when_session_exits():
    # alive at first has-session, gone after Ctrl-C -> no kill-session backstop
    seq = iter([0, 1])  # has-session: alive, then gone
    calls = []
    def run(cmd):
        calls.append(cmd)
        rc = next(seq) if "has-session" in cmd else 0
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="")
    tmuxctl.stop("demo", run=run, sleep=lambda _s: None)
    assert ["tmux", "-L", "devbox-launcher", "send-keys", "-t", "devbox-demo", "C-c"] in calls
    assert not any("kill-session" in c for c in calls)   # exited on its own


def test_stop_kills_as_backstop_if_still_alive():
    # always alive -> after polling, kill-session backstop fires
    calls = []
    def run(cmd):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    tmuxctl.stop("demo", run=run, sleep=lambda _s: None, attempts=3)
    assert ["tmux", "-L", "devbox-launcher", "send-keys", "-t", "devbox-demo", "C-c"] in calls
    assert calls[-1] == ["tmux", "-L", "devbox-launcher", "kill-session", "-t", "devbox-demo"]


def test_pane_capture_joins_wrapped_lines():
    # the connect URL is longer than the pane is wide, so without -J capture-pane
    # returns it cut at the wrap point and the UI shows a truncated, dead link
    calls, run = recorder(stdout=PANE_CONNECTED)
    tmuxctl.connect_url("demo", run=run)
    assert calls[0] == [
        "tmux", "-L", "devbox-launcher", "capture-pane", "-J", "-t", "devbox-demo", "-p"]


def test_connect_url_extracts_from_pane():
    _, run = recorder(stdout="Connected\nContinue in https://claude.ai/code/session_01ABC\n")
    assert tmuxctl.connect_url("demo", run=run) == "https://claude.ai/code/session_01ABC"
    _, run = recorder(stdout="no url here")
    assert tmuxctl.connect_url("demo", run=run) is None


def test_connect_url_matches_environment_query_form():
    _, run = recorder(stdout=(
        "Continue coding in the Claude mobile app or "
        "https://claude.ai/code?environment=env_01Rb8TnKqW3xMzJ5vHd7Ls2Y\n"
    ))
    assert tmuxctl.connect_url("demo", run=run) == "https://claude.ai/code?environment=env_01Rb8TnKqW3xMzJ5vHd7Ls2Y"


# Pane text as captured from real listeners on the devbox.
PANE_CONNECTED = """·✔︎· Connected · beacon · main
    Capacity: 1/32 · New sessions will be created in the current directory
    devbox-beacon
Continue coding in the Claude mobile app or https://claude.ai/code?environment=env_01Rb8
"""
PANE_FAILED = """[09:18:05] Session failed: Process exited with error cse_01Tz9nBvK4mXp2Rd
·✔︎· Ready · homelab · main
    Capacity: 0/32 · New sessions will be created in the current directory
Code anywhere with the Claude mobile app or https://claude.ai/code?environment=env_01Qw4
"""
PANE_READY = """·✔︎· Ready · homelab · main
    Capacity: 0/32 · New sessions will be created in the current directory
"""
# Seen ~3s into a start: the bridge has connected to the cloud but no session is
# live yet — the adopted one is about to die. "Connected" alone means nothing.
PANE_CONNECTED_NO_SESSION = """·✔︎· Connected · homelab · main
    Capacity: 0/32 · New sessions will be created in the current directory
Continue coding in the Claude mobile app or https://claude.ai/code?environment=env_01Qw4
"""


def sequence(stdouts):
    """Runner returning each stdout in turn; repeats the last one forever."""
    remaining = list(stdouts)
    def run(cmd):
        out = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    return run


def test_health_is_connected_when_a_session_is_live():
    _, run = recorder(stdout=PANE_CONNECTED)
    assert tmuxctl.health("demo", run=run) == tmuxctl.CONNECTED


def test_health_is_connected_in_single_session_resume_mode():
    # A listener started with --session-id runs in classic single-session mode,
    # which prints "Single session · exits when complete" INSTEAD of the
    # "Capacity: N/32" line. Without this the launcher parks a perfectly healthy
    # resumed listener at STARTING forever.
    pane = (
        "·✔︎· devbox-notes · notes · main\n"
        "    Single session · exits when complete\n"
        "Continue coding in the Claude mobile app or "
        "https://claude.ai/code/session_01Xk7PqMwTn4RsLv9Bd2Hy6C\n"
        "space to show QR code\n"
    )
    _, run = recorder(stdout=pane)
    assert tmuxctl.health("demo", run=run) == tmuxctl.CONNECTED


def test_health_is_failed_when_the_session_could_not_start():
    _, run = recorder(stdout=PANE_FAILED)
    assert tmuxctl.health("demo", run=run) == tmuxctl.FAILED


def test_health_is_ready_when_registered_without_a_session():
    _, run = recorder(stdout=PANE_READY)
    assert tmuxctl.health("demo", run=run) == tmuxctl.READY


def test_health_needs_a_live_session_not_just_a_connected_bridge():
    _, run = recorder(stdout=PANE_CONNECTED_NO_SESSION)
    assert tmuxctl.health("demo", run=run) == tmuxctl.READY


def test_wait_for_health_keeps_waiting_through_a_sessionless_connect():
    # the transient state that must not be mistaken for success: connected at
    # 0/32, then the adopted session dies
    run = sequence([PANE_CONNECTED_NO_SESSION, PANE_FAILED])
    assert tmuxctl.wait_for_health("demo", run=run, sleep=lambda _s: None) == tmuxctl.FAILED


def test_health_is_starting_before_the_bridge_reports_anything():
    _, run = recorder(stdout="")
    assert tmuxctl.health("demo", run=run) == tmuxctl.STARTING


def test_health_prefers_the_current_state_over_an_earlier_failure_line():
    # a failure scrolled up the pane, but the listener has since connected
    _, run = recorder(stdout=PANE_FAILED + PANE_CONNECTED)
    assert tmuxctl.health("demo", run=run) == tmuxctl.CONNECTED


def test_wait_for_health_polls_until_connected():
    run = sequence(["", PANE_READY, PANE_CONNECTED])
    assert tmuxctl.wait_for_health("demo", run=run, sleep=lambda _s: None) == tmuxctl.CONNECTED


def test_wait_for_health_does_not_trust_a_connect_that_immediately_dies():
    # a doomed session occupies a slot for ~1s before the child exits, so the
    # pane reads 1/32 on the way to failing — CONNECTED has to hold to count
    run = sequence([PANE_READY, PANE_CONNECTED, PANE_FAILED])
    assert tmuxctl.wait_for_health(
        "demo", run=run, sleep=lambda _s: None) == tmuxctl.FAILED


def test_wait_for_health_stops_as_soon_as_it_fails():
    run = sequence(["", PANE_FAILED])
    assert tmuxctl.wait_for_health("demo", run=run, sleep=lambda _s: None) == tmuxctl.FAILED


def test_wait_for_health_gives_up_and_reports_the_last_state():
    run = sequence([PANE_READY])
    assert tmuxctl.wait_for_health(
        "demo", run=run, sleep=lambda _s: None, attempts=3) == tmuxctl.READY


# A listener that died the instant it spawned, captured off the devbox after the
# box's `claude` CLI lost its login. remain-on-exit is what keeps this readable.
PANE_DEAD_LOGGED_OUT = """Error: You must be logged in to use Remote Control.

Remote Control is only available with claude.ai subscriptions. Please use `/login` to sign in with your claude.ai account.
"""


def test_health_is_failed_when_the_pane_is_dead():
    # the command has exited; whatever the text says, this listener is not
    # coming up, and its pane never grows a Capacity line to prove it
    run = responder(lambda cmd: (0, "1") if "list-panes" in cmd else (0, PANE_DEAD_LOGGED_OUT))
    assert tmuxctl.health("demo", run=run) == tmuxctl.FAILED


def test_health_reads_a_live_pane_normally():
    # guards the dead check itself: "0" is a live pane, not a dead one
    run = responder(lambda cmd: (0, "0") if "list-panes" in cmd else (0, PANE_CONNECTED))
    assert tmuxctl.health("demo", run=run) == tmuxctl.CONNECTED


def test_wait_for_health_fails_fast_when_the_session_vanished():
    # A session that disappears after start has failed, but polling an absent
    # session read as STARTING: capture-pane errors, so the pane looked merely
    # empty. Start then burned its full 15s budget and reported nothing wrong —
    # the "clicking Start does nothing" bug.
    polls = []
    def handler(cmd):
        if "has-session" in cmd:
            polls.append(cmd)
            return (1, "")
        return (0, "")
    assert tmuxctl.wait_for_health(
        "demo", run=responder(handler), sleep=lambda _s: None) == tmuxctl.FAILED
    assert len(polls) == 1          # conclusive at once, nothing to wait for


def test_stop_kills_a_dead_session_without_waiting_for_it_to_exit():
    # C-c means nothing to a pane whose process is already gone, so the graceful
    # path would burn its whole poll budget before the backstop fired
    calls = []
    def handler(cmd):
        calls.append(cmd)
        return (0, "1") if "list-panes" in cmd else (0, "")
    tmuxctl.stop("demo", run=responder(handler), sleep=lambda _s: None)
    assert not any("send-keys" in c for c in calls)
    assert calls[-1] == ["tmux", "-L", "devbox-launcher", "kill-session", "-t", "devbox-demo"]


def test_stop_returns_as_soon_as_the_listener_exits_into_a_dead_pane():
    # remain-on-exit means a clean C-c exit leaves the session standing, so
    # waiting for has-session to fail would burn the whole budget every time
    # and only ever finish via the backstop
    calls, sleeps = [], []
    exited = {"yet": False}
    def handler(cmd):
        calls.append(cmd)
        if "send-keys" in cmd:
            exited["yet"] = True          # claude shuts down on the interrupt
            return (0, "")
        if "list-panes" in cmd:
            return (0, "1" if exited["yet"] else "0")
        return (0, "")                    # has-session: the session never goes away
    tmuxctl.stop("demo", run=responder(handler), sleep=lambda s: sleeps.append(s))
    assert any("send-keys" in c for c in calls)
    assert len(sleeps) == 1               # noticed on the first poll, not the twelfth
    # and the emptied session is cleared, or the UI shows it as failed forever
    assert calls[-1] == ["tmux", "-L", "devbox-launcher", "kill-session", "-t", "devbox-demo"]


def test_failure_reason_reads_the_error_off_a_dead_pane():
    run = responder(lambda cmd: (0, PANE_DEAD_LOGGED_OUT))
    assert tmuxctl.failure_reason("demo", run=run) == (
        "Error: You must be logged in to use Remote Control.")


def test_failure_reason_reads_the_whole_scrollback():
    # the error is the first thing printed, so it scrolls out of the visible
    # pane as the shell pads it — -S - is what keeps it reachable
    calls = []
    def handler(cmd):
        calls.append(cmd)
        return (0, PANE_DEAD_LOGGED_OUT)
    tmuxctl.failure_reason("demo", run=responder(handler))
    assert calls[0][-2:] == ["-S", "-"]


def test_failure_reason_falls_back_to_the_first_line_it_finds():
    run = responder(lambda cmd: (0, "\n\nbash: claude: command not found\n"))
    assert tmuxctl.failure_reason("demo", run=run) == "bash: claude: command not found"


def test_failure_reason_is_none_when_the_pane_says_nothing():
    run = responder(lambda cmd: (0, "\n\n\n"))
    assert tmuxctl.failure_reason("demo", run=run) is None


def test_answer_enable_prompt_only_when_present():
    calls, run = recorder(stdout="Enable Remote Control? (y/n)")
    assert tmuxctl.answer_enable_prompt_if_present("demo", run=run) is True
    assert any("send-keys" in c for c in calls)
    calls, run = recorder(stdout="Connected")
    assert tmuxctl.answer_enable_prompt_if_present("demo", run=run) is False
    assert not any("send-keys" in c for c in calls)


# --- the batched snapshot ----------------------------------------------------
# The page asks about every project several times a minute. Asking tmux once per
# project cost four subprocesses each; these two whole-socket queries plus one
# capture per *running* session answer the same questions for the whole list.

SESSIONS = "devbox-demo 1000\ndevbox-blog 900\nlauncher-login 500\n"


def snapshot_run(panes=None, dead=(), sessions=SESSIONS, sessions_rc=0):
    panes = panes or {}
    calls = []

    def run(cmd):
        calls.append(cmd)
        if "list-sessions" in cmd:
            return subprocess.CompletedProcess(cmd, sessions_rc, stdout=sessions, stderr="")
        if "list-panes" in cmd:
            out = "".join(f"{name} 1\n" for name in dead)
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        if "capture-pane" in cmd:
            target = cmd[cmd.index("-t") + 1]
            return subprocess.CompletedProcess(cmd, 0, stdout=panes.get(target, ""), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return calls, run


def test_snapshot_reports_a_stopped_project_without_asking_about_its_pane():
    calls, run = snapshot_run()
    status = tmuxctl.snapshot(["webapp"], run=run, now=lambda: 2000)["webapp"]
    assert status.running is False and status.state == "stopped"
    assert status.age is None
    assert not [c for c in calls if "capture-pane" in c]


def test_snapshot_reads_health_and_url_for_a_live_session():
    calls, run = snapshot_run(panes={"devbox-demo": PANE_CONNECTED})
    status = tmuxctl.snapshot(["demo"], run=run, now=lambda: 2000)["demo"]
    assert status.running is True and status.state == tmuxctl.CONNECTED
    assert status.url == "https://claude.ai/code?environment=env_01Rb8"
    # one capture for the session, not one per question asked of it
    assert len([c for c in calls if "capture-pane" in c]) == 1


def test_snapshot_costs_two_calls_plus_one_per_running_session():
    calls, run = snapshot_run(panes={"devbox-demo": PANE_CONNECTED, "devbox-blog": PANE_CONNECTED})
    tmuxctl.snapshot(["demo", "blog", "webapp", "scratch"], run=run, now=lambda: 2000)
    assert len(calls) == 4  # list-sessions + list-panes + 2 captures


def test_snapshot_ages_a_session_from_its_creation_time():
    _, run = snapshot_run(panes={"devbox-demo": PANE_CONNECTED})
    assert tmuxctl.snapshot(["demo"], run=run, now=lambda: 1090)["demo"].age == 90


def test_snapshot_reports_a_dead_pane_as_failed_with_its_reason():
    _, run = snapshot_run(panes={"devbox-demo": PANE_DEAD_LOGGED_OUT}, dead=["devbox-demo"])
    status = tmuxctl.snapshot(["demo"], run=run, now=lambda: 2000)["demo"]
    assert status.state == tmuxctl.FAILED
    assert status.reason and status.reason.startswith("Error:")
    assert status.url is None  # a failed listener's stale link leads nowhere


def test_snapshot_survives_a_tmux_server_that_is_not_running():
    # no sessions at all: every project is simply stopped, not an exception
    _, run = snapshot_run(sessions="", sessions_rc=1)
    assert tmuxctl.snapshot(["demo"], run=run, now=lambda: 2000)["demo"].state == "stopped"


def test_snapshot_counts_the_live_sessions():
    pane = PANE_CONNECTED + "\nCapacity: 3/32\n"
    _, run = snapshot_run(panes={"devbox-demo": pane})
    assert tmuxctl.snapshot(["demo"], run=run, now=lambda: 2000)["demo"].sessions == 3


def test_snapshot_has_no_count_for_a_single_session_listener():
    _, run = snapshot_run(panes={"devbox-demo": "Single session · exits when complete\n"})
    assert tmuxctl.snapshot(["demo"], run=run, now=lambda: 2000)["demo"].sessions is None


def test_snapshot_takes_the_last_url_on_the_pane():
    # a retried listener leaves the previous attempt's link in the scrollback;
    # the live session is the one printed last
    pane = ("Continue in https://claude.ai/code?environment=env_01OLD\n"
            "Session failed\n" + PANE_CONNECTED.replace("env_01Rb8", "env_01NEW"))
    _, run = snapshot_run(panes={"devbox-demo": pane})
    assert tmuxctl.snapshot(["demo"], run=run, now=lambda: 2000)["demo"].url.endswith("env_01NEW")


# --- stuck listeners ---------------------------------------------------------

def test_a_listener_with_a_blank_pane_is_starting_while_it_is_young():
    _, run = snapshot_run(panes={"devbox-demo": ""})
    assert tmuxctl.snapshot(["demo"], run=run, now=lambda: 1010)["demo"].state == tmuxctl.STARTING


def test_a_listener_still_blank_past_the_grace_period_is_stuck():
    _, run = snapshot_run(panes={"devbox-demo": ""})
    status = tmuxctl.snapshot(["demo"], run=run, now=lambda: 1000 + 31)["demo"]
    assert status.state == tmuxctl.STUCK


def test_the_grace_boundary_itself_is_not_yet_stuck():
    _, run = snapshot_run(panes={"devbox-demo": ""})
    at_limit = 1000 + tmuxctl.STARTING_GRACE
    assert tmuxctl.snapshot(["demo"], run=run, now=lambda: at_limit)["demo"].state == tmuxctl.STARTING


def test_a_registered_listener_waiting_for_a_session_is_never_stuck():
    # READY is a resting state, not a symptom: a listener with 0/32 capacity is
    # doing exactly what it should until the app connects to it. Flagging it
    # after 30s would call every idle listener broken.
    _, run = snapshot_run(panes={"devbox-demo": "Ready\nCapacity: 0/32\n"})
    status = tmuxctl.snapshot(["demo"], run=run, now=lambda: 1000 + 86400)["demo"]
    assert status.state == tmuxctl.READY


def test_a_connected_listener_is_never_stuck():
    _, run = snapshot_run(panes={"devbox-demo": PANE_CONNECTED})
    status = tmuxctl.snapshot(["demo"], run=run, now=lambda: 1000 + 86400)["demo"]
    assert status.state == tmuxctl.CONNECTED


# --- the pane tail -----------------------------------------------------------

def test_pane_tail_returns_the_last_lines_with_the_blanks_dropped():
    pane = "\n".join(f"line {n}" for n in range(1, 31)) + "\n\n\n"
    _, run = recorder(stdout=pane)
    assert tmuxctl.pane_tail("demo", run=run, lines=5) == [
        "line 26", "line 27", "line 28", "line 29", "line 30"]


def test_pane_tail_of_a_session_that_says_nothing_is_empty():
    _, run = recorder(stdout="   \n\n")
    assert tmuxctl.pane_tail("demo", run=run) == []


# --- hardening ---------------------------------------------------------------

def test_start_quotes_the_session_id_on_the_command_line():
    # pointer.py refuses such an id already; this is the second line of defence
    # for a string that is interpolated into `bash -lc`.
    calls, run = recorder()
    tmuxctl.start(Project(slug="demo", name="Demo", path=Path("/home/dev/projects/Demo")),
                  run=run, session_id="x; touch /tmp/pwned")
    assert calls[0][-1] == "claude remote-control --name devbox-demo --session-id 'x; touch /tmp/pwned'"


def test_start_raises_when_tmux_refuses_the_session():
    _, run = recorder(returncode=1)
    try:
        tmuxctl.start(Project(slug="demo", name="Demo", path=Path("/home/dev/projects/Demo")), run=run)
    except tmuxctl.StartError:
        pass
    else:
        raise AssertionError("a refused new-session should not pass silently")


def test_default_run_treats_a_timeout_as_a_failed_command(monkeypatch):
    def hang(cmd, **kwargs):
        assert kwargs["timeout"] == tmuxctl.TIMEOUT
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
    monkeypatch.setattr(tmuxctl.subprocess, "run", hang)
    result = tmuxctl.default_run(tmuxctl.tmux_args(["list-sessions"]))
    assert result.returncode != 0
    assert result.stdout == ""


def test_health_does_not_read_disconnected_as_connected():
    # "Connected" in pane matched the tail of "Disconnected", so a bridge that
    # had dropped read as registered and waiting rather than still starting.
    assert tmuxctl._health_from("·✘· Disconnected · demo · main\n", dead=False) == tmuxctl.STARTING
    assert tmuxctl._health_from("·✔︎· Connected · demo · main\n", dead=False) == tmuxctl.READY
