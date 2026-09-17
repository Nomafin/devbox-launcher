from pathlib import Path

from launcher import pointer


def test_pointer_path_encodes_cwd_the_way_claude_does(tmp_path):
    path = pointer.pointer_path(Path("/home/dev/projects/homelab"), home=tmp_path)
    assert path == (
        tmp_path / ".claude" / "projects"
        / "-home-dev-projects-homelab" / "bridge-pointer.json"
    )


def test_clear_removes_the_pointer_file(tmp_path):
    cwd = Path("/home/dev/projects/homelab")
    target = pointer.pointer_path(cwd, home=tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text('{"environmentId": "env_01ARCHIVED"}')

    assert pointer.clear(cwd, home=tmp_path) is True
    assert not target.exists()


def test_clear_is_a_noop_when_there_is_no_pointer(tmp_path):
    assert pointer.clear(Path("/home/dev/projects/homelab"), home=tmp_path) is False


def _write_pointer(cwd, home, body):
    target = pointer.pointer_path(cwd, home=home)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    return target


def test_session_id_reads_the_cloud_session_from_the_pointer(tmp_path):
    # `claude remote-control --session-id` resolves server-side, so it needs the
    # cloud id (session_…) the bridge recorded — NOT the local transcript uuid,
    # which the server cannot look up.
    cwd = Path("/home/dev/projects/notes")
    _write_pointer(cwd, tmp_path, '{"sessionId": "session_01Xk7P", '
                                  '"environmentId": "env_01NA8q"}')
    assert pointer.session_id(cwd, home=tmp_path) == "session_01Xk7P"


def test_session_id_is_none_without_a_pointer(tmp_path):
    # A project that has never run a listener starts fresh rather than resuming.
    assert pointer.session_id(Path("/home/dev/projects/notes"), home=tmp_path) is None


def test_session_id_is_none_when_the_pointer_is_malformed(tmp_path):
    # A truncated pointer (the box lost power mid-write) must not stop Start
    # working — it should fall back to a fresh listener.
    cwd = Path("/home/dev/projects/notes")
    _write_pointer(cwd, tmp_path, '{"sessionId": "session_01Q3')
    assert pointer.session_id(cwd, home=tmp_path) is None


def test_session_id_is_none_when_the_pointer_has_no_session(tmp_path):
    cwd = Path("/home/dev/projects/notes")
    _write_pointer(cwd, tmp_path, '{"environmentId": "env_01NA8q"}')
    assert pointer.session_id(cwd, home=tmp_path) is None


def test_session_id_rejects_an_id_that_is_not_one(tmp_path, caplog):
    # The id lands on a `bash -lc` command line, so a pointer that has been
    # tampered with must read as "no pointer", not be passed through.
    cwd = Path("/home/dev/projects/notes")
    _write_pointer(cwd, tmp_path, '{"sessionId": "x; touch /tmp/pwned"}')
    with caplog.at_level("WARNING", logger="launcher"):
        assert pointer.session_id(cwd, home=tmp_path) is None
    assert "malformed sessionId" in caplog.text


def test_session_id_rejects_a_non_string(tmp_path):
    cwd = Path("/home/dev/projects/notes")
    _write_pointer(cwd, tmp_path, '{"sessionId": 42}')
    assert pointer.session_id(cwd, home=tmp_path) is None
