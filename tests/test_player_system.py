"""Own-player scoreboard data is bounded and projected through an explicit allowlist."""

import json
import time

import pytest

from pyrogenesis_mcp.engine import GameSession, POLICY
from pyrogenesis_mcp.privacy import ObservationError, project_observation


def scoreboard(**updates):
    value = {
        "phase": "town",
        "population": {"count": 31, "limit": 50, "max": 300},
        "resources": {"food": 50000, "wood": 50000, "stone": 50000, "metal": 50000},
        "gatherers": {"food": 12, "wood": 9, "stone": 2, "metal": 3},
        "idle_units": {"count": 4, "worker_count": 3},
        "territory_percent": 27,
        "current_research": [{"technology": "phase_city", "progress": 0.4,
            "time_remaining_ms": 30000, "researcher_id": 100, "paused": False}],
        "construction": [{"id": 101, "template": "structures/athen/house",
            "progress": 0.75, "builders": 2}],
        "busy_buildings": [{"id": 102, "template": "structures/athen/barracks", "queue": [
            {"template_name": None, "unit_template": "units/athen/infantry_spearman_b",
             "technology_template": None, "count": 5, "progress": 0.3,
             "time_remaining_ms": 12000, "paused": False}]}],
    }
    value.update(updates)
    return value


def test_scoreboard_allowlist_preserves_requested_own_player_metrics():
    raw = scoreboard(private_enemy="DO_NOT_LEAK", resources={"food": 50000, "enemy_food": 999999})
    result = project_observation({"player": 1, "entities": [], "scoreboard": raw})
    assert result["scoreboard"] == scoreboard(resources={"food": 50000})
    assert "DO_NOT_LEAK" not in str(result)
    assert "enemy_food" not in str(result)


def test_scoreboard_absent_remains_backward_compatible():
    assert project_observation({"player": 1, "entities": []}) == {"player": 1, "entities": [], "resources": {}}


@pytest.mark.parametrize(("outcome", "game_over"), [
    ("active", False), ("won", True), ("defeated", True), ("defeated", False), ("unknown", None),
])
def test_authoritative_outcome_and_game_over_are_projected_independently(outcome, game_over):
    result = project_observation({"player": 1, "entities": [], "outcome": outcome,
                                  "game_over": game_over, "opponent_state": "SECRET"})
    assert result["outcome"] == outcome
    assert result["gameOver"] is game_over
    assert "SECRET" not in str(result)


@pytest.mark.parametrize(("field", "value"), [
    ("outcome", "still_playing"), ("outcome", True), ("game_over", "false"), ("game_over", 0),
])
def test_invalid_outcome_fields_fail_closed(field, value):
    with pytest.raises(ObservationError):
        project_observation({"player": 1, "entities": [], field: value})


def test_scoreboard_resource_fields_are_filtered_and_optional_territory_is_explicit():
    raw = scoreboard(territory_percent=None, resources={"food": 50000, "enemy_food": 999999})
    safe = project_observation({"player": 1, "entities": [], "scoreboard": raw})["scoreboard"]
    assert safe["resources"] == {"food": 50000}
    assert safe["territory_percent"] is None
    assert "enemy_food" not in str(safe)


@pytest.mark.parametrize("changes", [
    {"phase": "SECRET"},
    {"resources": {"food": True}},
    {"gatherers": {"food": -1}},
    {"idle_units": {"count": True, "worker_count": 0}},
    {"territory_percent": 101},
    {"current_research": [{"technology": "phase_town", "progress": 2,
       "time_remaining_ms": 3, "researcher_id": 1, "paused": False}]},
    {"current_research": [{"technology": "phase_town", "progress": 0.5,
       "time_remaining_ms": 3, "researcher_id": True, "paused": False}]},
    {"construction": [{"id": 2, "template": "house", "progress": 0.4, "builders": -1}]},
    {"busy_buildings": [{"id": 2, "template": "barracks", "queue": [{
       "template_name": None, "unit_template": "unit", "technology_template": None,
       "count": 1, "progress": 0.5, "time_remaining_ms": 1, "paused": 0}]}]},
])
def test_malformed_or_out_of_scope_scoreboard_values_fail_closed(changes):
    raw = scoreboard()
    raw.update(changes)
    with pytest.raises(ObservationError):
        project_observation({"player": 1, "entities": [], "scoreboard": raw})


def test_bridge_engine_reprojects_scoreboard_and_drops_unrecognized_fields(tmp_path):
    game = GameSession()
    game.session = "player-system-session"
    game._bridge = tmp_path
    raw = {
        "version": 1, "session": game.session, "policy": POLICY, "available": True,
        "emitted_at_ms": time.time() * 1000, "player_id": 1, "game_time_ms": 2000,
        "map_size": 512, "resources": {"food": 50000}, "population": {"count": 31, "limit": 50},
        "scoreboard": scoreboard(private="SECRET", opponent_state={"food": 999999}), "own_entities": [],
    }
    (tmp_path / "observation.json").write_text(json.dumps(raw), encoding="utf-8")
    result = game.observe()
    assert result["scoreboard"] == scoreboard()
    assert "SECRET" not in str(result)
    assert "opponent_state" not in str(result)


def test_bridge_engine_projects_terminal_status_without_entities_or_enemy_fields(tmp_path):
    game = GameSession()
    game.session = "terminal-player-system-session"
    game._bridge = tmp_path
    raw = {
        "version": 1, "session": game.session, "policy": POLICY, "available": True,
        "emitted_at_ms": time.time() * 1000, "player_id": 1, "game_time_ms": 2000,
        "outcome": "defeated", "game_over": True, "own_entities": [],
        "opponent_state": {"resources": {"food": 91919191}},
    }
    (tmp_path / "observation.json").write_text(json.dumps(raw), encoding="utf-8")
    result = game.observe()
    assert result["player"] == 1
    assert result["outcome"] == "defeated" and result["gameOver"] is True
    assert result["entities"] == [] and result["resources"] == {}
    assert "opponent_state" not in str(result)
