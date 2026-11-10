"""Adversarial observations must not disclose or authorize foreign state."""

from copy import deepcopy
import math

import pytest

from pyrogenesis_mcp.privacy import (
    ObservationError,
    number,
    project_observation,
    validate_entities,
)


def own_unit(**updates):
    unit = {
        "id": 10,
        "owner": 1,
        "movable": True,
        "template": "units/athen/infantry_spearman_b",
        "position": {"x": 100, "z": 150},
        "hitpoints": 100,
        "maxHitpoints": 100,
    }
    unit.update(updates)
    return unit


def observation(**updates):
    raw = {
        "player": 1,
        "time": 1250,
        "resources": {"food": 300, "wood": 200, "stone": 100, "metal": 50},
        "population": 8,
        "populationLimit": 20,
        "entities": [own_unit()],
    }
    raw.update(updates)
    return raw


def test_projection_reconstructs_only_owned_ui_fields_without_mutating_input():
    raw = observation(
        players=[{"id": 2, "resources": {"food": 91919191}}],
        commands=[{"player": 2, "hidden_target": 98989898}],
        state={"all_entities": ["SECRET"]},
        error="SECRET_STACK_TRACE",
        templates={"SECRET_ENEMY_TEMPLATE": "SECRET"},
    )
    raw["resources"]["enemy_food"] = 91919191
    raw["resources"]["debug"] = {"SECRET": True}
    raw["entities"][0].update(
        queued_commands=["SECRET_TARGET"],
        enemy_resources={"food": 91919191},
        visibility="SECRET",
    )
    raw["entities"][0]["position"]["enemy_position"] = "SECRET"
    raw["entities"].extend([
        own_unit(id=20, owner=2, template="SECRET_ENEMY", hitpoints=91919191),
        own_unit(id=30, owner=0, template="SECRET_UNEXPLORED_GAIA"),
        {"owner": 2, "id": "SECRET_ID", "hitpoints": float("nan")},
        "SECRET_INVALID_ENTITY",
    ])
    original = deepcopy(raw)
    safe = project_observation(raw)
    assert safe == observation()
    assert raw == original
    # Mutating the returned graph must not reach the bridge's raw graph.
    safe["resources"]["food"] = 0
    safe["entities"][0]["position"]["x"] = 0
    assert raw["resources"]["food"] == 300
    assert raw["entities"][0]["position"]["x"] == 100


@pytest.mark.parametrize("player", [-1, 0, 9, True, False, 1.0, "1", None])
def test_observers_and_invalid_player_ids_are_rejected(player):
    with pytest.raises(ObservationError, match="playing participant"):
        project_observation(observation(player=player))


@pytest.mark.parametrize("owner", [True, False, 1.0, "1", None, -1, 0, 2, 8])
def test_foreign_or_ambiguous_entity_ownership_never_grants_control(owner):
    safe = project_observation(observation(entities=[own_unit(owner=owner)]))
    assert safe["entities"] == []
    with pytest.raises(ValueError, match="observed movable"):
        validate_entities([10], safe)


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), -float("inf"), True, "5", None, 10**1000],
    ids=["nan", "positive-infinity", "negative-infinity", "bool", "string", "null", "huge-integer"],
)
@pytest.mark.parametrize("field", ["time", "food", "population", "x", "z", "hitpoints", "maxHitpoints"])
def test_invalid_numbers_cannot_escape_projection(field, bad):
    raw = observation()
    if field == "food":
        raw["resources"][field] = bad
    elif field in ("x", "z"):
        raw["entities"][0]["position"][field] = bad
    elif field in ("hitpoints", "maxHitpoints"):
        raw["entities"][0][field] = bad
    else:
        raw[field] = bad
    with pytest.raises(ObservationError):
        project_observation(raw)


@pytest.mark.parametrize("entity_id", [True, 0, -1, 2**32, "10", 10.0, None])
def test_invalid_owned_entity_identity_fails_closed(entity_id):
    with pytest.raises(ObservationError, match="entity identity"):
        project_observation(observation(entities=[own_unit(id=entity_id)]))


def test_duplicate_owned_ids_fail_closed():
    with pytest.raises(ObservationError, match="entity identity"):
        project_observation(observation(entities=[own_unit(), own_unit()]))


def test_movement_requires_current_owned_movable_entities():
    safe = project_observation(observation(entities=[
        own_unit(), own_unit(id=11), own_unit(id=12, movable=False),
        own_unit(id=20, owner=2),
    ]))
    assert validate_entities([11, 10, 11], safe) == [11, 10]
    for selected in ([20], [12], [999], [True], [10.0], ["10"], [10, 20], [], [10] * 201):
        with pytest.raises(ValueError):
            validate_entities(selected, safe)
    # Removal from the latest frame revokes a previously valid selection.
    with pytest.raises(ValueError):
        validate_entities([10], project_observation(observation(entities=[])))


@pytest.mark.parametrize("raw", [None, [], "SECRET", {"player": 1, "resources": []}, {"player": 1, "entities": {}}])
def test_malformed_observation_fails_closed(raw):
    with pytest.raises(ObservationError):
        project_observation(raw)


def test_projection_limits_entity_count_and_untrusted_strings():
    with pytest.raises(ObservationError, match="entity list"):
        project_observation(observation(entities=[None] * 20001))
    safe = project_observation(observation(entities=[own_unit(template="u" * 1000)]))
    assert safe["entities"][0]["template"] == "u" * 200


def test_number_bounds_are_inclusive_and_results_are_finite():
    for value in (0, 1.25, 10**12):
        assert number(value) == value
        assert math.isfinite(number(value))
    for value in (-1, 10**12 + 1):
        with pytest.raises(ObservationError):
            number(value)
