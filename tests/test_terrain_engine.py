"""Terrain-only engine requests use the map bridge without entity enumeration."""

import time

from pyrogenesis_mcp.engine import GameSession, POLICY
from pyrogenesis_mcp.map_observation import MAP_POLICY


def test_terrain_analysis_requests_no_objects_and_returns_compact_grid(tmp_path, monkeypatch):
    game = GameSession()
    game.session = "terrain-session"
    game._bridge = tmp_path
    region = {"x_min": 0, "z_min": 0, "x_max": 16, "z_max": 16, "cell_size": 8}
    cells = []
    for z in (4, 12):
        for x in (4, 12):
            cells.append({"x": x, "z": z, "in_bounds": True, "explored": True,
                          "currently_visible": True,
                          "terrain": {"height": x + z, "water_level": 0, "last_seen": 1000}})
    raw = {
        "version": 1, "session": game.session, "policy": POLICY,
        "available": True, "emitted_at_ms": time.time() * 1000,
        "player_id": 1, "game_time_ms": 1000, "map_size": 512,
        "resources": {}, "population": {}, "own_entities": [],
        "map_request_id": "terrain-command-id",
        "map_region": {"policy": MAP_POLICY, "player": 1, "time": 1000,
                       "region": region, "columns": 2, "rows": 2,
                       "cells": cells, "objects": [], "truncated": False},
        "last_action": {"id": "terrain-command-id", "action": "observe_map", "ok": True},
    }
    writes = []

    def write(action, **fields):
        writes.append((action, fields))
        return "terrain-command-id"

    monkeypatch.setattr(game, "_read_raw", lambda **_kwargs: raw)
    monkeypatch.setattr(game, "_write_command", write)
    result = game.analyze_terrain_region(0, 0, 16, 16, 8, 8, 8)
    assert writes == [("observe_map", {"region": region, "include_objects": False})]
    assert result["source_policy"] == MAP_POLICY
    assert result["grid"]["height"] == [8, 16, 16, 24]
    assert len(result["grid"]["slope_degrees"]) == 4
    assert "objects" not in result
