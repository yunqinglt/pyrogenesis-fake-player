"""Linux installation discovery and version checks without launching a game."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from pyrogenesis_mcp.enet import find_enet_library


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("portable_check", ROOT / "portable/server/check_portable.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


@pytest.fixture(autouse=True)
def clean_public_override(monkeypatch):
    monkeypatch.delenv("PYROGENESIS_PUBLIC_MOD", raising=False)


@pytest.mark.parametrize("layout", [
    "data/mods/public", "share/0ad/data/mods/public", "share/0ad/mods/public",
    "share/games/0ad/mods/public", "share/games/0ad/data/mods/public",
])
@pytest.mark.parametrize("archive", [False, True])
def test_selected_linux_installation_version_in_each_layout(tmp_path, monkeypatch, layout, archive):
    monkeypatch.setattr(check.sys, "platform", "linux")
    public = tmp_path / layout
    public.mkdir(parents=True)
    metadata = json.dumps({"version": "0.0.28"})
    if archive:
        with zipfile.ZipFile(public / "public.zip", "w") as resource:
            resource.writestr("mod.json", metadata)
    else:
        (public / "mod.json").write_text(metadata, encoding="utf-8")
    assert check.check_public_version(tmp_path / "bin/pyrogenesis")["game_version_verified"] is True


@pytest.mark.parametrize("explicit_kind", ["directory", "json", "zip"])
def test_explicit_public_metadata_rejects_old_game(tmp_path, monkeypatch, explicit_kind):
    metadata = json.dumps({"version": "0.0.27"})
    path = tmp_path / "mod.json"
    path.write_text(metadata, encoding="utf-8")
    if explicit_kind == "directory":
        path = tmp_path
    elif explicit_kind == "zip":
        path = tmp_path / "public.zip"
        with zipfile.ZipFile(path, "w") as resource:
            resource.writestr("mod.json", metadata)
    monkeypatch.setenv("PYROGENESIS_PUBLIC_MOD", str(path))
    with pytest.raises(ValueError, match="selected public mod differs"):
        check.check_public_version(tmp_path / "unrelated/bin/pyrogenesis")


def test_missing_public_metadata_is_explicitly_unverified(tmp_path):
    result = check.check_public_version(tmp_path / "bin/pyrogenesis")
    assert result["game_version_verified"] is False
    assert "PYROGENESIS_PUBLIC_MOD" in result["version_note"]


def test_bad_explicit_public_path_does_not_silently_fall_back(tmp_path, monkeypatch):
    monkeypatch.setenv("PYROGENESIS_PUBLIC_MOD", str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError, match="does not exist"):
        check.check_public_version(tmp_path / "bin/pyrogenesis")
    monkeypatch.setenv("PYROGENESIS_PUBLIC_MOD", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="neither"):
        check.check_public_version(tmp_path / "bin/pyrogenesis")


def test_oversized_public_metadata_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "mod.json"
    path.write_bytes(b" " * 65537)
    monkeypatch.setenv("PYROGENESIS_PUBLIC_MOD", str(path))
    with pytest.raises(ValueError, match="metadata size"):
        check.check_public_version(tmp_path / "bin/pyrogenesis")


def test_library_override_precedence_and_legacy_compatibility(tmp_path, monkeypatch):
    modern, legacy, explicit = [tmp_path / name for name in ("libenet.so", "enet.dll", "explicit.so")]
    for path in (modern, legacy, explicit):
        path.touch()
    monkeypatch.setenv("PYROGENESIS_ENET_LIBRARY", str(modern))
    monkeypatch.setenv("PYROGENESIS_ENET_DLL", str(legacy))
    assert find_enet_library() == str(modern.resolve())
    assert find_enet_library(explicit) == str(explicit.resolve())
    monkeypatch.delenv("PYROGENESIS_ENET_LIBRARY")
    assert find_enet_library() == str(legacy.resolve())


@pytest.mark.skipif(os.name == "nt", reason="Linux filesystem path semantics")
@pytest.mark.parametrize("xdg", [None, "", "/tmp/0ad-data"])
def test_linux_user_mods_uses_default_or_xdg(tmp_path, monkeypatch, xdg):
    from pyrogenesis_mcp.engine import user_mods

    monkeypatch.delenv("PYROGENESIS_MODS_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    if xdg is None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_DATA_HOME", xdg)
    assert user_mods() == (Path(xdg) if xdg else tmp_path / ".local/share") / "0ad/mods"


@pytest.mark.skipif(sys.platform != "linux", reason="Requires Linux Bash")
@pytest.mark.parametrize("existing_kind", ["incomplete", "symlink"])
def test_installer_preserves_incomplete_or_redirected_environment(tmp_path, existing_kind):
    root = tmp_path / "安装 directory"
    root.mkdir()
    shutil.copyfile(ROOT / "portable/server/install.sh", root / "install.sh")
    if existing_kind == "symlink":
        other = tmp_path / "unrelated environment"
        other.mkdir()
        (root / ".venv").symlink_to(other, target_is_directory=True)
    else:
        (root / ".venv").mkdir()
    marker = root / ".venv/user-file"
    marker.write_text("retain")
    result = subprocess.run(["bash", str(root / "install.sh"), "--python", sys.executable],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert "fresh extraction" in result.stderr
    assert marker.read_text() == "retain"


@pytest.mark.skipif(sys.platform != "linux", reason="Requires Linux Bash")
def test_launcher_without_installation_fails_only_on_stderr(tmp_path):
    launcher = tmp_path / "run-server.sh"
    shutil.copyfile(ROOT / "portable/server/run-server.sh", launcher)
    result = subprocess.run(["bash", str(launcher)], capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "install.sh" in result.stderr
