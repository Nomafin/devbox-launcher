import json
from pathlib import Path

from launcher import state


def test_desired_is_empty_when_there_is_no_state_file(tmp_path):
    assert state.desired(tmp_path / "desired.json") == []


def test_record_started_then_desired_returns_the_slug(tmp_path):
    target = tmp_path / "sub" / "desired.json"   # parent dir created on demand
    state.record_started("homelab", target)
    assert state.desired(target) == ["homelab"]


def test_record_started_is_idempotent(tmp_path):
    target = tmp_path / "desired.json"
    state.record_started("homelab", target)
    state.record_started("homelab", target)
    assert state.desired(target) == ["homelab"]


def test_record_stopped_removes_the_slug(tmp_path):
    target = tmp_path / "desired.json"
    state.record_started("homelab", target)
    state.record_started("notes", target)
    state.record_stopped("homelab", target)
    assert state.desired(target) == ["notes"]


def test_record_stopped_is_a_noop_for_an_unknown_slug(tmp_path):
    target = tmp_path / "desired.json"
    state.record_started("homelab", target)
    state.record_stopped("ghost", target)
    assert state.desired(target) == ["homelab"]


def test_a_malformed_state_file_reads_as_empty(tmp_path):
    # boot path: bad state must never stop the web UI from coming up
    target = tmp_path / "desired.json"
    target.write_text("{not json")
    assert state.desired(target) == []


def test_a_state_file_of_the_wrong_shape_reads_as_empty(tmp_path):
    target = tmp_path / "desired.json"
    target.write_text(json.dumps({"running": "homelab"}))
    assert state.desired(target) == []


def test_desired_drops_non_string_entries_but_keeps_the_rest(tmp_path):
    target = tmp_path / "desired.json"
    target.write_text(json.dumps({"running": ["homelab", 5, None, "notes"]}))
    assert state.desired(target) == ["homelab", "notes"]


def test_writing_leaves_no_temp_file_behind(tmp_path):
    target = tmp_path / "desired.json"
    state.record_started("homelab", target)
    assert [p.name for p in tmp_path.iterdir()] == ["desired.json"]


def test_state_path_defaults_under_xdg_state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert state.state_path() == tmp_path / "devbox-launcher" / "desired.json"


def test_state_path_falls_back_to_local_state(monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/dev")))
    assert state.state_path() == Path("/home/dev/.local/state/devbox-launcher/desired.json")


def test_parallel_is_empty_by_default(tmp_path):
    assert state.parallel(tmp_path / "desired.json") == []


def test_set_parallel_on_and_off(tmp_path):
    target = tmp_path / "desired.json"
    state.set_parallel("homelab", True, target)
    state.set_parallel("homelab", True, target)
    assert state.parallel(target) == ["homelab"]
    state.set_parallel("homelab", False, target)
    assert state.parallel(target) == []


def test_parallel_and_running_do_not_overwrite_each_other(tmp_path):
    target = tmp_path / "desired.json"
    state.record_started("homelab", target)
    state.set_parallel("notes", True, target)
    state.record_started("blog", target)
    state.record_stopped("homelab", target)
    assert state.desired(target) == ["blog"]
    assert state.parallel(target) == ["notes"]


def test_an_old_state_file_without_parallel_still_reads(tmp_path):
    target = tmp_path / "desired.json"
    target.write_text(json.dumps({"running": ["homelab"]}))
    assert state.parallel(target) == []
    assert state.desired(target) == ["homelab"]


def test_instances_are_empty_without_a_state_file(tmp_path):
    assert state.instances(tmp_path / "desired.json") == {}


def test_record_instance_groups_by_project(tmp_path):
    target = tmp_path / "desired.json"
    state.record_instance("homelab", "fix-auth", target)
    state.record_instance("homelab", "wip", target)
    state.record_instance("notes", "spike", target)
    assert state.instances(target) == {"homelab": ["fix-auth", "wip"], "notes": ["spike"]}


def test_record_instance_is_idempotent(tmp_path):
    target = tmp_path / "desired.json"
    state.record_instance("homelab", "wip", target)
    state.record_instance("homelab", "wip", target)
    assert state.instances(target) == {"homelab": ["wip"]}


def test_forget_instance_drops_the_project_when_it_empties(tmp_path):
    target = tmp_path / "desired.json"
    state.record_instance("homelab", "wip", target)
    state.forget_instance("homelab", "wip", target)
    assert state.instances(target) == {}


def test_forget_instance_is_a_noop_for_an_unknown_name(tmp_path):
    target = tmp_path / "desired.json"
    state.record_instance("homelab", "wip", target)
    state.forget_instance("homelab", "ghost", target)
    state.forget_instance("ghost", "wip", target)
    assert state.instances(target) == {"homelab": ["wip"]}


def test_instances_live_alongside_running_and_parallel(tmp_path):
    # one file, three keys: a write to any of them must leave the others alone
    target = tmp_path / "desired.json"
    state.record_started("homelab", target)
    state.set_parallel("homelab", True, target)
    state.record_instance("homelab", "wip", target)
    state.record_started("homelab--wip", target)
    assert state.desired(target) == ["homelab", "homelab--wip"]
    assert state.parallel(target) == ["homelab"]
    assert state.instances(target) == {"homelab": ["wip"]}


def test_a_malformed_instances_table_reads_as_empty(tmp_path):
    target = tmp_path / "desired.json"
    target.write_text(json.dumps({"instances": ["not", "a", "table"]}))
    assert state.instances(target) == {}
    target.write_text(json.dumps({"instances": {"homelab": "wip"}}))
    assert state.instances(target) == {"homelab": []}


def test_permission_modes_are_empty_by_default(tmp_path):
    target = tmp_path / "desired.json"
    assert state.permission_modes(target) == {}
    assert state.permission_mode("homelab", target) is None


def test_set_permission_mode_records_and_replaces(tmp_path):
    target = tmp_path / "desired.json"
    state.set_permission_mode("homelab", "auto", target)
    assert state.permission_mode("homelab", target) == "auto"
    state.set_permission_mode("homelab", "plan", target)
    assert state.permission_modes(target) == {"homelab": "plan"}


def test_setting_a_mode_to_none_forgets_it(tmp_path):
    # "no entry" is what makes the launcher pass no flag at all, so this is
    # how a row goes back to whatever the CLI would have chosen for itself
    target = tmp_path / "desired.json"
    state.set_permission_mode("homelab", "auto", target)
    state.set_permission_mode("homelab", None, target)
    assert state.permission_modes(target) == {}
    state.set_permission_mode("ghost", None, target)      # nothing to forget
    assert state.permission_modes(target) == {}


def test_permission_modes_live_alongside_running_and_parallel(tmp_path):
    target = tmp_path / "desired.json"
    state.record_started("homelab", target)
    state.set_parallel("wiki", True, target)
    state.set_permission_mode("homelab", "auto", target)
    state.record_started("notes", target)
    assert state.desired(target) == ["homelab", "notes"]
    assert state.parallel(target) == ["wiki"]
    assert state.permission_modes(target) == {"homelab": "auto"}


def test_a_malformed_permission_table_reads_as_empty(tmp_path):
    target = tmp_path / "desired.json"
    target.write_text(json.dumps({"permissionMode": ["auto"]}))
    assert state.permission_modes(target) == {}
    target.write_text(json.dumps({"permissionMode": {"homelab": 7, "notes": "auto"}}))
    assert state.permission_modes(target) == {"notes": "auto"}
