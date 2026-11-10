"""Siege state transitions and garrison/queue selectors use owned current state."""

from copy import deepcopy

import pytest

from pyrogenesis_mcp.actions import validate_packing, validate_production_cancel, validate_unload
from pyrogenesis_mcp.engine import GameSession


@pytest.fixture
def observation():
    return {"player": 2, "mapSize": 512, "entities": [
        {"id": 10, "owner": 2, "movable": False, "properties": {
            "identity": {"classes": ["Unit", "Siege"]},
            "packing": {"packed": False, "changing": False, "progress": 0, "state": "unpacked"}}},
        {"id": 11, "owner": 2, "movable": False, "properties": {
            "identity": {"classes": ["Unit"]}, "garrisoned_in": {"holder_id": 20},
            "capabilities": {"occupy_turret": True}}},
        {"id": 12, "owner": 1, "movable": False, "properties": {
            "identity": {"classes": ["Unit"]}, "garrisoned_in": {"holder_id": 20}}},
        {"id": 20, "owner": 2, "movable": False, "properties": {
            "identity": {"classes": ["Structure"]}, "garrison": {"occupants": [11, 12]},
            "building": {"is_building": True, "foundation": None,
                         "production": {"queue": [{"id": 0}, {"id": 7}]}}}},
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


def test_explicit_packing_unloading_and_queue_cancellation_write_semantic_commands(game, observation):
    session, writes = game
    assert session.set_unit_packing([10, 10], True, queued=True)["accepted"]
    observation["entities"][0]["properties"]["packing"]["changing"] = True
    assert session.cancel_unit_packing([10])["accepted"]
    assert session.unload_garrison(20)["entityCount"] == 1
    assert session.cancel_production(20, 0)["accepted"]
    assert writes == [
        ("packing", {"entities": [10], "packed": True, "queued": True}),
        ("cancel_packing", {"entities": [10]}),
        ("unload", {"holder_id": 20, "entities": [11]}),
        ("cancel_production", {"entities": [20], "queue_id": 0}),
    ]


@pytest.mark.parametrize("packed", [None, 0, 1, "true", [], {}])
def test_non_boolean_packing_never_writes(game, packed):
    session, writes = game
    with pytest.raises(ValueError):
        session.set_unit_packing([10], packed)
    assert writes == []


@pytest.mark.parametrize("change", ["already", "changing", "missing", "foreign", "mixed"])
def test_invalid_packing_state_rejects_entire_selection(observation, change):
    unit = observation["entities"][0]
    selected = [10]
    if change == "already":
        unit["properties"]["packing"]["packed"] = True
    elif change == "changing":
        unit["properties"]["packing"]["changing"] = True
    elif change == "missing":
        unit["properties"]["packing"] = None
    elif change == "foreign":
        unit["owner"] = 1
    else:
        selected.append(11)
    with pytest.raises(ValueError):
        validate_packing(selected, observation, packed=True)


def test_cancel_requires_active_transition(observation):
    with pytest.raises(ValueError):
        validate_packing([10], observation, cancel=True)
    observation["entities"][0]["properties"]["packing"]["changing"] = True
    assert validate_packing([10], observation, cancel=True) == [10]


@pytest.mark.parametrize("holder,selected", [(True, None), (999, None), (20, []), (20, [12]),
    (20, [True]), (20, [10]), (20, [11, 12]), (20, [999])])
def test_unloading_rejects_foreign_missing_empty_or_nonresident_selection(observation, holder, selected):
    with pytest.raises(ValueError):
        validate_unload(holder, selected, observation)


def test_unload_requires_both_holder_membership_and_occupant_link(observation):
    assert validate_unload(20, [11, 11], observation) == [11]
    observation["entities"][1]["properties"]["garrisoned_in"]["holder_id"] = 99
    with pytest.raises(ValueError):
        validate_unload(20, [11], observation)
    observation["entities"][-1]["owner"] = 1
    with pytest.raises(ValueError):
        validate_unload(20, None, observation)


@pytest.mark.parametrize("building,queue", [(True, 7), (20, True), (20, -1), (20, 2**32),
                                           (20, 1.5), (20, 999), (999, 7)])
def test_queue_cancellation_requires_current_own_batch(observation, building, queue):
    with pytest.raises(ValueError):
        validate_production_cancel(building, queue, observation)


def test_later_invalid_actions_do_not_write(game):
    session, writes = game
    with pytest.raises(ValueError):
        session.set_unit_packing([10, 11], True)
    with pytest.raises(ValueError):
        session.set_unit_packing([10], True, queued=1)
    with pytest.raises(ValueError):
        session.unload_garrison(20, [11, 12])
    with pytest.raises(ValueError):
        session.cancel_production(20, 999)
    assert writes == []


def test_turret_exit_accepts_own_non_movable_capable_units_only(game):
    session, writes = game
    assert session.leave_turret([11])["accepted"]
    assert writes == [("leave_turret", {"entities": [11]})]
    for selected in [[10], [12], [11, 12], [True]]:
        with pytest.raises(ValueError):
            session.leave_turret(selected)
    assert len(writes) == 1
