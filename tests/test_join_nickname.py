"""Room nickname aliases resolve before any game launch or connection mutation."""

import asyncio

import pytest

from pyrogenesis_mcp import server


@pytest.mark.parametrize("arguments,expected", [
    ({}, "MCP Player"),
    ({"nickname": "模型甲"}, "模型甲"),
    ({"player_name": "Legacy Model"}, "Legacy Model"),
    ({"nickname": "Model A", "player_name": "Model A"}, "Model A"),
    ({"nickname": "MCP Player", "player_name": "MCP Player"}, "MCP Player"),
    ({"nickname": "A" * 32}, "A" * 32),
])
def test_join_forwards_effective_nickname_unchanged(monkeypatch, arguments, expected):
    calls = []

    def join(host, port, name):
        calls.append((host, port, name))
        return {"mode": "lan_client", "running": True}

    monkeypatch.setattr(server.game, "join", join)
    result = asyncio.run(server.join_lan_game("192.0.2.1", 20596, **arguments))
    assert calls == [("192.0.2.1", 20596, expected)]
    assert result == {"mode": "lan_client", "running": True}


@pytest.mark.parametrize("arguments", [
    {"nickname": "Model A", "player_name": "Model B"},
    {"nickname": "Model A", "player_name": "MCP Player"},
    {"nickname": ""},
    {"player_name": ""},
    {"nickname": " \t "},
    {"nickname": "\u3000"},
    {"nickname": "A" * 33},
    {"nickname": "Model\nA"},
    {"nickname": "Model\x00A"},
    {"nickname": "Model\x7fA"},
    {"nickname": "Model\u202eA"},
    {"nickname": "Model\ud800A"},
    {"nickname": "Model A", "player_name": "\n"},
    {"nickname": 123},
])
def test_bad_nickname_or_conflicting_aliases_never_call_engine(monkeypatch, arguments):
    def unexpected_join(*_args):
        raise AssertionError("Invalid nickname reached the game engine")

    monkeypatch.setattr(server.game, "join", unexpected_join)
    with pytest.raises(ValueError, match="nickname|player_name"):
        asyncio.run(server.join_lan_game("192.0.2.1", **arguments))
