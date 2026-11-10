"""Opt-in live test: two MCP processes join one private three-player room.

Starts only owned test processes; never connects to an existing user room.
All match observations/actions use public MCP tools. Writes a local JSON report.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import time
import traceback

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from pyrogenesis_mcp.engine import GameSession, free_udp_port


ROOT = Path(__file__).resolve().parents[1]


class Participant:
    def __init__(self, nickname: str, report: dict):
        self.nickname = nickname
        self.report = report
        self.queue = asyncio.Queue()
        self.task = None
        self.initialized = asyncio.Event()
        self.error = None

    async def worker(self):
        inherited = {key: os.environ[key] for key in (
            "PYROGENESIS_EXE", "PYROGENESIS_ENET_DLL", "PYROGENESIS_ENET_LIBRARY",
            "PYROGENESIS_MODS_DIR", "XDG_DATA_HOME", "LD_LIBRARY_PATH",
            "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR"
        ) if key in os.environ}
        inherited["PYTHONPATH"] = str(ROOT / "src")
        parameters = StdioServerParameters(command=sys.executable,
            args=["-m", "pyrogenesis_mcp.server"], cwd=ROOT, env=inherited)
        try:
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=180)) as session:
                    await session.initialize()
                    self.initialized.set()
                    while True:
                        request = await self.queue.get()
                        if request is None:
                            break
                        tool, arguments, future = request
                        try:
                            response = await session.call_tool(tool, arguments)
                            result = response.structuredContent
                            if result is None:
                                result = json.loads(response.content[0].text) if not response.isError else response.content[0].text
                            event = {"nickname": self.nickname, "tool": tool,
                                     "arguments": arguments, "ok": not response.isError, "result": result}
                            self.report["events"].append(event)
                            future.set_result(event)
                        except Exception as exc:
                            future.set_exception(exc)
        except Exception as exc:
            self.error = str(exc)
            self.initialized.set()
            raise

    async def start(self):
        self.task = asyncio.create_task(self.worker())
        await asyncio.wait_for(self.initialized.wait(), timeout=30)
        if self.error:
            raise RuntimeError(self.error)

    async def call(self, tool, arguments=None, *, reject=False):
        if self.task.done():
            await self.task
            raise RuntimeError("Participant server exited")
        future = asyncio.get_running_loop().create_future()
        await self.queue.put((tool, arguments or {}, future))
        event = await asyncio.wait_for(future, timeout=190)
        if event["ok"] == reject:
            raise AssertionError(f"{self.nickname}: unexpected {tool} result: {event}")
        return event["result"]

    async def stop(self):
        if self.task is not None:
            if not self.task.done():
                await self.queue.put(None)
            await asyncio.wait_for(self.task, timeout=40)
            self.task = None


async def wait_ready(players):
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        states = await asyncio.gather(*(player.call("player_status") for player in players))
        if all(state.get("ready") for state in states):
            return states
        await asyncio.sleep(2)
    raise TimeoutError("Both test clients did not become ready within 150 seconds")


async def exercise(report):
    host = GameSession()
    players = [Participant(name, report) for name in ("MCP-青龙", "MCP-白虎")]
    try:
        exe = await asyncio.to_thread(host._prepare)
        port = free_udp_port()
        host_exe = await asyncio.to_thread(host._runtime_exe, exe, "host")
        host._launch(host_exe, [*host._base_args(), "-autostart=random/mainland",
            "-autostart-host", "-autostart-host-players=3", "-autostart-players=3",
            f"-autostart-port={port}", "-autostart-playername=Coexistence-Test-Host",
            "-autostart-seed=7", "-autostart-size=192", "-autostart-ceasefire=45",
            "-autostart-civ=1:athen", "-autostart-civ=2:han", "-autostart-civ=3:brit",
            "-autostart-visibility=hidden", "-autostart-victory=endless"])
        report["host_pid"] = host.processes[0].pid
        report["host_port"] = port
        report["host_map"] = "random/mainland"
        for player in players:
            await player.start()
        await asyncio.gather(*(player.call("join_lan_game", {
            "host": "127.0.0.1", "port": port, "nickname": player.nickname}) for player in players))
        states = await wait_ready(players)
        assert len({state["player"] for state in states}) == 2
        assert all(state["player"] in {1, 2, 3} for state in states), states
        before = await asyncio.gather(*(player.call("observe_player") for player in players))
        assert set(entity["id"] for entity in before[0]["entities"]).isdisjoint(
            entity["id"] for entity in before[1]["entities"])
        selected = [next(entity for entity in observation["entities"]
                         if entity.get("movable") and entity.get("position")) for observation in before]
        # Starting workers stand beside the civic centre. Move away from its
        # footprint instead of choosing a point that may lie inside the building.
        orders = [{"entity_ids": [entity["id"]], "x": entity["position"]["x"] - 12,
                   "z": entity["position"]["z"] - 12} for entity in selected]
        await asyncio.gather(*(player.call("move_units", order) for player, order in zip(players, orders)))
        await asyncio.gather(*(player.call("move_units", {
            **orders[index], "entity_ids": [selected[1 - index]["id"]]}, reject=True)
            for index, player in enumerate(players)))
        await asyncio.sleep(4)
        after = await asyncio.gather(*(player.call("observe_player") for player in players))
        distances = []
        for index, observation in enumerate(after):
            moved = next(entity for entity in observation["entities"] if entity["id"] == selected[index]["id"])
            distance = sum((moved["position"][key] - selected[index]["position"][key]) ** 2
                           for key in ("x", "z")) ** 0.5
            assert distance > 0.5, "Accepted order must produce observable movement"
            distances.append(distance)
        await players[0].stop()  # Actual first MCP process exits; its owned client must close.
        await asyncio.sleep(2)
        survivor = await players[1].call("player_status")
        assert survivor["ready"] and survivor["running"]
        await players[1].call("stop_units", {"entity_ids": [selected[1]["id"]]})
        survivor_observation = await players[1].call("observe_player")
        assert survivor_observation["player"] == before[1]["player"]
        report["checks"] = {"separate_player_ids": [state["player"] for state in states],
            "simultaneous_observations": True, "owned_entity_sets_disjoint": True,
            "simultaneous_orders": True, "movement_distances": distances,
            "foreign_units_rejected": True, "survives_other_mcp_exit": True,
            "survivor_post_exit_action": True}
        report["ok"] = True
    finally:
        for player in players:
            try:
                await player.stop()
            except Exception as exc:
                report.setdefault("cleanup_errors", []).append(str(exc))
        await asyncio.to_thread(host.close)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch-game", action="store_true", help="Explicitly authorize three owned graphical test processes")
    parser.add_argument("--report", type=Path, default=ROOT / ".runtime/coexistence-live.json")
    args = parser.parse_args()
    if not args.launch_game:
        parser.error("Use --launch-game to run the live test; ordinary pytest never starts a game")
    report = {"ok": False, "events": [], "scope": "two independent MCP processes in one private LAN room"}
    try:
        asyncio.run(exercise(report))
    except Exception as exc:
        report["error"] = str(exc) or type(exc).__name__
        report["traceback"] = traceback.format_exc()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({key: value for key, value in report.items() if key != "events"}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
