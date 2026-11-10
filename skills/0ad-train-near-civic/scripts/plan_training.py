"""Plan bounded training from a public MCP response; never connect or execute."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path


def finite(value, name, low=0, high=1e12):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Invalid {name}")
    return value


def integer(value, name, low=1, high=10000):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"Invalid {name}")
    return value


def position(entity, size):
    point = entity.get("position")
    if not isinstance(point, dict):
        return None
    return tuple(finite(point.get(axis), f"position.{axis}", high=size) for axis in ("x", "z"))


def distance(left, right):
    return math.hypot(left[0] - right[0], left[1] - right[1])


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
    entities = obs.get("entities")
    if not isinstance(entities, list) or not all(isinstance(e, dict) for e in entities):
        raise ValueError("Invalid entities")
    ids = [integer(e.get("id"), "entity id", high=2**31 - 1) for e in entities]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate entity id")
    anchor_id = integer(req.get("civic_centre_id"), "civic_centre_id", high=2**31 - 1)
    count = integer(req.get("count"), "count")
    batch_size = integer(req.get("batch_size", 10), "batch_size", high=100)
    max_producers = integer(req.get("max_producers", 4), "max_producers", high=200)
    radius = finite(req.get("radius", 200), "radius", low=0.001, high=100000)
    template = req.get("template")
    if not isinstance(template, str) or not template.startswith("units/") or len(template) > 200:
        raise ValueError("Expected a unit template ID")
    owned = [e for e in entities if e.get("owner") == player]
    queued_unit_buildings = []
    for entity in owned:
        building = (entity.get("properties") or {}).get("building") or {}
        production = building.get("production") or {}
        queue = production.get("queue") or []
        if any(isinstance(item, dict) and isinstance(item.get("unit_template"), str) and
               item["unit_template"] and type(item.get("count")) is int and item["count"] > 0
               for item in queue):
            queued_unit_buildings.append(entity["id"])
    # Native Trainer reserves population at Item.Start, not Item.Queue. Public
    # queue entries omit started/reservation state, so even one queued unit
    # prevents treating limit-count as uncommitted capacity.
    population_reservation_uncertain = bool(queued_unit_buildings)
    centres = []
    for entity in owned:
        p = entity.get("properties") or {}
        b = p.get("building") or {}
        if "CivCentre" in (p.get("identity") or {}).get("classes", []) and b.get("foundation", True) is None:
            point = position(entity, size)
            if point:
                centres.append((entity["id"], point))
    anchor = next((point for eid, point in centres if eid == anchor_id), None)
    if anchor is None:
        raise ValueError("Anchor must be a completed owned civic centre with position")
    unit_cost = req.get("unit_cost")
    reference_seconds = 1.0
    resources_left, population_left = None, None
    population_headroom_upper_bound = None
    if unit_cost is not None:
        if not isinstance(unit_cost, dict):
            raise ValueError("Invalid unit_cost")
        unit_cost = {key: finite(unit_cost.get(key), f"unit_cost.{key}") for key in
                     ("food", "wood", "stone", "metal", "population", "time")}
        reference_seconds = max(0.001, unit_cost["time"])
        resources = obs.get("resources")
        pop = (obs.get("scoreboard") or {}).get("population")
        if not isinstance(resources, dict) or not isinstance(pop, dict):
            raise ValueError("Missing public resource or population budget")
        resources_left = {key: finite(resources.get(key), f"resources.{key}") for key in
                          ("food", "wood", "stone", "metal")}
        population_left = max(0, min(finite(pop.get("limit"), "population limit"),
                                     finite(pop.get("max"), "population max")) -
                              finite(pop.get("count"), "population count"))
        population_headroom_upper_bound = population_left
    candidates, excluded = [], []
    for entity in owned:
        properties = entity.get("properties") or {}
        building = properties.get("building") or {}
        if template not in (building.get("training") or {}).get("entities", []):
            continue
        point = position(entity, size)
        if point is None or building.get("foundation", True) is not None:
            continue
        dist = distance(anchor, point)
        if dist > radius or min(centres, key=lambda c: (distance(c[1], point), c[0]))[0] != anchor_id:
            continue
        production = building.get("production")
        reason = None
        if not isinstance(production, dict) or not isinstance(production.get("queue"), list):
            reason = "unknown_queue"
        elif production.get("autoqueue") is not False:
            reason = "autoqueue_or_unknown"
        elif len(production["queue"]) >= 16:
            reason = "queue_may_be_truncated"
        elif any(item.get("paused") is not False for item in production["queue"]):
            reason = "paused_or_unknown_queue"
        if reason:
            excluded.append({"id": entity["id"], "reason": reason})
            continue
        remaining = 0.0
        for item in production["queue"]:
            remaining += finite(item.get("time_remaining"), "queue time_remaining") / 1000
        candidates.append({"id": entity["id"], "distance": round(dist, 3),
                           "known_queue_seconds": remaining,
                           "queue_entries": len(production["queue"]),
                           "assigned": 0, "planned_batches": 0})
    candidates.sort(key=lambda c: (c["distance"], c["id"]))
    candidates = candidates[:max_producers]
    actions, remaining_count = [], count
    while remaining_count and candidates and len(actions) < 100:
        eligible = [c for c in candidates if c["queue_entries"] + c["planned_batches"] < 16]
        if not eligible:
            break
        selected = min(eligible, key=lambda c: (
            c["known_queue_seconds"] + c["assigned"] * reference_seconds,
            c["queue_entries"] + c["planned_batches"], c["distance"], c["id"]))
        amount = min(remaining_count, batch_size)
        if unit_cost is not None:
            for resource, stock in resources_left.items():
                if unit_cost[resource] > 0:
                    amount = min(amount, math.floor(stock / unit_cost[resource]))
            if unit_cost["population"] > 0:
                amount = min(amount, math.floor(population_left / unit_cost["population"]))
        if amount <= 0:
            break
        actions.append({"tool": "train_units", "arguments": {
            "building_id": selected["id"], "template": template, "count": amount}})
        selected["assigned"] += amount
        selected["planned_batches"] += 1
        remaining_count -= amount
        if unit_cost is not None:
            for resource in resources_left:
                resources_left[resource] -= unit_cost[resource] * amount
            population_left -= unit_cost["population"] * amount
    budget_status = ("needs_unit_cost" if unit_cost is None else
                     "needs_queue_reservation_review" if population_reservation_uncertain else
                     "reference_budget_only")
    return {"mode": "public-mcp-plan-only", "observation_time": game_time,
            "requires_live_revalidation": True, "cost_preflight_missing": unit_cost is None,
            "budget_status": budget_status,
            "population_reservation_uncertain": population_reservation_uncertain,
            "population_headroom_upper_bound": population_headroom_upper_bound,
            "queued_unit_building_ids": queued_unit_buildings,
            "budget_note": "limit-count is current upper bound; queued batches may have unreserved commitments. Verify before submitting.",
            "time_estimate": "Single-template proxy; not native batch ETA or optimal scheduling",
            "planned_count": count - remaining_count, "unplanned_count": remaining_count,
            "candidates": candidates, "excluded": excluded, "actions": actions}


def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError("Usage: python plan_training.py request.json (or - for stdin)")
        raw = sys.stdin.read() if sys.argv[1] == "-" else Path(sys.argv[1]).read_text(encoding="utf-8-sig")
        result = plan(json.loads(raw))
        print(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2))
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
