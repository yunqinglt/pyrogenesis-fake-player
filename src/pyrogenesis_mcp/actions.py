"""Small player-command schemas; native GUI code revalidates targets at issue time."""

from __future__ import annotations

import re
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .privacy import number, validate_entities


STANCES = frozenset({"violent", "aggressive", "defensive", "passive", "standground", "skittish"})
TARGET_ORDERS = frozenset({"attack", "capture", "gather", "repair", "garrison", "heal",
                           "guard", "collect_treasure", "return_resource", "trade", "occupy_turret"})
POSITION_ORDERS = frozenset({"patrol", "attack_move"})
RALLY_TARGET_ORDERS = frozenset({"attack", "capture", "gather", "repair", "garrison",
                                "occupy_turret", "trade", "collect_treasure"})
RALLY_POSITION_ORDERS = frozenset({"move", "patrol", "attack_move"})


class TargetOrder(BaseModel):
    """An explicit target must be currently visible when the GUI executes it."""

    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["attack", "capture", "gather", "repair", "garrison", "heal", "guard",
                  "collect_treasure", "return_resource", "trade", "occupy_turret"]
    target: int = Field(strict=True, gt=0, lt=2**31)


class BuildOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    type: Literal["build"]
    template: str = Field(min_length=1, max_length=200)
    x: float
    z: float
    angle: float = 0


class PositionOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    type: Literal["patrol", "attack_move"]
    x: float
    z: float


class RallyTargetOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["attack", "capture", "gather", "repair", "garrison", "occupy_turret",
                  "trade", "collect_treasure"]
    target: int = Field(strict=True, gt=0, lt=2**31)


class RallyPositionOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    type: Literal["move", "patrol", "attack_move"]
    x: float
    z: float


def validate_rally_orders(orders, map_size: float) -> list[dict]:
    if not isinstance(orders, list) or not 1 <= len(orders) <= 100:
        raise ValueError("Choose between one and 100 rally orders")
    safe = []
    for order in orders:
        if isinstance(order, (RallyTargetOrder, RallyPositionOrder)):
            order = order.model_dump()
        if not isinstance(order, dict) or not isinstance(order.get("type"), str):
            raise ValueError("Invalid rally order")
        kind = order["type"]
        if kind in RALLY_TARGET_ORDERS:
            if set(order) != {"type", "target"} or type(order["target"]) is not int or not 0 < order["target"] < 2**31:
                raise ValueError("Invalid rally target")
            safe.append({"type": kind, "target": order["target"]})
        elif kind in RALLY_POSITION_ORDERS:
            if set(order) != {"type", "x", "z"}:
                raise ValueError("Invalid rally position fields")
            safe.append({"type": kind, "x": number(order["x"], high=map_size),
                         "z": number(order["z"], high=map_size)})
        else:
            raise ValueError("Unsupported rally order")
    return safe


def validate_rally_selection(entity_ids, observation, *, additional: int = 0, queued: bool = False) -> list[int]:
    """RallyPoint also belongs to some immobile buildings and mobile holders."""
    if not isinstance(entity_ids, list) or not 1 <= len(entity_ids) <= 200:
        raise ValueError("Choose between one and 200 rally point owners")
    if type(queued) is not bool:
        raise ValueError("queued must be a boolean")
    by_id = {item["id"]: item for item in observation["entities"]}
    for entity_id in entity_ids:
        entity = by_id.get(entity_id) if type(entity_id) is int else None
        profile = (entity.get("properties") or {}) if entity else {}
        rally = profile.get("rally_point")
        if (not entity or entity.get("owner") != observation["player"] or
                not isinstance(rally, dict) or
                (profile.get("building") or {}).get("foundation") is not None):
            raise ValueError("Select completed owned entities with a rally point capability")
        if queued and (rally.get("truncated") is not False or
                       not isinstance(rally.get("points"), list) or
                       len(rally["points"]) + additional > 100):
            raise ValueError("The appended rally queue would exceed 100 points or is truncated")
    return list(dict.fromkeys(entity_ids))


def validate_orders(orders, map_size: float) -> list[dict]:
    """Reject an entire batch before writing anything to the file bridge."""
    if not isinstance(orders, list) or not 1 <= len(orders) <= 100:
        raise ValueError("Choose between one and 100 orders")
    safe = []
    for order in orders:
        if isinstance(order, (TargetOrder, BuildOrder, PositionOrder)):
            order = order.model_dump()
        if not isinstance(order, dict):
            raise ValueError("Each order must be a structured target, position or build order")
        kind = order.get("type")
        if not isinstance(kind, str):
            raise ValueError("Unsupported unit order")
        if kind in TARGET_ORDERS:
            if set(order) != {"type", "target"}:
                raise ValueError("Invalid target order fields")
            target = order["target"]
            if type(target) is not int or not 0 < target < 2**31:
                raise ValueError("Invalid target identity")
            safe.append({"type": kind, "target": target})
        elif kind in POSITION_ORDERS:
            if set(order) != {"type", "x", "z"}:
                raise ValueError("Invalid position order fields")
            safe.append({"type": kind, "x": number(order["x"], high=map_size),
                         "z": number(order["z"], high=map_size)})
        elif kind == "build":
            if not {"type", "template", "x", "z"}.issubset(order) or set(order) - {
                "type", "template", "x", "z", "angle"
            }:
                raise ValueError("Invalid build order fields")
            template = order["template"]
            if (not isinstance(template, str) or not 1 <= len(template) <= 200 or
                    any(character in template for character in "\x00\r\n") or
                    not template.startswith("structures/") or ".." in template.split("/")):
                raise ValueError("Invalid building template")
            safe.append({"type": kind, "template": template,
                         "x": number(order["x"], high=map_size),
                         "z": number(order["z"], high=map_size),
                         "angle": number(order.get("angle", 0), low=-math.tau, high=math.tau)})
        else:
            raise ValueError("Unsupported unit order")
    return safe


def validate_owned_selection(entity_ids, observation, *, entity_class: str | None = None) -> list[int]:
    if entity_class is None:
        return validate_entities(entity_ids, observation)
    if not isinstance(entity_ids, list) or not 1 <= len(entity_ids) <= 200:
        raise ValueError("Choose between one and 200 owned entities")
    allowed = {
        item["id"] for item in observation["entities"]
        if item.get("owner") == observation["player"] and
        entity_class in item.get("properties", {}).get("identity", {}).get("classes", [])
    }
    if any(type(entity_id) is not int or entity_id not in allowed for entity_id in entity_ids):
        raise ValueError("Select only currently observed owned entities of the requested class")
    return list(dict.fromkeys(entity_ids))


def validate_packing(entity_ids, observation, *, packed=None, cancel=False) -> list[int]:
    selected = validate_owned_selection(entity_ids, observation, entity_class="Unit")
    if not cancel and type(packed) is not bool:
        raise ValueError("packed must be a boolean")
    by_id = {item["id"]: item for item in observation["entities"]}
    for entity_id in selected:
        packing = by_id[entity_id].get("properties", {}).get("packing")
        if (not isinstance(packing, dict) or type(packing.get("packed")) is not bool or
                type(packing.get("changing")) is not bool):
            raise ValueError("Select only owned units with a packing capability")
        if cancel:
            if not packing["changing"]:
                raise ValueError("The selected units are not changing packing state")
        elif packing["changing"] or packing["packed"] == packed:
            raise ValueError("The selected units are changing or already in the requested state")
    return selected


def validate_unload(holder_id, entity_ids, observation) -> list[int]:
    if type(holder_id) is not int or not 0 < holder_id < 2**31:
        raise ValueError("Invalid garrison holder identity")
    holder = next((item for item in observation["entities"] if item["id"] == holder_id and
                   item.get("owner") == observation["player"]), None)
    garrison = holder.get("properties", {}).get("garrison") if holder else None
    if not isinstance(garrison, dict) or not isinstance(garrison.get("occupants"), list):
        raise ValueError("Select a currently owned garrison holder")
    occupants = set(garrison["occupants"])
    if entity_ids is None:
        entity_ids = [item["id"] for item in observation["entities"]
                      if item.get("owner") == observation["player"] and item["id"] in occupants]
    selected = validate_owned_selection(entity_ids, observation, entity_class="Unit")
    by_id = {item["id"]: item for item in observation["entities"]}
    for entity_id in selected:
        garrisoned = by_id[entity_id].get("properties", {}).get("garrisoned_in") or {}
        if entity_id not in occupants or garrisoned.get("holder_id") != holder_id:
            raise ValueError("Select only your current occupants of this garrison holder")
    return selected


def validate_production_cancel(building_id, queue_id, observation) -> int:
    if (type(building_id) is not int or not 0 < building_id < 2**31 or
            type(queue_id) is not int or not 0 <= queue_id < 2**32):
        raise ValueError("Invalid building or production queue identity")
    entity = next((item for item in observation["entities"] if item["id"] == building_id and
                   item.get("owner") == observation["player"]), None)
    profile = entity.get("properties", {}) if entity else {}
    building = profile.get("building") or {}
    production = building.get("production") or {}
    if ("Structure" not in profile.get("identity", {}).get("classes", []) or
            building.get("is_building") is not True or building.get("foundation") is not None or
            not any(item.get("id") == queue_id for item in production.get("queue", []))):
        raise ValueError("Select a current queue item in your completed production building")
    return building_id


def validate_production(building_id, template, observation, *, research: bool = False) -> int:
    """Only completed, owned producers may issue their advertised options."""
    if (type(building_id) is not int or not 0 < building_id < 2**31 or
            not isinstance(template, str) or len(template) > 200 or
            re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", template) is None):
        raise ValueError("Invalid production building or template")
    entity = next((item for item in observation["entities"] if item["id"] == building_id and
                   item["owner"] == observation["player"]), None)
    profile = (entity.get("properties") or {}) if entity else {}
    building = profile.get("building") or {}
    if ("Structure" not in profile.get("identity", {}).get("classes", []) or
            building.get("is_building") is not True or building.get("foundation") is not None or
            building.get("production") is None):
        raise ValueError("Select a completed, currently owned production building")
    if research:
        options = building.get("research") or {}
        allowed = list(options.get("technologies", []))
        for pair in options.get("pairs", []):
            allowed.extend((pair.get("top"), pair.get("bottom")))
    else:
        allowed = (building.get("training") or {}).get("entities", [])
    if template not in allowed:
        raise ValueError("The building does not offer this production template")
    return building_id
