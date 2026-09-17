import logging

import uvicorn

from launcher.__main__ import main


def test_main_configures_logging_so_info_records_actually_reach_a_handler(monkeypatch, capsys):
    # uvicorn's default logging config adds no root handler, so INFO records
    # from launcher's own loggers (the boot-time "restore: started <slug>"
    # line the runbook greps for) are dropped by logging.lastResort, which
    # only passes WARNING and above. This does not assume that basicConfig
    # fixes it — it drives an actual "launcher.restore" logger call after
    # main()'s setup and checks the record really reached stderr.
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)  # never bind a real port

    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    for h in saved_handlers:
        root.removeHandler(h)
    root.setLevel(logging.WARNING)
    try:
        main()
        logging.getLogger("launcher.restore").info("restore: started demo-project")
    finally:
        for h in root.handlers[:]:
            root.removeHandler(h)
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)

    assert "restore: started demo-project" in capsys.readouterr().err


def test_main_binds_loopback_by_default(monkeypatch):
    # The default has to stay loopback: the app has no auth of its own, so a
    # 0.0.0.0 default would hand an agent shell to the whole LAN.
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.delenv("LAUNCHER_HOST", raising=False)
    monkeypatch.delenv("LAUNCHER_PORT", raising=False)

    main()

    assert seen == {"host": "127.0.0.1", "port": 8765}


def test_main_honours_the_host_and_port_env_overrides(monkeypatch):
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setenv("LAUNCHER_HOST", "0.0.0.0")
    monkeypatch.setenv("LAUNCHER_PORT", "9000")

    main()

    assert seen == {"host": "0.0.0.0", "port": 9000}
