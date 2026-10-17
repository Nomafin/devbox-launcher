import json

from launcher.agents import cli
from launcher.agents.model import AgentError


def run(world, capsys, *argv):
    code = cli.main(list(argv), ctx=world.ctx)
    out, err = capsys.readouterr()
    return code, out, err


def test_start_prints_display_name(world, capsys):
    code, out, _ = run(world, capsys, "start", "--dir", str(world.repo))
    assert (code, out) == (0, "notes·main\n")


def test_unknown_agent_exit_code(world, capsys):
    code, out, err = run(world, capsys, "start", "--dir", str(world.repo), "drafts")
    assert code == AgentError.UNKNOWN and out == ""
    assert err.startswith("agentctl: no agent notes/drafts")


def test_create_flag(world, capsys):
    code, out, _ = run(world, capsys, "start", "--dir", str(world.repo), "drafts", "--create")
    assert (code, out) == (0, "notes·drafts\n")


def test_resolve_does_not_start(world, capsys):
    assert run(world, capsys, "resolve", "--dir", str(world.repo), "drafts")[:2] == (3, "notes·drafts\n")
    assert run(world, capsys, "resolve", "--dir", str(world.repo))[:2] == (0, "notes·main\n")
    assert world.tmux.sessions == {}


def test_key_form_and_json_ls(world, capsys):
    run(world, capsys, "new", "notes/drafts")
    code, out, _ = run(world, capsys, "--json", "ls")
    data = json.loads(out)
    assert code == 0 and [a["key"] for a in data["agents"]] == ["notes/drafts"]


def test_extra_args_reach_claude(world, capsys):
    run(world, capsys, "start", "--dir", str(world.repo), "--", "--model", "opus")
    assert world.tmux.argv("notes·main")[-2:] == ["--model", "opus"]


def test_json_error(world, capsys):
    code, out, _ = run(world, capsys, "--json", "archive", "notes/main")
    assert code == AgentError.REFUSED
    assert json.loads(out)["code"] == AgentError.REFUSED


def test_mode_and_brief_status(world, capsys):
    run(world, capsys, "start", "notes/main")
    assert run(world, capsys, "mode", "notes/main", "--set", "plan")[0] == 0
    code, out, _ = run(world, capsys, "--json", "brief-status", "notes/main")
    assert code == 0 and json.loads(out)["stale"] is False


def test_text_ls(world, capsys):
    run(world, capsys, "start", "notes/main")
    code, out, _ = run(world, capsys, "ls", "--dir", str(world.repo))
    assert code == 0 and out.startswith("notes·main") and "running" in out


def test_resolve_json_unknown_is_one_object(world, capsys):
    code, out, _ = run(world, capsys, "--json", "resolve", "--dir", str(world.repo), "drafts")
    assert code == 3
    assert json.loads(out) == {"error": "no agent notes/drafts", "code": 3, "display": "notes·drafts"}
    assert out.count("\n") == 1


def test_resolve_passes_other_errors_through(world, capsys, monkeypatch):
    def refuse(ctx, place, name):
        raise AgentError(AgentError.REFUSED, "nope")
    monkeypatch.setattr(cli.service, "lookup", refuse)
    code, out, err = run(world, capsys, "resolve", "--dir", str(world.repo), "drafts")
    assert (code, out) == (AgentError.REFUSED, "")
    assert err == "agentctl: nope\n"
