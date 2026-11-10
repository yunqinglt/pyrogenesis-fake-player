"""Build relocatable server, model-player and standalone skill ZIPs from an explicit allowlist."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
ZIP_TIME = (2026, 11, 6, 0, 0, 0)
TESTED_ENVIRONMENTS = [
    {"platform": "Windows x64", "python": "3.13.3", "mcp": "1.30.0",
     "scope": ["installation", "companion", "stdio", "native_files",
               "two_mcp_same_match", "nickname", "independent_shutdown", "same_nickname_rejoin"]},
    {"platform": "Linux x64 (Arch Linux / WSL2)", "python": "3.14.9", "mcp": "1.30.0",
     "scope": ["installation", "companion", "stdio", "bash_launcher", "enet_loopback",
               "session_isolation_unit_tests"]},
]
SKILL_NAMES = ("0ad-train-near-civic", "0ad-build-resource-storehouse", "0ad-rally-idle", "0ad-analyze-save")
SOURCE_EXTENSIONS = {".py", ".js", ".json", ".md", ".toml", ".ps1", ".sh", ".yaml", ".yml", ".txt"}
IGNORED_PARTS = {"__pycache__", ".pytest_cache", ".venv", ".runtime", "node_modules", ".git"}
PLAYER_DOCS = (
    "AGENT.md", "MCP.md", "analysis_rules.json", "docs/player-actions.md", "docs/properties.md",
    "docs/validation.md", "docs/terrain-and-strategy-plan.md", "docs/save-analysis.md",
    "docs/portable-distribution.md", "docs/portable-acceptance.md",
)
LINK = re.compile(r"\]\(([^)]+)\)")
PRIVATE_WINDOWS_PROFILE_PATH = re.compile(r"(?i)(C:(?:\\\\|\\|/)Users(?:\\\\|\\|/))[^\\\\/]+")


def read_file(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing or linked source file: " + str(path.relative_to(ROOT)))
    if not path.resolve().is_relative_to(ROOT):
        raise ValueError("Source file leaves the project")
    return path.read_bytes()


def add_tree(files: dict[str, bytes], source: Path, destination: str) -> None:
    if not source.is_dir() or source.is_symlink():
        raise ValueError("Missing or linked source directory: " + str(source.relative_to(ROOT)))
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if set(relative.parts) & IGNORED_PARTS:
            continue
        if path.is_dir():
            if path.is_symlink():
                raise ValueError("Linked source directory is not portable")
            continue
        if path.suffix not in SOURCE_EXTENSIONS:
            raise ValueError("Unexpected file type in allowlisted source: " + str(path.relative_to(ROOT)))
        target = str(PurePosixPath(destination) / PurePosixPath(relative.as_posix()))
        content = read_file(path)
        # Windows checkouts must still produce Bash scripts with Unix line endings.
        files[target] = content.replace(b"\r\n", b"\n") if path.suffix == ".sh" else content


def redact_document_paths(files: dict[str, bytes]) -> None:
    # Existing historical docs contain local sample paths. The distributed copy
    # retains their meaning while replacing the development machine's username.
    for name, content in list(files.items()):
        if name.endswith((".md", ".json", ".toml", ".yaml", ".yml")):
            text = content.decode("utf-8-sig")
            text = PRIVATE_WINDOWS_PROFILE_PATH.sub(lambda match: match.group(1) + "Player", text)
            if name.endswith(".md"):
                # Historical validation reports may link private local artifacts.
                # Keep the evidence reference as text; never ship the artifact.
                text = re.sub(
                    r"\[([^\]]+)\]\((?:\.\./)*\.runtime/[^)]+\)",
                    lambda match: match.group(1) + "（本机历史产物，未随包提供）", text,
                )
            files[name] = text.encode("utf-8")


def check_markdown_links(files: dict[str, bytes]) -> None:
    for name, content in files.items():
        if not name.endswith(".md"):
            continue
        for raw in LINK.findall(content.decode("utf-8-sig")):
            target = raw.split("#", 1)[0].strip("<>")
            if not target or "://" in target or target.startswith(("mailto:", "data:")):
                continue
            pieces = list(PurePosixPath(name).parent.parts)
            for piece in PurePosixPath(target).parts:
                if piece == "..":
                    if not pieces:
                        raise ValueError("Markdown link leaves the bundle: " + name + " -> " + target)
                    pieces.pop()
                elif piece not in ("", "."):
                    pieces.append(piece)
            resolved = "/".join(pieces)
            if resolved not in files:
                raise ValueError("Missing local Markdown target: " + name + " -> " + target)


def add_skill(files: dict[str, bytes], name: str) -> None:
    skill_root = ROOT / "skills" / name
    if not (skill_root / "SKILL.md").is_file():
        raise ValueError("Skill is not complete yet: " + name)
    destination = "skills/" + name
    add_tree(files, skill_root, destination)
    if name == "0ad-analyze-save":
        # Reuse the canonical parser without requiring the source checkout or
        # installing the MCP dependencies in the destination environment.
        add_tree(files, ROOT / "src/pyrogenesis_analysis", destination + "/scripts/pyrogenesis_analysis")
        files[destination + "/analysis_rules.json"] = read_file(ROOT / "analysis_rules.json")


def prepare_bundles() -> dict[str, dict[str, bytes]]:
    server: dict[str, bytes] = {}
    add_tree(server, ROOT / "portable/server", "")
    add_tree(server, ROOT / "src/pyrogenesis_mcp", "src/pyrogenesis_mcp")
    add_tree(server, ROOT / "mod/mcp_player", "src/pyrogenesis_mcp/companion")
    player: dict[str, bytes] = {}
    add_tree(player, ROOT / "portable/player-kit", "")
    for name in PLAYER_DOCS:
        player[name] = read_file(ROOT / name)
    for name in ("CONFIGURATION.md", "RUNBOOK.md", "RUNBOOK.json", "LINUX.md", "KNOWN_ISSUES.md"):
        player[name] = read_file(ROOT / "portable/server" / name)
    player["SERVER-README.md"] = read_file(ROOT / "portable/server/README.md")
    player["verify_bundle.py"] = read_file(ROOT / "portable/server/verify_bundle.py")
    # The development guide links to source templates. In the distributed copy,
    # retain its content but route links to the corresponding supplied documents.
    guide = player["docs/portable-distribution.md"].decode("utf-8-sig")
    for source, target in {
        "../portable/server/README.md": "../SERVER-README.md",
        "../portable/server/RUNBOOK.md": "../RUNBOOK.md",
        "../portable/server/RUNBOOK.json": "../RUNBOOK.json",
        "../portable/server/CONFIGURATION.md": "../CONFIGURATION.md",
        "../portable/server/LINUX.md": "../LINUX.md",
        "../portable/player-kit/ENTRYPOINT.md": "../ENTRYPOINT.md",
    }.items():
        guide = guide.replace(source, target)
    player["docs/portable-distribution.md"] = guide.encode("utf-8")
    for name in ("docs/portable-acceptance.md", "docs/validation.md"):
        for document in ("CONFIGURATION.md", "LINUX.md"):
            player[name] = player[name].replace(
                ("../portable/server/" + document).encode(), ("../" + document).encode())
    for name in SKILL_NAMES:
        add_skill(player, name)
    skills: dict[str, bytes] = {}
    add_tree(skills, ROOT / "portable/skill-kit", "")
    skills["verify_bundle.py"] = read_file(ROOT / "portable/server/verify_bundle.py")
    for name in SKILL_NAMES:
        add_skill(skills, name)
    for files in (server, player, skills):
        files["LICENSE"] = read_file(ROOT / "LICENSE")
        redact_document_paths(files)
        check_markdown_links(files)
    return {"server": server, "player-kit": player, "skill-kit": skills}


def encode_manifest(bundle: str, files: dict[str, bytes]) -> bytes:
    manifest = {
        "schema_version": 1, "bundle": bundle, "project_version": VERSION,
        "game_version": "0.28.0", "signed": False,
        "tested_platform": "Windows x64", "python_minimum": "3.11",
        "supported_platforms": ["Windows", "Linux"],
        "tested_environments": TESTED_ENVIRONMENTS,
        "linux_gameplay_verified": False,
        "dependencies": ["mcp>=1.20,<2"] if bundle == "server" else [],
        "tested_mcp_version": "1.30.0", "tested_python_version": "3.13.3",
        "network_required_for_server_install": True,
        "contains_game_engine": False, "contains_python_runtime": False,
        "contains_dependency_wheels": False,
        "files": [{"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                  for name, content in sorted(files.items())],
    }
    return (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_zip(path: Path, bundle: str, files: dict[str, bytes]) -> dict:
    # Fixed entry time/order removes build-time and absolute-path differences.
    content = dict(files)
    content["BUNDLE-MANIFEST.json"] = encode_manifest(bundle, files)
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(content.items()):
            entry = zipfile.ZipInfo(bundle + "/" + name, ZIP_TIME)
            entry.create_system = 3
            entry.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    data = path.read_bytes()
    return {"name": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "files": len(content)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/portable-20261106",
                        help="New output directory; an existing directory is never overwritten")
    parser.add_argument("--check", action="store_true", help="Validate all inputs/links without creating output")
    args = parser.parse_args()
    try:
        bundles = prepare_bundles()
        if args.check:
            print(json.dumps({"ok": True, "bundles": {name: len(files) for name, files in bundles.items()}}))
            return 0
        output = args.output.expanduser().resolve()
        if output.exists():
            raise ValueError("Output directory already exists; choose a fresh path")
        output.mkdir(parents=True, exist_ok=False)
        artifacts = []
        for name, files in bundles.items():
            artifacts.append(write_zip(output / ("pyrogenesis-" + name + "-" + VERSION + ".zip"), name, files))
        checksums = "".join(item["sha256"] + "  " + item["name"] + "\n" for item in artifacts)
        with (output / "SHA256SUMS.txt").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(checksums)
        with (output / "release.json").open("x", encoding="utf-8", newline="\n") as stream:
            json.dump({"schema_version": 1, "version": VERSION, "game_version": "0.28.0",
                       "artifacts": artifacts, "contains_game_engine": False,
                       "offline_installation_ready": False,
                       "network_required_for_server_install": True,
                       "tested_platform": "Windows x64", "python_minimum": "3.11",
                       "supported_platforms": ["Windows", "Linux"],
                       "tested_environments": TESTED_ENVIRONMENTS,
                       "linux_gameplay_verified": False},
                      stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(json.dumps({"ok": True, "output": str(output), "artifacts": artifacts}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, UnicodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
