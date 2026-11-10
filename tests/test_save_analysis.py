from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pyrogenesis_analysis.analysis import DEFAULT_RULES, analyze_save, audit_rules, clean, visibility_mode
from pyrogenesis_analysis.serialization import UNDEFINED


def case():
    settings = {"PlayerData": [None, {"Name": "Human", "AI": False, "Civ": "athen"},
                {"Name": "Bot", "AI": "petra", "Civ": "ptol"}],
                "RevealMap": False, "ExploreMap": False, "Ceasefire": 45,
                "VictoryConditions": ["conquest_units"], "Size": 128}
    archive = SimpleNamespace(metadata={"initAttributes": {"settings": settings}, "playerID": 1,
                              "engine_serialization_version": "0.28.0"},
                              path=Path("test.0adsave"), sha256="hash", entries=())
    templates = {10: "worker", 11: "animal", 12: "building", 13: "foundation|building", 14: "worker",
                 15: "mirage|worker", 16: "worker"}
    type_counts = {"Unit": {"worker": 3, "animal": 1}, "Worker": {"worker": 3},
                   "Animal": {"animal": 1}, "Structure": {"building": 1}}
    components = {
        "Player": {2: {"playerID": 0}, 3: {"playerID": 1, "popUsed": 6, "popBonuses": 10,
                                        "maxPop": 100, "resourceCount": {"food": 100},
                                        "resourceGatherers": {"wood": 0}}, 4: {"playerID": 2}},
        "TechnologyManager": {3: {"researchedTechs": {"$type": "Set", "values": ["phase_city_generic"]},
                                     "typeCountsByClass": type_counts,
                                     "classCounts": {"Unit": 4, "Worker": 3, "Animal": 1, "Structure": 1}}},
        "Ownership": {eid: {"owner": 1} for eid in templates},
        "UnitAI": {10: {"isIdle": True, "fsmStateName": "INDIVIDUAL.IDLE", "orderQueue": [], "isGuardOf": UNDEFINED},
                   14: {"isIdle": False, "fsmStateName": "INDIVIDUAL.RETURNRESOURCE.APPROACHING",
                        "orderQueue": [{"type": "ReturnResource", "data": {"target": 12}}]},
                   16: {"isIdle": True, "fsmStateName": "INDIVIDUAL.IDLE"}},
        "Garrisonable": {16: {"holder": 12}}, "GarrisonHolder": {12: {"entities": [16]}},
        "Foundation": {13: {"buildProgress": 0.5}}, "Mirage": {15: {}},
        "ResourceSupply": {12: {"amount": float("inf"), "cachedType": {"generic": "food"}}},
        "Position": {10: {"inWorld": True, "x": 1, "z": 2}, 16: {"inWorld": False, "x": 0, "z": 0}},
    }
    decoded = {"systems": {"Timer": {"time": 60000}, "CeasefireManager": {
        "ceasefireIsActive": True, "ceasefireStartedTime": 0, "ceasefireTime": 2700000}},
        "components": components, "templates": templates,
        "consumed_bytes": 42, "total_bytes": 42, "next_entity_id": 20}
    return archive, decoded


@pytest.mark.parametrize("reveal,explore,expected", [
    (False, False, "fog_unexplored"), (False, True, "fog_explored"),
    (True, False, "revealed"), (True, True, "revealed"), (None, False, "unknown")])
def test_visibility_protocol(reveal, explore, expected):
    assert visibility_mode({"RevealMap": reveal, "ExploreMap": explore}) == expected


def test_case_protocol_and_native_units_only_rule():
    archive, decoded = case()
    report = analyze_save(archive, decoded)
    assert report["rules_audit"]["matches"]
    assert report["scope"]["kind"] == "offline-save-omniscient"
    assert report["scope"]["fog_reconstructed"] is False
    player = report["players"][0]
    assert player["phase"] == "city"
    assert player["population"] == {"count": 6, "limit": 10, "max": 100}
    assert player["remaining_victory_unit_ids"] == [10, 14, 16]
    assert player["units"] == 4  # Unit+Animal counted in unit inventory, excluded from victory count.
    assert player["buildings"] == 2
    assert player["foundations"] == 1
    assert player["class_count_check"]["matches"]
    assert player["victory_count_complete"]
    assert player["idle_worker_ids"] == [10]  # Return-resource and garrisoned workers are not idle.
    assert report["match"]["ceasefire_actual"]["remaining_ms"] == 2640000
    farm = next(item for item in report["entities"] if item["id"] == 12)
    assert farm["resource"]["infinite"] and farm["resource"]["amount"] is None
    assert next(item for item in report["entities"] if item["id"] == 16)["position"] is None
    json.dumps(report, allow_nan=False)


def test_rules_mismatches_preserve_input_truth():
    archive, _ = case()
    archive.metadata["initAttributes"]["settings"]["PlayerData"][2]["AI"] = False
    archive.metadata["initAttributes"]["settings"]["VictoryConditions"] = ["conquest"]
    before = deepcopy(archive.metadata)
    audit = audit_rules(archive.metadata, DEFAULT_RULES)
    assert {item["rule"] for item in audit["mismatches"]} == {"opponents", "victory_conditions"}
    assert audit["actual"]["victory_conditions"] == ["conquest"]
    assert archive.metadata == before


def test_focus_never_changes_information_scope():
    archive, decoded = case()
    first = analyze_save(archive, decoded, focus_player=1)
    second = analyze_save(archive, decoded, focus_player=2)
    assert first["entities"] == second["entities"]
    assert first["players"] == second["players"]
    assert second["scope"]["fog_reconstructed"] is False
    with pytest.raises(ValueError, match="Focus player"):
        analyze_save(archive, decoded, focus_player=99)


def test_incomplete_decode_and_unknown_classification_are_explicit():
    archive, decoded = case()
    decoded["total_bytes"] += 1
    with pytest.raises(ValueError, match="partial"):
        analyze_save(archive, decoded)
    decoded["consumed_bytes"] += 1
    decoded["components"]["Ownership"][17] = {"owner": 1}
    decoded["templates"][17] = "unknown"
    report = analyze_save(archive, decoded)
    assert not report["players"][0]["victory_count_complete"]
    assert report["players"][0]["unknown_class_entity_ids"] == [17]
    assert any(item["code"] == "entity_classification_incomplete" for item in report["warnings"])


def test_clean_handles_cycles_undefined_nonfinite():
    data = {"undefined": UNDEFINED, "nan": float("nan")}
    data["self"] = data
    output = clean(data)
    assert output["undefined"] == {"$type": "undefined"}
    assert output["self"] == {"reference": "cycle"}
    assert output["nan"]["non_finite"] == "nan"
    json.dumps(output, allow_nan=False)
