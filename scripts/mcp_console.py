"""Persistent MCP client: one JSON tool request per stdin line, quit to leave."""

from __future__ import annotations

import asyncio
import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def compact(result):
    """Reduce printed profiles while retaining full permitted observations in reports."""
    if not isinstance(result, dict):
        return result
    result = dict(result)
    if "entities" in result:
        result["entities"] = [
            {**{key: value for key, value in entity.items() if key != "properties"},
             "properties": {key: value for key, value in entity.get("properties", {}).items()
                            if key in {"activity", "inventory", "current_target", "building", "garrisoned_in", "rally_point"}}}
            for entity in result["entities"]
        ]
    if "objects" in result:
        result["objects"] = [{key: value for key, value in entity.items() if key != "properties"}
                             for entity in result["objects"]]
    return result


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compact", action="store_true", help="Print shorter entity profiles")
    parser.add_argument("--report", type=Path, help="Append full MCP responses as JSON Lines")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "pyrogenesis_mcp.server"], cwd=root,
        env={key: os.environ[key] for key in
             ("PYROGENESIS_EXE", "PYROGENESIS_ENET_DLL", "PYROGENESIS_MODS_DIR")
             if key in os.environ},
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=120)) as session:
            initialized = await session.initialize()
            print(json.dumps({"event": "ready", "server": initialized.serverInfo.name}), flush=True)
            try:
                while True:
                    line = await asyncio.to_thread(sys.stdin.readline)
                    if not line or line.strip() == "quit":
                        break
                    try:
                        request = json.loads(line)
                        tool = request["tool"]
                        started = time.monotonic()
                        response = await session.call_tool(tool, request.get("arguments", {}))
                        result = response.structuredContent
                        if result is None:
                            result = [item.text for item in response.content if item.type == "text"]
                            if len(result) == 1:
                                try:
                                    result = json.loads(result[0])
                                except json.JSONDecodeError:
                                    result = result[0]
                        entry = {"tool": tool, "ok": not response.isError, "result": result,
                                 "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}
                        if args.report:
                            args.report.parent.mkdir(parents=True, exist_ok=True)
                            with args.report.open("a", encoding="utf-8") as report:
                                report.write(json.dumps({"request": request, **entry}) + "\n")
                        print(json.dumps({**entry, "result": compact(result) if args.compact else result}), flush=True)
                    except (KeyError, ValueError) as exc:
                        print(json.dumps({"ok": False, "error": str(exc)}), flush=True)
            finally:
                await session.call_tool("close_game", {})


if __name__ == "__main__":
    asyncio.run(main())
