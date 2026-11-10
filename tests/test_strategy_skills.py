"""Portable strategy planners must preserve scope and emit bounded public calls."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_planner(skill, filename):
    path = ROOT / "skills" / skill / "scripts" / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


training = load_planner("0ad-train-near-civic", "plan_training.py")
rally = load_planner("0ad-rally-idle", "plan_idle_rally.py")
TEMPLATE = "units/pers/infantry_archer_b"


def observation(entities):
    return {
        "policy": "own-player-only-v1", "player": 2, "time": 755200,
        "mapSize": 1536, "entities": entities,
        "resources": {key: 50000 for key in ("food", "wood", "stone", "metal")},
        "scoreboard": {"population": {"count": 80, "limit": 500, "max": 5500}},
    }


def producer(eid, x, queue=None, centre=False, owner=2):
    return {
        "id": eid, "owner": owner, "position": {"x": x, "z": 100},
        "properties": {
            "identity": {"classes": ["Structure", "CivCentre" if centre else "Barracks"]},
            "building": {
                "foundation": None, "training": {"entities": [TEMPLATE]},
                "production": {"autoqueue": False, "queue": queue or []},
            },
        },
    }


def queued(milliseconds, **extra):
    return {"time_remaining": milliseconds, "paused": False, **extra}


def training_request(entities, **kwargs):
    return {"observation": observation(entities), "request": {
        "civic_centre_id": 10, "template": TEMPLATE, "count": 250,
        "batch_size": 100, **kwargs,
    }}


def unit(eid, classes=None, owner=2):
    return {
        "id": eid, "owner": owner, "movable": True, "position": {"x": 100, "z": 100},
        "properties": {
            "identity": {"classes": ["Unit", *(classes or [])], "special_roles": []},
            "capabilities": {"move": True, "gather": True},
            "activity": {"state": "INDIVIDUAL.IDLE", "idle": True},
            "current_target": None, "garrisoned_in": {"holder_id": None}, "packing": None,
        },
    }


def test_training_prefers_empty_nearby_building_but_keeps_civic_centre_eligible():
    payload = training_request([
        producer(10, 100, [queued(60000)], centre=True),
        producer(11, 110, [queued(120000)]), producer(12, 140),
        producer(20, 400, centre=True), producer(21, 390), producer(30, 101, owner=1),
    ])
    before = deepcopy(payload)
    plan = training.plan(payload)
    assert plan["actions"][0]["arguments"]["building_id"] == 12
    assert {item["id"] for item in plan["candidates"]} == {10, 11, 12}
    assert 10 in {item["arguments"]["building_id"] for item in plan["actions"]}
    assert plan["planned_count"] == 250 and plan["unplanned_count"] == 0
    assert sum(action["arguments"]["count"] for action in plan["actions"]) == 250
    assert all(action["tool"] == "train_units" and 1 <= action["arguments"]["count"] <= 100
               for action in plan["actions"])
    assert payload == before


def test_training_uses_already_reserved_population_once():
    payload = training_request([producer(10, 100, [queued(60000, count=80)], centre=True)],
        count=50, unit_cost={"food": 50, "wood": 50, "stone": 0, "metal": 0,
                            "population": 2, "time": 10})
    payload["observation"]["scoreboard"]["population"]["limit"] = 100
    plan = training.plan(payload)
    assert plan["planned_count"] == 10 and plan["unplanned_count"] == 40
    assert plan["cost_preflight_missing"] is False


def test_training_does_not_claim_complete_population_budget_for_pending_batches():
    payload = training_request([producer(10, 100, [
        queued(1000, unit_template=TEMPLATE, count=5, progress=0.5),
        queued(10000, unit_template=TEMPLATE, count=10, progress=0),
    ], centre=True)], count=10,
        unit_cost={"food": 50, "wood": 50, "stone": 0, "metal": 0,
                   "population": 1, "time": 10})
    payload["observation"]["scoreboard"]["population"].update(count=90, limit=100)
    plan = training.plan(payload)
    assert plan["population_reservation_uncertain"] is True
    assert plan["budget_status"] == "needs_queue_reservation_review"


def test_training_population_uncertainty_includes_production_outside_selected_base():
    payload = training_request([producer(10, 100, centre=True), producer(20, 400, [
        queued(10000, unit_template="units/pers/cavalry_javelineer_b", count=10),
    ], centre=True)], count=5)
    plan = training.plan(payload)
    assert plan["population_reservation_uncertain"] is True
    assert {candidate["id"] for candidate in plan["candidates"]} == {10}


def test_training_resource_budget_stops_without_overtraining():
    payload = training_request([producer(10, 100, centre=True)], count=50,
        unit_cost={"food": 50, "wood": 50, "stone": 0, "metal": 0,
                   "population": 1, "time": 10})
    payload["observation"]["resources"]["wood"] = 125
    plan = training.plan(payload)
    assert plan["planned_count"] == 2 and plan["unplanned_count"] == 48


def test_training_skips_paused_autoqueue_unknown_or_potentially_truncated_producers():
    entities = [producer(10, 100, centre=True), producer(11, 110),
                producer(12, 120, [queued(1000, paused=True)]),
                producer(13, 130, [queued(1000)] * 16), producer(14, 140)]
    entities[0]["properties"]["building"]["training"]["entities"] = []
    entities[1]["properties"]["building"]["production"]["autoqueue"] = True
    entities[4]["properties"]["building"]["production"] = None
    plan = training.plan(training_request(entities, count=5))
    assert plan["actions"] == [] and plan["unplanned_count"] == 5
    assert {entry["id"] for entry in plan["excluded"]} == {11, 12, 13, 14}


def test_training_obeys_base_boundary_even_when_search_radius_is_widened():
    payload = training_request([producer(10, 100, centre=True),
                                producer(20, 400, centre=True), producer(21, 380)], radius=1000)
    payload["observation"]["entities"][0]["properties"]["building"]["training"]["entities"] = []
    plan = training.plan(payload)
    assert plan["actions"] == [] and plan["unplanned_count"] == 250


@pytest.mark.parametrize("count", [0, -1, 1.5, True, 10001])
def test_training_rejects_invalid_additional_count(count):
    with pytest.raises(ValueError):
        training.plan(training_request([producer(10, 100, centre=True)], count=count))


def test_rally_categories_do_not_confuse_citizens_civilians_or_champions():
    obs = observation([unit(1, ["CitizenSoldier", "Infantry"]),
                       unit(2, ["Civilian", "Worker"]),
                       unit(3, ["Cavalry", "Champion"]),
                       unit(4, ["Support", "Worker", "Elephant"])])
    expected = {"all": {1, 2, 3, 4}, "citizen": {1}, "civilian": {2},
                "cavalry": {3}, "champion": {3}}
    for category, ids in expected.items():
        plan = rally.plan({"observation": obs, "request": {"x": 600, "z": 500, "category": category}})
        assert {entry["id"] for entry in plan["selected"]} == ids


def test_rally_preserves_work_guarding_garrison_packing_and_unknown_states():
    entities = [unit(eid, ["CitizenSoldier"]) for eid in range(1, 11)]
    entities[1]["properties"]["activity"] = {"state": "INDIVIDUAL.GATHER.GATHERING", "idle": False}
    entities[2]["properties"]["current_target"] = {"order": "Guard", "entity_id": 500}
    entities[3]["properties"]["garrisoned_in"]["holder_id"] = 500
    entities[4]["properties"]["packing"] = {"changing": True}
    entities[5]["owner"] = 1
    del entities[6]["properties"]["current_target"]
    entities[7]["properties"]["activity"]["state"] = "INDIVIDUAL.GUARD.GUARDING"
    entities[8]["movable"] = False
    entities[9]["position"] = None
    payload = {"observation": observation(entities), "request": {"x": 600, "z": 500}}
    before = deepcopy(payload)
    plan = rally.plan(payload)
    assert plan["selected_count"] == 1 and plan["actions"][0]["arguments"]["entity_ids"] == [1]
    assert payload == before


def test_rally_includes_explicitly_non_garrisonable_idle_units_but_not_unknown_holders():
    normal, non_garrisonable, unknown = unit(1), unit(2, ["Siege"]), unit(3)
    non_garrisonable["properties"]["garrisoned_in"] = None
    non_garrisonable["properties"]["capabilities"]["garrison"] = False
    unknown["properties"]["garrisoned_in"] = None
    plan = rally.plan({"observation": observation([normal, non_garrisonable, unknown]),
                       "request": {"x": 500, "z": 500}})
    assert {entry["id"] for entry in plan["selected"]} == {1, 2}


def test_rally_batches_more_than_two_hundred_without_duplicates():
    obs = observation([unit(eid, ["Cavalry"]) for eid in range(1, 407)])
    plan = rally.plan({"observation": obs, "request": {
        "x": 600, "z": 500, "category": "cavalry", "exclude_ids": [1],
        "source": {"x": 100, "z": 100, "radius": 10}}})
    batches = [action["arguments"]["entity_ids"] for action in plan["actions"]]
    assert list(map(len, batches)) == [200, 200, 5]
    assert {eid for batch in batches for eid in batch} == set(range(2, 407))
    assert all(action["tool"] == "move_units" and action["arguments"]["queued"] is False
               for action in plan["actions"])


@pytest.mark.parametrize("parameters", [{"x": float("nan"), "z": 100}, {"x": 1537, "z": 100},
                                     {"x": 100, "z": 100, "category": "unknown"}])
def test_rally_rejects_invalid_destinations_and_categories(parameters):
    with pytest.raises(ValueError):
        rally.plan({"observation": observation([unit(1)]), "request": parameters})


@pytest.mark.parametrize("module,parameters", [(training, {"civic_centre_id": 10, "template": TEMPLATE, "count": 5}),
                                          (rally, {"x": 500, "z": 500})])
def test_planners_reject_offline_or_unlabelled_observations(module, parameters):
    obs = observation([producer(10, 100, centre=True)])
    obs["policy"] = "offline-save-omniscient"
    with pytest.raises(ValueError):
        module.plan({"observation": obs, "request": parameters})


def test_portable_planner_cli_accepts_stdin_from_an_unrelated_directory(tmp_path):
    payload = {"observation": observation([unit(1, ["Cavalry"])]),
               "request": {"x": 500, "z": 500, "category": "cavalry"}}
    process = subprocess.run([sys.executable, rally.__file__, "-"], input=json.dumps(payload),
                             text=True, capture_output=True, cwd=tmp_path, timeout=10)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["selected_count"] == 1
