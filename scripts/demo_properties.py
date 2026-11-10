"""Validate entity properties against an isolated native 0.28 roster via MCP.

Uses an existing unlocked Windows resource cache and writableRoot; never touches
the normal Documents bridge or a manually hosted multiplayer game.
"""
from __future__ import annotations

import argparse
import asyncio
import atexit
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get("LOCALAPPDATA", "")) / "pyrogenesis-mcp/host-runtime/875f44ad32363331da1d"
EXE = CACHE / "binaries/system/pyrogenesis.exe"
if os.environ.get("PYROGENESIS_TEST_EXE"):
    EXE = Path(os.environ["PYROGENESIS_TEST_EXE"]).resolve()
    CACHE = EXE.parents[2]
MODS = CACHE / "binaries/data/mods"
MAP = "skirmishes/mcp_properties_native"
ROSTER = {
    "worker": "units/athen/support_civilian",
    "priest": "units/athen/support_healer_b",
    "dog": "units/brit/war_dog",
    "minister": "units/han/support_minister",
    "elephant": "units/maur/support_elephant",
    "champion": "units/ptol/champion_cavalry",
    "mercenary": "units/cart/infantry_javelineer_iber_b",
    "hero": "units/mace/hero_alexander_iii",
    "poison_champion": "units/maur/champion_maiden_archer",
    "ram": "units/athen/siege_ram",
    "tower": "units/han/siege_tower",
    "ship": "units/athen/ship_arrow",
    "packed": "units/rome/siege_ballista_packed",
    "unpacked": "units/rome/siege_ballista_unpacked",
    "spearman": "units/athen/infantry_spearman_b",
    "occupant": "units/athen/infantry_spearman_a",
    "burn_champion": "units/iber/champion_cavalry",
    "catapult_packed": "units/athen/siege_lithobolos_packed",
    "catapult_unpacked": "units/athen/siege_lithobolos_unpacked",
}


def make_map():
    destination = MODS / "mcp_player/maps/skirmishes"
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(MODS / "public/public.zip") as archive:
        tree = ET.fromstring(archive.read("maps/skirmishes/skirmish_demo_2p.xml"))
        (destination / "mcp_properties_native.pmp").write_bytes(
            archive.read("maps/skirmishes/skirmish_demo_2p.pmp"))
        for template in ROSTER.values():
            assert f"simulation/templates/{template}.xml" in archive.namelist(), template
    settings = json.loads(tree.find("ScriptSettings").text)
    settings.update(Name="MCP properties native roster", PlayerData=[{"Civ": "athen"}, {"Civ": "athen"}],
                    VictoryConditions=[], RevealMap=False)
    tree.find("ScriptSettings").text = json.dumps(settings)
    entities = tree.find("Entities")
    entities.clear()

    def add(uid, template, x, z, player=1):
        entity = ET.SubElement(entities, "Entity", uid=str(uid))
        ET.SubElement(entity, "Template").text = template
        ET.SubElement(entity, "Player").text = str(player)
        ET.SubElement(entity, "Position", x=str(x), z=str(z))
        ET.SubElement(entity, "Orientation", y="0")
        return entity

    add(10, "structures/athen/civil_centre", 256, 320)
    add(11, "structures/athen/civil_centre", 560, 480, 2)
    for index, (role, template) in enumerate(ROSTER.items()):
        x, z = 288 + index % 4 * 16, 288 + index // 4 * 24
        if role == "ship":
            x, z = 464, 256
        entity = add(100 + index, template, x, z)
        if role == "ram":
            garrison = ET.SubElement(entity, "Garrison")
            ET.SubElement(garrison, "Entity", uid=str(100 + list(ROSTER).index("occupant")))
    ET.ElementTree(tree).write(destination / "mcp_properties_native.xml", encoding="utf-8", xml_declaration=True)


def serve():
    from pyrogenesis_mcp.engine import GameSession
    from pyrogenesis_mcp import server

    class RosterSession(GameSession):
        def start_local(self, *, networked=True):
            if networked:
                raise ValueError("The isolated roster test only supports singleplayer")
            with self._lock:
                executable = self._prepare()
                make_map()
                self._launch(executable, [*self._base_args(), "-writableRoot", "-mod=mcp_player",
                    f"-conf=mcp.session:{self.session}", f"-autostart={MAP}",
                    "-autostart-player=1", "-autostart-visibility=hidden", "-autostart-victory=endless"])
                self.mode = "properties_native"
                return self.status()

    server.game = RosterSession()
    atexit.register(server.game.close)
    server.main()


def preflight():
    if not EXE.is_file():
        raise FileNotFoundError("Isolated cached 0.28 runtime not found")
    if EXE.parent.name != "system" or EXE.parent.parent.name != "binaries":
        raise ValueError("Test executable must be in a dedicated binaries/system runtime")
    if os.name == "nt":
        result = subprocess.run(["powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name = 'pyrogenesis.exe'\" | Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        if str(CACHE).lower() in result.stdout.lower():
            raise RuntimeError("The isolated resource cache already has a running game")
    assert MODS.resolve() == (CACHE / "binaries/data/mods").resolve()


async def exercise(report):
    preflight()
    environment = dict(os.environ)
    environment.update(PYROGENESIS_EXE=str(EXE), PYROGENESIS_MODS_DIR=str(MODS))
    params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).resolve()), "--server"],
        cwd=ROOT, env=environment)
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=120)) as session:
            await session.initialize()

            async def call(name, **arguments):
                for attempt in range(5):
                    response = await session.call_tool(name, arguments)
                    if not response.isError:
                        return response.structuredContent if response.structuredContent is not None else json.loads(response.content[0].text)
                    message = response.content[0].text
                    # Native WriteJSONFile can briefly overlap a read. Retry
                    # only that transient availability error, not rejections.
                    if "Player observation is not ready" not in message or attempt == 4:
                        raise RuntimeError(f"{name}: {message}")
                    await asyncio.sleep(0.2)

            try:
                await call("start_local_game", networked=False)
                deadline = time.monotonic() + 100
                while time.monotonic() < deadline:
                    status = await call("player_status")
                    if status["ready"]:
                        break
                    if not status["running"]:
                        raise RuntimeError("Native engine exited during roster load")
                    await asyncio.sleep(0.5)
                else:
                    raise TimeoutError("Native roster did not become ready")
                report["status"] = status
                own = await call("observe_player")
                report["initial"] = own
                by_template = {item["template"]: item for item in own["entities"]}
                units = {}
                for role, template in ROSTER.items():
                    # Some native units instantly promote at initialization;
                    # follow the observed rank variant rather than old IDs.
                    item = by_template.get(template)
                    if item is None and template.endswith(("_b", "_a", "_e")):
                        stem = template[:-2]
                        item = next((value for key, value in by_template.items()
                            if key in {stem + suffix for suffix in ("_b", "_a", "_e")}), None)
                    assert item is not None, (role, template)
                    units[role] = item
                props = {role: item["properties"] for role, item in units.items()}
                for role, tag in (("dog", "war_dog"), ("minister", "minister"), ("elephant", "worker_elephant"),
                                  ("champion", "champion"), ("mercenary", "mercenary"), ("hero", "hero"),
                                  ("poison_champion", "champion")):
                    assert tag in props[role]["identity"]["special_roles"], (role, props[role])
                assert props["worker"]["capabilities"]["farm"] and props["worker"]["construction"]["templates"]
                assert props["priest"]["capabilities"]["heal"] and not props["priest"]["combat"]["can_attack"]
                assert not props["minister"]["combat"]["can_attack"] and props["minister"]["combat"]["can_capture"]
                assert props["minister"]["combat"]["auras"]
                assert props["elephant"]["capabilities"]["build"] and not props["elephant"]["capabilities"]["gather"]
                assert set(props["elephant"]["storage"]["accepts"]) == {"food", "wood", "stone", "metal"}
                for role in ("ram", "tower", "ship"):
                    assert props[role]["garrison"]["capacity"] > 0, role
                assert props["ram"]["garrison"]["occupied_slots"] > 0
                assert units["occupant"]["id"] in props["ram"]["garrison"]["occupants"]
                assert props["occupant"]["garrisoned_in"]["holder_id"] == units["ram"]["id"]
                assert props["packed"]["packing"]["state"] == "packed"
                assert props["unpacked"]["packing"]["state"] == "unpacked"
                assert props["catapult_packed"]["packing"]["state"] == "packed"
                assert props["catapult_unpacked"]["packing"]["state"] == "unpacked"
                def status_effects(profile):
                    return [effect for attack in profile["combat"]["attacks"].values()
                            for effect in attack["status_effects"]]

                poison = status_effects(props["poison_champion"])
                burn = status_effects(props["burn_champion"])
                assert any(effect["damage"].get("Poison", 0) > 0 for effect in poison), poison
                assert any(effect["damage"].get("Fire", 0) > 0 for effect in burn), burn
                immunity = props["ram"]["combat"]["resistance"]["status_effects"]
                assert any(effect["block_chance"] == 1 for effect in immunity.values()), immunity
                report["periodic_effects"] = {"poison_champion": poison, "champion": burn,
                                               "ram_immunity": immunity}
                bonuses = props["spearman"]["combat"]["attacks"]["Melee"]["bonuses"]
                assert any("Cavalry" in item["classes"] and item["multiplier"] > 1 for item in bonuses)
                report["anti_cavalry_bonuses"] = bonuses
                map_result = await call("observe_map_region", x_min=224, z_min=240, x_max=512, z_max=416, cell_size=16)
                report["map"] = map_result
                profiles = [item["properties"] for item in map_result["objects"] if "properties" in item]
                assert profiles and all(p["inventory"] is None and p["current_target"] is None and
                    p["construction"] is None and p["activity"] is None for p in profiles)
                assert all(p["garrison"] is None or p["garrison"]["occupants"] is None for p in profiles)
                worker = units["worker"]
                position = worker["position"]
                await call("move_units", entity_ids=[worker["id"]], x=position["x"]+32, z=position["z"])
                deadline = time.monotonic()+8
                while time.monotonic() < deadline:
                    current = await call("observe_player")
                    current_worker = next(item for item in current["entities"] if item["id"] == worker["id"])
                    target = current_worker["properties"]["current_target"]
                    if target and target["order"] == "Walk" and target["position"] is not None:
                        report["moving_worker"] = current_worker
                        break
                    await asyncio.sleep(0.25)
                else:
                    raise AssertionError("Owned current movement target was not observed")
                report["stop"] = await call("stop_units", entity_ids=[worker["id"]])
                report["passed"] = True
            finally:
                report["closed"] = await call("close_game")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", action="store_true")
    args = parser.parse_args()
    if args.server:
        serve()
        return
    report = {"test": "native_properties_roster", "passed": False, "isolated_runtime": str(CACHE)}
    try:
        asyncio.run(exercise(report))
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        output = ROOT / ".runtime/properties-native.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key not in {"initial", "map"}}, indent=2))


if __name__ == "__main__":
    main()
