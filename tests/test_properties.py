"""Properties preserve useful game mechanics without disclosing private maps."""

from copy import deepcopy

import pytest

from pyrogenesis_mcp.map_observation import project_map_region, validate_region
from pyrogenesis_mcp.privacy import ObservationError, project_observation
from pyrogenesis_mcp.properties import CAPABILITIES, project_properties


def properties(**updates):
    value = {
        "version": 1,
        "identity": {"civ": "athen", "classes": ["Unit", "Worker", "Infantry"],
                     "rank": "Basic", "special_roles": []},
        "capabilities": {key: key in {"move", "build", "repair", "gather", "farm", "attack"}
                         for key in CAPABILITIES},
        "gathering": {"rates": {"food.grain": 0.5, "wood.tree": 0.75},
                      "capacities": {"food": 10, "wood": 20}},
        "construction": {"templates": ["structures/athen/field", "structures/athen/house"], "rate": 1.25},
        "storage": None,
        "inventory": {"resources": [{"type": "food", "amount": 7, "capacity": 10}],
                      "trade_goods": None},
        "current_target": {"order": "Gather", "entity_id": 44, "position": None},
        "activity": {"state": "INDIVIDUAL.GATHER.GATHERING", "idle": False, "stance": "aggressive"},
        "combat": {"can_attack": True, "can_capture": False, "attacks": {"Melee": attack()},
                   "resistance": {"damage": {"Hack": 2, "Pierce": 3}, "capture": 5, "status_effects": {}},
                   "healing": None, "auras": []},
        "garrison": None,
        "garrisoned_in": {"holder_id": None, "slot_size": 1},
        "packing": None,
        "building": None,
        "rally_point": None,
    }
    value.update(updates)
    return value


def building(**updates):
    value = {
        "is_building": True,
        "research": {"technologies": ["phase_town"], "pairs": [{"top": "armor_a", "bottom": "armor_b"}],
                     "tech_cost_multiplier": {"food": 1, "wood": 1, "stone": 1, "metal": 1, "time": 0.9}},
        "training": {"entities": ["units/athen/support_female_citizen"]},
        "production": {"queue": [{"id": 1, "unit_template": "units/athen/support_female_citizen",
            "technology_template": "", "count": 3, "progress": 0.25, "time_remaining": 7500,
            "paused": False}], "autoqueue": True},
        "building_ai": {"default_arrows": 3, "max_arrows": 10, "garrison_arrow_multiplier": 0.5,
                        "garrison_arrow_classes": ["Infantry+Ranged"], "arrows": 5},
        "resource": {"infinite": False, "type": "food.grain", "amount": 200, "max": 250,
                     "max_gatherers": 5, "gatherers": 2},
        "loyalty": {"current": [0, 400, 100], "max": 500},
        "placement": {"placement": "land", "territory": ["own"], "category": "Structure",
                      "distance": {"from_class": "CivilCentre", "min": 0, "max": 120}},
        "owner": 1, "hitpoints": 850, "max_hitpoints": 1000,
        "foundation": {"builders": 2, "time_remaining_seconds": 10,
                       "time_remaining_with_one_more_worker_seconds": 8},
        "repair": None,
    }
    value.update(updates)
    return value


def attack(**updates):
    value = {"damage": {"Hack": 8, "Pierce": 2}, "capture": 0,
             "bonuses": [{"name": "BonusCavMelee", "classes": ["Cavalry"], "civs": [], "multiplier": 3}],
             "min_range": 0, "max_range": 4, "prepare_ms": 300, "repeat_ms": 1000,
             "restricted_classes": ["Structure+Defensive"], "preferred_classes": ["Cavalry"], "splash": None,
             "status_effects": []}
    value.update(updates)
    return value


def test_worker_private_projection_keeps_live_capabilities_inventory_and_target():
    raw = properties()
    safe = project_properties(raw, private=True)
    assert safe == raw
    assert safe["capabilities"]["farm"]
    assert safe["gathering"]["capacities"]["wood"] == 20
    assert safe["inventory"]["resources"] == [{"type": "food", "amount": 7, "capacity": 10}]
    assert safe["current_target"]["entity_id"] == 44
    assert safe["combat"]["attacks"]["Melee"]["bonuses"][0]["multiplier"] == 3


@pytest.mark.parametrize("private", [False, True])
def test_native_identity_labels_are_public_bounded_and_allowlisted(private):
    raw = properties()
    raw["identity"].update(
        name={"generic": "Imperial Minister", "specific": "钦差大臣", "hidden": "SECRET"},
        tooltip="Improves nearby production.", translation="source-language", hidden="SECRET")
    safe = project_properties(raw, private=private)
    assert safe["identity"]["name"] == {"generic": "Imperial Minister", "specific": "钦差大臣"}
    assert safe["identity"]["tooltip"] == "Improves nearby production."
    assert safe["identity"]["translation"] == "source-language"
    assert "SECRET" not in str(safe)
    safe["identity"]["name"]["generic"] = "Changed"
    assert raw["identity"]["name"]["generic"] == "Imperial Minister"


@pytest.mark.parametrize("labels", [
    {"name": None}, {"name": {"generic": "Worker"}},
    {"name": {"generic": "x" * 301, "specific": "Worker"}},
    {"name": {"generic": "Worker", "specific": False}},
    {"tooltip": "x" * 4001}, {"tooltip": None},
    {"translation": "guaranteed-Chinese"},
])
def test_invalid_native_identity_labels_fail_closed(labels):
    raw = properties()
    raw["identity"].update(labels)
    with pytest.raises(ObservationError):
        project_properties(raw)


def test_every_level_is_allowlisted_and_returned_graph_is_independent():
    raw = properties()
    raw["debug"] = "SECRET"
    for record in (raw["identity"], raw["capabilities"], raw["gathering"], raw["construction"],
                   raw["inventory"], raw["inventory"]["resources"][0], raw["current_target"],
                   raw["activity"], raw["combat"], raw["combat"]["resistance"],
                   raw["combat"]["attacks"]["Melee"], raw["combat"]["attacks"]["Melee"]["bonuses"][0],
                   raw["garrisoned_in"]):
        record["hidden_order_queue"] = "SECRET"
    raw["current_target"]["position"] = {"x": 20, "z": 30, "target_live_position": "SECRET"}
    before = deepcopy(raw)
    safe = project_properties(raw, private=True)
    assert "SECRET" not in str(safe)
    safe["identity"]["classes"].append("Changed")
    safe["gathering"]["rates"]["food.grain"] = 999
    safe["inventory"]["resources"][0]["amount"] = 999
    safe["combat"]["attacks"]["Melee"]["bonuses"][0]["classes"].append("Changed")
    assert raw == before


def test_public_map_discards_even_malformed_private_payloads_without_inspection():
    raw = properties(inventory="SECRET", construction={"SECRET": float("nan")}, current_target={"SECRET": float("nan")},
                     activity=["SECRET"], garrisoned_in=True,
                     garrison={"capacity": 10, "allowed_classes": ["Infantry+Melee"],
                               "occupied_slots": "SECRET", "occupants": {"SECRET": True}})
    safe = project_properties(raw)
    assert all(safe[key] is None for key in ("inventory", "construction", "current_target", "activity", "garrisoned_in"))
    assert safe["garrison"] == {"capacity": 10, "allowed_classes": ["Infantry+Melee"],
                                "occupied_slots": None, "occupants": None}
    assert "SECRET" not in str(safe)
    with pytest.raises(ObservationError):
        project_properties(raw, private=True)


def test_healer_has_healing_without_inventing_an_attack():
    raw = properties()
    raw["capabilities"].update(attack=False, heal=True)
    raw["combat"].update(can_attack=False, can_capture=False, attacks={}, healing={"health": 10, "range": 12,
        "interval_ms": 1000, "healable_classes": ["Organic"], "unhealable_classes": ["Siege", "Ship"]})
    safe = project_properties(raw)
    assert safe["combat"]["can_attack"] is False
    assert safe["combat"]["can_capture"] is False
    assert safe["combat"]["attacks"] == {}
    assert safe["combat"]["healing"]["health"] == 10


@pytest.mark.parametrize("role", ["war_dog", "minister", "worker_elephant"])
def test_special_units_use_roles_and_real_mechanics_without_prescribed_combat(role):
    raw = properties()
    raw["identity"]["special_roles"] = [role]
    raw["identity"]["civ"] = {"war_dog": "brit", "minister": "han", "worker_elephant": "maur"}[role]
    if role == "worker_elephant":
        raw["gathering"] = None
        raw["capabilities"].update(gather=False, farm=False, resource_dropsite=True)
        raw["storage"] = {"accepts": ["food", "wood", "stone", "metal"]}
    if role == "minister":
        raw["capabilities"].update(attack=False, capture=True)
        raw["combat"].update(can_attack=False, can_capture=True,
            attacks={"Capture": attack(damage={}, capture=4, bonuses=[])})
        raw["combat"]["auras"] = [{"id": "units/han/minister", "name": "Administration",
            "description": "Nearby workers gather resources faster.", "range": 20}]
    safe = project_properties(raw)
    assert safe["identity"]["special_roles"] == [role]
    assert safe["combat"]["can_attack"] is (role != "minister")
    if role == "minister":
        assert safe["combat"]["can_capture"] is True
        assert safe["capabilities"]["capture"] is True
        assert safe["combat"]["attacks"]["Capture"]["capture"] == 4
    if role == "worker_elephant":
        assert safe["gathering"] is None
        assert safe["storage"]["accepts"] == ["food", "wood", "stone", "metal"]


def test_siege_transport_occupancy_and_packing_transition_are_explicit():
    raw = properties(garrison={"capacity": 20, "allowed_classes": ["Infantry"],
        "occupied_slots": 3, "occupants": [12, 13, 14]},
        packing={"packed": True, "changing": True, "progress": 0.25, "state": "unpacking"})
    assert project_properties(raw, private=True)["garrison"]["occupants"] == [12, 13, 14]
    assert project_properties(raw)["garrison"]["occupants"] is None
    assert project_properties(raw)["packing"] == raw["packing"]


def test_class_based_special_roles_can_overlap_for_a_hero_mercenary():
    raw = properties()
    raw["identity"]["classes"] = ["Unit", "Hero", "Mercenary", "Champion"]
    raw["identity"]["special_roles"] = ["hero", "mercenary", "champion"]
    safe = project_properties(raw)
    assert safe["identity"]["special_roles"] == ["hero", "mercenary", "champion"]
    assert safe["identity"]["classes"] == raw["identity"]["classes"]


def test_trade_cargo_exposes_amounts_without_market_identities():
    raw = properties()
    raw["inventory"]["trade_goods"] = {"type": "metal", "trader_gain": 15, "market1_gain": 2,
        "market2_gain": 2, "market1_owner": "SECRET", "market2": "SECRET"}
    safe = project_properties(raw, private=True)
    assert safe["inventory"]["trade_goods"] == {"type": "metal", "trader_gain": 15,
                                               "market1_gain": 2, "market2_gain": 2}
    assert "SECRET" not in str(safe)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True, "8", None, 10**20])
@pytest.mark.parametrize("field", ["gather_rate", "capacity", "damage", "bonus", "cargo", "x", "heal", "garrison", "aura"])
def test_malformed_nested_numbers_fail_closed(field, bad):
    raw = properties()
    if field == "gather_rate":
        raw["gathering"]["rates"]["food.grain"] = bad
    elif field == "capacity":
        raw["gathering"]["capacities"]["food"] = bad
    elif field == "damage":
        raw["combat"]["attacks"]["Melee"]["damage"]["Hack"] = bad
    elif field == "bonus":
        raw["combat"]["attacks"]["Melee"]["bonuses"][0]["multiplier"] = bad
    elif field == "cargo":
        raw["inventory"]["resources"][0]["amount"] = bad
    elif field == "x":
        raw["current_target"]["position"] = {"x": bad, "z": 1}
        # Slightly negative world coordinates are a legitimate bounded position.
        if bad == -1 and type(bad) is int:
            raw["current_target"]["position"]["x"] = -10001
    elif field == "heal":
        raw["combat"]["healing"] = {"health": bad, "range": 1, "interval_ms": 1,
                                     "healable_classes": [], "unhealable_classes": []}
    elif field == "garrison":
        raw["garrison"] = {"capacity": bad, "allowed_classes": [], "occupied_slots": 0, "occupants": []}
    elif field == "aura":
        raw["combat"]["auras"] = [{"id": "aura", "name": "name", "description": "", "range": bad}]
        if bad is None:  # A global aura has no finite range by design.
            assert project_properties(raw, private=True)["combat"]["auras"][0]["range"] is None
            return
    with pytest.raises(ObservationError):
        project_properties(raw, private=True)


@pytest.mark.parametrize("bad", [True, 0, -1, 2**32, "12", 12.0])
def test_private_references_require_real_entity_ids(bad):
    raw = properties()
    raw["current_target"]["entity_id"] = bad
    with pytest.raises(ObservationError):
        project_properties(raw, private=True)
    assert project_properties(raw)["current_target"] is None


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(version=True),
    lambda p: p.update(version=2),
    lambda p: p.pop("combat"),
    lambda p: p["identity"].update(special_roles=["SECRET_ROLE"]),
    lambda p: p["identity"].update(classes=["Unit"] * 65),
    lambda p: p["capabilities"].update(build=1),
    lambda p: p["gathering"].update(rates={"enemy.gold": 10}),
    lambda p: p["gathering"].update(capacities={"food.raw": 10}),
    lambda p: p["inventory"].update(resources=[{"type": "food", "amount": 1, "capacity": 10}] * 5),
    lambda p: p["construction"].update(templates=["house"] * 257),
    lambda p: p["combat"]["attacks"]["Melee"].update(min_range=8, max_range=4),
    lambda p: p["combat"]["attacks"]["Melee"].update(bonuses=[{}] * 65),
    lambda p: p["combat"]["attacks"].update({"attack" + str(i): attack() for i in range(9)}),
    lambda p: p["combat"].update(auras=[{"id": "a", "name": "a", "description": "a" * 2001, "range": None}]),
    lambda p: p.update(packing={"packed": True, "changing": False, "progress": 0, "state": "unpacked"}),
    lambda p: p.update(packing={"packed": False, "changing": True, "progress": 1.1, "state": "packing"}),
    lambda p: p.update(garrison={"capacity": 20, "allowed_classes": [], "occupied_slots": 0, "occupants": [12, 12]}),
    lambda p: p.update(garrison={"capacity": 20, "allowed_classes": [], "occupied_slots": 0, "occupants": list(range(1, 258))}),
])
def test_invalid_shapes_limits_and_contradictions_are_rejected(mutate):
    raw = properties()
    mutate(raw)
    with pytest.raises(ObservationError):
        project_properties(raw, private=True)


def test_splash_aura_storage_and_private_subobjects_are_all_allowlisted():
    raw = properties(storage={"accepts": ["food"], "secret": "SECRET"})
    raw["combat"]["attacks"]["Melee"]["splash"] = {"shape": "Circular", "range": 4,
        "friendly_fire": True, "damage": {"Crush": 20}, "capture": 0, "bonuses": [], "secret": "SECRET"}
    raw["combat"]["healing"] = {"health": 5, "range": 10, "interval_ms": 1000,
        "healable_classes": ["Organic"], "unhealable_classes": ["Siege"], "secret": "SECRET"}
    raw["combat"]["auras"] = [{"id": "aura", "name": "name", "description": "text", "range": None,
                               "affected_entities": "SECRET"}]
    safe = project_properties(raw, private=True)
    assert "SECRET" not in str(safe)
    assert safe["combat"]["attacks"]["Melee"]["splash"]["damage"] == {"Crush": 20}


def test_owned_observation_preserves_private_properties_and_discards_foreign_entities():
    entity = {"id": 10, "owner": 1, "movable": True, "properties": properties()}
    foreign = {"id": 20, "owner": 2, "movable": True, "properties": "SECRET"}
    result = project_observation({"player": 1, "entities": [entity, foreign]})
    assert len(result["entities"]) == 1
    assert result["entities"][0]["properties"]["inventory"]["resources"][0]["amount"] == 7
    assert result["entities"][0]["properties"]["current_target"]["entity_id"] == 44
    assert "SECRET" not in str(result)
    # Legacy entities do not acquire invented property data before mod upgrade.
    assert project_observation({"player": 1, "entities": [{"id": 11, "owner": 1}]})["entities"] == [
        {"id": 11, "owner": 1, "movable": False}]


@pytest.mark.parametrize("owner", [1, 2])
def test_map_observation_never_reintroduces_private_properties_even_for_owned_objects(owner):
    region = validate_region(0, 0, 4, 4, 4, 128)
    raw_properties = properties(inventory="SECRET", construction="SECRET", current_target="SECRET",
        activity="SECRET", garrisoned_in="SECRET",
        garrison={"capacity": 10, "allowed_classes": ["Infantry"],
                  "occupied_slots": "SECRET", "occupants": "SECRET"})
    frame = {"policy": "player-los-map-v1", "player": 1, "time": 1000,
        "region": region, "columns": 1, "rows": 1,
        "cells": [{"x": 2, "z": 2, "in_bounds": True, "explored": True,
                   "currently_visible": True, "terrain": None}],
        "objects": [{"id": 20, "owner": owner, "kind": "unit", "template": "units/athen/infantry",
                     "position": {"x": 2, "z": 2}, "currently_visible": True,
                     "last_seen": 1000, "static": False, "properties": raw_properties}], "truncated": False}
    result = project_map_region(frame, region, 1, 128)
    prop = result["objects"][0]["properties"]
    assert all(prop[key] is None for key in ("inventory", "construction", "current_target", "activity", "garrisoned_in"))
    assert prop["garrison"]["occupied_slots"] is None and prop["garrison"]["occupants"] is None
    assert prop["combat"]["attacks"]["Melee"]["bonuses"][0]["multiplier"] == 3
    assert "SECRET" not in str(result)


def test_periodic_special_attacks_and_status_immunity_are_bounded_and_copied():
    raw = properties()
    effect = {"name": "Poison", "duration_ms": 3600, "interval_ms": 600,
              "stackability": "Extend", "damage": {"Poison": 2}, "capture": 0,
              "target_ids": "SECRET"}
    raw["combat"]["attacks"]["Melee"]["status_effects"] = [effect]
    raw["combat"]["resistance"]["status_effects"] = {"Poison": {
        "duration_multiplier": 0, "block_chance": 1, "hidden_targets": "SECRET"}}
    safe = project_properties(raw)
    assert safe["combat"]["attacks"]["Melee"]["status_effects"][0]["damage"] == {"Poison": 2}
    assert safe["combat"]["resistance"]["status_effects"]["Poison"]["block_chance"] == 1
    assert "SECRET" not in str(safe)
    safe["combat"]["attacks"]["Melee"]["status_effects"][0]["damage"]["Poison"] = 999
    assert effect["damage"]["Poison"] == 2


@pytest.mark.parametrize("invalid", [True, -1, 1.1, float("nan"), "1"])
def test_invalid_status_resistance_probability_fails_closed(invalid):
    raw = properties()
    raw["combat"]["resistance"]["status_effects"] = {"Poison": {"duration_multiplier": 1, "block_chance": invalid}}
    with pytest.raises(ObservationError):
        project_properties(raw)


def test_building_projection_preserves_native_research_pairs_arrows_loyalty_and_work():
    raw = properties(building=building())
    safe = project_properties(raw, private=True)
    assert safe["building"] == raw["building"]
    assert safe["building"]["research"]["pairs"] == [{"top": "armor_a", "bottom": "armor_b"}]
    safe["building"]["loyalty"]["current"][1] = 0
    safe["building"]["research"]["pairs"][0]["top"] = "changed"
    assert raw["building"]["loyalty"]["current"][1] == 400
    assert raw["building"]["research"]["pairs"][0]["top"] == "armor_a"


def test_public_building_snapshot_withholds_production_and_worker_state_without_inspecting_them():
    raw = properties(building=building(production="SECRET", foundation="SECRET", repair="SECRET"))
    raw["building"]["resource"]["gatherers"] = "SECRET"
    safe = project_properties(raw)["building"]
    assert safe["production"] is None and safe["foundation"] is None and safe["repair"] is None
    assert safe["resource"]["gatherers"] is None
    assert safe["hitpoints"] == 850 and safe["owner"] == 1
    assert safe["loyalty"]["current"] == [0, 400, 100]
    assert safe["building_ai"]["arrows"] == 5
    assert "SECRET" not in str(safe)
    with pytest.raises(ObservationError):
        project_properties(raw, private=True)


def test_infinite_fields_and_neutral_shore_placement_are_explicit():
    raw = properties(building=building(
        resource={"infinite": True, "type": "food.grain", "amount": float("inf"), "max": float("inf"),
                  "max_gatherers": 5, "gatherers": 0},
        placement={"placement": "shore", "territory": ["own", "ally", "neutral"], "category": "Dock", "distance": None},
        owner=0))
    safe = project_properties(raw, private=True)["building"]
    assert safe["resource"]["infinite"] is True
    assert safe["resource"]["amount"] is None and safe["resource"]["max"] is None
    assert safe["placement"]["territory"] == ["own", "ally", "neutral"] and safe["owner"] == 0
    raw["building"]["placement"].update(placement=None, category=None)
    assert project_properties(raw)["building"]["placement"]["category"] is None


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(is_building=1),
    lambda p: p.update(owner=True),
    lambda p: p.update(owner=9),
    lambda p: p.update(hitpoints=float("nan")),
    lambda p: p["research"].update(pairs=[{"top": "a", "bottom": "b"}] * 256),
    lambda p: p["research"].update(pairs=[{"top": True, "bottom": "b"}]),
    lambda p: p["training"].update(entities=["unit"] * 257),
    lambda p: p["loyalty"].update(current={"owner": 500}),
    lambda p: p["loyalty"].update(current=[0] * 10),
    lambda p: p["loyalty"].update(current=[float("inf")]),
    lambda p: p["building_ai"].update(arrows=1.5),
    lambda p: p["building_ai"].update(garrison_arrow_multiplier=-1),
    lambda p: p["resource"].update(max_gatherers=True),
    lambda p: p["resource"].update(amount=float("inf")),
    lambda p: p["placement"].update(territory=["opponent"]),
    lambda p: p["placement"]["distance"].update(min=121, max=120),
    lambda p: p["foundation"].update(builders=2.5),
    lambda p: p["production"]["queue"][0].update(id=True),
    lambda p: p["production"]["queue"][0].update(count=1.5),
    lambda p: p["production"]["queue"][0].update(progress=1.1),
    lambda p: p["production"].update(queue=p["production"]["queue"] * 17),
])
def test_invalid_building_shapes_numbers_and_bounds_fail_closed(mutate):
    raw = properties(building=building())
    mutate(raw["building"])
    with pytest.raises(ObservationError):
        project_properties(raw, private=True)


def test_every_building_group_is_allowlisted_and_previous_v1_profiles_remain_compatible():
    raw = properties(building=building())
    records = [raw["building"], raw["building"]["research"], raw["building"]["research"]["pairs"][0],
               raw["building"]["training"], raw["building"]["production"], raw["building"]["production"]["queue"][0],
               raw["building"]["building_ai"], raw["building"]["resource"], raw["building"]["loyalty"],
               raw["building"]["placement"], raw["building"]["placement"]["distance"], raw["building"]["foundation"]]
    for record in records:
        record["hidden"] = "SECRET"
    assert "SECRET" not in str(project_properties(raw, private=True))
    raw.pop("building")
    assert project_properties(raw, private=True)["building"] is None
