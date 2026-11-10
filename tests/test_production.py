"""Production commands require owned, completed, advertised producers."""

from copy import deepcopy

import pytest

from pyrogenesis_mcp.actions import validate_production
from pyrogenesis_mcp.engine import GameSession


@pytest.fixture
def producer():
    return {"player": 2, "entities": [{"id": 10, "owner": 2, "properties": {
        "identity": {"classes": ["Structure"]}, "building": {
            "is_building": True, "foundation": None, "production": {"queue": []},
            "training": {"entities": ["units/ptol/support_civilian", "gaia/fauna_sheep"]},
            "research": {"technologies": ["phase_town_generic"],
                         "pairs": [{"top": "pair/top", "bottom": "pair/bottom"}]},
        }}}]}


@pytest.fixture
def game(producer, monkeypatch):
    session, writes = GameSession(), []
    monkeypatch.setattr(session, "observe", lambda: deepcopy(producer))
    monkeypatch.setattr(session, "_write_command", lambda action, **fields:
                        writes.append((action, fields)) or "ack")
    monkeypatch.setattr(session, "_action_ack", lambda command_id, action, count:
                        {"accepted": True, "action": action, "entityCount": count})
    return session, writes


def test_native_training_and_research_use_distinct_fields(game):
    session, writes = game
    assert session.train_units(10, "units/ptol/support_civilian", 2)["requestedCount"] == 2
    assert session.research_technology(10, "phase_town_generic")["accepted"]
    assert writes == [
        ("train", {"entities": [10], "template": "units/ptol/support_civilian", "count": 2}),
        ("research", {"entities": [10], "template": "phase_town_generic"}),
    ]


@pytest.mark.parametrize("option,research", [
    ("units/ptol/support_civilian", False), ("gaia/fauna_sheep", False),
    ("phase_town_generic", True), ("pair/top", True), ("pair/bottom", True),
])
def test_advertised_templates_include_paired_techs_and_corral_animals(producer, option, research):
    assert validate_production(10, option, producer, research=research) == 10


@pytest.mark.parametrize("count", [True, 0, -1, 101, 1.5, "2"])
def test_invalid_training_count_never_posts(game, count):
    session, writes = game
    with pytest.raises(ValueError):
        session.train_units(10, "units/ptol/support_civilian", count)
    assert writes == []


@pytest.mark.parametrize("building_id,template", [
    (True, "phase_town_generic"), (999, "phase_town_generic"),
    (10, "../phase_town_generic"), (10, "units/ptol/support_civilian"),
    (10, "phase_town_generic\n"), (10, {"template": "phase_town_generic"}),
    (10, "unavailable_technology"),
])
def test_unavailable_or_malformed_research_never_posts(game, building_id, template):
    session, writes = game
    with pytest.raises(ValueError):
        session.research_technology(building_id, template)
    assert writes == []


@pytest.mark.parametrize("change", ["foreign", "foundation", "unit", "no_queue", "no_profile"])
def test_stale_or_inappropriate_producer_is_rejected(producer, change):
    entity = producer["entities"][0]
    if change == "foreign":
        entity["owner"] = 1
    elif change == "foundation":
        entity["properties"]["building"]["foundation"] = {"builders": 2}
    elif change == "unit":
        entity["properties"]["identity"]["classes"] = ["Unit"]
    elif change == "no_queue":
        entity["properties"]["building"]["production"] = None
    else:
        entity.pop("properties")
    with pytest.raises(ValueError):
        validate_production(10, "phase_town_generic", producer, research=True)
