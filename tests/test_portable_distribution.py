"""Release archives must work independently and contain the promised player material."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import struct
import sys
import tomllib
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("portable_builder", ROOT / "scripts/build_portable.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_bundles_include_runtime_companion_game_manuals_and_all_strategy_skills():
    bundles = builder.prepare_bundles()
    server, player = bundles["server"], bundles["player-kit"]
    standalone = bundles["skill-kit"]
    assert "src/pyrogenesis_mcp/server.py" in server
    assert "src/pyrogenesis_mcp/companion/gui/session/zy_mcp_rally.js" in server
    assert "src/pyrogenesis_mcp/companion/simulation/components/zy_mcp_properties.js" in server
    assert {"RUNBOOK.json", "RUNBOOK.md", "CONFIGURATION.md", "LINUX.md", "install.ps1",
            "install.sh", "run-server.sh", "check_portable.py"} <= server.keys()
    assert {"AGENT.md", "MCP.md", "docs/player-actions.md", "docs/properties.md",
            "docs/terrain-and-strategy-plan.md", "prompts/player-system.md",
            "prompts/task-template.md", "ENTRYPOINT.md"} <= player.keys()
    for skill in builder.SKILL_NAMES:
        assert f"skills/{skill}/SKILL.md" in player
        assert standalone[f"skills/{skill}/SKILL.md"] == player[f"skills/{skill}/SKILL.md"]
    assert "skills/0ad-train-near-civic/scripts/plan_training.py" in player
    assert "skills/0ad-rally-idle/scripts/plan_idle_rally.py" in player
    for files in bundles.values():
        assert files["LICENSE"] == (ROOT / "LICENSE").read_bytes()
        builder.check_markdown_links(files)
        for name in files:
            assert not name.startswith(("/", "\\")) and ".." not in Path(name).parts
            assert not {".venv", ".runtime", "replays", "_0ad-src", "__pycache__"}.intersection(Path(name).parts)
            assert not name.endswith((".png", ".dmp", ".0adsave", ".pyc"))
        assert not any(builder.PRIVATE_WINDOWS_PROFILE_PATH.search(content.decode("utf-8-sig"))
                       for content in files.values())


def test_portable_package_declares_companion_data_and_has_no_analysis_entrypoint():
    config = tomllib.loads(builder.prepare_bundles()["server"]["pyproject.toml"].decode())
    assert config["project"]["scripts"] == {"pyrogenesis-mcp": "pyrogenesis_mcp.server:main"}
    assert "pyrogenesis_mcp" in config["tool"]["setuptools"]["package-data"]
    assert not any(name.startswith("src/pyrogenesis_analysis/") for name in builder.prepare_bundles()["server"])


def test_save_skill_is_identical_in_both_kits_and_carries_the_canonical_parser():
    bundles = builder.prepare_bundles()
    prefix = "skills/0ad-analyze-save/"
    player = {name: data for name, data in bundles["player-kit"].items() if name.startswith(prefix)}
    standalone = {name: data for name, data in bundles["skill-kit"].items() if name.startswith(prefix)}
    assert player == standalone
    assert json.loads(player[prefix + "analysis_rules.json"])["victory_conditions"] == ["conquest_units"]
    for path in (ROOT / "src/pyrogenesis_analysis").glob("*.py"):
        assert player[prefix + "scripts/pyrogenesis_analysis/" + path.name] == path.read_bytes()
    assert not any("pyrogenesis_mcp" in name for name in player)


def test_save_skill_runs_without_checkout_or_site_packages_and_never_overwrites(tmp_path):
    files = builder.prepare_bundles()["skill-kit"]
    prefix = "skills/0ad-analyze-save/"
    skill = tmp_path / "moved skill with spaces"
    for name, data in files.items():
        if name.startswith(prefix):
            target = skill / name.removeprefix(prefix)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    templates = tmp_path / "public"
    templates.mkdir()
    (templates / "mod.json").write_text('{"version":"0.28.0"}', encoding="utf-8")

    def u32(value):
        return struct.pack("<I", value)

    def native(value):
        encoded = value.encode()
        return u32(len(encoded)) + encoded

    # A complete minimal native stream containing one Player component.
    player = b"\x01\x0b" + native("Player") + b"\x03" + u32(1)
    player += b"\x01" + native("playerID") + b"\x05" + u32(1)
    simulation = native("42") + u32(10) + u32(0) + u32(1)
    simulation += native("Player") + u32(1) + u32(2) + player
    save = tmp_path / "snapshot.0adsave"
    metadata = {"engine_serialization_version": "0.28.0", "playerID": 1}
    with zipfile.ZipFile(save, "w") as archive:
        archive.writestr("metadata.json", json.dumps(metadata))
        archive.writestr("simulation.dat", simulation)
    original = save.read_bytes()
    output = tmp_path / "report"
    # -I -S removes ambient import paths and all installed third-party packages.
    command = [sys.executable, "-I", "-S", str(skill / "scripts/analyze_save.py"),
               str(save), "--output", str(output), "--templates", str(templates)]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert {path.name for path in output.iterdir()} == {"report.json", "report.md", "map.html"}
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["scope"]["kind"] == "offline-save-omniscient"
    assert report["scope"]["focus_player"] == 1
    assert report["scope"]["fog_reconstructed"] is False
    assert report["scope"]["live_commands"] is False
    assert report["parse"]["complete"]
    assert report["parse"]["consumed_bytes"] == report["parse"]["total_bytes"] == len(simulation)
    assert report["rules_audit"]["expected"]["recorded_on"] == "2026-11-06"
    assert report["source"]["sha256"] == hashlib.sha256(original).hexdigest()
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    repeat = subprocess.run(command, cwd=tmp_path, capture_output=True, timeout=20)
    assert repeat.returncode != 0
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before
    assert save.read_bytes() == original


def test_repeated_archives_are_identical_and_manifests_hash_every_payload(tmp_path):
    files = builder.prepare_bundles()["player-kit"]
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    a = builder.write_zip(first, "player-kit", files)
    b = builder.write_zip(second, "player-kit", files)
    assert a["sha256"] == b["sha256"]
    with zipfile.ZipFile(first) as archive:
        manifest = json.loads(archive.read("player-kit/BUNDLE-MANIFEST.json"))
        assert manifest["contains_game_engine"] is False
        assert manifest["contains_python_runtime"] is False
        assert manifest["signed"] is False
        assert {item["path"] for item in manifest["files"]} == set(files)
        for item in manifest["files"]:
            payload = archive.read("player-kit/" + item["path"])
            assert len(payload) == item["bytes"]
            assert hashlib.sha256(payload).hexdigest() == item["sha256"]
    with pytest.raises(FileExistsError):
        builder.write_zip(first, "player-kit", files)


def test_extracted_manifest_verifier_runs_after_moving_and_detects_corruption(tmp_path):
    archive_path = tmp_path / "bundle.zip"
    builder.write_zip(archive_path, "player-kit", builder.prepare_bundles()["player-kit"])
    destination = tmp_path / "portable with spaces"
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(destination)
    root = destination / "player-kit"
    command = [sys.executable, str(root / "verify_bundle.py")]
    result = subprocess.run(command, text=True, capture_output=True, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True
    (root / "AGENT.md").write_text("damaged copy", encoding="utf-8")
    failed = subprocess.run(command, text=True, capture_output=True, cwd=tmp_path, timeout=10)
    assert failed.returncode != 0
    assert json.loads(failed.stderr)["ok"] is False


def test_builder_will_not_overwrite_existing_release_directory(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    sentinel = target / "user-file.txt"
    sentinel.write_text("retain", encoding="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "scripts/build_portable.py"), "--output", str(target)],
                            text=True, capture_output=True, timeout=15)
    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "retain"
    assert list(target.iterdir()) == [sentinel]


def test_linux_archive_scripts_have_unix_lines_and_executable_modes(tmp_path):
    files = builder.prepare_bundles()["server"]
    archive_path = tmp_path / "server.zip"
    builder.write_zip(archive_path, "server", files)
    with zipfile.ZipFile(archive_path) as archive:
        for name in ("install.sh", "run-server.sh"):
            assert files[name].startswith(b"#!/usr/bin/env bash\n")
            assert b"\r" not in files[name]
            assert (archive.getinfo("server/" + name).external_attr >> 16) & 0o777 == 0o755
    runbook = json.loads(files["RUNBOOK.json"])
    linux = runbook["platform_overrides"]["linux"]
    assert linux["install"]["command"] == "bash"
    assert linux["mcp_server"]["command"] == "{BUNDLE}/.venv/bin/python"
    assert linux["check"]["game_launch"] is False
