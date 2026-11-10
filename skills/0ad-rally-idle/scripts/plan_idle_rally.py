"""Select idle units from a public MCP response and emit move plans only."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path


CATEGORIES = {"all", "citizen", "civilian", "cavalry", "champion"}


def finite(value, name, low=0, high=1e12):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Invalid {name}")
    return value


def integer(value, name, low=1, high=2**31 - 1):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"Invalid {name}")
    return value


def point(raw, size, name):
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid {name}")
    return {axis: finite(raw.get(axis), f"{name}.{axis}", high=size) for axis in ("x", "z")}


def matches(properties, category):
    identity = properties.get("identity") or {}
    classes = set(identity.get("classes") or [])
    roles = set(identity.get("special_roles") or [])
    if category == "all":
        return True
    if category == "citizen":
        return "CitizenSoldier" in classes
    if category == "cavalry":
        return "Cavalry" in classes
    if category == "champion":
        return "Champion" in classes or "champion" in roles
    excluded = {"CitizenSoldier", "Champion", "Cavalry", "Hero", "Animal", "Elephant", "Ship", "Siege"}
    return not classes.intersection(excluded) and (
        bool(classes.intersection({"Civilian", "FemaleCitizen"})) or
        "Worker" in classes and (properties.get("capabilities") or {}).get("gather") is True)


def plan(payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected observation and request objects")
    obs, req = payload.get("observation"), payload.get("request")
    if not isinstance(obs, dict) or obs.get("policy") != "own-player-only-v1":
        raise ValueError("Use a public observe_player result")
    if not isinstance(req, dict):
        raise ValueError("Missing request")
    player = integer(obs.get("player"), "player", high=8)
    game_time = finite(obs.get("time"), "time")
    size = finite(obs.get("mapSize"), "mapSize", low=1, high=100000)
    destination = point(req, size, "destination")
    category = req.get("category", "all")
    if category not in CATEGORIES:
        raise ValueError("category must be all/citizen/civilian/cavalry/champion")
    excluded = req.get("exclude_ids", [])
    if not isinstance(excluded, list):
        raise ValueError("Invalid exclude_ids")
    excluded = {integer(eid, "exclude id") for eid in excluded}
    limit = req.get("limit")
    if limit is not None:
        limit = integer(limit, "limit", high=100000)
    source = req.get("source")
    if source is not None:
        source = {**point(source, size, "source"),
                  "radius": finite(source.get("radius"), "source.radius", low=0.001, high=100000)}
    entities = obs.get("entities")
    if not isinstance(entities, list) or not all(isinstance(e, dict) for e in entities):
        raise ValueError("Invalid entities")
    seen, selected = set(), []
    for entity in entities:
        eid = integer(entity.get("id"), "entity id")
        if eid in seen:
            raise ValueError("Duplicate entity id")
        seen.add(eid)
        p = entity.get("properties") or {}
        classes = (p.get("identity") or {}).get("classes") or []
        activity, holder = p.get("activity"), p.get("garrisoned_in")
        packing = p.get("packing")
        outside_garrison = (
            isinstance(holder, dict) and "holder_id" in holder and holder["holder_id"] is None or
            "garrisoned_in" in p and holder is None and
            (p.get("capabilities") or {}).get("garrison") is False)
        if (entity.get("owner") != player or eid in excluded or "Unit" not in classes or
                entity.get("movable") is not True or (p.get("capabilities") or {}).get("move") is not True or
                not isinstance(activity, dict) or activity.get("idle") is not True or
                not isinstance(activity.get("state"), str) or not activity["state"].endswith(".IDLE") or
                "current_target" not in p or p["current_target"] is not None or
                not outside_garrison or
                packing is not None and (not isinstance(packing, dict) or packing.get("changing") is not False) or
                not isinstance(entity.get("position"), dict) or not matches(p, category)):
            continue
        pos = point(entity["position"], size, "unit position")
        if source is not None and math.hypot(pos["x"] - source["x"], pos["z"] - source["z"]) > source["radius"]:
            continue
        special = sorted(set((p.get("identity") or {}).get("special_roles") or []))
        selected.append({"id": eid, "distance": math.hypot(pos["x"] - destination["x"], pos["z"] - destination["z"]),
                         "special_roles": special})
    selected.sort(key=lambda entry: (entry["distance"], entry["id"]))
    if limit is not None:
        selected = selected[:limit]
    ids = [entry["id"] for entry in selected]
    actions = [{"tool": "move_units", "arguments": {"entity_ids": ids[start:start + 200],
                **destination, "queued": False}} for start in range(0, len(ids), 200)]
    return {"mode": "public-mcp-plan-only", "observation_time": game_time,
            "requires_live_revalidation": True, "category": category, "selected_count": len(ids),
            "selected": selected, "actions": actions}


def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError("Usage: python plan_idle_rally.py request.json (or - for stdin)")
        raw = sys.stdin.read() if sys.argv[1] == "-" else Path(sys.argv[1]).read_text(encoding="utf-8-sig")
        print(json.dumps(plan(json.loads(raw)), ensure_ascii=True, allow_nan=False, indent=2))
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
