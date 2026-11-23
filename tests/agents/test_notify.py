from launcher.agents import notify


def test_no_url_means_no_push(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda *a, **k: called.append(a))
    notify.send(tmp_path, "hello")
    assert notify.url(tmp_path) is None and called == []


def test_url_from_the_installers_record_then_the_environment(tmp_path, monkeypatch):
    record = tmp_path / ".config" / "devbox-launcher" / "ntfy-url"
    record.parent.mkdir(parents=True)
    record.write_text("https://ntfy.example/topic\n")
    assert notify.url(tmp_path) == "https://ntfy.example/topic"
    monkeypatch.setenv("LAUNCHER_NTFY_URL", "https://ntfy.example/other")
    assert notify.url(tmp_path) == "https://ntfy.example/other"


def test_push_posts_the_message_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("LAUNCHER_NTFY_URL", "https://ntfy.example/topic")
    sent = []

    class Done:
        def close(self):
            pass

    def urlopen(request, timeout):
        sent.append((request.full_url, request.data))
        return Done()
    monkeypatch.setattr(notify.urllib.request, "urlopen", urlopen)
    notify.send(tmp_path, "notes/main woke notes/drafts")
    assert sent == [("https://ntfy.example/topic", b"notes/main woke notes/drafts")]

    def fail(request, timeout):
        raise OSError("offline")
    monkeypatch.setattr(notify.urllib.request, "urlopen", fail)
    notify.send(tmp_path, "x")
