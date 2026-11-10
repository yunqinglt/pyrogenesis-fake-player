"""Bounded descriptions of options advertised by the current player's entities.

Descriptions originate in the running engine's native templates and localization,
not the research checkout. They contain public rules, never other-player state.
"""

from __future__ import annotations

import re

from .privacy import ObservationError, number


KINDS = frozenset({"research", "training", "construction", "self"})
MAX_OPTIONS = 16
_ID = re.compile(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*")
_RESOURCES = ("food", "wood", "stone", "metal", "population", "time")
_RULE_KEYS = frozenset({
    "all", "any", "not", "tech", "techs", "civ", "entity", "entities",
    "class", "number", "check", "count", "variants", "Techs", "Entities",
    "All", "Any", "Not", "Class", "Count", "Variants", "Number", "_string",
})


def _identifier(value):
    if not isinstance(value, str) or not 0 < len(value) <= 200 or _ID.fullmatch(value) is None:
        raise ObservationError("Invalid public option identifier")
    return value


def advertised_options(entity: dict, kind: str) -> list[str]:
    profile = entity.get("properties") or {}
    building = profile.get("building") or {}
    if kind == "self":
        template = entity.get("template")
        if isinstance(template, str) and template.startswith("foundation|"):
            template = template[len("foundation|"):]
        candidates = [template]
    elif kind == "research":
        research = building.get("research") or {}
        candidates = list(research.get("technologies", []))
        for pair in research.get("pairs", []):
            candidates.extend((pair.get("top"), pair.get("bottom")))
    elif kind == "training":
        candidates = (building.get("training") or {}).get("entities", [])
    elif kind == "construction":
        candidates = (profile.get("construction") or {}).get("templates", [])
    else:
        raise ValueError("Choose research, training, construction or self descriptions")
    return list(dict.fromkeys(_identifier(item) for item in candidates))


def validate_options_request(entity_id, kind, ids, observation) -> dict:
    if (type(entity_id) is not int or not 0 < entity_id < 2**31 or
            not isinstance(kind, str) or kind not in KINDS):
        raise ValueError("Invalid owned entity or option kind")
    entity = next((item for item in observation["entities"]
                   if item["id"] == entity_id and item["owner"] == observation["player"]), None)
    if entity is None:
        raise ValueError("Describe only currently observed owned entities")
    allowed = advertised_options(entity, kind)
    if ids is not None:
        if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_OPTIONS:
            raise ValueError("Choose between one and 16 advertised option identifiers")
        ids = [_identifier(item) for item in ids]
        if len(set(ids)) != len(ids) or any(item not in allowed for item in ids):
            raise ValueError("Choose distinct identifiers advertised by this entity")
    return {"entity_id": entity_id, "kind": kind, "ids": ids}


def _text(value, limit=4000):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ObservationError("Invalid native description text")
    return value


def _texts(value, limit=64):
    if not isinstance(value, list) or len(value) > limit:
        raise ObservationError("Invalid native description list")
    return [_text(item, 200) for item in value]


def _optional_number(value, *, low=0):
    return None if value is None else number(value, low=low)


def _cost(value):
    if not isinstance(value, dict):
        raise ObservationError("Invalid native option cost")
    return {key: number(value[key]) for key in _RESOURCES if key in value}


def _requirements(value, depth=0, budget=None):
    if budget is None:
        budget = [256]
    budget[0] -= 1
    if depth > 8 or budget[0] < 0:
        raise ObservationError("Native requirements exceed supported bounds")
    if value is None or type(value) is bool:
        return value
    if isinstance(value, str):
        return _text(value, 1000)
    if isinstance(value, (int, float)):
        return number(value, low=-10**12)
    if isinstance(value, list) and len(value) <= 64:
        return [_requirements(item, depth + 1, budget) for item in value]
    if isinstance(value, dict) and len(value) <= 32:
        return {key: _requirements(item, depth + 1, budget)
                for key, item in value.items() if key in _RULE_KEYS}
    raise ObservationError("Invalid native requirements")


def _requirements_complete(value):
    if isinstance(value, dict):
        return all(key in _RULE_KEYS and _requirements_complete(item) for key, item in value.items())
    if isinstance(value, list):
        return all(_requirements_complete(item) for item in value)
    return True


def _modifications(value):
    if not isinstance(value, list) or len(value) > 64:
        raise ObservationError("Invalid native technology modifications")
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ObservationError("Invalid native modification")
        projected = {"value": _text(item.get("value"), 300),
                     "affects": _texts(item.get("affects", []))}
        if not projected["value"]:
            raise ObservationError("Missing native modification path")
        for key in ("add", "multiply"):
            if key in item:
                projected[key] = number(item[key], low=-10**12)
        if "replace" in item:
            replacement = item["replace"]
            if isinstance(replacement, str):
                projected["replace"] = _text(replacement, 1000)
            elif type(replacement) is bool:
                projected["replace"] = replacement
            else:
                projected["replace"] = number(replacement, low=-10**12)
        result.append(projected)
    return result


def _stats(value):
    if not isinstance(value, dict):
        raise ObservationError("Invalid native template statistics")
    result = {"max_hitpoints": _optional_number(value.get("max_hitpoints")),
              "population_bonus": _optional_number(value.get("population_bonus")),
              "classes": _texts(value.get("classes", []))}
    for key, fields in (("speed", ("walk", "run", "acceleration")),
                        ("garrison", ("capacity", "heal_rate"))):
        raw = value.get(key)
        if raw is not None and not isinstance(raw, dict):
            raise ObservationError("Invalid native template statistics group")
        result[key] = None if raw is None else {
            field: _optional_number(raw.get(field)) for field in fields}
    packing = value.get("packing")
    if packing is not None and (not isinstance(packing, dict) or
                                packing.get("state") not in ("packed", "unpacked")):
        raise ObservationError("Invalid native packing statistics")
    result["packing"] = None if packing is None else {
        "state": packing["state"], "time_ms": _optional_number(packing.get("time_ms"))}
    return result


def project_options(raw, request, player):
    """Rebuild every exported description; reject mismatched acknowledgements."""
    if (not isinstance(raw, dict) or raw.get("entity_id") != request["entity_id"] or
            raw.get("kind") != request["kind"] or type(raw.get("player")) is not int or
            raw["player"] != player or raw.get("source") != "running-game-native-templates"):
        raise ObservationError("Native option response does not match this request")
    options = raw.get("options")
    if not isinstance(options, list) or len(options) > MAX_OPTIONS:
        raise ObservationError("Invalid native option response size")
    total = raw.get("total_available")
    if type(total) is not int or not len(options) <= total <= 1024:
        raise ObservationError("Invalid native option count")
    if type(raw.get("truncated")) is not bool:
        raise ObservationError("Invalid native option truncation flag")
    result = {"entity_id": request["entity_id"], "player": player, "kind": request["kind"],
              "source": raw["source"], "engine_version": _text(raw.get("engine_version"), 100),
              "locale": _text(raw.get("locale"), 100),
              "translation": "game-locale-or-source-fallback", "total_available": total,
              "truncated": raw["truncated"], "options": []}
    seen = set()
    for item in options:
        if not isinstance(item, dict):
            raise ObservationError("Invalid native option description")
        identity = _identifier(item.get("id"))
        if identity in seen or request["ids"] is not None and identity not in request["ids"]:
            raise ObservationError("Unexpected native option identifier")
        seen.add(identity)
        name = item.get("name")
        if not isinstance(name, dict):
            raise ObservationError("Invalid native option name")
        available = item.get("requirements_met")
        if available is not None and type(available) is not bool:
            raise ObservationError("Invalid native requirement state")
        complete = item.get("requirements_complete")
        if type(complete) is not bool:
            raise ObservationError("Invalid native requirements completeness flag")
        pair = item.get("pair")
        if pair is not None:
            if not isinstance(pair, dict) or pair.get("position") not in ("top", "bottom"):
                raise ObservationError("Invalid native technology pair")
            if type(pair.get("requirements_complete")) is not bool:
                raise ObservationError("Invalid native pair completeness flag")
            pair = {"group": None if pair.get("group") is None else _identifier(pair["group"]),
                    "position": pair["position"], "other": _identifier(pair.get("other")),
                    "requirements": _requirements(pair.get("requirements")),
                    "requirements_complete": pair["requirements_complete"] and
                        _requirements_complete(pair.get("requirements"))}
        effect_complete = item.get("modifications_complete")
        if type(effect_complete) is not bool:
            raise ObservationError("Invalid native effect completeness flag")
        result["options"].append({
            "id": identity, "name": {key: _text(name.get(key), 300) for key in ("generic", "specific")},
            "description": _text(item.get("description")), "tooltip": _text(item.get("tooltip")),
            "requirements_tooltip": _text(item.get("requirements_tooltip")),
            "cost": _cost(item.get("cost")), "base_cost": _cost(item.get("base_cost")),
            "cost_scope": _text(item.get("cost_scope"), 100),
            "cost_notes": _text(item.get("cost_notes"), 1000),
            "requirements": _requirements(item.get("requirements")),
            "requirements_complete": complete and _requirements_complete(item.get("requirements")) and
                (pair is None or pair["requirements_complete"]), "requirements_met": available,
            "affects": _texts(item.get("affects", [])),
            "modifications": _modifications(item.get("modifications", [])),
            "modifications_complete": effect_complete and all(
                set(effect).issubset({"value", "affects", "add", "multiply", "replace"})
                for effect in item.get("modifications", [])),
            "pair": pair, "supersedes": _text(item.get("supersedes"), 200),
            "replaces": _texts(item.get("replaces", [])), "stats": _stats(item.get("stats", {})),
        })
    if request["ids"] is not None and seen != set(request["ids"]):
        raise ObservationError("Native option response omitted requested identifiers")
    return result
