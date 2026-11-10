"""Check the file bridge and command boundary without launching Pyrogenesis."""

import json
import time

import pytest

from pyrogenesis_mcp.engine import GameSession, POLICY
from pyrogenesis_mcp.privacy import ObservationError


@pytest.fixture
def bridge(tmp_path):
    game = GameSession()
    game.session = "active-session"
    game._bridge = tmp_path

    def write(**updates):
        raw = {
            "version": 1,
            "session": "active-session",
            "policy": POLICY,
            "available": True,
            "emitted_at_ms": time.time() * 1000,
            "player_id": 1,
            "game_time_ms": 1000,
            "map_size": 256,
            "resources": {"food": 300, "wood": 200},
            "population": {"count": 8, "limit": 20},
            "own_entities": [{
                "id": 10, "owner": 1, "movable": True,
                "template": "units/athen/infantry_spearman_b",
                "position": {"x": 100, "z": 100},
                "hitpoints": 100, "max_hitpoints": 100,
            }],
        }
        raw.update(updates)
        (tmp_path / "observation.json").write_text(json.dumps(raw), encoding="utf-8")
        return raw

    return game, write


def test_bridge_observation_is_projected_again_before_model_output(bridge):
    game, write = bridge
    raw = write(
        all_players={"2": {"resources": {"food": 91919191}}},
        raw_packets=["SECRET_LOCKSTEP_PAYLOAD"],
        last_action={"error": "SECRET_STACK_TRACE"},
        sync_hash="SECRET_HASH",
    )
    raw["resources"]["enemy_food"] = 91919191
    raw["population"]["enemy_count"] = 91919191
    raw["own_entities"][0].update(enemy_target="SECRET", command_queue=["SECRET"])
    raw["own_entities"][0]["position"]["hidden"] = "SECRET"
    raw["own_entities"].append({
        "id": 20, "owner": 2, "template": "SECRET_ENEMY", "movable": True,
        "position": {"x": 9, "z": 9},
    })
    write(**raw)
    assert game.observe() == {
        "player": 1, "time": 1000, "resources": {"food": 300, "wood": 200},
        "population": 8, "populationLimit": 20, "policy": POLICY, "mapSize": 256,
        "entities": [{
            "id": 10, "owner": 1, "movable": True,
            "template": "units/athen/infantry_spearman_b",
            "position": {"x": 100, "z": 100}, "hitpoints": 100, "maxHitpoints": 100,
        }],
    }


def test_engine_forwards_properties_through_owned_projection_without_bypassing_validation(bridge):
    # Exercise the actual engine normalization, not only the schema function.
    from test_properties import properties

    game, write = bridge
    raw = write()
    raw["own_entities"][0]["properties"] = properties()
    raw["own_entities"][0]["properties"]["private_engine_dump"] = "SECRET"
    write(**raw)
    observed = game.observe()["entities"][0]["properties"]
    assert observed["capabilities"]["build"]
    assert observed["inventory"]["resources"][0]["amount"] == 7
    assert observed["current_target"]["entity_id"] == 44
    assert "SECRET" not in str(observed)
    raw["own_entities"][0]["properties"]["version"] = True
    write(**raw)
    with pytest.raises(ObservationError):
        game.observe()


@pytest.mark.parametrize("changes", [
    {"session": "previous-session"}, {"policy": "unfiltered-world"},
    {"version": 2}, {"version": True}, {"available": False},
    {"available": "true"}, {"available": None},
    {"emitted_at_ms": (time.time() - 30) * 1000},
    {"emitted_at_ms": (time.time() + 30) * 1000},
    {"player_id": -1}, {"player_id": 0},
])
def test_wrong_session_policy_observer_or_stale_frame_cannot_be_observed(bridge, changes):
    game, write = bridge
    write(**changes)
    with pytest.raises(ObservationError):
        game.observe()


@pytest.mark.parametrize("missing", ["session", "policy", "version", "available", "emitted_at_ms"])
def test_missing_bridge_contract_fields_fail_closed(bridge, missing):
    game, write = bridge
    raw = write()
    del raw[missing]
    (game._bridge / "observation.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ObservationError):
        game.observe()


def test_stale_frame_cannot_authorize_a_command(bridge):
    game, write = bridge
    write(emitted_at_ms=(time.time() - 30) * 1000)
    with pytest.raises(ObservationError, match="stale"):
        game.act("move", [10], x=110, z=120)
    assert not (game._bridge / "command.json").exists()


@pytest.mark.parametrize("selected", [[20], [999], [True], [10, 20]])
def test_foreign_or_unknown_entities_cannot_authorize_bridge_writes(bridge, selected):
    game, write = bridge
    write()
    with pytest.raises(ValueError):
        game.act("move", selected, x=110, z=120)
    assert not (game._bridge / "command.json").exists()


@pytest.mark.parametrize("x,z", [(float("nan"), 100), (100, float("inf")), (-1, 100), (257, 100)])
def test_invalid_or_outside_map_coordinates_do_not_write_commands(bridge, x, z):
    game, write = bridge
    write()
    with pytest.raises(ObservationError):
        game.act("move", [10], x=x, z=z)
    assert not (game._bridge / "command.json").exists()


def test_action_ack_does_not_disclose_bridge_extras(bridge, monkeypatch):
    game, write = bridge
    raw = write()
    raw["last_action"] = {
        "id": "safe-command-id", "ok": True,
        "opponent_commands": ["SECRET"], "debug": "SECRET_STACK_TRACE",
    }
    monkeypatch.setattr(game, "_read_raw", lambda **_kwargs: raw)
    monkeypatch.setattr(game, "_write_command", lambda *_args, **_kwargs: "safe-command-id")
    assert game.act("move", [10, 10], x=110, z=120) == {
        "accepted": True, "action": "move", "entityCount": 1,
    }


def test_status_exposes_only_connection_state_from_relay():
    game = GameSession()

    class FakeRelay:
        def status(self):
            return {
                "state": "connected", "packets_to_client": 91919191,
                "packets_to_host": 98989898, "error": "SECRET",
            }

    game.relay = FakeRelay()
    assert game.status() == {
        "mode": "stopped", "running": False, "ready": False,
        "policy": POLICY, "network": "connected",
    }


@pytest.mark.parametrize("network,ready", [("listening", False), ("error", False), ("connected", True)])
def test_network_player_is_ready_only_when_its_relay_is_connected(monkeypatch, network, ready):
    game = GameSession()

    class RunningProcess:
        def poll(self):
            return None

    class Relay:
        def status(self):
            return {"state": network}

    game.processes = [RunningProcess()]
    game.relay = Relay()
    monkeypatch.setattr(game, "observe", lambda: {"player": 2})
    assert game.status()["ready"] is ready


def test_status_reports_terminal_outcome_without_advertising_actions(monkeypatch):
    game = GameSession()

    class RunningProcess:
        def poll(self):
            return None

    game.processes = [RunningProcess()]
    monkeypatch.setattr(game, "observe", lambda: {
        "player": 2, "outcome": "won", "gameOver": True,
    })
    assert game.status() == {
        "mode": "stopped", "running": True, "ready": False, "policy": POLICY,
        "player": 2, "outcome": "won", "gameOver": True,
        "state": "won", "reason": "game_over",
    }


@pytest.mark.parametrize(("outcome", "expected_outcome"), [
    ("defeated", "defeated"), ("won", "won"), ("active", None), ("unknown", None),
])
def test_disconnected_status_surfaces_only_fresh_terminal_bridge_outcome(bridge, outcome, expected_outcome):
    game, write = bridge

    class RunningProcess:
        def poll(self):
            return None

    class DisconnectedRelay:
        def status(self):
            return {"state": "host_disconnected"}

    game.processes = [RunningProcess()]
    game.relay = DisconnectedRelay()
    write(outcome=outcome, game_over=True if outcome in ("won", "defeated") else None)
    result = game.status()
    assert result["ready"] is False
    if expected_outcome:
        assert result["outcome"] == expected_outcome
        assert result["gameOver"] is True
        assert result["player"] == 1
        assert result["state"] == expected_outcome
        assert result["reason"] == "network_host_disconnected"
    else:
        assert "outcome" not in result and "gameOver" not in result
        assert result["state"] == "host_disconnected"
        assert result["reason"] == "network_unavailable"


def test_disconnected_status_does_not_surface_a_stale_terminal_frame(bridge):
    game, write = bridge

    class RunningProcess:
        def poll(self):
            return None

    class DisconnectedRelay:
        def status(self):
            return {"state": "client_disconnected"}

    game.processes = [RunningProcess()]
    game.relay = DisconnectedRelay()
    write(outcome="defeated", game_over=True, emitted_at_ms=(time.time() - 30) * 1000)
    result = game.status()
    assert result["state"] == "client_disconnected"
    assert result["reason"] == "network_unavailable"
    assert "outcome" not in result and "gameOver" not in result
