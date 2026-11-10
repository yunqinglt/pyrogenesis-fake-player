"""Exercise bounded map observations through real MCP stdio and the native engine."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import timedelta
import json
import math
import os
from pathlib import Path
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def exercise(singleplayer: bool, report: dict) -> None:
    root = Path(__file__).resolve().parents[1]
    environment = {key: os.environ[key] for key in
                   ("PYROGENESIS_EXE", "PYROGENESIS_ENET_DLL", "PYROGENESIS_MODS_DIR")
                   if key in os.environ}
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "pyrogenesis_mcp.server"],
                                      cwd=root, env=environment)
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=120)) as session:
            await session.initialize()

            async def call(name, **arguments):
                response = await session.call_tool(name, arguments)
                if response.isError:
                    raise RuntimeError(f"MCP {name} failed: {response.content[0].text}")
                return (response.structuredContent if response.structuredContent is not None
                        else json.loads(response.content[0].text))

            async def observe_map(observation):
                size = observation["mapSize"]
                result = await call("observe_map_region", x_min=0, z_min=0, x_max=size, z_max=size,
                                    cell_size=max(16, math.ceil(size / 64)))
                assert len(result["cells"]) <= 4096 and len(result["objects"]) <= 2000
                assert all(cell["terrain"] is None for cell in result["cells"] if not cell["explored"])
                assert all(cell["explored"] for cell in result["cells"] if cell["currently_visible"])
                assert all(item["currently_visible"] or item["static"] for item in result["objects"])
                return result

            def summarize(result):
                return {
                    "time": result["time"], "cell_count": len(result["cells"]),
                    "unknown": sum(not cell["explored"] and cell["in_bounds"] for cell in result["cells"]),
                    "visible": sum(cell["currently_visible"] for cell in result["cells"]),
                    "fogged": sum(cell["explored"] and not cell["currently_visible"] for cell in result["cells"]),
                    "remembered_terrain": sum(cell["terrain"] is not None and not cell["currently_visible"]
                                              for cell in result["cells"]),
                    "objects": dict(Counter(item["kind"] for item in result["objects"])),
                    "remembered_objects": sum(not item["currently_visible"] for item in result["objects"]),
                    "truncated": result["truncated"],
                }

            try:
                await call("start_local_game", networked=not singleplayer)
                print("Loading native map test...", flush=True)
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    status = await call("player_status")
                    if status["ready"]:
                        break
                    if not status["running"]:
                        raise RuntimeError("Native engine stopped while loading")
                    await asyncio.sleep(0.5)
                else:
                    raise TimeoutError("Native map test startup timed out")
                report["status"] = status
                own = await call("observe_player")
                initial = await observe_map(own)
                report["initial"] = summarize(initial)
                print(json.dumps({"initial": report["initial"]}), flush=True)
                assert report["initial"]["unknown"] > 0 and report["initial"]["visible"] > 0
                assert initial["objects"], "No visible native map objects"
                scout = next((item for item in own["entities"] if item["movable"] and
                              "cavalry" in item.get("template", "")),
                             next(item for item in own["entities"] if item["movable"]))
                origin = scout["position"]
                size = own["mapSize"]
                # Walk toward the center of this test map, away from the starting base.
                dx, dz = size / 2 - origin["x"], size / 2 - origin["z"]
                length = math.hypot(dx, dz)
                target = {"x": origin["x"] + dx / length * 220,
                          "z": origin["z"] + dz / length * 220}
                await call("move_units", entity_ids=[scout["id"]], **target)
                best_fogged = initial
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    await asyncio.sleep(1)
                    own = await call("observe_player")
                    current = next(item for item in own["entities"] if item["id"] == scout["id"])
                    position = current["position"]
                    best_fogged = await observe_map(own)
                    if math.hypot(position["x"] - origin["x"], position["z"] - origin["z"]) >= 140:
                        break
                report["outward"] = summarize(best_fogged)
                await call("move_units", entity_ids=[scout["id"]], **origin)
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    await asyncio.sleep(1)
                    own = await call("observe_player")
                    final = await observe_map(own)
                    report["final"] = summarize(final)
                    if report["final"]["remembered_terrain"] > 0:
                        break
                await call("stop_units", entity_ids=[scout["id"]])
                report["ending_status"] = await call("player_status")
                assert report["ending_status"]["ready"]
                assert report["final"]["remembered_terrain"] > 0, "No native visible-to-fog transition observed"
                # The real historical records must match an earlier sample, not present time.
                assert any(cell["terrain"] is not None and not cell["currently_visible"] and
                           cell["terrain"]["last_seen"] < final["time"] for cell in final["cells"])
                report["passed"] = True
            finally:
                report["closed"] = await call("close_game")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--singleplayer", action="store_true")
    args = parser.parse_args()
    report = {"networked": not args.singleplayer, "passed": False}
    try:
        asyncio.run(exercise(args.singleplayer, report))
    finally:
        path = Path(__file__).resolve().parents[1] / ".runtime" / (
            "map-singleplayer.json" if args.singleplayer else "map-network.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
