import subprocess

import pytest

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


VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                    1000.
Pages active:                                  5000.
Pages inactive:                                2000.
Pages speculative:                              500.
Pages wired down:                              3000.
"""


def _vm_stat(stdout=VM_STAT, returncode=0):
    def run(cmd):
        assert cmd == ["/usr/bin/vm_stat"]
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")
    return run


def test_darwin_fields_count_free_inactive_and_speculative_as_available(monkeypatch):
    monkeypatch.setattr(hostinfo.os, "sysconf", lambda _n: 1024 if _n == "SC_PAGE_SIZE" else 16 * 1024 * 1024)
    fields = hostinfo._darwin_fields(_vm_stat())
    assert fields["MemAvailable"] == 3500 * 16
    assert fields["MemTotal"] == 16 * 1024 * 1024


@pytest.mark.parametrize("run", [_vm_stat(returncode=1), _vm_stat(stdout="nonsense\n")])
def test_darwin_fields_that_cannot_be_read_are_empty(run):
    assert hostinfo._darwin_fields(run) == {}


def test_a_vm_stat_that_cannot_run_is_empty():
    def boom(_cmd):
        raise OSError("nope")
    assert hostinfo._darwin_fields(boom) == {}


def test_memory_on_a_mac_comes_from_vm_stat(monkeypatch, tmp_path):
    monkeypatch.setattr(hostinfo.sys, "platform", "darwin")
    monkeypatch.setattr(hostinfo, "MEMINFO", tmp_path / "nope")
    monkeypatch.setattr(hostinfo, "_darwin_fields",
                        lambda: {"MemTotal": 12 * 1024 * 1024, "MemAvailable": 8 * 1024 * 1024})
    assert hostinfo.memory() == "4.0 of 12 GB"


def test_memory_elsewhere_never_asks_vm_stat(monkeypatch, tmp_path):
    monkeypatch.setattr(hostinfo.sys, "platform", "linux")
    monkeypatch.setattr(hostinfo, "MEMINFO", tmp_path / "nope")
    monkeypatch.setattr(hostinfo, "_darwin_fields", lambda: pytest.fail("asked vm_stat"))
    assert hostinfo.memory() == ""


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
