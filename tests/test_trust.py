import json
import threading
from pathlib import Path

from launcher.trust import ensure_trusted


def test_seeds_flags_when_missing(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"projects": {}, "keepme": 1}))
    changed = ensure_trusted("/home/dev/projects/demo", config_path=cfg)
    assert changed is True
    data = json.loads(cfg.read_text())
    assert data["projects"]["/home/dev/projects/demo"]["hasTrustDialogAccepted"] is True
    assert data["remoteDialogSeen"] is True
    assert data["keepme"] == 1  # other keys preserved


def test_idempotent_second_call_is_noop(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({}))
    assert ensure_trusted("/p/x", config_path=cfg) is True
    assert ensure_trusted("/p/x", config_path=cfg) is False


def test_creates_file_when_absent(tmp_path):
    cfg = tmp_path / ".claude.json"
    assert ensure_trusted("/p/y", config_path=cfg) is True
    data = json.loads(cfg.read_text())
    assert data["projects"]["/p/y"]["hasTrustDialogAccepted"] is True


def test_preserves_existing_file_mode(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({}))
    cfg.chmod(0o600)
    ensure_trusted("/p/z", config_path=cfg)
    assert (cfg.stat().st_mode & 0o777) == 0o600


def test_new_file_is_private(tmp_path):
    cfg = tmp_path / ".claude.json"
    ensure_trusted("/p/w", config_path=cfg)
    assert (cfg.stat().st_mode & 0o777) == 0o600


def test_concurrent_ensure_trusted_calls_do_not_lose_either_write(tmp_path, monkeypatch):
    # Reproduces the boot-window race: the reconcile thread and a POST /start
    # handler can both call ensure_trusted at once, both writing through the
    # same fixed ".launcher.tmp" path. This test does not rely on real thread
    # scheduling luck — it uses events to deterministically pause thread A
    # *inside* its read-modify-write (patched write_text) and then proves,
    # deterministically, that thread B cannot make progress until A finishes.
    # Without the module-level lock, B runs concurrently on A's stale read and
    # A's later os.replace clobbers B's write, permanently losing project "b" —
    # this assertion then fails every single time, not flakily.
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({}))

    entered_write = threading.Event()
    release_a = threading.Event()
    paused_once = []

    real_write_text = Path.write_text

    def gated_write_text(self, *args, **kwargs):
        if self.name == cfg.name + ".launcher.tmp" and not paused_once:
            paused_once.append(True)
            entered_write.set()
            release_a.wait(timeout=2)
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", gated_write_text)

    thread_a = threading.Thread(target=lambda: ensure_trusted("/p/a", config_path=cfg))
    thread_a.start()
    assert entered_write.wait(timeout=2), "thread A never reached its write"

    finished_b = threading.Event()
    thread_b = threading.Thread(
        target=lambda: (ensure_trusted("/p/b", config_path=cfg), finished_b.set()))
    thread_b.start()

    # Deterministic, not a timing guess: while A holds the lock inside its
    # paused write, B literally cannot acquire it and finish — any wait here
    # returns False every time the lock is actually held.
    assert not finished_b.wait(timeout=0.2), "thread B finished while A still held the lock"

    release_a.set()
    thread_a.join(timeout=2)
    thread_b.join(timeout=2)
    assert finished_b.is_set()

    data = json.loads(cfg.read_text())  # must still be valid JSON
    assert "/p/a" in data["projects"]
    assert "/p/b" in data["projects"]
