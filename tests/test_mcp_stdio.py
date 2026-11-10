"""Use the public MCP wire protocol against an actual stdio subprocess."""

import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_handshake_tool_surface_and_public_idle_status():
    async def exercise():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "pyrogenesis_mcp.server"],
            cwd=Path(__file__).resolve().parents[1],
            # A standalone Linux Python may need its own shared-library search path.
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                 **({"LD_LIBRARY_PATH": os.environ["LD_LIBRARY_PATH"]}
                    if "LD_LIBRARY_PATH" in os.environ else {})},
        )
        async with asyncio.timeout(20):
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(
                    reader, writer, read_timeout_seconds=timedelta(seconds=10)
                ) as session:
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == "Pyrogenesis Fair Player"
                    listed = await session.list_tools()
                    tools = {tool.name: tool for tool in listed.tools}
                    assert set(tools) == {
                        "start_local_game", "join_lan_game", "player_status", "observe_player",
                        "move_units", "stop_units", "close_game", "observe_map_region",
                        "analyze_terrain_region",
                        "order_units", "abandon_targets", "set_unit_stance",
                        "delete_own_units", "delete_own_buildings",
                        "train_units", "research_technology",
                        "describe_options", "set_unit_packing", "cancel_unit_packing",
                        "unload_garrison", "leave_turret", "cancel_production",
                        "set_rally_point", "clear_rally_point",
                    }
                    expected_parameters = {
                        "start_local_game": {"networked"},
                        "join_lan_game": {"host", "port", "player_name", "nickname"},
                        "player_status": set(),
                        "observe_player": set(),
                        "observe_map_region": {"x_min", "z_min", "x_max", "z_max", "cell_size"},
                        "analyze_terrain_region": {"x_min", "z_min", "x_max", "z_max",
                                                    "footprint_width", "footprint_depth", "cell_size"},
                        "move_units": {"entity_ids", "x", "z", "queued"},
                        "stop_units": {"entity_ids"},
                        "order_units": {"entity_ids", "orders", "queued"},
                        "set_rally_point": {"entity_ids", "orders", "queued"},
                        "clear_rally_point": {"entity_ids"},
                        "abandon_targets": {"entity_ids"},
                        "set_unit_stance": {"entity_ids", "stance"},
                        "delete_own_units": {"entity_ids"},
                        "delete_own_buildings": {"entity_ids"},
                        "train_units": {"building_id", "template", "count"},
                        "research_technology": {"building_id", "technology"},
                        "describe_options": {"entity_id", "kind", "ids"},
                        "set_unit_packing": {"entity_ids", "packed", "queued"},
                        "cancel_unit_packing": {"entity_ids"},
                        "unload_garrison": {"holder_id", "entity_ids"},
                        "leave_turret": {"entity_ids"},
                        "cancel_production": {"building_id", "queue_id"},
                        "close_game": set(),
                    }
                    for name, expected in expected_parameters.items():
                        properties = set(tools[name].inputSchema.get("properties", {}))
                        assert properties == expected
                        assert properties.isdisjoint({
                            "packet", "payload", "raw", "script", "javascript", "command",
                            "commands", "opponent", "state", "player_id", "file", "path",
                        })
                    response = await session.call_tool("player_status", {})
                    assert response.isError is False
                    # The public idle response contains neither traffic counters nor bridge paths.
                    expected_status = {
                        "mode": "stopped", "running": False, "ready": False,
                        "policy": "own-player-only-v1",
                    }
                    if response.structuredContent is not None:
                        assert response.structuredContent == expected_status
                    else:
                        assert json.loads(response.content[0].text) == expected_status
                    # Reading before startup returns a generic error, without launching a game.
                    unavailable = await session.call_tool("observe_player", {})
                    assert unavailable.isError is True
                    assert "Start or join a game first" in unavailable.content[0].text
                    # Name errors must be rejected before any game is launched or joined.
                    for arguments in (
                        {"host": "127.0.0.1", "nickname": "Model A", "player_name": "Model B"},
                        {"host": "127.0.0.1", "nickname": "   "},
                        {"host": "127.0.0.1", "nickname": "Model\nA"},
                    ):
                        rejected = await session.call_tool("join_lan_game", arguments)
                        assert rejected.isError is True
                    still_idle = await session.call_tool("player_status", {})
                    assert still_idle.isError is False
                    status = still_idle.structuredContent or json.loads(still_idle.content[0].text)
                    assert status == expected_status

    asyncio.run(exercise())
