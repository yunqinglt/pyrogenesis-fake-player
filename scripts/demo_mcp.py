"""Control a real player using only the public MCP stdio tools."""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
import json
import math
from pathlib import Path
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def exercise(networked: bool, timeout: float, report: dict) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "pyrogenesis_mcp.server"],
        cwd=Path(__file__).resolve().parents[1],
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=timeout)) as session:
            initialized = await session.initialize()
            report["server"] = initialized.serverInfo.name
            report["protocol"] = initialized.protocolVersion

            async def call(name: str, **arguments) -> dict:
                result = await session.call_tool(name, arguments)
                if result.isError:
                    raise RuntimeError(f"MCP tool {name} failed")
                return (result.structuredContent if result.structuredContent is not None
                        else json.loads(result.content[0].text))

            try:
                report["tool_count"] = len((await session.list_tools()).tools)
                await call("start_local_game", networked=networked)
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    status = await call("player_status")
                    if status["ready"]:
                        break
                    if not status["running"]:
                        raise RuntimeError("Engine exited before MCP player became ready")
                    await asyncio.sleep(0.5)
                else:
                    raise TimeoutError("MCP player did not finish loading")
                report["status"] = status
                observation = await call("observe_player")
                assert observation["policy"] == "own-player-only-v1"
                assert all(entity["owner"] == observation["player"] for entity in observation["entities"])
                unit = next(entity for entity in observation["entities"]
                            if entity["movable"] and "position" in entity)
                before = unit["position"]
                target = {"x": min(observation["mapSize"] - 2, before["x"] + 12), "z": before["z"]}
                report.update(player=observation["player"], own_entity_count=len(observation["entities"]),
                              entity=unit["id"], before=before, target=target)
                report["move"] = await call("move_units", entity_ids=[unit["id"]], **target)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    await asyncio.sleep(0.5)
                    observation = await call("observe_player")
                    after = next(entity["position"] for entity in observation["entities"]
                                 if entity["id"] == unit["id"])
                    distance = math.hypot(after["x"] - before["x"], after["z"] - before["z"])
                    if distance >= 2:
                        report.update(after=after, distance=distance)
                        break
                else:
                    raise RuntimeError("MCP move acknowledged without observed displacement")
                report["stop"] = await call("stop_units", entity_ids=[unit["id"]])
                report["passed"] = True
            finally:
                report["closed"] = await call("close_game")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--singleplayer", action="store_true")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    report = {"test": "mcp_stdio_player_control", "networked": not args.singleplayer, "passed": False}
    try:
        asyncio.run(exercise(not args.singleplayer, args.timeout, report))
    except Exception as exc:
        report.update(passed=False, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        output = Path(".runtime/mcp-singleplayer.json" if args.singleplayer else ".runtime/mcp-network.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
