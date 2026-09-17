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
