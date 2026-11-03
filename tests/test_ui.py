import re
import shutil
import subprocess

import pytest

from launcher import ui


def scan_for_unterminated_strings(js: str) -> list[int]:
    """Line numbers where a quoted JS string literal runs off the end of a line.

    `_JS` is Python source that happens to be JavaScript, so a stray escape —
    a `'\\n'` in a non-raw string, say — turns into a real newline and leaves
    the literal open. JavaScript then fails to parse the *whole* block, which
    is silent: the forms still work, so the page merely stops auto-refreshing.
    Comments are skipped because apostrophes in prose ("details' toggle event")
    would otherwise read as an opening quote.
    """
    bad: list[int] = []
    i, line, n = 0, 1, len(js)
    quote = None
    while i < n:
        c = js[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            elif c == "\n":
                bad.append(line)
                quote = None          # resync so one fault does not cascade
                line += 1
            i += 1
            continue
        if c == "\n":
            line += 1
            i += 1
        elif c == "/" and i + 1 < n and js[i + 1] == "/":
            while i < n and js[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and js[i + 1] == "*":
            i += 2
            while i + 1 < n and not (js[i] == "*" and js[i + 1] == "/"):
                line += js[i] == "\n"
                i += 1
            i += 2
        elif c == "`":                # template literals may span lines
            i += 1
            while i < n and js[i] != "`":
                if js[i] == "\\":
                    i += 1
                line += js[i] == "\n"
                i += 1
            i += 1
        else:
            if c in "\"'":
                quote = c
            i += 1
    return bad


def test_the_client_script_has_no_unterminated_string_literals():
    assert scan_for_unterminated_strings(ui._JS) == []


def test_the_scanner_would_actually_catch_the_bug_it_guards_against():
    # The exact regression: a literal newline inside a single-quoted literal.
    # It flags the offending line; the resync then reads the orphaned closing
    # quote as opening a new literal, so trailing lines may be flagged too.
    # Pinpointing the first fault is the contract — the cascade is noise.
    assert 1 in scan_for_unterminated_strings("var a = 'oops\n';\n")


def test_the_scanner_does_not_cry_wolf_over_ordinary_javascript():
    clean = (
        "var url = 'https://example.com/a//b';   // details' toggle event\n"
        "var s = \"it's fine\";\n"
        "var esc = 'a\\nb';\n"
        "/* a block comment's apostrophe */\n"
    )
    assert scan_for_unterminated_strings(clean) == []


def test_the_pane_tail_joins_on_an_escaped_newline():
    # Belt and braces for the specific line that broke: the emitted JavaScript
    # must contain a backslash and an n, not a line break.
    assert r"join('\n')" in ui._JS


def test_the_rendered_page_embeds_a_parsable_script():
    page = ui.page(rows=["<div class='row'></div>"], count="1 of 1 running",
                   usage="RAM 1 of 2 GB", signed_in=True)
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", page, re.S)
    assert scripts, "the page should carry its client script inline"
    for block in scripts:
        assert scan_for_unterminated_strings(block) == []


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_node_parses_the_client_script():
    # The authoritative check, when a JS engine happens to be available.
    result = subprocess.run(["node", "--check", "-"], input=ui._JS,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_agent_row_states():
    from launcher import ui
    html = ui.agent_row(key="notes/drafts", name="drafts", display="notes/drafts", state="stopped",
                        status="", running=False, parked=True, scratch=False, archivable=True,
                        resumable=True, broken=False, mode=None, mission="Alumni")
    assert 'class="row agent parked"' in html and "Resume" in html and "Archive" in html
    assert 'action="/agents/notes/drafts/fresh"' in html and "Alumni" in html


def test_unrecorded_mode_reads_as_the_settings_default():
    from launcher import ui
    assert ui.permission_label(None) == "Default (settings)"
    assert ui.permission_label("default") == "Default (settings)"
    assert "Ask" not in ui._permission_agent("notes/main", None, False)


def test_terminal_command_is_built_from_the_page_address():
    assert "'ssh -t ' + button.dataset.user + '@' + location.hostname" in ui._JS


def test_run_view_names_an_interrupt():
    html = ui.run_view(slug="demo", command="sleep 100", cwd="~", output="", running=False,
                       exit_code=130, signal=2)
    assert "Finished — exit code 130 (interrupted)" in html
    html = ui.run_view(slug="demo", command="x", cwd="~", output="", running=False,
                       exit_code=137, signal=9)
    assert "exit code 137 (killed by signal 9)" in html


def test_run_script_hints_at_a_silent_prompt():
    assert "Waiting for input? Answer below." in ui._RUN_JS


def _agent(**kw):
    args = dict(key="notes/main", name="main", display="notes·main", state="connected",
                status="Live", running=True, parked=False, scratch=False, archivable=False,
                resumable=True, broken=False, mode=None, ssh_user="dev", branch="main")
    args.update(kw)
    return ui.agent_row(**args)


def test_collapsed_agent_shows_branch_state_terminal_and_stop():
    html = _agent()
    head, detail = html.split('class="adetail"')
    assert "Agent on <code>main</code>" in head and "Live" in head
    assert "data-terminal=" in head and 'data-user="dev"' in head
    assert 'action="/agents/notes/main/stop"' in head and ">Stop</button>" in head
    assert 'd="M6 3.5 10.5 8 6 12.5"' in head


def test_expanded_controls_are_in_the_markup_not_fetched():
    _, detail = _agent().split('class="adetail"')
    for text in ("Fresh conversation", "Permission mode", "Screen now",
                 "Paste the terminal command"):
        assert text in detail
    _, detail = _agent(running=False, status="", archivable=True,
                       stale=True).split('class="adetail"')
    assert "Fresh anyway" in detail and "Park" in detail and "Archive" in detail
    assert ">Resume</button>" not in detail


def test_reason_shows_while_the_agent_is_collapsed():
    head, _ = _agent(reason="Remote Control is off").split('class="adetail"')
    assert "Remote Control is off" in head


def test_branch_names_are_escaped_in_title_and_badge():
    # A branch name comes from the repo and may hold markup.
    evil = "x<img src=x onerror=alert(1)>"
    for html in (_agent(branch=evil),
                 ui.project_row(slug="demo", name="demo", ident="~", branch=evil)):
        assert "<img" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html


def test_terminal_target_is_quoted_for_both_shells():
    # The local shell strips the double quotes; the box's login shell gets
    # '=notes/main' still single-quoted, which zsh needs too.
    expected = 'data-terminal="tmux -u -L agents attach -t &quot;&#x27;=notes/main&#x27;&quot;"'
    assert expected in _agent(display="notes/main")


def test_agent_without_a_known_branch_is_titled_by_name():
    assert '<div class="name">main</div>' in _agent(branch=None)


def test_project_footer_holds_the_project_actions():
    html = ui.project_row(slug="demo", name="demo", ident="~/projects/demo", run="",
                          branch="main")
    foot = html[html.index('class="pfoot"'):]
    for text in ("+ New agent on its own branch", "Quick question", "Run a command"):
        assert text in foot
    assert 'class="pbranch"' in html


def test_project_without_a_branch_or_extras_has_neither_badge_nor_them():
    html = ui.project_row(slug="demo", name="demo", ident="~/x")
    assert "pbranch" not in html and "Run a command" not in html
    assert "Quick question" in html


def test_nest_puts_agent_rows_inside_their_card():
    rows = [{"slug": "demo", "html": ui.project_row(slug="demo", name="demo", ident="~")},
            {"slug": "demo/main", "html": "<div id=a></div>"},
            {"slug": "other", "html": ui.project_row(slug="other", name="other", ident="~")}]
    out = ui.nest(rows)
    assert len(out) == 2 and '<div class="alist"><div id=a></div></div>' in out[0]
    assert '<div class="alist"></div>' in out[1]
