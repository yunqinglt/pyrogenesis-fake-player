"""Bounded, allowlisted entity properties from the trusted player-view bridge.

Only the owned-player observation may include inventory, orders or garrison
occupancy. A public map projection always discards those private payloads,
including for owned entities, before inspecting their contents.
"""

from __future__ import annotations

import re

from .privacy import ObservationError, number


PROPERTY_VERSION = 1
CAPABILITIES = (
    "move", "build", "repair", "gather", "farm", "trade", "heal", "attack",
    "garrison", "occupy_turret", "pack", "guard", "patrol", "collect_treasure",
    "resource_dropsite", "capture",
)
RESOURCE_TYPES = frozenset({"food", "wood", "stone", "metal"})
SPECIAL_ROLES = frozenset({
    "war_dog", "minister", "worker_elephant", "champion", "mercenary", "hero",
})
PROPERTY_FIELDS = frozenset({
    "version", "identity", "capabilities", "gathering", "inventory",
    "current_target", "activity", "combat", "garrison", "garrisoned_in",
    "packing", "storage", "construction", "building", "rally_point",
})
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}\Z")


def _invalid(description):
    raise ObservationError(f"Invalid entity properties {description}")


def _record(value, description):
    if not isinstance(value, dict):
        _invalid(description)
    return value


def _required(value, fields, description):
    value = _record(value, description)
    if not all(field in value for field in fields):
        _invalid(description)
    return value


def _boolean(value):
    if type(value) is not bool:
        _invalid("boolean")
    return value


def _text(value, limit=200):
    if not isinstance(value, str) or len(value) > limit:
        _invalid("string")
    return value


def _texts(value, *, limit=64, length=80):
    if not isinstance(value, list) or len(value) > limit:
        _invalid("string list")
    return [_text(item, length) for item in value]


def _optional(value, projector):
    return None if value is None else projector(value)


def _entity_id(value):
    if type(value) is not int or not 1 <= value <= 2**32 - 1:
        _invalid("entity identity")
    return value


def _resource_type(value, *, subtype=False):
    value = _text(value, 80)
    parts = value.split(".")
    if parts[0] not in RESOURCE_TYPES or len(parts) > (2 if subtype else 1):
        _invalid("resource type")
    if len(parts) == 2 and not _IDENTIFIER.fullmatch(parts[1]):
        _invalid("resource subtype")
    return value


def _numeric_map(value, *, limit=32, resources=False, subtypes=False):
    value = _record(value, "numeric map")
    if len(value) > limit:
        _invalid("numeric map size")
    result = {}
    for key, amount in value.items():
        if resources:
            key = _resource_type(key, subtype=subtypes)
        elif not isinstance(key, str) or not _IDENTIFIER.fullmatch(key):
            _invalid("numeric map key")
        result[key] = number(amount)
    return result


def _identity(raw):
    raw = _required(raw, ("civ", "classes", "rank", "special_roles"), "identity")
    roles = _texts(raw["special_roles"], limit=len(SPECIAL_ROLES))
    if any(role not in SPECIAL_ROLES for role in roles):
        _invalid("special role")
    result = {
        "civ": _text(raw["civ"], 80), "classes": _texts(raw["classes"]),
        "rank": _text(raw["rank"], 80), "special_roles": roles,
    }
    if "name" in raw:
        name = _required(raw["name"], ("generic", "specific"), "identity name")
        result["name"] = {key: _text(name[key], 300) for key in ("generic", "specific")}
    if "tooltip" in raw:
        result["tooltip"] = _text(raw["tooltip"], 4000)
    if "translation" in raw:
        if raw["translation"] != "source-language":
            _invalid("identity translation source")
        result["translation"] = "source-language"
    return result


def _gathering(raw):
    raw = _required(raw, ("rates", "capacities"), "gathering")
    return {
        "rates": _numeric_map(raw["rates"], limit=64, resources=True, subtypes=True),
        "capacities": _numeric_map(raw["capacities"], limit=4, resources=True),
    }


def _construction(raw):
    raw = _required(raw, ("templates", "rate"), "construction")
    return {"templates": _texts(raw["templates"], limit=256, length=200),
            "rate": number(raw["rate"])}


def _storage(raw):
    raw = _required(raw, ("accepts",), "storage")
    accepts = _texts(raw["accepts"], limit=4)
    return {"accepts": [_resource_type(item) for item in accepts]}


def _inventory(raw):
    raw = _required(raw, ("resources", "trade_goods"), "inventory")
    resources = raw["resources"]
    if not isinstance(resources, list) or len(resources) > 4:
        _invalid("inventory resource list")
    safe_resources = []
    for resource in resources:
        resource = _required(resource, ("type", "amount", "capacity"), "carried resource")
        safe_resources.append({"type": _resource_type(resource["type"]),
            "amount": number(resource["amount"]), "capacity": number(resource["capacity"])})
    goods = raw["trade_goods"]
    if goods is not None:
        goods = _required(goods, ("type", "trader_gain", "market1_gain", "market2_gain"), "trade goods")
        goods = {"type": _optional(goods["type"], _resource_type),
            **{key: number(goods[key]) for key in ("trader_gain", "market1_gain", "market2_gain")}}
    return {"resources": safe_resources, "trade_goods": goods}


def _position(raw):
    raw = _required(raw, ("x", "z"), "target position")
    return {axis: number(raw[axis], low=-10000, high=100000) for axis in ("x", "z")}


def _target(raw):
    raw = _required(raw, ("order", "entity_id", "position"), "current target")
    return {"order": _text(raw["order"], 80),
        "entity_id": _optional(raw["entity_id"], _entity_id),
        "position": _optional(raw["position"], _position)}


def _rally_point(raw):
    raw = _required(raw, ("points", "truncated"), "rally point")
    if not isinstance(raw["points"], list) or len(raw["points"]) > 100:
        _invalid("rally queue size")
    points = [_target(item) for item in raw["points"]]
    allowed = {"move", "attack_move", "patrol", "attack", "capture", "gather", "gather_near_position",
               "repair", "garrison", "occupy_turret", "trade", "collect_treasure",
               "collect_treasure_near_position", "unsupported"}
    if any(item["order"] not in allowed for item in points):
        _invalid("rally order")
    return {"points": points, "truncated": _boolean(raw["truncated"])}


def _activity(raw):
    raw = _required(raw, ("state", "idle", "stance"), "activity")
    return {"state": _text(raw["state"], 128), "idle": _boolean(raw["idle"]),
        "stance": _optional(raw["stance"], lambda item: _text(item, 80))}


def _bonuses(raw):
    if not isinstance(raw, list) or len(raw) > 64:
        _invalid("attack bonus list")
    result = []
    for item in raw:
        item = _required(item, ("name", "classes", "civs", "multiplier"), "attack bonus")
        result.append({"name": _text(item["name"]), "classes": _texts(item["classes"]),
            "civs": _texts(item["civs"]), "multiplier": number(item["multiplier"], high=10**6)})
    return result


def _status_effects(raw):
    if not isinstance(raw, list) or len(raw) > 32:
        _invalid("status effect list")
    result = []
    for effect in raw:
        effect = _required(effect, ("name", "duration_ms", "interval_ms", "stackability", "damage", "capture"),
                           "status effect")
        result.append({"name": _text(effect["name"]),
            "duration_ms": number(effect["duration_ms"]), "interval_ms": number(effect["interval_ms"]),
            "stackability": _text(effect["stackability"], 80),
            "damage": _numeric_map(effect["damage"]), "capture": number(effect["capture"])})
    return result


def _splash(raw):
    raw = _required(raw, ("shape", "range", "friendly_fire", "damage", "capture", "bonuses"), "splash")
    return {"shape": _text(raw["shape"], 80), "range": number(raw["range"]),
        "friendly_fire": _boolean(raw["friendly_fire"]),
        "damage": _numeric_map(raw["damage"]), "capture": number(raw["capture"]),
        "bonuses": _bonuses(raw["bonuses"]), "status_effects": _status_effects(raw.get("status_effects", []))}


def _attack(raw):
    raw = _required(raw, ("damage", "capture", "bonuses", "min_range", "max_range",
        "prepare_ms", "repeat_ms", "restricted_classes", "preferred_classes", "splash"), "attack")
    result = {"damage": _numeric_map(raw["damage"]), "capture": number(raw["capture"]),
        "bonuses": _bonuses(raw["bonuses"]),
        **{key: number(raw[key]) for key in ("min_range", "max_range", "prepare_ms", "repeat_ms")},
        "restricted_classes": _texts(raw["restricted_classes"]),
        "preferred_classes": _texts(raw["preferred_classes"]),
        "splash": _optional(raw["splash"], _splash),
        "status_effects": _status_effects(raw.get("status_effects", []))}
    if result["min_range"] > result["max_range"]:
        _invalid("attack range")
    return result


def _resistance(raw):
    raw = _required(raw, ("damage", "capture"), "resistance")
    statuses = _record(raw.get("status_effects", {}), "status resistance")
    if len(statuses) > 32:
        _invalid("status resistance count")
    safe_statuses = {}
    for name, resistance in statuses.items():
        resistance = _required(resistance, ("duration_multiplier", "block_chance"), "status resistance")
        safe_statuses[_text(name)] = {"duration_multiplier": number(resistance["duration_multiplier"]),
            "block_chance": number(resistance["block_chance"], high=1)}
    return {"damage": _numeric_map(raw["damage"]), "capture": number(raw["capture"]),
            "status_effects": safe_statuses}


def _healing(raw):
    raw = _required(raw, ("health", "range", "interval_ms", "healable_classes", "unhealable_classes"), "healing")
    return {**{key: number(raw[key]) for key in ("health", "range", "interval_ms")},
        "healable_classes": _texts(raw["healable_classes"]),
        "unhealable_classes": _texts(raw["unhealable_classes"])}


def _auras(raw):
    if not isinstance(raw, list) or len(raw) > 64:
        _invalid("aura list")
    result = []
    for item in raw:
        item = _required(item, ("id", "name", "description", "range"), "aura")
        result.append({"id": _text(item["id"]), "name": _text(item["name"]),
            "description": _text(item["description"], 2000),
            "range": _optional(item["range"], number)})
    return result


def _combat(raw):
    raw = _required(raw, ("can_attack", "can_capture", "attacks", "resistance", "healing", "auras"), "combat")
    attacks = _record(raw["attacks"], "attack types")
    if len(attacks) > 8:
        _invalid("attack type count")
    safe_attacks = {}
    for attack_type, attack in attacks.items():
        if not isinstance(attack_type, str) or not _IDENTIFIER.fullmatch(attack_type):
            _invalid("attack type")
        safe_attacks[attack_type] = _attack(attack)
    return {"can_attack": _boolean(raw["can_attack"]), "can_capture": _boolean(raw["can_capture"]),
        "attacks": safe_attacks,
        "resistance": _optional(raw["resistance"], _resistance),
        "healing": _optional(raw["healing"], _healing), "auras": _auras(raw["auras"])}


def _garrison(raw, private):
    raw = _required(raw, ("capacity", "allowed_classes", "occupied_slots", "occupants"), "garrison")
    occupied_slots, occupants = None, None
    if private:
        occupied_slots = _optional(raw["occupied_slots"], lambda value: number(value, high=100000))
        if raw["occupants"] is not None:
            if not isinstance(raw["occupants"], list) or len(raw["occupants"]) > 256:
                _invalid("garrison occupant list")
            occupants = [_entity_id(item) for item in raw["occupants"]]
            if len(set(occupants)) != len(occupants):
                _invalid("duplicate garrison occupants")
    return {"capacity": number(raw["capacity"], high=100000),
        "allowed_classes": _texts(raw["allowed_classes"]),
        "occupied_slots": occupied_slots, "occupants": occupants}


def _garrisoned_in(raw):
    raw = _required(raw, ("holder_id", "slot_size"), "garrisoned unit")
    return {"holder_id": _optional(raw["holder_id"], _entity_id),
        "slot_size": number(raw["slot_size"], high=100000)}


def _packing(raw):
    raw = _required(raw, ("packed", "changing", "progress", "state"), "packing")
    packed, changing = _boolean(raw["packed"]), _boolean(raw["changing"])
    state = ("unpacking" if packed else "packing") if changing else ("packed" if packed else "unpacked")
    if raw["state"] != state:
        _invalid("contradictory packing state")
    return {"packed": packed, "changing": changing,
        "progress": number(raw["progress"], high=1), "state": state}


def _research(raw):
    raw = _required(raw, ("technologies", "pairs", "tech_cost_multiplier"), "research")
    technologies = _texts(raw["technologies"], limit=256, length=200)
    if not isinstance(raw["pairs"], list) or len(raw["pairs"]) + len(technologies) > 256:
        _invalid("research pair count")
    pairs = []
    for pair in raw["pairs"]:
        pair = _required(pair, ("top", "bottom"), "research pair")
        pairs.append({key: _text(pair[key], 200) for key in ("top", "bottom")})
    return {"technologies": technologies, "pairs": pairs,
            "tech_cost_multiplier": _numeric_map(raw["tech_cost_multiplier"], limit=5)}


def _production(raw):
    raw = _required(raw, ("queue", "autoqueue"), "production queue")
    if not isinstance(raw["queue"], list) or len(raw["queue"]) > 16:
        _invalid("production queue size")
    def integer(value, *, high):
        value = number(value, high=high)
        if type(value) is not int:
            _invalid("integer")
        return value
    entries = []
    for item in raw["queue"]:
        item = _required(item, ("id", "unit_template", "technology_template", "count", "progress",
                               "time_remaining", "paused"), "production item")
        entries.append({"id": _optional(item["id"], lambda value: integer(value, high=2**32 - 1)),
            "unit_template": _text(item["unit_template"], 200),
            "technology_template": _text(item["technology_template"], 200),
            "count": integer(item["count"], high=100000),
            "progress": number(item["progress"], high=1),
            "time_remaining": number(item["time_remaining"], high=1e9),
            "paused": _boolean(item["paused"])})
    return {"queue": entries, "autoqueue": _boolean(raw["autoqueue"])}


def _building_ai(raw):
    raw = _required(raw, ("default_arrows", "max_arrows", "garrison_arrow_multiplier",
                          "garrison_arrow_classes", "arrows"), "building AI")
    def integer(value, *, high):
        value = number(value, high=high)
        if type(value) is not int:
            _invalid("integer")
        return value
    return {"default_arrows": integer(raw["default_arrows"], high=100000),
        "max_arrows": _optional(raw["max_arrows"], lambda value: integer(value, high=100000)),
        "garrison_arrow_multiplier": number(raw["garrison_arrow_multiplier"], high=100000),
        "garrison_arrow_classes": _texts(raw["garrison_arrow_classes"]),
        "arrows": integer(raw["arrows"], high=100000)}


def _building_resource(raw, private):
    raw = _required(raw, ("infinite", "type", "amount", "max", "max_gatherers", "gatherers"), "building resource")
    infinite = _boolean(raw["infinite"])
    def integer(value, *, high):
        value = number(value, high=high)
        if type(value) is not int:
            _invalid("integer")
        return value
    # Infinite supply has no finite amount. Ignore serialized Infinity/null so
    # the machine-readable representation remains valid JSON in both cases.
    return {"infinite": infinite, "type": _optional(raw["type"], lambda value: _resource_type(value, subtype=True)),
        "amount": None if infinite else _optional(raw["amount"], number),
        "max": None if infinite else _optional(raw["max"], number),
        "max_gatherers": _optional(raw["max_gatherers"], lambda value: integer(value, high=100000)),
        "gatherers": _optional(raw["gatherers"], lambda value: integer(value, high=100000)) if private else None}


def _loyalty(raw):
    raw = _required(raw, ("current", "max"), "loyalty")
    points = raw["current"]
    if not isinstance(points, list) or len(points) > 9:
        _invalid("loyalty distribution")
    return {"current": [number(value, high=1e9) for value in points],
            "max": _optional(raw["max"], lambda value: number(value, high=1e9))}


def _building_distance(raw):
    raw = _required(raw, ("from_class", "min", "max"), "building distance")
    minimum, maximum = _optional(raw["min"], number), _optional(raw["max"], number)
    if minimum is not None and maximum is not None and minimum > maximum:
        _invalid("building distance range")
    return {"from_class": _text(raw["from_class"], 80), "min": minimum, "max": maximum}


def _building_placement(raw):
    raw = _required(raw, ("placement", "territory", "category", "distance"), "building placement")
    territory = _texts(raw["territory"], limit=4)
    if any(item not in {"own", "ally", "neutral", "enemy"} for item in territory):
        _invalid("building territory")
    return {"placement": _optional(raw["placement"], lambda value: _text(value, 40)),
        "territory": territory, "category": _optional(raw["category"], lambda value: _text(value, 80)),
        "distance": _optional(raw["distance"], _building_distance)}


def _building_work(raw):
    raw = _required(raw, ("builders", "time_remaining_seconds", "time_remaining_with_one_more_worker_seconds"), "building work")
    builders = number(raw["builders"], high=100000)
    if type(builders) is not int:
        _invalid("builder count")
    return {"builders": builders,
        "time_remaining_seconds": number(raw["time_remaining_seconds"], high=1e9),
        "time_remaining_with_one_more_worker_seconds": number(raw["time_remaining_with_one_more_worker_seconds"], high=1e9)}


def _building(raw, private):
    raw = _required(raw, ("is_building", "research", "training", "production", "building_ai",
                          "resource", "loyalty", "placement", "owner", "hitpoints", "max_hitpoints",
                          "foundation", "repair"), "building")
    if raw["is_building"] is not True:
        _invalid("building marker")
    owner = _optional(raw["owner"], lambda value: number(value, low=-1, high=8))
    if owner is not None and type(owner) is not int:
        _invalid("owner")
    training = _optional(raw["training"], lambda value: {
        "entities": _texts(_required(value, ("entities",), "training")["entities"], limit=256, length=200)})
    return {"is_building": True, "research": _optional(raw["research"], _research), "training": training,
        "production": _optional(raw["production"], _production) if private else None,
        "building_ai": _optional(raw["building_ai"], _building_ai),
        "resource": _optional(raw["resource"], lambda value: _building_resource(value, private)),
        "loyalty": _optional(raw["loyalty"], _loyalty), "placement": _optional(raw["placement"], _building_placement),
        "owner": owner,
        "hitpoints": _optional(raw["hitpoints"], number), "max_hitpoints": _optional(raw["max_hitpoints"], number),
        "foundation": _optional(raw["foundation"], _building_work) if private else None,
        "repair": _optional(raw["repair"], _building_work) if private else None}


def project_properties(raw, *, private: bool = False) -> dict:
    """Reconstruct version 1 properties, withholding private fields on maps."""
    # New optional groups may be absent in a v1 profile from a running older
    # mod; return null until the client has loaded the upgraded companion.
    raw = _required(raw, PROPERTY_FIELDS - {"building", "rally_point"}, "envelope")
    if type(raw["version"]) is not int or raw["version"] != PROPERTY_VERSION:
        _invalid("version")
    capabilities = _required(raw["capabilities"], CAPABILITIES, "capabilities")
    return {
        "version": PROPERTY_VERSION, "identity": _identity(raw["identity"]),
        "capabilities": {name: _boolean(capabilities[name]) for name in CAPABILITIES},
        "gathering": _optional(raw["gathering"], _gathering),
        "construction": _optional(raw["construction"], _construction) if private else None,
        "storage": _optional(raw["storage"], _storage),
        "inventory": _optional(raw["inventory"], _inventory) if private else None,
        "current_target": _optional(raw["current_target"], _target) if private else None,
        "rally_point": _optional(raw.get("rally_point"), _rally_point) if private else None,
        "activity": _optional(raw["activity"], _activity) if private else None,
        "combat": _combat(raw["combat"]),
        "garrison": None if raw["garrison"] is None else _garrison(raw["garrison"], private),
        "garrisoned_in": _optional(raw["garrisoned_in"], _garrisoned_in) if private else None,
        "packing": _optional(raw["packing"], _packing),
        "building": _optional(raw.get("building"), lambda value: _building(value, private)),
    }
