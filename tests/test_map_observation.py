"""The map response is bounded and cannot carry arbitrary simulation fields."""

from copy import deepcopy

import pytest

from pyrogenesis_mcp.map_observation import MAP_POLICY, project_map_region, validate_region
from pyrogenesis_mcp.privacy import ObservationError


def request(**updates):
    result = {"x_min": 0, "z_min": 0, "x_max": 10, "z_max": 9, "cell_size": 8}
    result.update(updates)
    return result


def frame(**updates):
    raw = {
        "policy": MAP_POLICY, "player": 1, "time": 1000,
        "region": request(), "columns": 2, "rows": 2,
        "cells": [
            {"x": x, "z": z, "in_bounds": True, "explored": True,
             "currently_visible": True,
             "terrain": {"height": -2, "water_level": 1, "last_seen": 1000}}
            for z in (4, 8.5) for x in (4, 9)
        ],
        "objects": [], "truncated": False,
    }
    raw.update(updates)
    return raw


def static_object(**updates):
    result = {
        "id": 11, "owner": 0, "kind": "tree", "template": "gaia/tree/oak",
        "position": {"x": 2, "z": 3}, "currently_visible": True,
        "last_seen": 1000, "static": True,
        "resource": {"type": "wood", "amount": 200, "max": 200},
        "classes": ["Tree", "Resource"], "hitpoints": 100,
    }
    result.update(updates)
    return result


def project(raw=None, **kwargs):
    return project_map_region(frame() if raw is None else raw, request(), 1, 512, **kwargs)


def test_grid_uses_centers_and_clips_final_cell():
    result = project()
    assert [(cell["x"], cell["z"]) for cell in result["cells"]] == [
        (4, 4), (9, 4), (4, 8.5), (9, 8.5),
    ]
    assert (result["columns"], result["rows"]) == (2, 2)
    assert result["cells"][0]["terrain"]["height"] == -2


def test_projection_allowlists_every_level_and_copies_input():
    raw = frame(objects=[static_object()])
    raw.update(seed="SECRET", players=[{"resources": "SECRET"}], error="SECRET")
    raw["region"]["x_min"] = 0
    raw["cells"][0]["raw_map"] = "SECRET"
    raw["cells"][0]["terrain"]["texture_private"] = "SECRET"
    raw["objects"][0].update(orders=["SECRET"], queue="SECRET", enemy_economy="SECRET")
    raw["objects"][0]["position"]["destination"] = "SECRET"
    raw["objects"][0]["resource"]["gatherers"] = "SECRET"
    before = deepcopy(raw)
    result = project(raw)
    assert "SECRET" not in str(result)
    assert raw == before
    result["region"]["x_min"] = 5
    result["objects"][0]["position"]["x"] = 5
    result["objects"][0]["classes"].append("Changed")
    result["cells"][0]["terrain"]["height"] = 5
    assert raw == before


def test_unknown_and_off_map_samples_cannot_leak_injected_terrain():
    raw = frame()
    raw["cells"][0].update(explored=False, currently_visible=False, terrain={"SECRET": float("nan")})
    raw["cells"][1].update(in_bounds=False, explored=False, currently_visible=False, terrain="SECRET")
    result = project(raw)
    assert result["cells"][0]["terrain"] is None
    assert result["cells"][1]["terrain"] is None
    assert "SECRET" not in str(result)


def test_explored_fog_allows_only_historical_geometry_and_static_objects():
    raw = frame(objects=[static_object(currently_visible=False, last_seen=800),
                         {"currently_visible": False, "static": False, "SECRET": True}])
    raw["cells"][0].update(currently_visible=False,
                            terrain={"height": -2, "water_level": 1, "last_seen": 800})
    raw["cells"][1].update(currently_visible=False, terrain=None)
    result = project(raw)
    assert result["cells"][0]["terrain"]["last_seen"] == 800
    assert result["cells"][1]["terrain"] is None
    assert len(result["objects"]) == 1
    assert result["objects"][0]["last_seen"] == 800
    assert result["objects"][0]["currently_visible"] is False


def test_fog_unit_is_dropped_even_if_mislabeled_static():
    assert project(frame(objects=[static_object(kind="unit", currently_visible=False)]))["objects"] == []


@pytest.mark.parametrize("updates", [
    {"x_min": -1}, {"x_max": 513}, {"x_min": 10}, {"x_max": 0},
    {"z_max": 0}, {"z_min": 10}, {"cell_size": 3.99}, {"cell_size": 257},
    {"x_min": True}, {"cell_size": True}, {"z_max": float("nan")},
    {"x_max": float("inf")}, {"x_max": "10"}, {"z_min": None},
])
def test_bad_region_requests_are_rejected(updates):
    with pytest.raises(ObservationError):
        validate_region(**request(**updates), map_size=512)


def test_sample_cap_is_exact_and_applies_before_allocating_grid():
    assert validate_region(0, 0, 256, 256, 4, 100000)["cell_size"] == 4
    with pytest.raises(ObservationError, match="sample limit"):
        validate_region(0, 0, 257, 256, 4, 100000)
    with pytest.raises(ObservationError, match="sample limit"):
        validate_region(0, 0, 100000, 100000, 4, 100000)


@pytest.mark.parametrize("updates", [
    {"policy": "raw-world"}, {"player": 2}, {"player": True}, {"player": 1.0},
    {"time": -1}, {"time": float("nan")}, {"columns": True}, {"columns": 3},
    {"rows": 1}, {"cells": []}, {"objects": {}}, {"truncated": 1},
    {"region": request(x_max=11)}, {"objects": [None] * 2001},
])
def test_invalid_result_envelope_fails_closed(updates):
    with pytest.raises(ObservationError):
        project(frame(**updates))


@pytest.mark.parametrize("updates", [
    {"x": 4.01}, {"z": True}, {"in_bounds": 1}, {"explored": "true"},
    {"currently_visible": 0}, {"explored": False}, {"in_bounds": False},
    {"terrain": {}}, {"terrain": []},
    {"terrain": {"height": True, "water_level": 1, "last_seen": 1000}},
    {"terrain": {"height": 1, "water_level": float("nan"), "last_seen": 1000}},
    {"terrain": {"height": 1, "water_level": 1}},
    {"terrain": {"height": 1, "water_level": 1, "last_seen": 1001}},
    {"terrain": {"height": 1, "water_level": 1, "last_seen": 999}},
])
def test_invalid_or_contradictory_cells_fail_closed(updates):
    raw = frame()
    raw["cells"][0].update(updates)
    with pytest.raises(ObservationError):
        project(raw)


@pytest.mark.parametrize("updates", [
    {"id": True}, {"id": 0}, {"id": 2**32}, {"owner": True}, {"owner": 9},
    {"kind": "raw"}, {"kind": []}, {"template": "x" * 201}, {"template": None},
    {"position": {"x": 11, "z": 3}}, {"position": {"x": 10, "z": 3}},
    {"position": {"x": 2, "z": 9}}, {"currently_visible": 1}, {"static": "true"},
    {"last_seen": 1001}, {"last_seen": 999}, {"last_seen": True},
    {"hitpoints": float("nan")}, {"classes": "SECRET"}, {"classes": [1]},
    {"resource": {"type": [], "amount": 1, "max": 2}},
    {"resource": {"type": "SECRET", "amount": 1, "max": 2}},
    {"resource": {"type": "wood", "amount": float("inf"), "max": 2}},
])
def test_invalid_visible_objects_fail_closed(updates):
    with pytest.raises(ObservationError):
        project(frame(objects=[static_object(**updates)]))


def test_duplicate_identity_and_wrong_grid_order_fail_closed():
    with pytest.raises(ObservationError, match="Duplicate"):
        project(frame(objects=[static_object(), static_object()]))
    raw = frame()
    raw["cells"][0], raw["cells"][1] = raw["cells"][1], raw["cells"][0]
    with pytest.raises(ObservationError, match="grid"):
        project(raw)


@pytest.mark.parametrize("resource_type", ["wood", "wood.tree", "food.meat", "stone.rock",
                                          "metal.ore_1", "food.fruit-red"])
def test_resource_generic_and_subtypes_are_projected(resource_type):
    raw = frame(objects=[static_object(resource={"type": resource_type, "amount": 10, "max": 20})])
    assert project(raw)["objects"][0]["resource"] == {"type": resource_type, "amount": 10, "max": 20}


@pytest.mark.parametrize("resource_type", ["SECRET.tree", "wood.", ".tree", "wood.tree.oak",
                                          "wood.bad/name", "wood.space here", "wood.树", "wood." + "a" * 81])
def test_resource_subtypes_are_bounded_identifiers(resource_type):
    raw = frame(objects=[static_object(resource={"type": resource_type, "amount": 10, "max": 20})])
    with pytest.raises(ObservationError, match="resource"):
        project(raw)


@pytest.mark.parametrize("omit", [False, True])
def test_unknown_water_level_and_resource_max_are_null_not_nonfinite(omit):
    raw = frame(objects=[static_object(resource={"type": "wood.tree", "amount": 10, "max": None})])
    raw["cells"][0]["terrain"]["water_level"] = None
    if omit:
        del raw["objects"][0]["resource"]["max"]
        del raw["cells"][0]["terrain"]["water_level"]
    result = project(raw)
    assert result["cells"][0]["terrain"]["water_level"] is None
    assert result["objects"][0]["resource"]["max"] is None


@pytest.mark.parametrize("field", ["water_level", "max"])
def test_infinite_optional_numbers_still_fail_closed(field):
    raw = frame(objects=[static_object()])
    if field == "water_level":
        raw["cells"][0]["terrain"][field] = float("inf")
    else:
        raw["objects"][0]["resource"][field] = float("inf")
    with pytest.raises(ObservationError):
        project(raw)


@pytest.mark.parametrize("player", [-1, 0, 9, True, 1.0, "1"])
def test_expected_player_must_be_ordinary_participant(player):
    with pytest.raises(ObservationError):
        project_map_region(frame(), request(), player, 512)


def test_terrain_only_projection_rejects_object_payloads():
    with pytest.raises(ObservationError, match="Terrain-only"):
        project_map_region(frame(objects=[static_object()]), request(), 1, 512,
                           include_objects=False)
