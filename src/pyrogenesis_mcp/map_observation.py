"""Bounded, allowlisted map observations from the trusted player-view bridge.

The companion must perform LOS checks before writing data, and must supply fog
records from its historical cache rather than live simulation entities.  This
module validates that contract and strips all non-map fields.  It cannot infer
historical provenance from a single JSON frame.
"""

from __future__ import annotations

import math
import re

from .privacy import ObservationError, number


MAP_POLICY = "player-los-map-v1"
MAX_CELLS = 4096
MAX_OBJECTS = 2000
REGION_KEYS = ("x_min", "z_min", "x_max", "z_max", "cell_size")
OBJECT_KINDS = frozenset({"building", "tree", "resource", "treasure", "unit", "other"})
RESOURCE_TYPES = frozenset({"food", "wood", "stone", "metal"})
RESOURCE_SUBTYPE = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


def validate_region(x_min, z_min, x_max, z_max, cell_size, map_size) -> dict:
    """Validate world-coordinate bounds and cap the requested sampling work."""
    size = number(map_size, high=100000)
    if size <= 0:
        raise ObservationError("Invalid map size")
    request = {
        "x_min": number(x_min, high=size),
        "z_min": number(z_min, high=size),
        "x_max": number(x_max, high=size),
        "z_max": number(z_max, high=size),
        "cell_size": number(cell_size, low=4, high=256),
    }
    if request["x_min"] >= request["x_max"] or request["z_min"] >= request["z_max"]:
        raise ObservationError("Map region must have positive area")
    columns, rows = _dimensions(request)
    if columns * rows > MAX_CELLS:
        raise ObservationError("Map region exceeds the sample limit; use a larger cell size")
    return request


def _dimensions(request: dict) -> tuple[int, int]:
    return (
        math.ceil((request["x_max"] - request["x_min"]) / request["cell_size"]),
        math.ceil((request["z_max"] - request["z_min"]) / request["cell_size"]),
    )


def _center(request: dict, axis: str, index: int):
    start = request[f"{axis}_min"] + index * request["cell_size"]
    end = min(start + request["cell_size"], request[f"{axis}_max"])
    return (start + end) / 2


def _boolean(value) -> bool:
    if type(value) is not bool:
        raise ObservationError("Invalid map visibility flag")
    return value


def _integer(value, *, low, high, description):
    if type(value) is not int or not low <= value <= high:
        raise ObservationError(f"Invalid map {description}")
    return value


def _last_seen(value, time):
    return number(value, high=time)


def _optional_number(value, *, low=0, high=10**12):
    return None if value is None else number(value, low=low, high=high)


def _terrain(raw, time) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ObservationError("Invalid map terrain")
    return {
        "height": number(raw.get("height"), low=-100000, high=100000),
        "water_level": _optional_number(raw.get("water_level"), low=-100000, high=100000),
        "last_seen": _last_seen(raw.get("last_seen"), time),
    }


def _object(raw, request: dict, time, seen: set) -> dict | None:
    if not isinstance(raw, dict):
        raise ObservationError("Invalid map object")
    visible = _boolean(raw.get("currently_visible"))
    static = _boolean(raw.get("static"))
    # Never return mobile-unit history under fog.  Hidden objects have no
    # schema here; the GUI may only send observed static historical snapshots.
    if not visible and not static:
        return None
    eid = _integer(raw.get("id"), low=1, high=2**32 - 1, description="object identity")
    if eid in seen:
        raise ObservationError("Duplicate map object identity")
    seen.add(eid)
    owner = _integer(raw.get("owner"), low=-1, high=8, description="object owner")
    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in OBJECT_KINDS:
        raise ObservationError("Invalid map object kind")
    if kind == "unit" and not visible:
        return None
    template = raw.get("template")
    if not isinstance(template, str) or not template or len(template) > 200:
        raise ObservationError("Invalid map object template")
    position = raw.get("position")
    if not isinstance(position, dict):
        raise ObservationError("Invalid map object position")
    pos = {
        axis: number(position.get(axis), low=request[f"{axis}_min"], high=request[f"{axis}_max"])
        for axis in ("x", "z")
    }
    if any(pos[axis] >= request[f"{axis}_max"] for axis in ("x", "z")):
        raise ObservationError("Map object lies outside the half-open region")
    last_seen = _last_seen(raw.get("last_seen"), time)
    if visible and last_seen != time:
        raise ObservationError("Visible map object has an invalid observation time")
    result = {
        "id": eid,
        "owner": owner,
        "kind": kind,
        "template": template,
        "position": pos,
        "currently_visible": visible,
        "last_seen": last_seen,
        "static": static,
    }
    if "hitpoints" in raw:
        result["hitpoints"] = number(raw["hitpoints"])
    if "resource" in raw:
        resource = raw["resource"]
        resource_type = resource.get("type") if isinstance(resource, dict) else None
        if not isinstance(resource_type, str):
            raise ObservationError("Invalid map resource")
        parts = resource_type.split(".")
        if (parts[0] not in RESOURCE_TYPES or len(parts) > 2 or
                len(parts) == 2 and not RESOURCE_SUBTYPE.fullmatch(parts[1])):
            raise ObservationError("Invalid map resource")
        result["resource"] = {
            "type": resource["type"],
            "amount": number(resource.get("amount")),
            "max": _optional_number(resource.get("max")),
        }
    if "classes" in raw:
        classes = raw["classes"]
        if (not isinstance(classes, list) or len(classes) > 64 or
                any(not isinstance(item, str) or len(item) > 80 for item in classes)):
            raise ObservationError("Invalid map object classes")
        result["classes"] = list(classes)
    if "properties" in raw:
        from .properties import project_properties
        # Map profiles never carry orders, cargo, or occupant identities, even
        # for an owned object. The dedicated own-player view carries those.
        result["properties"] = project_properties(raw["properties"], private=False)
    return result


def project_map_region(raw: dict, request: dict, player: int, map_size, *, include_objects=True) -> dict:
    """Reconstruct a map response; never copy arbitrary engine data to MCP."""
    if type(include_objects) is not bool:
        raise ObservationError("Invalid map object selection flag")
    if not isinstance(request, dict) or set(request) != set(REGION_KEYS):
        raise ObservationError("Invalid map region request")
    region = validate_region(**request, map_size=map_size)
    player = _integer(player, low=1, high=8, description="player")
    if (not isinstance(raw, dict) or raw.get("policy") != MAP_POLICY or
            type(raw.get("player")) is not int or raw["player"] != player or
            raw.get("region") != region):
        raise ObservationError("Map observation does not match the requested player region")
    time = number(raw.get("time"))
    columns, rows = _dimensions(region)
    if (type(raw.get("columns")) is not int or raw["columns"] != columns or
            type(raw.get("rows")) is not int or raw["rows"] != rows):
        raise ObservationError("Invalid map grid dimensions")
    cells = raw.get("cells")
    if not isinstance(cells, list) or len(cells) != columns * rows:
        raise ObservationError("Invalid map cell list")
    safe_cells = []
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            raise ObservationError("Invalid map cell")
        x, z = _center(region, "x", index % columns), _center(region, "z", index // columns)
        if number(cell.get("x"), high=map_size) != x or number(cell.get("z"), high=map_size) != z:
            raise ObservationError("Map sample does not match the requested grid")
        in_bounds = _boolean(cell.get("in_bounds"))
        explored = _boolean(cell.get("explored"))
        visible = _boolean(cell.get("currently_visible"))
        if visible and not explored or not in_bounds and (visible or explored):
            raise ObservationError("Contradictory map visibility flags")
        # Unknown terrain is discarded without inspecting any injected payload.
        terrain = _terrain(cell.get("terrain"), time) if explored and in_bounds else None
        if visible and terrain is not None and terrain["last_seen"] != time:
            raise ObservationError("Visible terrain has an invalid observation time")
        safe_cells.append({
            "x": x, "z": z, "in_bounds": in_bounds,
            "explored": explored, "currently_visible": visible, "terrain": terrain,
        })
    objects = raw.get("objects")
    if not isinstance(objects, list) or len(objects) > MAX_OBJECTS:
        raise ObservationError("Invalid map object list")
    if not include_objects and (objects or raw.get("truncated") is not False):
        raise ObservationError("Terrain-only map response contains objects")
    safe_objects = []
    seen = set()
    for item in objects:
        projected = _object(item, region, time, seen)
        if projected is not None:
            safe_objects.append(projected)
    return {
        "policy": MAP_POLICY, "player": player, "time": time,
        "region": region, "columns": columns, "rows": rows,
        "cells": safe_cells, "objects": safe_objects,
        "truncated": _boolean(raw.get("truncated")),
    }
