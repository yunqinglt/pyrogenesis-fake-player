"""Bounded, non-executing decoder for 0 A.D. 0.28 simulation snapshots.

The format has no component length framing.  Every component and JavaScript
prototype must therefore have an explicitly known layout; errors never trigger
heuristic resynchronisation.  Layout reference: 0ad/0ad commit
a2cae4d69f816e9e9d7eecb6bf88f762afc0c90d, simulation2/{serialization,components}.
"""

from __future__ import annotations

import base64
import math
import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


class DecodeError(ValueError):
    """Unsupported or malformed binary snapshot, with its byte offset."""


@dataclass(frozen=True)
class _Sentinel:
    name: str


UNDEFINED = _Sentinel("undefined")
HOLE = _Sentinel("array_hole")


class JSObject(dict):
    """Object whose prototype is metadata, never executable Python code."""

    prototype: str | None = None


class JSArray(list):
    """Array preserving sparse holes and non-index enumerable properties."""

    def __init__(self, size: int):
        super().__init__([HOLE] * size)
        self.properties: dict[str, Any] = {}


_CUSTOM_PROTOTYPES = frozenset("""
MultiKeyMap AIInterface Diplomacy Formation Foundation GuiInterface Identity
ModifiersManager Player ProductionQueue Researcher StatusBars TechnologyManager Trainer
""".split())
_NULL_PROTOTYPES = frozenset("""
AIProxy DeathDamage Loot Looter RangeOverlayManager SkirmishReplacer Sound
TrainingRestrictions ValueModificationManager WallPiece WallSet Wonder
""".split())
_PROPERTY_PROTOTYPES = frozenset("""
AlertRaiser Attack AttackDetection Auras AutoBuildable Barter BattleDetection
Builder BuildingAI BuildRestrictions Capturable CeasefireManager Cost DelayedDamage
EndGameManager EntityLimits Fogging FormationAttack Garrisonable GarrisonHolder
Gate Guard Heal Health Market Mirage MotionBall Pack PlayerManager Population PopulationCapManager
Promotion RallyPoint Repairable Resistance ResourceDropsite ResourceGatherer
ResourceSupply ResourceTrickle Settlement StatisticsTracker StatusEffectsReceiver
TerritoryDecay TerritoryDecayManager Timer Trader Treasure TreasureCollector
Trigger TriggerPoint Turretable TurretHolder UnitAI UnitMotionFlying Upgrade
Upkeep Visibility VisionSharing Vector2D Vector3D
""".split())
_SCRIPT_COMPONENTS = (_CUSTOM_PROTOTYPES | _NULL_PROTOTYPES | _PROPERTY_PROTOTYPES) - {
    "MultiKeyMap", "Vector2D", "Vector3D"
}
_CUSTOM_PROTOTYPES |= {"SharedScript", "BaseAI", "PetraBot"}
_PROPERTY_PROTOTYPES |= frozenset("""
CapturableMirage FoundationMirage HealthMirage IdentityMirage MarketMirage
RepairableMirage ResistanceMirage ResourceSupplyMirage
""".split())
_TYPED_ARRAYS = (
    ("Int8Array", "b", 1), ("Uint8Array", "B", 1),
    ("Int16Array", "h", 2), ("Uint16Array", "H", 2),
    ("Int32Array", "i", 4), ("Uint32Array", "I", 4),
    ("Float32Array", "f", 4), ("Float64Array", "d", 8),
    ("Uint8ClampedArray", "B", 1),
)


class Reader:
    """Length-, depth-, and allocation-limited little-endian reader."""

    MAX_ITEMS = 2_000_000
    MAX_STRING = 16_000_000
    MAX_DEPTH = 256
    MAX_NODES = 4_000_000
    MAX_BYTES = 256 * 1024 * 1024
    MAX_TOTAL_ITEMS = 8_000_000

    def __init__(self, data: bytes):
        if len(data) > self.MAX_BYTES:
            raise DecodeError("simulation exceeds 256 MiB decoder limit")
        self.data = memoryview(data)
        self.offset = 0
        self.context = "header"
        self.backrefs: list[Any] = [None]
        self.nodes = 0
        self.allocated_items = 0
        self.templates: dict[int, str] = {}
        self.obstruction_types: Mapping[str, str] | Callable[[str], str | None] = {}

    def fail(self, message: str) -> None:
        raise DecodeError(f"{message} at byte {self.offset} ({self.context})")

    def raw(self, size: int) -> bytes:
        if size < 0 or size > len(self.data) - self.offset:
            self.fail(f"truncated input: need {size} bytes")
        start = self.offset
        self.offset += size
        return self.data[start:self.offset].tobytes()

    def number(self, fmt: str) -> int | float:
        size = struct.calcsize(fmt)
        if size > len(self.data) - self.offset:
            self.fail(f"truncated {fmt} number")
        value = struct.unpack_from("<" + fmt, self.data, self.offset)[0]
        self.offset += size
        return value

    def u8(self) -> int:
        return self.number("B")

    def i8(self) -> int:
        return self.number("b")

    def u16(self) -> int:
        return self.number("H")

    def u32(self) -> int:
        return self.number("I")

    def i32(self) -> int:
        return self.number("i")

    def fixed(self) -> float:
        return self.i32() / 65536

    def boolean(self) -> bool:
        value = self.u8()
        if value > 1:
            self.fail(f"invalid boolean {value}")
        return bool(value)

    def count(self, limit: int | None = None) -> int:
        value = self.u32()
        if value > (self.MAX_ITEMS if limit is None else limit):
            self.fail(f"collection count {value} exceeds limit")
        return value

    def reserve(self, count: int) -> int:
        self.allocated_items += count
        if self.allocated_items > self.MAX_TOTAL_ITEMS:
            self.fail("aggregate collection allocation exceeds limit")
        return count

    def text(self, ascii_only: bool = False) -> str:
        value = self.raw(self.count(self.MAX_STRING))
        try:
            result = value.decode("ascii" if ascii_only else "utf-8")
        except UnicodeError:
            self.fail("invalid native string encoding")
        if "\0" in result:
            self.fail("native string contains NUL")
        return result

    def js_string(self) -> str:
        latin1 = self.boolean()
        size = self.count(self.MAX_STRING)
        # JS permits unpaired UTF-16 surrogates; preserve rather than discard.
        return self.raw(size if latin1 else size * 2).decode(
            "latin-1" if latin1 else "utf-16-le", errors="surrogatepass"
        )

    def vector(self, read: Callable[[], Any]) -> list:
        return [read() for _ in range(self.reserve(self.count()))]

    def mapping(self, key: Callable[[], Any], value: Callable[[], Any]) -> dict:
        result = {}
        for _ in range(self.reserve(self.count())):
            k = key()
            if k in result:
                self.fail(f"duplicate native map key {k!r}")
            result[k] = value()
        return result

    def fields(self, names: str, read: Callable[[], Any]) -> dict:
        return {name: read() for name in names.split()}

    def grid(self, read: Callable[[], Any]) -> dict:
        width, height = self.u16(), self.u16()
        size = width * height
        if size > self.MAX_ITEMS * 8:
            self.fail("grid dimensions exceed limit")
        runs, total = [], 0
        while total < size:
            count = self.u32()
            if not count or total + count > size:
                self.fail("invalid grid RLE run")
            self.reserve(1)
            runs.append([count, read()])
            total += count
        return {"width": width, "height": height, "encoding": "rle", "runs": runs}

    def _register(self, obj: Any) -> Any:
        self.reserve(1)
        self.backrefs.append(obj)
        if len(self.backrefs) > self.MAX_ITEMS:
            self.fail("JavaScript object count exceeds limit")
        return obj

    def _properties(self, obj: dict | JSArray, depth: int) -> None:
        seen = set()
        for _ in range(self.reserve(self.count())):
            key = self.js_string()
            if key in seen:
                self.fail(f"duplicate JavaScript property {key!r}")
            seen.add(key)
            value = self.script_value(depth + 1)
            if isinstance(obj, JSArray):
                index = int(key) if len(key) <= 10 and key.isascii() and key.isdecimal() else -1
                if 0 <= index < len(obj) and str(index) == key:
                    obj[index] = value
                else:
                    if 0 <= index < 2**32 - 1 and str(index) == key:
                        self.fail("array index exceeds declared length")
                    obj.properties[key] = value
            else:
                obj[key] = value

    def script_value(self, depth: int = 0) -> Any:
        if depth > self.MAX_DEPTH:
            self.fail("JavaScript nesting exceeds limit")
        self.nodes += 1
        if self.nodes > self.MAX_NODES:
            self.fail("JavaScript value count exceeds limit")
        tag = self.u8()
        if tag == 0:
            return UNDEFINED
        if tag == 1:
            return None
        if tag == 4:
            return self.js_string()
        if tag == 5:
            return self.i32()
        if tag == 6:
            return self.number("d")
        if tag == 7:
            return self.boolean()
        if tag == 8:
            index = self.i32()
            if not 0 < index < len(self.backrefs):
                self.fail(f"invalid JavaScript backreference {index}")
            return self.backrefs[index]
        if tag == 2:
            obj = self._register(JSArray(self.reserve(self.count())))
            self._properties(obj, depth)
            return obj
        if tag == 3:
            obj = self._register(JSObject())
            self._properties(obj, depth)
            return obj
        if tag == 11:
            obj = self._register(JSObject())
            obj.prototype = self.text()
            if obj.prototype in _NULL_PROTOTYPES:
                return obj
            if obj.prototype in _CUSTOM_PROTOTYPES:
                payload = self.script_value(depth + 1)
                if isinstance(payload, dict) and not payload.get("$type"):
                    obj.update(payload)
                else:
                    obj["$value"] = payload
                return obj
            if obj.prototype not in _PROPERTY_PROTOTYPES:
                self.fail(f"unknown JavaScript prototype {obj.prototype!r}")
            self._properties(obj, depth)
            return obj
        if tag == 10:
            return self._register(bytearray(self.raw(self.count(self.MAX_BYTES))))
        if tag == 9:
            obj = self._register({"$type": "TypedArray"})
            subtype = self.u8()
            if subtype >= len(_TYPED_ARRAYS):
                self.fail(f"unknown typed array subtype {subtype}")
            name, _, itemsize = _TYPED_ARRAYS[subtype]
            offset, length = self.u32(), self.count()
            buffer = self.script_value(depth + 1)
            if not isinstance(buffer, bytearray) or offset % itemsize or offset + length * itemsize > len(buffer):
                self.fail("invalid typed array buffer bounds")
            obj.update(name=name, byteOffset=offset, length=length, buffer=buffer)
            return obj
        if tag in (12, 13, 14):
            obj = self._register({"$type": ("Number", "String", "Boolean")[tag - 12]})
            obj["value"] = (self.number("d") if tag == 12 else self.js_string() if tag == 13 else self.boolean())
            return obj
        if tag == 15:
            obj = self._register({"$type": "Map", "entries": []})
            for _ in range(self.reserve(self.count())):
                obj["entries"].append([self.script_value(depth + 1), self.script_value(depth + 1)])
            return obj
        if tag == 16:
            obj = self._register({"$type": "Set", "values": []})
            for _ in range(self.reserve(self.count())):
                obj["values"].append(self.script_value(depth + 1))
            return obj
        self.fail(f"unknown JavaScript value tag {tag}")

    def position(self) -> dict:
        result = {"inWorld": self.boolean()}
        if result["inWorld"]:
            result.update(self.fields("x y z lastX lastYDifference lastZ", self.fixed))
        result.update(self.fields("rotX rotY rotZ rotYSpeed altitude", self.fixed))
        result.update(self.fields("relative floating", self.boolean))
        result.update(self.fields("floatDepth constructionProgress", self.fixed))
        result["turretParent"] = self.u32()
        if result["turretParent"]:
            result.update(self.fields("turretX turretY turretZ", self.fixed))
        result["turrets"] = self.vector(self.u32)
        return result

    def template_manager(self) -> dict:
        entries = self.mapping(lambda: self.text(True), lambda: self.vector(self.u32))
        for name, ids in entries.items():
            self.reserve(len(ids))
            for entity in ids:
                if entity in self.templates:
                    self.fail(f"entity {entity} has multiple templates")
                self.templates[entity] = name
        return {"templates": entries}

    def spatial(self) -> dict:
        result = {"divisionSize": self.fixed(), "width": self.u32(), "height": self.u32()}
        size = result["width"] * result["height"]
        if size > self.MAX_ITEMS:
            self.fail("spatial subdivision dimensions exceed limit")
        self.reserve(size)
        result["cells"] = [self.vector(self.u32) for _ in range(size)]
        return result

    def obstruction_manager(self) -> dict:
        result = {"unitSubdivision": self.spatial(), "staticSubdivision": self.spatial(), "maxClearance": self.fixed()}

        def shape(unit: bool) -> dict:
            item = {"entity": self.u32()}
            item.update(self.fields("x z clearance" if unit else "x z ux uz vx vz halfWidth halfHeight", self.fixed))
            item["flags"] = self.u8()
            item.update(self.fields("group" if unit else "group group2", self.u32))
            return item

        result["unitShapes"] = self.mapping(self.u32, lambda: shape(True))
        result["staticShapes"] = self.mapping(self.u32, lambda: shape(False))
        result.update(self.fields("unitShapeNext staticShapeNext", self.u32))
        result["circular"] = self.boolean()
        result.update(self.fields("worldX0 worldZ0 worldX1 worldZ1", self.fixed))
        return result

    def obstruction(self, entity: int) -> dict:
        result = self.fields("active moving", self.boolean)
        result.update(self.fields("controlGroup controlGroup2 tag", self.u32))
        result["flags"] = self.u8()
        template = self.templates.get(entity)
        resolver = self.obstruction_types
        kind = resolver(template) if callable(resolver) else resolver.get(template)
        if kind is None:
            self.fail(f"Obstruction layout requires template kind for {template!r} (entity {entity})")
        kind = kind.lower()
        if kind == "cluster":
            result["clusterTags"] = self.vector(self.u32)
        elif kind == "unit":
            result["clearance"] = self.fixed()
        elif kind != "static":
            self.fail(f"unknown Obstruction template kind {kind!r}")
        result["kind"] = kind
        return result

    def range_manager(self) -> dict:
        result = self.fields("worldX0 worldZ0 worldX1 worldZ1", self.fixed)
        result["queryNext"] = self.u32()

        def query() -> dict:
            item = self.fields("minRange maxRange yOrigin", self.fixed)
            item["ownersMask"] = self.u32()
            item["interface"] = self.i32()
            item["lastMatch"] = self.vector(self.u32)
            item["flagsMask"] = self.u8()
            item.update(self.fields("enabled parabolic accountForSize", self.boolean))
            item["source"] = self.u32()
            return item

        def entity() -> dict:
            item = self.fields("x z visionRange", self.fixed)
            item.update(self.fields("visibilities size", self.u32))
            item["visionSharing"] = self.u16()
            item["owner"] = self.i8()
            item["flags"] = self.u8()
            return item

        result["queries"] = self.mapping(self.u32, query)
        result["entities"] = self.mapping(self.u32, entity)
        result["losRevealAll"] = [self.boolean() for _ in range(18)]
        result["losCircular"] = self.boolean()
        result["losVerticesPerSide"] = self.i32()
        result["globalVisibilityUpdate"] = self.boolean()
        result["globalPlayerVisibilityUpdate"] = [self.boolean() for _ in range(16)]
        result["dirtyVisibility"] = self.grid(self.u16)
        result["modifiedEntities"] = self.vector(self.u32)
        result["losState"] = self.grid(self.u32)
        result["sharedLosMasks"] = [self.u32() for _ in range(18)]
        result["sharedDirtyVisibilityMasks"] = [self.u16() for _ in range(18)]
        return result

    def path_goal(self) -> dict:
        result = {"type": self.u8()}
        if result["type"] > 4:
            self.fail("unknown PathGoal type")
        result.update(self.fields("x z ux uz vx vz halfWidth halfHeight maxDistance", self.fixed))
        return result

    def pathfinder(self) -> dict:
        def request(short: bool) -> dict:
            item = {"ticket": self.u32()}
            item.update(self.fields("x0 z0 clearance range" if short else "x0 z0", self.fixed))
            item["goal"] = self.path_goal()
            item["passClass"] = self.u16()
            if short:
                item["avoidMovingUnits"] = self.boolean()
                item["group"] = self.u32()
            item["notify"] = self.u32()
            return item

        return {"longRequests": self.vector(lambda: request(False)), "shortRequests": self.vector(lambda: request(True)), "nextTicket": self.u32(), "gridSize": self.u16()}

    def unit_motion(self) -> dict:
        result = {"passClass": self.text(True), "expectedPathTicket": self.u32()}
        result.update(self.fields("ticketType failedMovements followKnownImperfectPathCountdown moveRequestType", self.u8))
        result["targetEntity"] = self.u32()
        result.update(self.fields("targetPosX targetPosY targetMinRange targetMaxRange", self.fixed))
        result["formationController"] = self.u32()
        result.update(self.fields("speedMultiplier lastTurnSpeed currentSpeed instantTurnAngle acceleration", self.fixed))
        result.update(self.fields("facePointAfterMove pushing", self.boolean))
        result["longPath"] = self.vector(lambda: self.fields("x z", self.fixed))
        result["shortPath"] = self.vector(lambda: self.fields("x z", self.fixed))
        return result

    def visual_actor(self) -> dict:
        result = self.fields("r g b", self.fixed)
        result["animationName"] = self.text(True)
        result["animOnce"] = self.boolean()
        result["animSpeed"] = self.fixed()
        result["soundGroup"] = self.text()
        result.update(self.fields("animDesync animSyncRepeatTime animSyncOffsetTime", self.fixed))
        result["variantSelections"] = self.mapping(lambda: self.text(True), lambda: self.text(True))
        result["seed"] = self.u32()
        result["actorName"] = self.text()
        return result

    def cinema_manager(self) -> dict:
        result = {"enabled": self.boolean(), "elapsed": self.fixed(), "currentPathElapsed": self.fixed(), "mapRevealed": self.boolean()}

        def path() -> dict:
            item = self.fields("name orientation mode style", self.text)
            item["timescale"] = self.fixed()
            item["lookAtTarget"] = self.boolean()
            item["nodes"] = self.vector(lambda: self.fields("deltaTime x y z rotX rotY rotZ", self.fixed))
            if item["lookAtTarget"]:
                item["targetNodes"] = self.vector(lambda: self.fields("deltaTime x y z", self.fixed))
            return item

        result["paths"] = self.vector(path)
        result["queuedPaths"] = self.vector(self.text)
        return result

    def ai_manager(self) -> dict:
        result = {"numAIs": self.count(16)}
        if not result["numAIs"]:
            return result
        # CAIWorker creates its own CStdSerializer on the same byte stream.
        # Its JS objects therefore have a separate backreference numbering domain.
        outer_backrefs, self.backrefs = self.backrefs, [None]
        try:
            result["rngState"] = self.text(True)
            result["turn"] = self.u32()
            result["useSharedScript"] = self.boolean()
            if result["useSharedScript"]:
                result["sharedData"] = self.script_value()
            result["players"] = []
            for _ in range(result["numAIs"]):
                player = {"name": self.text(), "player": self.i32(), "difficulty": self.u8(), "behavior": self.text()}
                player["commands"] = self.vector(self.script_value)
                player["data"] = self.script_value()
                result["players"].append(player)
            result["nonPathfindingPassClasses"] = self.mapping(lambda: self.text(True), self.u16)
            result["pathfindingPassClasses"] = self.mapping(lambda: self.text(True), self.u16)
            width, height = self.u16(), self.u16()
            if width * height > self.MAX_ITEMS * 8:
                self.fail("AI passability grid dimensions exceed limit")
            result["passabilityGrid"] = {"width": width, "height": height, "encoding": "u16-le", "data": bytearray(self.raw(width * height * 2))}
        finally:
            self.backrefs = outer_backrefs
        return result

    def component(self, name: str, entity: int = 0) -> Any:
        if name in _SCRIPT_COMPONENTS:
            # 0.28 ICmpPlayer.cpp serializes a native activity flag before its JS object.
            is_active = self.boolean() if name == "Player" else None
            result = self.script_value()
            if not isinstance(result, JSObject) or result.prototype != name:
                self.fail(f"component {name!r} does not contain its registered prototype")
            if name == "Player":
                if "$nativeIsActive" in result:
                    self.fail("Player state collides with native decoder metadata")
                result["$nativeIsActive"] = is_active
            return result
        if name in {"Terrain", "Footprint", "Decay", "Selectable", "ParticleManager", "SoundManager", "OverlayRenderer", "RangeOverlayRenderer", "RallyPointRenderer", "UnitRenderer", "TerritoryInfluence", "Vision"}:
            return {}
        if name == "TemplateManager":
            return self.template_manager()
        if name == "AIManager":
            return self.ai_manager()
        if name == "CommandQueue":
            return {"commands": self.vector(lambda: {"player": self.i32(), "data": self.script_value()})}
        if name == "Position":
            return self.position()
        if name == "Ownership":
            return {"owner": self.i32()}
        if name == "WaterManager":
            return {"height": self.fixed()}
        if name == "TerritoryManager":
            return {"triggerEvent": self.boolean()}
        if name == "ProjectileManager":
            return {"nextID": self.u32()}
        if name == "CinemaManager":
            return self.cinema_manager()
        if name == "RangeManager":
            return self.range_manager()
        if name == "ObstructionManager":
            return self.obstruction_manager()
        if name == "Obstruction":
            return self.obstruction(entity)
        if name == "Pathfinder":
            return self.pathfinder()
        if name == "UnitMotion":
            return self.unit_motion()
        if name == "UnitMotionManager":
            return {"units": self.mapping(self.u32, self.u8), "formationControllers": self.mapping(self.u32, self.u8)}
        if name == "VisualActor":
            return self.visual_actor()
        if name == "Minimap":
            result = {"active": self.boolean()}
            if result["active"]:
                result.update(self.fields("x z", self.fixed))
            return result
        self.fail(f"unsupported component {name!r}")


def decode_simulation(
    data: bytes,
    version: str = "0.28.0",
    *,
    obstruction_types: Mapping[str, str] | Callable[[str], str | None] | None = None,
) -> dict:
    """Decode a complete simulation stream, rejecting trailing data.

    ``obstruction_types`` resolves a template name to ``unit``, ``static``, or
    ``cluster`` from that template's merged Obstruction XML. It is required when
    Obstruction components exist, because their native payload is template-bound.
    Native computer AI workers use a separate JavaScript backreference domain;
    the stock Petra AI and its shared script are supported without executing JS.
    """
    if version != "0.28.0":
        raise DecodeError(f"unsupported serialization version {version!r}; expected '0.28.0'")
    reader = Reader(data)
    reader.obstruction_types = obstruction_types or {}
    result = {"rng_state": reader.text(True), "next_entity_id": reader.u32(), "systems": {}, "components": {}}
    for _ in range(reader.count(256)):
        name = reader.text(True)
        reader.context = f"system {name}"
        if name in result["systems"]:
            reader.fail(f"duplicate system {name!r}")
        result["systems"][name] = reader.component(name)
    for _ in range(reader.count(256)):
        name = reader.text(True)
        reader.context = f"component {name}"
        if name in result["components"]:
            reader.fail(f"duplicate component type {name!r}")
        components = result["components"][name] = {}
        for _ in range(reader.reserve(reader.count())):
            entity = reader.u32()
            if entity in components:
                reader.fail(f"duplicate entity {entity} for component {name}")
            reader.context = f"component {name}, entity {entity}, template {reader.templates.get(entity)!r}"
            components[entity] = reader.component(name, entity)
    if reader.offset != len(data):
        reader.fail(f"unexpected trailing {len(data) - reader.offset} bytes")
    result["templates"] = reader.templates
    result["consumed_bytes"] = reader.offset
    result["total_bytes"] = len(data)
    return result


def to_jsonable(value: Any) -> Any:
    """Convert decoded values to JSON, preserving cycles/shared object identity.

    Repeated containers become ``{"$ref": "#/path"}``. Maps and Sets retain
    explicit type markers; sparse holes differ from undefined and null. Binary
    ArrayBuffers use base64 rather than lossy text conversion.
    """
    seen: dict[int, str] = {}

    def walk(obj: Any, path: str) -> Any:
        if isinstance(obj, _Sentinel):
            return {"$type": obj.name}
        if isinstance(obj, float) and not math.isfinite(obj):
            return {"$type": "Number", "value": str(obj)}
        if not isinstance(obj, (dict, list, bytearray)):
            return obj
        if id(obj) in seen:
            return {"$ref": seen[id(obj)]}
        seen[id(obj)] = path
        if isinstance(obj, bytearray):
            return {"$type": "ArrayBuffer", "base64": base64.b64encode(obj).decode("ascii")}

        def child_path(key: Any) -> str:
            return path + "/" + str(key).replace("~", "~0").replace("/", "~1")

        if isinstance(obj, JSArray) and obj.properties:
            return {"$type": "Array", "items": [walk(v, path + "/items/" + str(i)) for i, v in enumerate(obj)], "properties": {k: walk(v, path + "/properties/" + k.replace("~", "~0").replace("/", "~1")) for k, v in obj.properties.items()}}
        if isinstance(obj, list):
            return [walk(v, child_path(i)) for i, v in enumerate(obj)]
        result = {str(k): walk(v, child_path(k)) for k, v in obj.items()}
        if isinstance(obj, JSObject) and obj.prototype:
            result["$prototype"] = obj.prototype
        return result

    return walk(value, "#")
