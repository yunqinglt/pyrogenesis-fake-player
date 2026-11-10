"""Evidence-based analysis of a decoded, omniscient saved simulation."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import math

from .serialization import HOLE, UNDEFINED


DEFAULT_RULES = {
    "schema_version": 1, "opponents": "human_vs_ai",
    "visibility_modes": ["fog_unexplored", "fog_explored", "revealed"],
    "ceasefire_minutes": 45, "victory_conditions": ["conquest_units"],
    "victory_unit_expression": "Unit+!Animal",
}
RESOURCES = ("food", "wood", "stone", "metal")


def mapping(value):
    return value if isinstance(value, dict) else {}


def sequence(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and value.get("$type", value.get("__js_type__")) == "Set":
        return value.get("values", [])
    return []


def number(value):
    if type(value) is int:
        return value
    return value if type(value) is float and math.isfinite(value) else None


def first_number(data, *keys):
    return next((number(data[key]) for key in keys if number(data.get(key)) is not None), None)


def strings(value):
    return [item for item in sequence(value) if isinstance(item, str)]


def clean(value, depth=0, seen=None):
    """Keep report JSON finite and bounded, including cyclic JS objects."""
    if seen is None:
        seen = set()
    if value is UNDEFINED or value is HOLE:
        return {"$type": value.name}
    if value is None or type(value) in (bool, str, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"non_finite": str(value)}
    if depth > 12:
        return {"truncated": "depth"}
    if isinstance(value, (dict, list)):
        if id(value) in seen:
            return {"reference": "cycle"}
        seen = seen | {id(value)}
        if isinstance(value, list):
            return [clean(item, depth + 1, seen) for item in value[:1000]]
        return {str(key): clean(item, depth + 1, seen) for key, item in list(value.items())[:1000]}
    return {"unsupported": type(value).__name__}


def visibility_mode(settings):
    reveal, explore = settings.get("RevealMap"), settings.get("ExploreMap")
    if reveal is True:
        return "revealed"
    if reveal is False and type(explore) is bool:
        return "fog_explored" if explore else "fog_unexplored"
    return "unknown"


def audit_rules(metadata, rules):
    settings = mapping(mapping(metadata.get("initAttributes")).get("settings"))
    players = sequence(settings.get("PlayerData"))
    configured = [item for index, item in enumerate(players)
                  if index > 0 and isinstance(item, dict) and not item.get("Removed")]
    ai = sum(bool(item.get("AI")) for item in configured)
    humans = len(configured) - ai
    opponents = "human_vs_ai" if humans == 1 and ai == 1 else (
        "human_vs_human" if humans == 2 and ai == 0 else "other")
    actual = {"opponents": opponents, "human_players": humans, "ai_players": ai,
              "visibility_mode": visibility_mode(settings),
              "ceasefire_minutes": number(settings.get("Ceasefire")),
              "victory_conditions": strings(settings.get("VictoryConditions"))}
    mismatches = []
    if actual["opponents"] != rules["opponents"]:
        mismatches.append({"rule": "opponents", "expected": rules["opponents"],
                           "actual": actual["opponents"]})
    if actual["visibility_mode"] not in rules["visibility_modes"]:
        mismatches.append({"rule": "visibility_mode", "expected": rules["visibility_modes"],
                           "actual": actual["visibility_mode"]})
    for key in ("ceasefire_minutes", "victory_conditions"):
        expected, found = rules[key], actual[key]
        matches = sorted(expected) == sorted(found) if key == "victory_conditions" else expected == found
        if not matches:
            mismatches.append({"rule": key, "expected": expected, "actual": found})
    return {"expected": rules, "actual": actual, "matches": not mismatches,
            "mismatches": mismatches,
            "note": "Rules audit only; the original save is never rewritten."}


def _position(state):
    if state.get("in_world") is False or state.get("inWorld") is False:
        return None
    x, z = number(state.get("x")), number(state.get("z"))
    return None if x is None or z is None else {"x": x, "z": z}


def _orders(ai):
    result = []
    for order in sequence(ai.get("orderQueue"))[:100]:
        if not isinstance(order, dict):
            continue
        data = mapping(order.get("data"))
        result.append({"type": order.get("type"), "data": clean({key: data[key] for key in
            ("target", "x", "z", "force", "template", "autocontinue", "allowCapture", "type")
            if key in data})})
    return result


def _phase(techs):
    for phase in ("city", "town", "village"):
        if any(tech == f"phase_{phase}" or tech.startswith(f"phase_{phase}_") for tech in techs):
            return phase
    return None


def analyze_save(archive, decoded, catalog=None, focus_player=None, rules=None):
    """Analyze saved facts. No commands, no live connection, no reconstruction of fog."""
    metadata = archive.metadata
    rules = dict(DEFAULT_RULES if rules is None else rules)
    components = decoded["components"]
    settings = mapping(mapping(metadata.get("initAttributes")).get("settings"))
    player_data = sequence(settings.get("PlayerData"))
    warnings = []
    if decoded["consumed_bytes"] != decoded["total_bytes"]:
        raise ValueError("A partial simulation cannot be reported as a complete analysis")
    player_states = {}
    player_entities = {}
    for entity_id, state in components.get("Player", {}).items():
        state = mapping(state)
        pid = state.get("playerID")
        if type(pid) is int:
            player_states[pid] = state
            player_entities[pid] = entity_id
    if not player_states:
        raise ValueError("Decoded simulation has no readable Player components")
    focus = metadata.get("playerID") if focus_player is None else focus_player
    if type(focus) is not int or focus not in player_states:
        raise ValueError("Focus player must exist in this saved simulation")

    classes_by_player_template = {}
    for pid, player_entity in player_entities.items():
        tm = mapping(components.get("TechnologyManager", {}).get(player_entity))
        by_template = defaultdict(set)
        for cls, counts in mapping(tm.get("typeCountsByClass")).items():
            for template, count in mapping(counts).items():
                if number(count) is not None and count > 0:
                    by_template[template].add(cls)
        classes_by_player_template[pid] = by_template

    entity_components = defaultdict(dict)
    for component_name, states in components.items():
        for entity_id, state in states.items():
            entity_components[entity_id][component_name] = state
    descriptions = {}
    failures = {}
    entities = []
    for entity_id, parts in sorted(entity_components.items()):
        template = decoded["templates"].get(entity_id)
        if not template:
            continue
        owner_state = mapping(parts.get("Ownership"))
        owner = owner_state.get("owner")
        owner = owner if type(owner) is int else None
        classes = sorted(classes_by_player_template.get(owner, {}).get(template, []))
        class_source = "saved-technology-manager" if classes else "unknown"
        description = None
        if catalog is not None and not template.startswith("actor|"):
            if template not in descriptions and template not in failures:
                try:
                    descriptions[template] = catalog.describe(template)
                except ValueError as exc:
                    failures[template] = str(exc)
            description = descriptions.get(template)
        if not classes and description:
            classes = list(description["classes"])
            class_source = "unmodified-template"
        foundation = "Foundation" in parts
        if foundation:
            classes = sorted(set(classes) | {"Foundation", "Structure"})
        ai = mapping(parts.get("UnitAI"))
        orders = _orders(ai)
        fsm = ai.get("fsmStateName")
        idle = ai.get("isIdle")
        if type(idle) is not bool:
            idle = isinstance(fsm, str) and fsm.endswith(".IDLE") and not orders if fsm else None
        gatherer = mapping(parts.get("ResourceGatherer"))
        health = mapping(parts.get("Health"))
        supply = mapping(parts.get("ResourceSupply"))
        garrisoned = mapping(parts.get("Garrisonable"))
        holder = garrisoned.get("holder")
        pack = mapping(parts.get("Pack"))
        production = mapping(parts.get("ProductionQueue"))
        capturable = mapping(parts.get("Capturable"))
        building_ai = mapping(parts.get("BuildingAI"))
        record = {
            "id": entity_id, "template": template, "owner": owner, "classes": classes,
            "representation": "mirage" if "Mirage" in parts else "simulation",
            "class_source": class_source, "name": description.get("name") if description else None,
            "components": sorted(parts), "position": _position(mapping(parts.get("Position"))),
            "health": None if not health else {
                "current": first_number(health, "hitpoints"),
                "max": first_number(health, "maxHitpoints")},
            "activity": None if not ai else {"state": clean(fsm), "idle": idle,
                "stance": clean(ai.get("stance")), "guard_target": clean(ai.get("isGuardOf")),
                "orders": orders, "orders_truncated": len(sequence(ai.get("orderQueue"))) > 100},
            "cargo": {key: number(value) for key, value in mapping(gatherer.get("carrying")).items()},
            "garrison": None if "GarrisonHolder" not in parts else {
                "occupants": clean(mapping(parts["GarrisonHolder"]).get("entities", []))},
            "garrisoned_in": holder if type(holder) is int and holder > 0 else None,
            "loyalty": None if not capturable else clean({key: capturable[key] for key in
                ("capturePoints", "maxCapturePoints", "regenRate", "garrisonRegenRate") if key in capturable}),
            "building_fire": None if not building_ai else clean({key: building_ai[key] for key in
                ("archersGarrisoned", "arrowsLeft", "currentRound", "targetUnits", "focusTargets") if key in building_ai}),
            "packing": None if not pack else clean({key: pack[key] for key in
                ("packed", "packing", "progress", "elapsedTime") if key in pack}),
            "production": None if "ProductionQueue" not in parts else {
                "queue": clean(sequence(production.get("queue"))),
                "autoqueue": clean(production.get("autoqueuing", False))},
            "trainer": clean(parts.get("Trainer")), "researcher": clean(parts.get("Researcher")),
            "foundation": clean(parts.get("Foundation")) if foundation else None,
            "resource": None if "ResourceSupply" not in parts else {
                "type": clean(supply.get("cachedType")), "amount": number(supply.get("amount")),
                "infinite": supply.get("maxAmount", supply.get("amount")) == float("inf"),
                "gatherers": clean(supply.get("gatherers", [])),
                "active_gatherers": clean(supply.get("activeGatherers", []))},
            "trade": clean(parts.get("Trader")),
        }
        entities.append(record)
    if failures:
        warnings.append({"code": "template_descriptions_unavailable", "count": len(failures),
                         "examples": dict(list(failures.items())[:12])})

    players = []
    findings = []
    for pid in sorted(player_states):
        if pid == 0:
            continue
        state = player_states[pid]
        setup = mapping(player_data[pid]) if pid < len(player_data) else {}
        own = [item for item in entities if item["owner"] == pid and item["representation"] == "simulation"]
        units = [item for item in own if "Unit" in item["classes"]]
        workers = [item for item in units if "Worker" in item["classes"]]
        idle_workers = [item["id"] for item in workers if item["activity"] and
                        item["activity"]["idle"] is True and item["garrisoned_in"] is None]
        tm = mapping(components.get("TechnologyManager", {}).get(player_entities[pid]))
        researched = sorted(strings(tm.get("researchedTechs")))
        pop = {"count": first_number(state, "popCount", "popUsed"),
               "limit": first_number(state, "popBonuses"), "max": first_number(state, "maxPop")}
        if pop["limit"] is not None and pop["max"] is not None:
            pop["limit"] = min(pop["limit"], pop["max"])
        resources = {key: number(mapping(state.get("resourceCount")).get(key)) for key in RESOURCES}
        gatherers = {key: number(mapping(state.get("resourceGatherers")).get(key)) for key in RESOURCES}
        class_counts = Counter(cls for item in own for cls in item["classes"])
        victory_units = [item["id"] for item in units if "Animal" not in item["classes"]]
        unknown_classes = [item["id"] for item in own if not item["classes"]]
        completed_counts = Counter(cls for item in own if item["foundation"] is None for cls in item["classes"])
        saved_counts = mapping(tm.get("classCounts"))
        count_differences = {cls: {"saved": saved_counts.get(cls, 0), "reconstructed": completed_counts[cls]}
            for cls in sorted(set(saved_counts) | set(completed_counts))
            if saved_counts.get(cls, 0) != completed_counts[cls]}
        statistics = mapping(components.get("StatisticsTracker", {}).get(player_entities[pid]))
        activity_counts = Counter(item["activity"]["state"] or "unknown" for item in units if item["activity"])
        player = {"id": pid, "entity_id": player_entities[pid], "name": setup.get("Name"),
            "civ": setup.get("Civ"), "ai": setup.get("AI"), "color": setup.get("Color"),
            "state": state.get("state"), "phase": _phase(researched), "resources": resources,
            "population": pop, "population_note": "Saved population includes native production reservations.",
            "gatherers": gatherers, "entities": len(own), "units": len(units),
            "buildings": class_counts["Structure"], "foundations": sum(item["foundation"] is not None for item in own),
            "workers": len(workers), "idle_worker_ids": idle_workers,
            "class_counts": dict(sorted(class_counts.items())),
            "saved_completed_class_counts": clean(tm.get("classCounts")),
            "class_count_check": {"matches": not count_differences, "differences": count_differences},
            "unknown_class_entity_ids": unknown_classes,
            "unit_type_counts": dict(sorted(Counter(item["template"] for item in units).items())),
            "building_type_counts": dict(sorted(Counter(item["template"] for item in own
                if "Structure" in item["classes"]).items())),
            "researched_technologies": researched,
            "research_queue": clean(tm.get("researchQueued", [])),
            "busy_building_ids": [item["id"] for item in own if item["production"] and item["production"]["queue"]],
            "garrison_holders": [{"id": item["id"], **item["garrison"]} for item in own if item["garrison"]],
            "remaining_victory_unit_ids": victory_units,
            "victory_count_complete": not unknown_classes and not count_differences,
            "victory_count_note": "Unit+!Animal; contextual count only, not a declaration of victory.",
            "unit_activity_counts": dict(sorted(activity_counts.items())),
            "statistics": clean({key: statistics[key] for key in
                ("unitsTrained", "unitsLost", "enemyUnitsKilled", "buildingsConstructed", "buildingsLost",
                 "resourcesGathered", "resourcesUsed", "tradeIncome", "peakPercentMapControlled") if key in statistics}),
        }
        players.append(player)
        if count_differences or unknown_classes:
            warnings.append({"code": "entity_classification_incomplete", "player": pid,
                "unknown_entity_ids": unknown_classes, "class_count_differences": count_differences})
        if idle_workers:
            findings.append({"player": pid, "code": "idle_workers", "severity": "opportunity",
                "message": f"{len(idle_workers)} 名 Worker 当前空闲；先检查其留守职责，再决定是否改派。",
                "evidence": {"entity_ids": idle_workers}})
        if pop["count"] is not None and pop["limit"] is not None and pop["limit"] - pop["count"] <= 2:
            findings.append({"player": pid, "code": "population_headroom", "severity": "attention",
                "message": "当前人口接近或达到可用上限，生产可能等待人口条件。", "evidence": pop})
        if gatherers["wood"] == 0 and idle_workers:
            findings.append({"player": pid, "code": "no_wood_gathering", "severity": "opportunity",
                "message": "当前没有木材采集者且存在空闲 Worker；库存和既定任务决定是否需要恢复采木。",
                "evidence": {"wood": resources["wood"], "gatherers": 0}})

    timer = mapping(decoded["systems"].get("Timer"))
    game_time = first_number(timer, "time")
    if game_time is None:
        game_time = number(mapping(metadata.get("gui")).get("timeElapsed"))
        warnings.append({"code": "game_time_from_gui", "message": "No readable native Timer.time; using saved GUI timeElapsed."})
    ceasefire = number(settings.get("Ceasefire"))
    remaining = None if ceasefire is None or game_time is None else max(0, ceasefire * 60_000 - game_time)
    ceasefire_state = mapping(decoded["systems"].get("CeasefireManager"))
    active = ceasefire_state.get("ceasefireIsActive")
    actual_remaining = None
    if active is False:
        actual_remaining = 0
    elif active is True and game_time is not None:
        duration = number(ceasefire_state.get("ceasefireTime"))
        started = number(ceasefire_state.get("ceasefireStartedTime"))
        if duration is not None and started is not None:
            actual_remaining = max(0, started + duration - game_time)
    saved_at = number(metadata.get("time"))
    try:
        utc = datetime.fromtimestamp(saved_at, timezone.utc).isoformat() if saved_at is not None else None
    except (ValueError, OverflowError, OSError):
        utc = None
    audit = audit_rules(metadata, rules)
    if not audit["matches"]:
        findings.append({"player": None, "code": "case_rules_mismatch", "severity": "data_quality",
            "message": "存档设置与未来案例规范不完全一致；保留实际设置，不自动修正。",
            "evidence": audit["mismatches"]})
    size_tiles = number(settings.get("Size"))
    return {"schema_version": 1,
        "source": {"file": str(archive.path), "sha256": archive.sha256,
            "engine_version": metadata.get("engine_version"),
            "serialization_version": metadata.get("engine_serialization_version"),
            "saved_at_utc": utc, "mods": metadata.get("mods"), "entries": list(archive.entries)},
        "scope": {"kind": "offline-save-omniscient", "focus_player": focus,
            "fog_reconstructed": False, "live_commands": False,
            "note": "Focus marks the player of interest; all players remain in this omniscient report. Fog is not reconstructed."},
        "parse": {"complete": True, "consumed_bytes": decoded["consumed_bytes"],
            "total_bytes": decoded["total_bytes"], "next_entity_id": decoded["next_entity_id"],
            "system_components": sorted(decoded["systems"]),
            "component_counts": {name: len(states) for name, states in sorted(components.items())}},
        "match": {"map": settings.get("mapName"), "map_id": mapping(metadata.get("initAttributes")).get("map"),
            "match_id": mapping(metadata.get("initAttributes")).get("matchID"),
            "seed": settings.get("Seed"), "ai_seed": settings.get("AISeed"),
            "game_time_ms": game_time, "size_tiles": size_tiles,
            "world_size": size_tiles * 4 if size_tiles is not None else None,
            "world_size_basis": "0.28.0 terrain tile width is 4 world units",
            "visibility_mode": visibility_mode(settings), "ceasefire_minutes": ceasefire,
            "estimated_ceasefire_remaining_ms": remaining,
            "ceasefire_note": "Estimate from configured start-of-match ceasefire; scripted changes are not inferred.",
            "ceasefire_actual": {"active": active if type(active) is bool else None,
                "remaining_ms": actual_remaining, "basis": "saved-CeasefireManager"},
            "victory_conditions": strings(settings.get("VictoryConditions")),
            "starting_resources": settings.get("StartingResources")},
        "rules_audit": audit, "players": players, "entities": entities,
        "findings": findings, "warnings": warnings,
        "template_context": {"available": catalog is not None,
            "notes": list(getattr(catalog, "discovery_notes", [])),
            "sources": [{"path": str(source.path), "version": source.version}
                        for source in getattr(catalog, "sources", [])],
            "basis": "Public base-template labels only; current state comes from the save."},
        "limits": ["Snapshot only: no action history, user intent or measured future resource income.",
            "No complete heightmap; the object map is not a terrain or pathability reconstruction.",
            "No automatic victory declaration or scalar combat-strength prediction.",
            "Omniscient saved state must not be fed back as live player-view evidence."]}
