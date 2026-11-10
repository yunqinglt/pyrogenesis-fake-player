"""MCP stdio entry point. Only public metadata and permitted player-view data leave here."""

from __future__ import annotations

import atexit
import asyncio
from typing import Annotated, Literal
import unicodedata

from pydantic import Field

from mcp.server.fastmcp import FastMCP

from .engine import GameSession
from .actions import BuildOrder, PositionOrder, TargetOrder, RallyTargetOrder, RallyPositionOrder


EntityId = Annotated[int, Field(strict=True, gt=0, lt=2**31)]


game = GameSession()
atexit.register(game.close)
mcp = FastMCP(
    "Pyrogenesis Fair Player",
    instructions=(
        "Control one ordinary 0 A.D. 0.28.0 player. Start a local game or join a LAN host, "
        "poll player_status until ready, then observe_player. Observations contain only your "
        "own entities and economy, including structured unit properties (capabilities, cargo, current target, "
        "combat, special roles, garrison and packing state), building properties and your live scoreboard. "
        "Use describe_options for native names, descriptions, costs and rules of your own templates/options. "
        "Use order_units for queued target/position/build tasks, abandon_targets to clear orders, "
        "set_rally_point/clear_rally_point for future trained units' default tasks (read properties.rally_point), "
        "and set_unit_stance for chase/flee preferences. Use train_units and research_technology "
        "with the advertised options of completed owned buildings; cancel_production removes a current queue item. "
        "Use set_unit_packing, cancel_unit_packing, unload_garrison and leave_turret for siege and transport control. "
        "Use observe_map_region for LOS-filtered terrain and visible objects, "
        "including last-seen static snapshots under fog. Choose movable owned IDs for move_units or stop_units. "
        "Use analyze_terrain_region for compact row-major height, slope and approximate footprint-flatness arrays; "
        "these are sampled suitability hints, never native placement validation. "
        "Coordinates are world x/z; exploration by moving into black terrain is a normal action. "
        "There is no raw packet, opponent state, arbitrary command or scripting tool. "
        "The official engine supplies deterministic simulation through a Python ENet relay."
    ),
)


@mcp.tool()
async def start_local_game(networked: bool = True) -> dict:
    """Launch a local test match. Network mode runs host + Python ENet relay + a controlled player.

    With networked=false, run one engine against a sandbox AI. Startup is asynchronous;
    poll player_status until ready; use its actual player ID assigned by the host.
    One controlled game may run at a time.
    """
    return await asyncio.to_thread(game.start_local, networked=networked)


@mcp.tool()
async def join_lan_game(host: str, port: int = 20595, player_name: str | None = None,
                        nickname: str | None = None) -> dict:
    """Join an unpassworded 0.28.0 LAN room through Python ENet. Host must start the match.

    nickname is the visible in-room player name; give each model a distinct nickname.
    player_name remains a compatible alias. Omit both for "MCP Player". If both are
    supplied they must be identical; conflicting aliases fail before joining.
    Names must contain 1..32 Unicode characters, include a non-whitespace character
    and contain no control, surrogate or formatting characters.
    XMPP lobby discovery/authentication is outside this direct LAN connector.
    """
    for name in (player_name, nickname):
        if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > 32 or
                                 any(unicodedata.category(char) in {"Cc", "Cs", "Cf"} for char in name)):
            raise ValueError("Use a non-blank player nickname of 1 to 32 characters without control characters")
    if nickname is not None and player_name is not None and nickname != player_name:
        raise ValueError("nickname and player_name must match when both are supplied")
    selected_name = nickname if nickname is not None else player_name if player_name is not None else "MCP Player"
    return await asyncio.to_thread(game.join, host, port, selected_name)


@mcp.tool()
async def player_status() -> dict:
    """Get readiness, connection state, controlled player ID and authoritative match outcome."""
    return await asyncio.to_thread(game.status)


@mcp.tool()
async def observe_player() -> dict:
    """Observe your own units, resources and population in the active player's normal view.

    Owned entities include versioned properties: capabilities, carried resources/trade goods,
    a sanitized current target, attacks/bonuses/healing, special roles, garrison and packing.
    Includes your authoritative outcome (active, won, defeated, or unknown) and whether all
    player slots have reached a terminal state. Also includes your scoreboard: phase, gatherers, idle units, territory, population, resources,
    research, construction and busy building queues. Building profiles include production,
    loyalty, placement rules, auras, garrison and arrows. Hidden opponents are never returned.
    """
    return await asyncio.to_thread(game.observe)


@mcp.tool()
async def describe_options(entity_id: EntityId,
                           kind: Literal["research", "training", "construction", "self"] = "research",
                           ids: list[str] | None = None) -> dict:
    """Read native names, descriptions, costs and rules advertised by an owned entity.

    kind=self describes its current template; other kinds describe advertised research,
    training or construction options. Omit ids for the first bounded page, or select up
    to 16 advertised IDs. Research pairs keep separate IDs, effects and pair links even
    when names match. Descriptions use the game's current language. No arbitrary template lookup.
    """
    return await asyncio.to_thread(game.describe_options, entity_id, kind, ids)


@mcp.tool()
async def observe_map_region(x_min: float, z_min: float, x_max: float, z_max: float,
                             cell_size: float = 16) -> dict:
    """Sample a world x/z rectangle through the controlled player's exploration and LOS.

    Cell size 4..256, at most 4096 center samples and 2000 objects. Unknown terrain is null.
    Visible objects include other players, Gaia resources, trees, buildings and treasures.
    Fog contains only previously observed static snapshots with last_seen game milliseconds;
    explored terrain never sampled while visible remains null. Mobile units are visible-only.
    Unit/building properties are public profiles; cargo, orders and garrison occupants are omitted.
    Sample visibility applies at the center, not every point in its cell. No texture/passability data.
    """
    return await asyncio.to_thread(game.observe_map_region, x_min, z_min, x_max, z_max, cell_size)


@mcp.tool()
async def analyze_terrain_region(x_min: float, z_min: float, x_max: float, z_max: float,
                                 footprint_width: float, footprint_depth: float,
                                 cell_size: float = 8) -> dict:
    """Analyze visible or previously observed terrain as compact row-major numeric arrays.

    Returns height, slope and approximate building-footprint elevation statistics. Only
    player-view terrain is used; unknown samples remain null. A favorable score does not
    guarantee a legal building location: placement rules, obstructions and native checks
    are still authoritative.
    """
    return await asyncio.to_thread(
        game.analyze_terrain_region, x_min, z_min, x_max, z_max,
        footprint_width, footprint_depth, cell_size,
    )


@mcp.tool()
async def move_units(entity_ids: list[EntityId], x: float, z: float, queued: bool = False) -> dict:
    """Order currently owned movable units to world coordinates. Revalidates ownership in game.

    Accepted means the official command queue received the order; it does not guarantee arrival.
    """
    return await asyncio.to_thread(game.act, "move", entity_ids, x=x, z=z, queued=queued)


@mcp.tool()
async def stop_units(entity_ids: list[EntityId]) -> dict:
    """Stop your currently owned movable units with an ordinary network command."""
    return await asyncio.to_thread(game.act, "stop", entity_ids)


@mcp.tool()
async def order_units(entity_ids: list[EntityId], orders: list[TargetOrder | BuildOrder | PositionOrder], queued: bool = False) -> dict:
    """Issue 1..100 tasks in order; queued=false replaces prior tasks, true appends.

    Target tasks: attack (damage, no capture), capture, gather, repair (also foundations),
    garrison, occupy_turret, heal, guard, collect_treasure, return_resource and trade. Support and trade
    targets must be your own; treasure must be visible Gaia. Position tasks patrol and
    attack_move use world x/z. Build tasks use an allowed structure template plus world
    x/z and optional angle in radians. Every task is validated before
    any command is posted. Native placement, costs, diplomacy and pathing still apply.
    Accepted means submitted; observe later to verify the outcome. No formations are created.
    """
    return await asyncio.to_thread(game.order_units, entity_ids, orders, queued=queued)


@mcp.tool()
async def set_rally_point(entity_ids: list[EntityId], orders: list[RallyTargetOrder | RallyPositionOrder],
                          queued: bool = False) -> dict:
    """Set 1..100 rally tasks on 1..200 completed owned entities with a rally point.

    Positions: move, attack_move, patrol with x/z. Targets: gather, repair (including
    foundations), garrison, occupy_turret, attack (damage), capture, trade, collect_treasure.
    Support/trade targets must be owned; other targets must be currently visible.
    queued=false replaces, true appends (total at most 100). No existing unit is redirected.
    Units use the current rally queue at birth, not at training enqueue time; incompatible
    unit capabilities, target loss, capacity and native AI may change the outcome.
    Some holders also use rally points when unloading. Read properties.rally_point and
    observe newborn units to verify. No raw commands or formations.
    """
    return await asyncio.to_thread(game.set_rally_point, entity_ids, orders, queued=queued)


@mcp.tool()
async def clear_rally_point(entity_ids: list[EntityId]) -> dict:
    """Clear owned rally queues. Already born/unloaded units keep their current tasks."""
    return await asyncio.to_thread(game.clear_rally_point, entity_ids)


@mcp.tool()
async def abandon_targets(entity_ids: list[EntityId]) -> dict:
    """Remove guard bindings, stop your units and clear their current and queued tasks.

    This preserves stance and native autonomous reactions; it does not disable UnitAI.
    """
    return await asyncio.to_thread(game.abandon_targets, entity_ids)


@mcp.tool()
async def set_unit_stance(entity_ids: list[EntityId], stance: str) -> dict:
    """Set violent, aggressive, defensive, passive, standground or skittish.

    violent chases beyond unit vision; aggressive chases within vision; defensive stays near
    its position; standground never pursues; passive flees when attacked; skittish flees on sight.
    """
    return await asyncio.to_thread(game.set_stance, entity_ids, stance)


@mcp.tool()
async def set_unit_packing(entity_ids: list[EntityId], packed: bool, queued: bool = False) -> dict:
    """Fold siege units for transport (packed=true) or deploy them (false).

    All selected units must support packing, be stable and differ from the desired state.
    This submits the native pack/unpack order; observe packing.state to confirm completion.
    """
    return await asyncio.to_thread(game.set_unit_packing, entity_ids, packed, queued=queued)


@mcp.tool()
async def cancel_unit_packing(entity_ids: list[EntityId]) -> dict:
    """Cancel an active folding or deployment transition of each selected owned siege unit."""
    return await asyncio.to_thread(game.cancel_unit_packing, entity_ids)


@mcp.tool()
async def unload_garrison(holder_id: EntityId, entity_ids: list[EntityId] | None = None) -> dict:
    """Unload your occupants from your building, ship or mobile garrison holder.

    Omit entity_ids to select all of your currently observed occupants; allied occupants
    are excluded. Native ejection/pathing restrictions still apply. Observe to confirm.
    """
    return await asyncio.to_thread(game.unload_garrison, holder_id, entity_ids)


@mcp.tool()
async def leave_turret(entity_ids: list[EntityId]) -> dict:
    """Order your ejectable occupants to leave turret positions using the native command.

    Garrison occupants use unload_garrison instead. The game revalidates actual turret
    occupancy and ejection permissions; fixed non-ejectable turret occupants are refused.
    """
    return await asyncio.to_thread(game.leave_turret, entity_ids)


@mcp.tool()
async def delete_own_units(entity_ids: list[EntityId]) -> dict:
    """Permanently kill selected currently owned Unit objects using the normal delete command.

    The engine may refuse protected entities. This is destructive; observe later to verify.
    """
    return await asyncio.to_thread(game.delete_owned, entity_ids)


@mcp.tool()
async def delete_own_buildings(entity_ids: list[EntityId]) -> dict:
    """Permanently demolish selected currently owned Structure objects, including foundations.

    Normal engine restrictions and foundation refunds apply. Observe later to verify.
    """
    return await asyncio.to_thread(game.delete_owned, entity_ids, buildings=True)


@mcp.tool()
async def train_units(building_id: EntityId, template: str,
                      count: Annotated[int, Field(strict=True, ge=1, le=100)] = 1) -> dict:
    """Queue 1..100 units advertised by your completed building's training properties.

    Revalidates ownership and available training options in game. Native technology,
    resource, population and entity limits apply; observe the queue and spawned units.
    """
    return await asyncio.to_thread(game.train_units, building_id, template, count)


@mcp.tool()
async def research_technology(building_id: EntityId, technology: str) -> dict:
    """Queue one technology advertised by your completed building's research properties.

    Checks your current technology requirements before submission. For phase changes,
    build prerequisites first, then observe current_research and the resulting phase.
    """
    return await asyncio.to_thread(game.research_technology, building_id, technology)


@mcp.tool()
async def cancel_production(building_id: EntityId,
                            queue_id: Annotated[int, Field(strict=True, ge=0, lt=2**32)]) -> dict:
    """Cancel a current training/research queue item in your completed building.

    Use the production.queue item's id from observe_player. Normal engine refunds apply;
    a training batch is removed as a whole. Observe again if the queue changed meanwhile.
    """
    return await asyncio.to_thread(game.cancel_production, building_id, queue_id)


@mcp.tool()
async def close_game() -> dict:
    """Close only the engine processes and ENet relay created by this MCP session."""
    return await asyncio.to_thread(game.close)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
