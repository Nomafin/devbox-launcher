import json
import subprocess

from launcher import runctl


def responder(handler):
    """Runner dispatching on the command: handler(cmd) -> (returncode, stdout)."""
    def run(cmd):
        rc, out = handler(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
    return run


def recorder(handler=lambda cmd: (0, "")):
    calls = []
    def h(cmd):
        calls.append(cmd)
        return handler(cmd)
    return calls, responder(h)


TMUX = ["tmux", "-L", "devbox-launcher"]


# --- starting ---------------------------------------------------------------

def test_session_name_cannot_collide_with_a_listener_or_the_login():
    assert runctl.session_name("demo") == "launcher-run-demo"
    assert not runctl.session_name("demo").startswith("devbox-")
    assert runctl.session_name("x") != "launcher-login"


def test_start_runs_the_command_in_bash_in_the_given_dir(tmp_path):
    calls, run = recorder(lambda cmd: (1, "") if "has-session" in cmd else (0, ""))
    runctl.start("demo", "gcloud functions deploy x", tmp_path, run=run, directory=tmp_path)
    cmd = next(c for c in calls if "new-session" in c)
    assert cmd[:3] == TMUX
    # remain-on-exit, in the same invocation: a command that exits at once must
    # not take its output (or its exit status) with it
    assert cmd[3:8] == ["set-option", "-g", "remain-on-exit", "on", ";"]
    assert cmd[cmd.index("-s") + 1] == "launcher-run-demo"
    assert cmd[cmd.index("-c") + 1] == str(tmp_path)
    # passed as one argument, never re-split: the command is the user's, verbatim
    assert cmd[-3:] == ["bash", "-lc", "gcloud functions deploy x"]


def test_start_clears_a_finished_run_first(tmp_path):
    calls, run = recorder(lambda cmd: (0, ""))    # a session exists
    runctl.start("demo", "ls", tmp_path, run=run, directory=tmp_path)
    killed = [i for i, c in enumerate(calls) if "kill-session" in c]
    created = [i for i, c in enumerate(calls) if "new-session" in c]
    assert killed and created and killed[0] < created[0]
    assert calls[killed[0]][-1] == "launcher-run-demo"


def test_start_records_what_ran_and_where_per_project(tmp_path):
    state = tmp_path / "state"
    _, run = recorder(lambda cmd: (1, "") if "has-session" in cmd else (0, ""))
    runctl.start("demo", "echo hi", tmp_path, run=run, directory=state)
    assert json.loads((state / "run-demo.json").read_text()) == {
        "command": "echo hi", "cwd": str(tmp_path)}
    assert runctl.last("demo", state) == {"command": "echo hi", "cwd": str(tmp_path)}
    assert runctl.last("other", state) == {}


def test_start_raises_when_tmux_refuses(tmp_path):
    def handler(cmd):
        return (1, "") if "has-session" in cmd or "new-session" in cmd else (0, "")
    try:
        runctl.start("demo", "ls", tmp_path, run=responder(handler), directory=tmp_path)
    except runctl.StartError:
        return
    raise AssertionError("expected StartError")


def test_last_is_empty_for_a_bad_record(tmp_path):
    (tmp_path / "run-demo.json").write_text("not json")
    assert runctl.last("demo", tmp_path) == {}


# --- reading ----------------------------------------------------------------

def test_status_while_running():
    calls, run = recorder(lambda cmd: (0, "0 \n"))
    assert runctl.status("demo", run=run) == runctl.RunStatus(True, True, None)
    assert calls[0][calls[0].index("-t") + 1] == "launcher-run-demo"


def test_status_once_finished_carries_the_exit_code():
    run = responder(lambda cmd: (0, "1 3\n"))
    assert runctl.status("demo", run=run) == runctl.RunStatus(True, False, 3)


def test_status_without_a_session():
    assert runctl.status("demo", run=responder(lambda cmd: (1, ""))) == runctl.NONE


def test_statuses_answers_for_every_project_in_one_call():
    listing = ("devbox-demo 0 \n"
               "launcher-login 1 0\n"
               "launcher-run-demo 0 \n"
               "launcher-run-notes 1 2\n")
    calls, run = recorder(lambda cmd: (0, listing))
    assert runctl.statuses(run=run) == {
        "demo": runctl.RunStatus(True, True, None),
        "notes": runctl.RunStatus(True, False, 2),
    }
    assert len(calls) == 1 and "-a" in calls[0]


def test_statuses_is_empty_without_a_tmux_server():
    assert runctl.statuses(run=responder(lambda cmd: (1, ""))) == {}


def test_output_reads_the_joined_scrollback():
    calls, run = recorder(lambda cmd: (0, "hello\n"))
    runctl.output("demo", run=run)
    assert "-J" in calls[0] and calls[0][-2:] == ["-S", "-"]
    assert "launcher-run-demo" in calls[0]


def test_output_drops_tmux_notice_and_trailing_blank_lines():
    pane = "Deploying…\nDone.\n\nPane is dead (status 0, Thu Sep 17 18:00:00 2026)\n\n\n"
    assert runctl.output("demo", run=responder(lambda cmd: (0, pane))) == "Deploying…\nDone."


# --- driving ----------------------------------------------------------------

def test_send_input_is_literal_then_enter():
    calls, run = recorder()
    runctl.send_input("demo", "y; kill-server", run=run)
    assert calls == [
        TMUX + ["send-keys", "-t", "launcher-run-demo", "-l", "y; kill-server"],
        TMUX + ["send-keys", "-t", "launcher-run-demo", "Enter"],
    ]


def test_send_input_of_nothing_is_just_enter():
    # "press Enter to continue" is a prompt too, and -l with an empty string
    # is an error in tmux
    calls, run = recorder()
    runctl.send_input("demo", "", run=run)
    assert calls == [TMUX + ["send-keys", "-t", "launcher-run-demo", "Enter"]]


def test_interrupt_sends_ctrl_c():
    calls, run = recorder()
    runctl.interrupt("demo", run=run)
    assert calls == [TMUX + ["send-keys", "-t", "launcher-run-demo", "C-c"]]


def test_close_kills_the_session():
    calls, run = recorder()
    runctl.close("demo", run=run)
    assert calls == [TMUX + ["kill-session", "-t", "launcher-run-demo"]]
