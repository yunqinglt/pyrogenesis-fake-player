"""The Windows host cache must not share archive file handles with the player."""

import os
from pathlib import Path
import subprocess
import sys

from pyrogenesis_mcp.engine import GameSession, prepare_host_runtime


def test_host_cache_copies_archives_and_reuses_completed_runtime(tmp_path, monkeypatch):
    binaries = tmp_path / "installed/binaries"
    exe = binaries / "system/pyrogenesis.exe"
    archive = binaries / "data/mods/public/public.zip"
    exe.parent.mkdir(parents=True)
    archive.parent.mkdir(parents=True)
    exe.write_bytes(b"test executable")
    archive.write_bytes(b"test game archive")
    cache_root = tmp_path / "cache"
    cached = prepare_host_runtime(exe, cache_root)
    cached_archive = cached.parent.parent / archive.relative_to(binaries)
    assert cached.read_bytes() == exe.read_bytes()
    assert cached_archive.read_bytes() == archive.read_bytes()
    assert not os.path.samefile(cached_archive, archive)

    def unexpected_copy(*_args, **_kwargs):
        raise AssertionError("A completed cache should not recopy resources")

    with monkeypatch.context() as patch:
        patch.setattr("pyrogenesis_mcp.engine.shutil.copy2", unexpected_copy)
        assert prepare_host_runtime(exe, cache_root) == cached

    archive.write_bytes(b"updated game archive")
    updated = prepare_host_runtime(exe, cache_root)
    assert updated != cached
    assert (updated.parent.parent / archive.relative_to(binaries)).read_bytes() == archive.read_bytes()


def test_host_cache_falls_back_when_hard_links_are_unavailable(tmp_path, monkeypatch):
    exe = tmp_path / "installed/binaries/system/pyrogenesis.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"test executable")

    def no_hard_links(*_args):
        raise OSError("Different filesystems")

    monkeypatch.setattr(type(exe), "hardlink_to", no_hard_links)
    cached = prepare_host_runtime(exe, tmp_path / "cache")
    assert cached.read_bytes() == exe.read_bytes()
    assert not os.path.samefile(cached, exe)


def test_lan_join_uses_isolated_runtime_and_leaves_host_unmanaged(tmp_path, monkeypatch):
    game = GameSession()
    original = tmp_path / "installed/binaries/system/pyrogenesis.exe"
    isolated = tmp_path / "cached/binaries/system/pyrogenesis.exe"
    launched = []

    class Relay:
        def __init__(self, host, port, listen_port):
            assert (host, port, listen_port) == ("127.0.0.1", 20595, 30000)

        def start(self):
            pass

        def status(self):
            return {"state": "listening"}

    monkeypatch.setattr(game, "_prepare", lambda: original)
    monkeypatch.setattr(game, "_runtime_exe", lambda exe, role: isolated)
    monkeypatch.setattr("pyrogenesis_mcp.engine.free_udp_port", lambda: 30000)
    monkeypatch.setattr("pyrogenesis_mcp.engine.EnetRelay", Relay)
    monkeypatch.setattr(game, "_launch_player", lambda *args: launched.append(args))
    assert game.join("127.0.0.1")["mode"] == "lan_client"
    assert launched == [(isolated, "127.0.0.1", 30000, "MCP Player")]
    assert not game.processes


def test_two_processes_can_publish_one_template_without_partial_archives(tmp_path):
    exe = tmp_path / "installed/binaries/system/pyrogenesis.exe"
    archive = tmp_path / "installed/binaries/data/mods/public/public.zip"
    exe.parent.mkdir(parents=True)
    archive.parent.mkdir(parents=True)
    exe.write_bytes(b"test executable")
    archive.write_bytes(b"archive content" * 100000)
    code = ("from pathlib import Path; import sys; "
            "from pyrogenesis_mcp.engine import prepare_host_runtime; "
            "print(prepare_host_runtime(Path(sys.argv[1]),Path(sys.argv[2])))")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    processes = [subprocess.Popen([sys.executable, "-c", code, str(exe), str(tmp_path / "cache")],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                 for _ in range(2)]
    results = [process.communicate(timeout=20) for process in processes]
    assert all(process.returncode == 0 for process in processes), results
    assert results[0][0] == results[1][0]
    cached = Path(results[0][0].strip()).parent.parent / "data/mods/public/public.zip"
    assert cached.read_bytes() == archive.read_bytes()
    assert not os.path.samefile(cached, archive)
