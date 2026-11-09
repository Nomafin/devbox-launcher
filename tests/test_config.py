from launcher import config


def _home(monkeypatch, tmp_path):
    monkeypatch.delenv("LAUNCHER_BASE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))


def test_default_without_anything(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    assert config.base_dir() == tmp_path / "projects"


def test_what_install_recorded_is_used_without_the_environment(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    record = tmp_path / ".config" / "devbox-launcher" / "base-dir"
    record.parent.mkdir(parents=True)
    record.write_text(f"{tmp_path}/dev\n")
    assert config.base_dir() == tmp_path / "dev"
    monkeypatch.setenv("LAUNCHER_BASE_DIR", "/elsewhere")
    assert str(config.base_dir()) == "/elsewhere"
