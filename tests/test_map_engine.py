"""Region requests use the same session-bound, serialized bridge as actions."""

import time

import pytest

from pyrogenesis_mcp.engine import GameSession, POLICY
from pyrogenesis_mcp.map_observation import MAP_POLICY
from pyrogenesis_mcp.privacy import ObservationError


@pytest.fixture
def map_bridge(tmp_path, monkeypatch):
    game = GameSession()
    game.session = "map-session"
    game._bridge = tmp_path
    region = {"x_min": 0, "z_min": 0, "x_max": 32, "z_max": 32, "cell_size": 16}
    payload = {
        "policy": MAP_POLICY, "player": 1, "time": 1000,
        "region": region, "columns": 2, "rows": 2,
        "cells": [
            {"x": x, "z": z, "in_bounds": True, "explored": False,
             "currently_visible": False, "terrain": None}
            for z in (8, 24) for x in (8, 24)
        ],
        "objects": [], "truncated": False,
    }
    raw = {
        "version": 1, "session": game.session, "policy": POLICY,
        "available": True, "emitted_at_ms": time.time() * 1000,
        "player_id": 1, "game_time_ms": 1000, "map_size": 512,
        "resources": {}, "population": {}, "own_entities": [],
        "map_request_id": "map-command-id", "map_region": payload,
        "last_action": {"id": "map-command-id", "action": "observe_map", "ok": True},
    }
    writes = []

    def write(action, **fields):
        writes.append((action, fields))
        return "map-command-id"

    monkeypatch.setattr(game, "_read_raw", lambda **_kwargs: raw)
    monkeypatch.setattr(game, "_write_command", write)
    return game, raw, writes, payload


def fast_deadline(monkeypatch):
    times = iter([0, 1, 6])
    monkeypatch.setattr("pyrogenesis_mcp.engine.time.monotonic", lambda: next(times, 100))
    monkeypatch.setattr("pyrogenesis_mcp.engine.time.sleep", lambda _seconds: None)


def test_map_request_uses_exact_bounded_region_and_projects_response(map_bridge):
    game, raw, writes, payload = map_bridge
    payload["private_seed"] = "SECRET"
    payload["cells"][0]["terrain"] = {"SECRET": True}
    raw["last_action"]["error"] = "SECRET"
    result = game.observe_map_region(0, 0, 32, 32)
    assert writes == [("observe_map", {"region": payload["region"]})]
    assert result["policy"] == MAP_POLICY
    assert result["player"] == 1
    assert result["region"] == payload["region"]
    assert len(result["cells"]) == 4
    assert "SECRET" not in str(result)


@pytest.mark.parametrize("coordinates", [
    (-1, 0, 32, 32, 16), (0, 0, 513, 32, 16), (0, 0, 0, 32, 16),
    (32, 0, 0, 32, 16), (0, 32, 32, 0, 16), (0, 0, 32, 32, 3),
    (0, 0, 32, 32, 257), (True, 0, 32, 32, 16),
    (0, 0, float("nan"), 32, 16), (0, 0, 32, float("inf"), 16),
    (0, 0, 512, 512, 4),
])
def test_invalid_map_region_never_writes_bridge_command(map_bridge, coordinates):
    game, _raw, writes, _payload = map_bridge
    with pytest.raises((ValueError, ObservationError)):
        game.observe_map_region(*coordinates)
    assert writes == []


@pytest.mark.parametrize("change", [
    {"last_action": {"id": "other-request", "action": "observe_map", "ok": True}},
    {"last_action": {"id": "map-command-id", "action": "move", "ok": True}},
    {"last_action": {"id": "map-command-id", "action": "observe_map", "ok": False,
                     "error": "SECRET_STACK_TRACE"}},
    {"map_request_id": "previous-request"},
    {"map_region": None},
])
def test_wrong_ack_or_result_cannot_satisfy_map_request(map_bridge, monkeypatch, change):
    game, raw, writes, _payload = map_bridge
    raw.update(change)
    fast_deadline(monkeypatch)
    with pytest.raises((ValueError, RuntimeError, TimeoutError)) as caught:
        game.observe_map_region(0, 0, 32, 32)
    assert len(writes) == 1
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("change", [
    {"policy": "raw-world"}, {"player": 2}, {"time": float("nan")},
    {"time": 999},
    {"region": {"x_min": 0, "z_min": 0, "x_max": 64, "z_max": 64, "cell_size": 16}},
    {"columns": 3}, {"cells": []}, {"objects": [None] * 2001},
])
def test_map_result_is_revalidated_after_successful_ack(map_bridge, change):
    game, _raw, writes, payload = map_bridge
    payload.update(change)
    with pytest.raises((ValueError, RuntimeError)):
        game.observe_map_region(0, 0, 32, 32)
    assert len(writes) == 1
