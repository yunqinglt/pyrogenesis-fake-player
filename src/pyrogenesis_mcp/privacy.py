"""Fail-closed projection of the game bridge into the model's observation.

Never return the bridge JSON unchanged.  In particular, other players' resources,
commands, hidden entities, templates, sync state and arbitrary errors are absent.
"""

from __future__ import annotations

import math


class ObservationError(ValueError):
    pass


def number(value, *, low=0, high=10**12):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ObservationError("Invalid numeric observation")
    if not low <= value <= high or not math.isfinite(value):
        raise ObservationError("Numeric observation outside supported range")
    return value


def _scoreboard(raw):
    """Validate the small, own-player-only live scoreboard allowlist."""
    if not isinstance(raw, dict):
        raise ObservationError("Invalid player scoreboard")
    result = {}
    phase = raw.get("phase")
    if phase is not None and phase not in ("village", "town", "city"):
        raise ObservationError("Invalid player phase")
    result["phase"] = phase

    population = raw.get("population")
    if not isinstance(population, dict):
        raise ObservationError("Invalid scoreboard population")
    if any(key not in ("count", "limit", "max") for key in population):
        raise ObservationError("Invalid scoreboard population fields")
    result["population"] = {key: number(population[key], high=100000)
                            for key in ("count", "limit", "max") if key in population}

    def resources(record, description):
        if not isinstance(record, dict):
            raise ObservationError(f"Invalid scoreboard {description}")
        return {key: number(record[key]) for key in ("food", "wood", "stone", "metal") if key in record}

    result["resources"] = resources(raw.get("resources", {}), "resources")
    result["gatherers"] = {key: number(value, high=100000)
                           for key, value in resources(raw.get("gatherers", {}), "gatherers").items()}

    idle = raw.get("idle_units")
    if not isinstance(idle, dict):
        raise ObservationError("Invalid idle unit scoreboard")
    if any(key not in ("count", "worker_count") for key in idle):
        raise ObservationError("Invalid idle unit scoreboard fields")
    result["idle_units"] = {key: number(idle[key], high=20000)
                            for key in ("count", "worker_count") if key in idle}
    if "territory_percent" in raw:
        value = raw["territory_percent"]
        result["territory_percent"] = None if value is None else number(value, high=100)

    def text(value, description, limit=200):
        if not isinstance(value, str) or not value or len(value) > limit:
            raise ObservationError(f"Invalid scoreboard {description}")
        return value

    def optional_number(record, key, *, high=10**12):
        value = record.get(key)
        return None if value is None else number(value, high=high)

    research = raw.get("current_research", [])
    if not isinstance(research, list) or len(research) > 64:
        raise ObservationError("Invalid current research list")
    safe_research = []
    for item in research:
        if not isinstance(item, dict):
            raise ObservationError("Invalid current research item")
        if any(key not in ("technology", "progress", "time_remaining_ms", "researcher_id", "paused")
               for key in item):
            raise ObservationError("Invalid current research fields")
        researcher = item.get("researcher_id")
        if researcher is not None and (type(researcher) is not int or not 0 < researcher < 2**32):
            raise ObservationError("Invalid research entity identity")
        progress = optional_number(item, "progress", high=1)
        paused = item.get("paused")
        if type(paused) is not bool:
            raise ObservationError("Invalid research paused flag")
        safe_research.append({
            "technology": text(item.get("technology"), "technology"),
            "progress": progress,
            "time_remaining_ms": optional_number(item, "time_remaining_ms"),
            "researcher_id": researcher,
            "paused": paused,
        })
    result["current_research"] = safe_research

    def buildings(records, description, with_queue=False):
        if not isinstance(records, list) or len(records) > 2000:
            raise ObservationError(f"Invalid scoreboard {description}")
        safe = []
        seen = set()
        for item in records:
            if not isinstance(item, dict):
                raise ObservationError(f"Invalid scoreboard {description} item")
            allowed = {"id", "template", "queue"} if with_queue else {"id", "template", "progress", "builders"}
            if any(key not in allowed for key in item):
                raise ObservationError(f"Invalid scoreboard {description} fields")
            entity_id = item.get("id")
            if type(entity_id) is not int or not 0 < entity_id < 2**32 or entity_id in seen:
                raise ObservationError("Invalid scoreboard building identity")
            seen.add(entity_id)
            value = {"id": entity_id, "template": text(item.get("template"), "building template")}
            if with_queue:
                queue = item.get("queue")
                if not isinstance(queue, list) or not 1 <= len(queue) <= 16:
                    raise ObservationError("Invalid building production queue")
                safe_queue = []
                for queued in queue:
                    if not isinstance(queued, dict):
                        raise ObservationError("Invalid building production item")
                    if any(key not in ("template_name", "unit_template", "technology_template", "count",
                                       "progress", "time_remaining_ms", "paused") for key in queued):
                        raise ObservationError("Invalid building production fields")
                    count = optional_number(queued, "count", high=100000)
                    paused = queued.get("paused")
                    if type(paused) is not bool:
                        raise ObservationError("Invalid production paused flag")
                    safe_queue.append({
                        key: None if queued.get(key) is None else text(queued[key], "production template")
                        for key in ("template_name", "unit_template", "technology_template")
                    } | {
                        "count": count,
                        "progress": optional_number(queued, "progress", high=1),
                        "time_remaining_ms": optional_number(queued, "time_remaining_ms"),
                        "paused": paused,
                    })
                value["queue"] = safe_queue
            else:
                builders = item.get("builders")
                if type(builders) is not int or not 0 <= builders <= 20000:
                    raise ObservationError("Invalid construction builder count")
                value["progress"] = optional_number(item, "progress", high=1)
                value["builders"] = builders
            safe.append(value)
        return safe

    result["construction"] = buildings(raw.get("construction", []), "construction")
    result["busy_buildings"] = buildings(raw.get("busy_buildings", []), "busy buildings", with_queue=True)
    return result


def project_observation(raw: dict) -> dict:
    """Accept only explicitly owned entities, with an allowlist of UI fields."""
    if not isinstance(raw, dict):
        raise ObservationError("Invalid observation")
    player = raw.get("player")
    if isinstance(player, bool) or not isinstance(player, int) or not 1 <= player <= 8:
        raise ObservationError("Observation must belong to a playing participant")
    result = {"player": player, "entities": []}
    if "outcome" in raw:
        outcome = raw["outcome"]
        if outcome not in ("unknown", "active", "won", "defeated"):
            raise ObservationError("Invalid player outcome")
        result["outcome"] = outcome
    if "game_over" in raw:
        game_over = raw["game_over"]
        if game_over is not None and type(game_over) is not bool:
            raise ObservationError("Invalid game-over status")
        result["gameOver"] = game_over
    if "time" in raw:
        result["time"] = number(raw["time"])
    resources = raw.get("resources", {})
    if not isinstance(resources, dict):
        raise ObservationError("Invalid resources")
    result["resources"] = {key: number(resources[key]) for key in ("food", "wood", "stone", "metal")
                           if key in resources}
    for key in ("population", "populationLimit"):
        if key in raw:
            result[key] = number(raw[key], high=100000)
    if "scoreboard" in raw:
        result["scoreboard"] = _scoreboard(raw["scoreboard"])
    entities = raw.get("entities", [])
    if not isinstance(entities, list) or len(entities) > 20000:
        raise ObservationError("Invalid entity list")
    seen = set()
    for item in entities:
        if not isinstance(item, dict) or type(item.get("owner")) is not int or item["owner"] != player:
            # Exclude the entire entity; never copy unknown fields.
            continue
        eid = item.get("id")
        if isinstance(eid, bool) or not isinstance(eid, int) or not 0 < eid < 2**32 or eid in seen:
            raise ObservationError("Invalid entity identity")
        seen.add(eid)
        safe = {"id": eid, "owner": player, "movable": item.get("movable") is True}
        if isinstance(item.get("template"), str):
            safe["template"] = item["template"][:200]
        position = item.get("position")
        if isinstance(position, dict) and "x" in position and "z" in position:
            safe["position"] = {axis: number(position[axis], low=-10000, high=100000)
                                for axis in ("x", "z")}
        for key in ("hitpoints", "maxHitpoints"):
            if key in item:
                safe[key] = number(item[key])
        if "properties" in item:
            # Imported here because the property validator shares the numeric
            # and error definitions above. Never copy its raw nested graph.
            from .properties import project_properties
            safe["properties"] = project_properties(item["properties"], private=True)
        result["entities"].append(safe)
    return result


def validate_entities(entity_ids: list[int], observation: dict) -> list[int]:
    if not isinstance(entity_ids, list) or not 1 <= len(entity_ids) <= 200:
        raise ValueError("Choose between 1 and 200 of your observed units")
    allowed = {item["id"] for item in observation["entities"] if item["movable"]}
    if any(isinstance(eid, bool) or not isinstance(eid, int) or eid not in allowed for eid in entity_ids):
        raise ValueError("Only your currently observed movable units can be commanded")
    return list(dict.fromkeys(entity_ids))
