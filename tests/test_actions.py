"""Command batches are bounded, all-or-nothing and never accept foreign selectors."""

from copy import deepcopy

import pytest

from pyrogenesis_mcp.actions import STANCES, validate_orders, validate_owned_selection
from pyrogenesis_mcp.engine import GameSession


def observation():
    return {"player": 1, "mapSize": 256, "entities": [
        {"id": 10, "owner": 1, "movable": True,
         "properties": {"identity": {"classes": ["Unit", "Worker"]}}},
        {"id": 11, "owner": 1, "movable": False,
         "properties": {"identity": {"classes": ["Structure"]}}},
    ]}


def test_multi_target_and_build_schema_copies_only_explicit_fields():
    raw = [{"type": "gather", "target": 20},
           {"type": "repair", "target": 11},
           {"type": "build", "template": "structures/athen/house", "x": 100, "z": 100}]
    safe = validate_orders(raw, 256)
    assert safe[:2] == raw[:2]
    assert safe[2] == raw[2] | {"angle": 0}
    safe[0]["target"] = 999
    assert raw[0]["target"] == 20


@pytest.mark.parametrize("kind", ["heal", "guard", "collect_treasure", "return_resource", "trade", "occupy_turret"])
def test_support_orders_keep_explicit_target_only(kind):
    assert validate_orders([{"type": kind, "target": 20}], 256) == [{"type": kind, "target": 20}]


@pytest.mark.parametrize("kind", ["patrol", "attack_move"])
def test_position_orders_require_bounded_coordinates_and_no_target(kind):
    assert validate_orders([{"type": kind, "x": 10, "z": 20}], 256) == [{"type": kind, "x": 10, "z": 20}]
    for order in [{"type": kind, "x": True, "z": 1}, {"type": kind, "x": 257, "z": 1},
                  {"type": kind, "x": 1, "z": 2, "target": 20}]:
        with pytest.raises(ValueError):
            validate_orders([order], 256)


@pytest.mark.parametrize("orders", [None, {}, [], [{}] * 101,
    [{"type": "cheat", "target": 20}],
    [{"type": "attack", "target": True}],
    [{"type": "attack", "target": 2**31}],
    [{"type": "attack", "target": 0}],
    [{"type": "attack", "target": 20, "player": 2}],
    [{"type": "build", "template": "structures/athen/house", "x": 257, "z": 1}],
    [{"type": "build", "template": "structures/athen/house", "x": True, "z": 1}],
    [{"type": "build", "template": "structures/athen/house", "x": 1, "z": 1, "angle": float("nan")}],
    [{"type": "build", "template": "units/athen/hero", "x": 1, "z": 1}],
    [{"type": "build", "template": "structures/../units/hero", "x": 1, "z": 1}],
    [{"type": "build", "template": "structures/x", "x": 1, "z": 1, "script": "bad"}],
])
def test_invalid_orders_rejected_before_the_bridge(orders):
    with pytest.raises(ValueError):
        validate_orders(orders, 256)


def test_selector_class_gates_deletion_and_deduplicates():
    assert validate_owned_selection([11, 11], observation(), entity_class="Structure") == [11]
    assert validate_owned_selection([10], observation(), entity_class="Unit") == [10]
    for selected, kind in [([10], "Structure"), ([11], "Unit"), ([999], "Unit"), ([True], "Unit")]:
        with pytest.raises(ValueError):
            validate_owned_selection(selected, observation(), entity_class=kind)


@pytest.fixture
def game(monkeypatch):
    session = GameSession()
    writes = []
    monkeypatch.setattr(session, "observe", observation)
    monkeypatch.setattr(session, "_write_command", lambda action, **fields: writes.append((action, fields)) or "ack")
    monkeypatch.setattr(session, "_action_ack", lambda command_id, action, count: {
        "accepted": True, "action": action, "entityCount": count})
    return session, writes


def test_game_issues_target_batch_once_with_ordered_tasks(game):
    session, writes = game
    orders = [{"type": "gather", "target": 20}, {"type": "gather", "target": 21}]
    assert session.order_units([10, 10], orders, queued=True) == {
        "accepted": True, "action": "orders", "entityCount": 1, "orderCount": 2}
    assert writes == [("orders", {"entities": [10], "orders": orders, "queued": True})]


def test_bad_later_order_cannot_partially_submit(game):
    session, writes = game
    with pytest.raises(ValueError):
        session.order_units([10], [{"type": "attack", "target": 20}, {"type": "cheat", "target": 21}])
    assert writes == []


@pytest.mark.parametrize("stance", sorted(STANCES))
def test_each_native_stance_is_explicitly_allowlisted(game, stance):
    session, writes = game
    assert session.set_stance([10], stance)["accepted"]
    assert writes == [("stance", {"entities": [10], "stance": stance})]


@pytest.mark.parametrize("stance", [None, "attack", "none", "passive-defensive", True, ["aggressive"]])
def test_unknown_stances_never_write(game, stance):
    session, writes = game
    with pytest.raises(ValueError):
        session.set_stance([10], stance)
    assert writes == []


def test_clear_and_delete_have_distinct_actions_and_selector_classes(game):
    session, writes = game
    session.abandon_targets([10])
    session.delete_owned([10])
    session.delete_owned([11], buildings=True)
    assert [kind for kind, _ in writes] == ["abandon_target", "destroy_own_units", "destroy_own_buildings"]
    before = deepcopy(writes)
    with pytest.raises(ValueError):
        session.delete_owned([10], buildings=True)
    with pytest.raises(ValueError):
        session.delete_owned([11])
    assert writes == before


def test_abandon_and_stop_keep_distinct_bridge_actions(game):
    session, writes = game
    session.abandon_targets([10])
    session.act("stop", [10])
    assert writes == [("abandon_target", {"entities": [10]}), ("stop", {"entities": [10]})]
