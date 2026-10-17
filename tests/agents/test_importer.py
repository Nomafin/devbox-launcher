import fcntl
import json
import subprocess

from launcher import worktrees
from launcher.agents import importer, registry


def dbx_panes(rows):
    def run(cmd):
        assert cmd[:3] == ["tmux", "-L", "dbx"]
        return subprocess.CompletedProcess(cmd, 0, "".join(f"{n}\t{p}\n" for n, p in rows), "")
    return run


def test_imports_launcher_instance_keeping_branch(world, tmp_path):
    assert worktrees.create(world.repo, "intra") is None
    desired = tmp_path / "desired.json"
    desired.write_text(json.dumps({"instances": {"notes": ["intra"]}}))
    done = importer.run(world.ctx, desired_path=desired, dbx_run=dbx_panes([]), ask=lambda q: False)
    a = registry.load(world.ctx.root)["notes/intra"]
    assert a.branch == "intra" and a.workdir == str(worktrees.path(world.repo, "intra"))
    assert any("notes/intra" in line for line in done)


def test_dbx_main_aliases_are_skipped(world, tmp_path):
    rows = [("cc-notes_notes", str(world.repo)), ("cc-notes", str(world.repo))]
    importer.run(world.ctx, desired_path=tmp_path / "none.json", dbx_run=dbx_panes(rows),
                 ask=lambda q: True)
    assert registry.load(world.ctx.root) == {}


def test_dbx_worktree_session_is_adopted(world, tmp_path):
    assert worktrees.create(world.repo, "videos") is None
    rows = [("cc-notes_videos", str(worktrees.path(world.repo, "videos")))]
    importer.run(world.ctx, desired_path=tmp_path / "none.json", dbx_run=dbx_panes(rows),
                 ask=lambda q: False)
    assert registry.load(world.ctx.root)["notes/videos"].branch == "videos"


def test_shared_checkout_session_needs_confirmation(world, tmp_path):
    rows = [("cc-notes_live", str(world.repo))]
    importer.run(world.ctx, desired_path=tmp_path / "none.json", dbx_run=dbx_panes(rows),
                 ask=lambda q: False)
    assert "notes/live" not in registry.load(world.ctx.root)
    importer.run(world.ctx, desired_path=tmp_path / "none.json", dbx_run=dbx_panes(rows),
                 ask=lambda q: True)
    assert registry.load(world.ctx.root)["notes/live"].branch == "agent/live"


def test_dry_run_writes_nothing(world, tmp_path):
    rows = [("cc-notes_live", str(world.repo))]
    done = importer.run(world.ctx, dry_run=True, desired_path=tmp_path / "none.json",
                        dbx_run=dbx_panes(rows), ask=lambda q: True)
    assert registry.load(world.ctx.root) == {} and done
    assert not (world.ctx.root / "registry.json").exists()
    assert not (world.ctx.root / "registry.lock").exists()


def test_questions_are_asked_while_the_registry_lock_is_free(world, tmp_path):
    rows = [("cc-notes_live", str(world.repo))]
    seen = []

    def ask(question):
        world.ctx.root.mkdir(parents=True, exist_ok=True)
        with open(world.ctx.root / "registry.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)   # raises if the import holds it
            fcntl.flock(lock, fcntl.LOCK_UN)
        seen.append(question)
        return True

    importer.run(world.ctx, desired_path=tmp_path / "none.json", dbx_run=dbx_panes(rows), ask=ask)
    assert seen and registry.load(world.ctx.root)["notes/live"].branch == "agent/live"
