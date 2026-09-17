from launcher import hostinfo


def _meminfo(tmp_path, total_kb: int, available_kb: int):
    path = tmp_path / "meminfo"
    path.write_text(
        f"MemTotal:       {total_kb} kB\n"
        f"MemFree:        1234 kB\n"
        f"MemAvailable:   {available_kb} kB\n"
        f"Buffers:        5678 kB\n"
    )
    return path


def test_reports_used_and_total_in_gb(tmp_path):
    # 12 GiB box with 8 GiB available -> 4 GiB in use
    path = _meminfo(tmp_path, 12 * 1024 * 1024, 8 * 1024 * 1024)
    assert hostinfo.memory(path) == "4.0 of 12 GB"


def test_rounds_to_one_decimal(tmp_path):
    path = _meminfo(tmp_path, 12 * 1024 * 1024, 7_800_000)
    assert hostinfo.memory(path) == "4.6 of 12 GB"


def test_total_is_shown_whole_when_it_is_whole(tmp_path):
    # "12 GB", never "12.0 GB": the total is a constant of the box and the
    # decimal only competes with the number that actually moves.
    path = _meminfo(tmp_path, 12 * 1024 * 1024, 6 * 1024 * 1024)
    assert hostinfo.memory(path).endswith("of 12 GB")


def test_a_fractional_total_keeps_its_decimal(tmp_path):
    path = _meminfo(tmp_path, 7_500_000, 3_000_000)
    assert hostinfo.memory(path) == "4.3 of 7.2 GB"


def test_a_missing_meminfo_reports_nothing_rather_than_raising(tmp_path):
    # The header is decoration; it must never be the reason the page 500s.
    assert hostinfo.memory(tmp_path / "nope") == ""


def test_a_meminfo_without_the_fields_reports_nothing(tmp_path):
    path = tmp_path / "meminfo"
    path.write_text("Something: 1 kB\n")
    assert hostinfo.memory(path) == ""


class FakeStatvfs:
    def __init__(self, total_gb, free_gb, frsize=4096):
        self.f_frsize = frsize
        self.f_blocks = int(total_gb * 1024**3 / frsize)
        self.f_bavail = int(free_gb * 1024**3 / frsize)


def test_disk_reports_used_and_total(monkeypatch):
    monkeypatch.setattr(hostinfo.os, "statvfs", lambda _p: FakeStatvfs(20, 14))
    assert hostinfo.disk() == "6.0 of 20 GB"


def test_disk_uses_the_space_actually_available(monkeypatch):
    # f_bavail, not f_bfree: the root-reserved blocks are not space you can fill
    monkeypatch.setattr(hostinfo.os, "statvfs", lambda _p: FakeStatvfs(20, 0))
    assert hostinfo.disk() == "20.0 of 20 GB"


def test_disk_that_cannot_be_read_reports_nothing(monkeypatch):
    def boom(_p):
        raise OSError("nope")
    monkeypatch.setattr(hostinfo.os, "statvfs", boom)
    assert hostinfo.disk() == ""
