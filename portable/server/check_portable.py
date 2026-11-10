"""Non-game self-check: installed package, companion data, MCP stdio and optional native files."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
from datetime import timedelta
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import struct
import sys
import zipfile


EXPECTED_TOOLS = {
    "start_local_game", "join_lan_game", "player_status", "observe_player",
    "describe_options", "observe_map_region", "move_units", "stop_units",
    "analyze_terrain_region",
    "order_units", "set_rally_point", "clear_rally_point", "abandon_targets",
    "set_unit_stance", "set_unit_packing", "cancel_unit_packing", "unload_garrison",
    "leave_turret", "train_units", "research_technology", "cancel_production",
    "delete_own_units", "delete_own_buildings", "close_game",
}


def pe_machine(path: Path) -> int | None:
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            return None
        stream.seek(0x3C)
        offset = stream.read(4)
        if len(offset) != 4:
            raise ValueError("Truncated executable header")
        stream.seek(struct.unpack("<I", offset)[0])
        if stream.read(4) != b"PE\0\0":
            raise ValueError("Invalid PE header")
        return struct.unpack("<H", stream.read(2))[0]


def public_metadata_paths(exe: Path) -> list[Path]:
    """Probe only the selected installation's prefix, or an explicit public mod."""
    if configured := os.environ.get("PYROGENESIS_PUBLIC_MOD"):
        path = Path(configured).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError("PYROGENESIS_PUBLIC_MOD does not exist")
        return [path / "mod.json", path / "public.zip"] if path.is_dir() else [path]
    prefix = exe.parent.parent
    roots = [prefix / "data/mods/public"]  # Windows and source builds
    if sys.platform.startswith("linux"):
        roots.extend(prefix / layout for layout in (
            "share/0ad/data/mods/public", "share/0ad/mods/public",
            "share/games/0ad/mods/public", "share/games/0ad/data/mods/public",
        ))
    return [root / name for root in roots for name in ("mod.json", "public.zip")]


def check_public_version(exe: Path) -> dict:
    details = {"required_game_version": "0.28.0", "game_version_verified": False}
    paths = public_metadata_paths(exe)
    path = next((candidate for candidate in paths if candidate.is_file()), None)
    if path is None:
        if os.environ.get("PYROGENESIS_PUBLIC_MOD"):
            raise FileNotFoundError("PYROGENESIS_PUBLIC_MOD contains neither mod.json nor public.zip")
        details["version_note"] = (
            "Public metadata was not found under the selected installation. Set "
            "PYROGENESIS_PUBLIC_MOD to its public directory, public.zip or mod.json; "
            "confirm game 0.28.0 before joining."
        )
        return details
    details["public_metadata"] = str(path)
    try:
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as resource:
                entry = resource.getinfo("mod.json")
                if entry.file_size > 65536:
                    raise ValueError("Unexpected public mod metadata size")
                metadata = json.loads(resource.read(entry))
        else:
            with path.open("rb") as stream:
                content = stream.read(65537)
            if len(content) > 65536:
                raise ValueError("Unexpected public mod metadata size")
            metadata = json.loads(content)
        version = str(metadata.get("version", ""))
        details["public_mod_version"] = version
        if version not in {"0.0.28", "0.28.0"}:
            raise ValueError("This bundle targets game 0.28.0; the selected public mod differs")
        details["game_version_verified"] = True
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        # A running Windows host can lock public.zip. Never stop it to read a version.
        details["version_note"] = (
            "Public metadata could not be read (possibly locked by a running game); "
            "confirm game 0.28.0 manually. " + type(exc).__name__
        )
    return details


def check_game() -> dict:
    from pyrogenesis_mcp.engine import find_game, user_mods
    from pyrogenesis_mcp.enet import find_enet_library

    exe = find_game()
    # An explicit game path may be outside auto-discovery roots. Prefer its ENet.
    adjacent = exe.parent / "enet.dll"
    explicit_enet = (os.environ.get("PYROGENESIS_ENET_LIBRARY")
                     or os.environ.get("PYROGENESIS_ENET_DLL") or os.environ.get("ENET_DLL"))
    library = find_enet_library(explicit_enet or (adjacent if adjacent.is_file() else None))
    details = {"executable": str(exe), "enet_library": library,
               "user_mods_directory": str(user_mods()), "game_launch_verified": False}
    if sys.platform.startswith("linux"):
        if not os.access(exe, os.X_OK):
            raise ValueError("The selected Linux game file is not executable")
        details["display_environment_available"] = bool(
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if os.name == "nt":
        native_path = Path(library)
        native_machine = pe_machine(native_path)
        exe_machine = pe_machine(exe)
        interpreter_machine = pe_machine(Path(sys.executable))
        details["pe_machine"] = {"python": interpreter_machine, "game": exe_machine,
                                 "enet": native_machine}
        if native_machine is None or native_machine != interpreter_machine:
            raise ValueError("Python and ENet must use the same Windows architecture")
        if exe_machine != native_machine:
            raise ValueError("Use the ENet library from the selected game installation")
    native = ctypes.CDLL(library)
    native.enet_linked_version.restype = ctypes.c_uint32
    native.enet_linked_version.argtypes = []
    encoded = native.enet_linked_version()
    details["enet_linked_version"] = ".".join(str((encoded >> shift) & 255) for shift in (16, 8, 0))
    if encoded >> 8 != 0x0103:
        raise ValueError("Expected an ENet 1.3 library compatible with the relay")
    details.update(check_public_version(exe))
    return details


async def check_stdio() -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # Pass only configuration variables needed by the server. No game is started.
    inherited = {key: os.environ[key] for key in (
        "PYROGENESIS_EXE", "PYROGENESIS_ENET_LIBRARY", "PYROGENESIS_ENET_DLL",
        "ENET_DLL", "PYROGENESIS_MODS_DIR", "PYROGENESIS_PUBLIC_MOD", "XDG_DATA_HOME",
        "LD_LIBRARY_PATH"
    ) if key in os.environ}
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "pyrogenesis_mcp.server"],
        cwd=str(Path(__file__).resolve().parent), env=inherited,
    )
    async with asyncio.timeout(30):
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=15)) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                if names != EXPECTED_TOOLS:
                    raise ValueError("The installed tool surface does not match this bundle")
                response = await session.call_tool("player_status", {})
                if response.isError:
                    raise ValueError("Idle player_status returned an MCP error")
                status = response.structuredContent
                if status is None:
                    status = json.loads(response.content[0].text)
                if status.get("running") is not False or status.get("mode") != "stopped":
                    raise ValueError("The isolated self-check must stay in stopped mode")
                return {"server_name": initialized.serverInfo.name, "tool_count": len(names),
                        "tools": sorted(names), "idle_status": status}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-game", action="store_true",
                        help="Also locate native game/ENet files; never launch or join a game")
    args = parser.parse_args()
    try:
        import pyrogenesis_mcp
        root = Path(pyrogenesis_mcp.__file__).resolve().parent
        companion = root / "companion"
        required = ["mod.json", "gui/session/zz_mcp_player.js", "gui/session/zy_mcp_rally.js",
                    "gui/session/zy_mcp_metadata.js", "gui/session/zy_mcp_map_memory.js",
                    "simulation/components/zz_mcp_map.js", "simulation/components/zy_mcp_properties.js"]
        if not all((companion / name).is_file() for name in required):
            raise ValueError("Installed companion package data is incomplete")
        report = {"ok": True, "python": platform.python_version(),
                  "python_bits": struct.calcsize("P") * 8, "system": platform.system(),
                  "package_version": pyrogenesis_mcp.__version__,
                  "mcp_version": importlib.metadata.version("mcp"),
                  "companion_files_verified": len(required), "stdio": asyncio.run(check_stdio())}
        if args.check_game:
            report["native_files"] = check_game()
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
