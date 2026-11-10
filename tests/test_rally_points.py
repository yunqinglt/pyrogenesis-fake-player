"""Rally queues are bounded own-player defaults, separate from current unit orders."""

from copy import deepcopy

import pytest

from pyrogenesis_mcp.actions import (
    RallyPositionOrder, RallyTargetOrder, validate_rally_orders, validate_rally_selection,
)
from pyrogenesis_mcp.engine import GameSession
from pyrogenesis_mcp.privacy import ObservationError
from pyrogenesis_mcp.properties import project_properties
from test_properties import properties


@pytest.fixture
def observation():
    return {"player": 2, "mapSize": 512, "entities": [
        {"id": 10, "owner": 2, "movable": False, "properties": {
            "rally_point": {"points": [], "truncated": False},
            "building": {"foundation": None}}},
        {"id": 11, "owner": 2, "movable": True, "properties": {
            "rally_point": {"points": [], "truncated": False}, "building": None}},
        {"id": 12, "owner": 1, "properties": {"rally_point": {"points": [], "truncated": False}}},
    ]}


@pytest.fixture
def game(observation, monkeypatch):
    session, writes = GameSession(), []
    monkeypatch.setattr(session, "observe", lambda: deepcopy(observation))
    monkeypatch.setattr(session, "_write_command", lambda action, **fields:
                        writes.append((action, fields)) or "ack")
    monkeypatch.setattr(session, "_action_ack", lambda command_id, action, count:
                        {"accepted": True, "action": action, "entityCount": count})
    return session, writes


def test_rally_defaults_do_not_use_movable_selection_or_unit_orders(game):
    session, writes = game
    orders = [RallyPositionOrder(type="move", x=40, z=50), RallyTargetOrder(type="gather", target=90)]
    result = session.set_rally_point([10, 10, 11], orders, queued=True)
    assert result == {"accepted": True, "action": "set_rally", "entityCount": 2, "orderCount": 2}
    assert session.clear_rally_point([10])["accepted"]
    assert writes == [
        ("set_rally", {"entities": [10, 11], "orders": [
            {"type": "move", "x": 40, "z": 50}, {"type": "gather", "target": 90}], "queued": True}),
        ("clear_rally", {"entities": [10]}),
    ]


@pytest.mark.parametrize("kind", ["attack", "capture", "gather", "repair", "garrison",
                                  "occupy_turret", "trade", "collect_treasure"])
def test_supported_target_orders(kind):
    assert validate_rally_orders([{"type": kind, "target": 9}], 512) == [{"type": kind, "target": 9}]


@pytest.mark.parametrize("orders", [[], None, {}, [True], [{"type": []}],
    [{"type": "move", "x": -1, "z": 1}], [{"type": "move", "x": 513, "z": 1}],
    [{"type": "move", "x": True, "z": 1}], [{"type": "move", "x": float("nan"), "z": 1}],
    [{"type": "move", "x": 1, "z": float("inf")}], [{"type": "move", "x": 1}],
    [{"type": "move", "x": 1, "z": 2, "target": 9}], [{"type": "gather", "target": True}],
    [{"type": "gather", "target": 2**31}], [{"type": "gather", "target": 0}],
    [{"type": "garrison", "target": 9, "command": "raw"}], [{"type": "heal", "target": 9}],
    [{"type": "guard", "target": 9}], [{"type": "return_resource", "target": 9}],
    [{"type": "build", "target": 9}], [{"type": "move", "x": 1, "z": 2}] * 101])
def test_bad_batches_write_nothing(game, orders):
    session, writes = game
    with pytest.raises((ValueError, ObservationError)):
        session.set_rally_point([10], orders)
    assert not writes


@pytest.mark.parametrize("selection", [[], [True], [12], [999], [10, 12], [10] * 201])
def test_foreign_or_invalid_selection_is_atomic(game, selection):
    session, writes = game
    for method in (lambda: session.set_rally_point(selection, [{"type": "move", "x": 0, "z": 0}]),
                   lambda: session.clear_rally_point(selection)):
        with pytest.raises(ValueError):
            method()
    assert not writes


def test_capability_foundation_and_queue_bound(observation):
    profile = observation["entities"][0]["properties"]
    profile["rally_point"]["points"] = [{}] * 99
    assert validate_rally_selection([10], observation, additional=1, queued=True) == [10]
    with pytest.raises(ValueError):
        validate_rally_selection([10], observation, additional=2, queued=True)
    profile["rally_point"]["truncated"] = True
    with pytest.raises(ValueError):
        validate_rally_selection([10], observation, additional=1, queued=True)
    # Replacing or clearing an oversized existing queue remains possible.
    assert validate_rally_selection([10], observation) == [10]
    profile["building"]["foundation"] = {"builders": 1}
    with pytest.raises(ValueError):
        validate_rally_selection([10], observation)
    profile["building"]["foundation"] = None
    profile["rally_point"] = None
    with pytest.raises(ValueError):
        validate_rally_selection([10], observation)


@pytest.mark.parametrize("queued", [1, "true", None])
def test_strict_queue_flag(game, queued):
    session, writes = game
    with pytest.raises(ValueError):
        session.set_rally_point([10], [{"type": "move", "x": 0, "z": 0}], queued=queued)
    assert not writes


def test_private_rally_projection_is_allowlisted_and_legacy_optional():
    raw = properties(rally_point={"points": [
        {"order": "gather", "entity_id": 7, "position": {"x": 3, "z": 4, "secret": 9}, "raw": "SECRET"}],
        "truncated": False, "raw": "SECRET"})
    before = deepcopy(raw)
    projected = project_properties(raw, private=True)["rally_point"]
    assert projected == {"points": [{"order": "gather", "entity_id": 7,
                                    "position": {"x": 3, "z": 4}}], "truncated": False}
    projected["points"][0]["position"]["x"] = 99
    assert raw == before
    legacy = properties()
    del legacy["rally_point"]
    assert project_properties(legacy, private=True)["rally_point"] is None
    raw["rally_point"] = "malformed private data"
    assert project_properties(raw, private=False)["rally_point"] is None


@pytest.mark.parametrize("rally", [{"points": [], "truncated": 1}, {"points": "bad", "truncated": False},
    {"points": [{"order": "raw", "entity_id": None, "position": None}], "truncated": False},
    {"points": [{"order": "move", "entity_id": None, "position": None}] * 101, "truncated": True}])
def test_malformed_owned_rally_data_fails_closed(rally):
    with pytest.raises(ObservationError):
        project_properties(properties(rally_point=rally), private=True)
