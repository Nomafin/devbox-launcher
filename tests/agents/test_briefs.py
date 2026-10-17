import os

from launcher.agents import briefs
from launcher.agents.model import Agent


def agent():
    return Agent(project="notes", name="drafts", cwd="/p", workdir="/p/wt")


def test_ensure_writes_template_once(tmp_path):
    p = briefs.ensure(tmp_path, agent())
    assert p == tmp_path / "briefs" / "notes" / "drafts.md"
    assert p.read_text().startswith("# notes·drafts\n")
    for section in briefs.SECTIONS:
        assert f"## {section}\n" in p.read_text()
    p.write_text("mine")
    briefs.ensure(tmp_path, agent())
    assert p.read_text() == "mine"


def test_mission_first_line():
    text = "# x\n\n## Mission\n\nAlumni stars in Drafts.\nMore.\n\n## State\n\nwip\n"
    assert briefs.mission(text) == "Alumni stars in Drafts."
    assert briefs.mission("# x\n\n## Mission\n\n## State\n\nwip\n") is None


def test_never_active_is_not_stale(tmp_path):
    assert not briefs.is_stale(tmp_path / "missing.md", 0.0)


def test_missing_brief_after_activity_is_stale(tmp_path):
    assert briefs.is_stale(tmp_path / "missing.md", 1000.0)


def test_not_stale_within_grace(tmp_path):
    p = briefs.ensure(tmp_path, agent())
    os.utime(p, (1000.0, 1000.0))
    assert not briefs.is_stale(p, 1000.0 + 60)       # the reply to /handoff
    assert briefs.is_stale(p, 1000.0 + briefs.STALE_GRACE + 1)


def test_seed_has_preamble_and_brief(tmp_path):
    p = briefs.ensure(tmp_path, agent())
    p.write_text("# notes·drafts\n\n## Mission\n\nAlumni.\n")
    seed = briefs.write_seed(tmp_path, agent()).read_text()
    assert "You are agent `notes·drafts`" in seed
    assert str(p) in seed
    assert seed.rstrip().endswith("Alumni.")


def test_archive_moves_brief(tmp_path):
    briefs.ensure(tmp_path, agent())
    dest = briefs.archive(tmp_path, agent(), "20261016")
    assert dest == tmp_path / "archive" / "notes" / "drafts-20261016.md"
    assert dest.exists() and not briefs.path(tmp_path, agent()).exists()
    assert briefs.archive(tmp_path, agent(), "20261016") is None


def test_preamble_says_fresh_not_clear():
    assert "--fresh" in briefs.PREAMBLE and "not /clear" in briefs.PREAMBLE
