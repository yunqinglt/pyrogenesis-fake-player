"""Native metadata is bounded, request-bound and owned-option scoped."""

from copy import deepcopy

import pytest

from pyrogenesis_mcp.metadata import (
    advertised_options, project_options, validate_options_request,
)
from pyrogenesis_mcp.privacy import ObservationError


@pytest.fixture
def observation():
    return {"player": 2, "entities": [{"id": 10, "owner": 2, "properties": {
        "construction": {"templates": ["structures/han/ministry"]},
        "building": {"research": {"technologies": ["phase_city_generic"], "pairs": [
            {"top": "civil_service_01", "bottom": "civil_service_02"}]},
            "training": {"entities": ["units/han/support_minister"]}}},
        "template": "structures/han/ministry"}]}


def option(identity="civil_service_01"):
    return {"id": identity, "name": {"generic": "Efficient Bureaucracy", "specific": "九卿"},
            "description": "", "tooltip": "大臣训练时间 -50%。", "requirements_tooltip": "城镇阶段解锁。",
            "cost": {"food": 100, "wood": 200, "time": 20},
            "base_cost": {"food": 200, "wood": 200, "time": 40},
            "cost_scope": "producer-adjusted-research",
            "requirements": [{"techs": ["phase_town"]}], "requirements_complete": True,
            "requirements_met": True, "affects": ["Minister"],
            "modifications": [{"value": "Cost/BuildTime", "multiply": 0.5}],
            "modifications_complete": True,
            "pair": {"group": "pair_unlock_civil_service_han", "position": "top", "other": "civil_service_02",
                     "requirements": [{"civ": "han"}], "requirements_complete": True},
            "supersedes": None, "replaces": [], "stats": {}}


def response(options=None):
    return {"entity_id": 10, "player": 2, "kind": "research", "source": "running-game-native-templates",
            "engine_version": "0.28.0", "locale": "zh_CN", "translation": "game-locale-or-source-fallback",
            "total_available": 3, "truncated": True, "options": options or [option()]}


@pytest.mark.parametrize("kind,identity", [("research", "civil_service_01"),
    ("research", "civil_service_02"), ("training", "units/han/support_minister"),
    ("construction", "structures/han/ministry"), ("self", "structures/han/ministry")])
def test_request_accepts_only_own_current_advertised_options(observation, kind, identity):
    assert validate_options_request(10, kind, [identity], observation) == {
        "entity_id": 10, "kind": kind, "ids": [identity]}


def test_default_request_describes_advertised_options_without_lookup_ids(observation):
    assert validate_options_request(10, "research", None, observation)["ids"] is None
    assert advertised_options(observation["entities"][0], "research") == [
        "phase_city_generic", "civil_service_01", "civil_service_02"]


@pytest.mark.parametrize("entity_id,kind,ids", [
    (True, "research", None), (999, "research", None), (10, "secret", None),
    (10, [], None), (10, {}, None),
    (10, "research", []), (10, "research", ["civil_service_01"] * 2),
    (10, "research", ["civil_service_01"] * 17), (10, "research", ["../private"]),
    (10, "research", ["hidden_tech"]), (10, "research", "civil_service_01"),
    (10, "research", ["civil_service_01\n"]),
])
def test_invalid_or_forged_requests_fail_before_native_queries(observation, entity_id, kind, ids):
    with pytest.raises(ValueError):
        validate_options_request(entity_id, kind, ids, observation)


def test_foreign_entity_cannot_become_metadata_oracle(observation):
    observation["entities"][0]["owner"] = 1
    with pytest.raises(ValueError):
        validate_options_request(10, "research", ["civil_service_01"], observation)


def test_real_han_pair_names_remain_distinct_by_id_and_structured_effect():
    top, bottom = option(), option("civil_service_02")
    bottom.update(tooltip="大臣最大生命值 +50%。", modifications=[{"value": "Health/Max", "multiply": 1.5}],
                  pair={"group": "pair_unlock_civil_service_han", "position": "bottom", "other": "civil_service_01",
                        "requirements": [{"civ": "han"}], "requirements_complete": True})
    request = {"entity_id": 10, "kind": "research", "ids": ["civil_service_01", "civil_service_02"]}
    result = project_options(response([top, bottom]), request, 2)
    assert result["options"][0]["name"]["specific"] == result["options"][1]["name"]["specific"]
    assert result["options"][0]["modifications"][0]["multiply"] == 0.5
    assert result["options"][1]["modifications"][0]["multiply"] == 1.5
    assert result["options"][0]["pair"]["position"] == "top"
    assert result["options"][0]["pair"]["requirements"] == [{"civ": "han"}]
    assert result["options"][0]["cost"]["time"] == 20
    assert result["options"][0]["base_cost"]["time"] == 40


def test_requirements_keep_or_and_groups_and_unknown_fields_are_dropped():
    raw = response()
    raw["secret"] = "SECRET"
    raw["options"][0]["secret"] = "SECRET"
    raw["options"][0]["requirements"] = [{"techs": ["phase_town"],
        "entities": [{"class": "Town", "number": 3, "check": "count", "secret": "SECRET"}]},
        {"all": [{"tech": "phase_city"}, {"any": [{"civ": "han"}, {"civ": "ptol"}]}]}]
    raw["options"][0]["modifications"] = [{"value": "Attack/Ranged/MinRange", "add": -10,
        "replace": "Infantry+!Hero", "secret": "SECRET"}]
    result = project_options(raw, {"entity_id": 10, "kind": "research", "ids": None}, 2)
    assert "SECRET" not in repr(result)
    assert result["options"][0]["modifications"][0]["add"] == -10
    assert result["options"][0]["modifications"][0]["replace"] == "Infantry+!Hero"
    assert result["options"][0]["requirements"][1]["all"][1]["any"][1]["civ"] == "ptol"
    assert raw["options"][0]["secret"] == "SECRET"
    assert result["options"][0]["requirements_complete"] is False
    assert result["options"][0]["modifications_complete"] is False


def test_self_normalizes_only_known_native_foundation_filter(observation):
    observation["entities"][0]["template"] = "foundation|structures/han/ministry"
    assert validate_options_request(10, "self", ["structures/han/ministry"], observation)["ids"] == ["structures/han/ministry"]
    observation["entities"][0]["template"] = "private|structures/han/ministry"
    with pytest.raises(ValueError):
        validate_options_request(10, "self", None, observation)


def test_engine_metadata_ack_is_request_bound_and_reprojected(observation, monkeypatch):
    from pyrogenesis_mcp.engine import GameSession

    session = GameSession()
    writes = []
    raw = response()
    raw["secret"] = "SECRET"
    monkeypatch.setattr(session, "observe", lambda: deepcopy(observation))
    monkeypatch.setattr(session, "_write_command", lambda action, **fields:
        writes.append((action, fields)) or "metadata-ack")
    monkeypatch.setattr(session, "_read_raw", lambda: {"last_action": {
        "id": "metadata-ack", "action": "describe_options", "ok": True, "metadata": raw}})
    result = session.describe_options(10, "research", ["civil_service_01"])
    assert "SECRET" not in repr(result)
    assert result["options"][0]["id"] == "civil_service_01"
    assert writes == [("describe_options", {"entity_id": 10, "kind": "research", "ids": ["civil_service_01"]})]


def test_engine_rejects_metadata_ack_from_another_action(observation, monkeypatch):
    from pyrogenesis_mcp.engine import GameSession

    session = GameSession()
    monkeypatch.setattr(session, "observe", lambda: deepcopy(observation))
    monkeypatch.setattr(session, "_write_command", lambda action, **fields: "metadata-ack")
    monkeypatch.setattr(session, "_read_raw", lambda: {"last_action": {
        "id": "metadata-ack", "action": "observe_map", "ok": True, "metadata": response()}})
    with pytest.raises(RuntimeError):
        session.describe_options(10, "research", ["civil_service_01"])


@pytest.mark.parametrize("change", ["player", "entity", "kind", "source", "id", "missing", "duplicate",
                                     "nan", "too_many", "requirements_depth", "effect_size", "pair"])
def test_metadata_response_rejects_mismatch_and_unbounded_payload(change):
    raw = response()
    request = {"entity_id": 10, "kind": "research", "ids": ["civil_service_01"]}
    if change == "player":
        raw["player"] = 1
    elif change == "entity":
        raw["entity_id"] = 11
    elif change == "kind":
        raw["kind"] = "training"
    elif change == "source":
        raw["source"] = "filesystem-checkout"
    elif change == "id":
        raw["options"][0]["id"] = "private_tech"
    elif change == "missing":
        raw["options"] = []
    elif change == "duplicate":
        raw["options"].append(deepcopy(raw["options"][0]))
    elif change == "nan":
        raw["options"][0]["cost"]["food"] = float("nan")
    elif change == "too_many":
        raw["options"] *= 17
    elif change == "requirements_depth":
        deep = []
        for _ in range(10):
            deep = [deep]
        raw["options"][0]["requirements"] = deep
    elif change == "effect_size":
        raw["options"][0]["modifications"] *= 65
    elif change == "pair":
        raw["options"][0]["pair"]["position"] = "both"
    with pytest.raises(ObservationError):
        project_options(raw, request, 2)
