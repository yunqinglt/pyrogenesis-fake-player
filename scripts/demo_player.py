"""Run a real game, move one own unit, verify displacement and stop the session."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time

from pyrogenesis_mcp.engine import GameSession


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--singleplayer", action="store_true")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args()
    game = GameSession()
    report = {"test": "native_player_control", "networked": not args.singleplayer}
    try:
        print(json.dumps(game.start_local(networked=not args.singleplayer)), flush=True)
        deadline = time.monotonic() + args.timeout
        last_state = None
        while time.monotonic() < deadline:
            status = game.status()
            if status != last_state:
                print(json.dumps(status), flush=True)
                last_state = status
            if status["ready"]:
                break
            if not status["running"]:
                raise RuntimeError("The engine exited before the player became ready")
            time.sleep(0.5)
        else:
            raise TimeoutError("The player did not finish loading")
        observation = game.observe()
        unit = next(item for item in observation["entities"] if item["movable"] and "position" in item)
        before = unit["position"]
        target = {"x": min(observation["mapSize"] - 2, before["x"] + 12), "z": before["z"]}
        report.update(player=observation["player"], own_entity_count=len(observation["entities"]),
                      entity=unit["id"], before=before, target=target, policy=observation["policy"])
        report["move"] = game.act("move", [unit["id"]], **target)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            time.sleep(0.5)
            observation = game.observe()
            after = next(item["position"] for item in observation["entities"] if item["id"] == unit["id"])
            distance = math.hypot(after["x"] - before["x"], after["z"] - before["z"])
            if distance >= 2:
                report.update(after=after, distance=distance)
                break
        else:
            raise RuntimeError("Move was acknowledged but unit displacement was not observed")
        report["stop"] = game.act("stop", [unit["id"]])
        if game.relay:
            report["relay"] = game.relay.status()  # local developer evidence, never an MCP output
            if (report["relay"]["state"] != "connected" or
                    report["relay"]["packets_to_host"] == 0 or
                    report["relay"]["packets_to_client"] == 0):
                raise RuntimeError("Player traffic did not pass through the Python ENet relay")
        report["passed"] = True
    except Exception as exc:
        report.update(passed=False, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        output = Path(".runtime/demo-network.json" if not args.singleplayer else ".runtime/demo-singleplayer.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        if not args.keep_open:
            game.close()


if __name__ == "__main__":
    main()
