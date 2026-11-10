"""Concurrent sessions must keep commands, mod snapshots and process ownership separate."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import time

import pytest

from pyrogenesis_mcp.engine import GameSession, MOD_ID, POLICY, install_mod
from pyrogenesis_mcp.privacy import ObservationError


def test_two_prepared_sessions_keep_mods_and_command_streams_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("PYROGENESIS_MODS_DIR", str(tmp_path / "mods"))
    monkeypatch.setattr("pyrogenesis_mcp.engine.find_game", lambda: tmp_path / "pyrogenesis")
    a, b = GameSession(), GameSession()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda game: game._prepare(), (a, b))) == [tmp_path / "pyrogenesis"] * 2
    try:
        assert a.session != b.session and len(a.session) == len(b.session) == 32
        assert a._bridge != b._bridge
        assert a._bridge.name == a.session and b._bridge.name == b.session
        assert a._mod_path != b._mod_path
        assert a._mod_path.name == MOD_ID + "_" + a.session
        assert b._mod_path.name == MOD_ID + "_" + b.session
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda game: game._write_command("stop", entities=[10]), (a, b)))
        for game, command_id in zip((a, b), ids):
            command = json.loads((game._bridge / "command.json").read_text())
            assert command["id"] == command_id and command["session"] == game.session
        a.close()
        assert b._mod_path.is_dir()
        assert (b._bridge / "command.json").is_file()
        assert not (tmp_path / "mods" / (MOD_ID + "_" + a.session)).exists()
        assert not (tmp_path / "mods" / "user/moddata/mcp_player/command.json").exists()
    finally:
        a.close()
        b.close()


def test_sibling_observations_cannot_authorize_own_session(tmp_path):
    a, b = GameSession(), GameSession()
    for index, game in enumerate((a, b), start=1):
        game.session = str(index) * 32
        game._bridge = tmp_path / game.session
        game._bridge.mkdir()
        observation = {"version": 1, "session": game.session, "policy": POLICY,
                       "available": True, "emitted_at_ms": time.time() * 1000,
                       "player_id": index, "own_entities": [], "resources": {},
                       "population": {}, "game_time_ms": 1000, "map_size": 256}
        (game._bridge / "observation.json").write_text(json.dumps(observation))
    assert a.observe()["player"] == 1 and b.observe()["player"] == 2
    (a._bridge / "observation.json").write_bytes((b._bridge / "observation.json").read_bytes())
    with pytest.raises(ObservationError, match="another game session"):
        a.observe()
    assert b.observe()["player"] == 2


@pytest.mark.parametrize("mod_id", ["../outside", "mcp_player_../outside", "mcp_player_", "mcp_player_" + "g" * 32])
def test_private_mod_paths_are_validated_before_writing(tmp_path, monkeypatch, mod_id):
    monkeypatch.setenv("PYROGENESIS_MODS_DIR", str(tmp_path / "mods"))
    with pytest.raises(ValueError, match="instance directory"):
        install_mod(mod_id)
    assert not (tmp_path / "mods").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows archive isolation")
def test_windows_instances_and_host_player_use_different_archives_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    exe = tmp_path / "game/binaries/system/pyrogenesis.exe"
    archive = tmp_path / "game/binaries/data/mods/public/public.zip"
    exe.parent.mkdir(parents=True)
    archive.parent.mkdir(parents=True)
    exe.write_bytes(b"executable")
    archive.write_bytes(b"game archive")
    a, b = GameSession(), GameSession()
    a.session, b.session = "a" * 32, "b" * 32
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            players = list(pool.map(lambda game: game._runtime_exe(exe, "player"), (a, b)))
        host = a._runtime_exe(exe, "host")
        cached = [path.parent.parent / "data/mods/public/public.zip" for path in (*players, host)]
        assert all(path.read_bytes() == b"game archive" for path in cached)
        assert all(not os.path.samefile(first, second) for index, first in enumerate(cached)
                   for second in cached[index + 1:])
        a.close()
        assert not cached[0].exists() and not cached[2].exists()
        assert cached[1].is_file() and archive.is_file()
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize("name", [" ", "\t", "A\x00B", "A\u200bB", "A" * 33])
def test_engine_rejects_invalid_nickname_before_preparing_game(name, monkeypatch):
    game = GameSession()
    monkeypatch.setattr(game, "_prepare", lambda: pytest.fail("Must validate before side effects"))
    with pytest.raises(ValueError, match="player name"):
        game.join("127.0.0.1", name=name)


@pytest.mark.parametrize("failure_stage", ["before_directory", "partial_copy", "command_write"])
def test_prepare_rolls_back_private_mod_and_preserves_original_failure(tmp_path, monkeypatch, failure_stage):
    mods = tmp_path / "mods"
    monkeypatch.setenv("PYROGENESIS_MODS_DIR", str(mods))
    monkeypatch.setattr("pyrogenesis_mcp.engine.find_game", lambda: tmp_path / "pyrogenesis")
    sibling = mods / (MOD_ID + "_" + "b" * 32)
    sibling.mkdir(parents=True)
    sentinel = sibling / "keep.txt"
    sentinel.write_text("other session")
    original = RuntimeError("injected " + failure_stage)

    def install(mod_id):
        if failure_stage == "before_directory":
            raise original
        target = mods / mod_id
        target.mkdir()
        (target / "partial.txt").write_text("partially installed")
        if failure_stage == "partial_copy":
            raise original
        return target

    monkeypatch.setattr("pyrogenesis_mcp.engine.install_mod", install)
    game = GameSession()
    if failure_stage == "command_write":
        def fail_write(*_args, **_kwargs):
            raise original
        monkeypatch.setattr(game, "_write_command", fail_write)

    with pytest.raises(RuntimeError) as raised:
        game._prepare()
    assert raised.value is original
    assert game._mod_path is None and game.mode == "stopped"
    assert not (mods / (MOD_ID + "_" + game.session)).exists()
    assert sentinel.read_text() == "other session"
    assert game.close() == {"running": False, "mode": "stopped"}


def test_missing_runtime_does_not_interrupt_private_mod_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYROGENESIS_MODS_DIR", str(tmp_path / "mods"))
    game = GameSession()
    game.session = "a" * 32
    game.mode = "lan_client"
    game._runtime = tmp_path / "appdata/pyrogenesis-mcp/instances" / (game.session + "-gone")
    game._mod_path = tmp_path / "mods" / (MOD_ID + "_" + game.session)
    game._mod_path.mkdir(parents=True)
    mod_path = game._mod_path
    assert game.close() == {"running": False, "mode": "stopped"}
    assert game._runtime is None and game._mod_path is None
    assert not mod_path.exists()
    assert game.close() == {"running": False, "mode": "stopped"}


def test_private_mod_disappearing_during_cleanup_is_already_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("PYROGENESIS_MODS_DIR", str(tmp_path / "mods"))
    game = GameSession()
    game.session = "a" * 32
    game.mode = "lan_client"
    game._mod_path = tmp_path / "mods" / (MOD_ID + "_" + game.session)
    game._mod_path.mkdir(parents=True)
    original_rmtree = shutil.rmtree

    def concurrently_removed(path):
        original_rmtree(path)
        raise FileNotFoundError("Directory was removed before this cleanup")

    monkeypatch.setattr("pyrogenesis_mcp.engine.shutil.rmtree", concurrently_removed)
    assert game.close() == {"running": False, "mode": "stopped"}
    assert game._mod_path is None
    assert game.close() == {"running": False, "mode": "stopped"}
