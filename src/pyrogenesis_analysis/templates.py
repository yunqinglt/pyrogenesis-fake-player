"""Optional, read-only native XML templates for offline savegame reports.

These are unmodified template values, never current technology-adjusted stats.
The catalog is independent of the MCP, game processes and the running simulation.
Sources follow the supplied low-to-high mod precedence. No archive is extracted.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile


MAX_XML_BYTES = 1024 * 1024
MAX_XML_NODES = 20000
MAX_XML_DEPTH = 64
MAX_INHERITANCE_DEPTH = 100
MAX_LAYERS = 512
_PREFIX = "simulation/templates/"
_PART = re.compile(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*")
_ACTOR = re.compile(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\.xml")
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_CONTROLS = {"parent", "disable", "replace", "merge", "filtered", "op"}


class TemplateError(ValueError):
    """A requested static template could not be safely and faithfully loaded."""


class TemplateNotFound(TemplateError):
    """No supplied source contains the requested template."""


def discover_public_sources() -> list[Path]:
    """Find installed public data only; never silently substitute a source checkout."""
    candidates = []
    executable = os.environ.get("PYROGENESIS_EXE")
    if executable:
        candidates.append(Path(executable).expanduser().parent.parent / "data/mods/public")
    for variable in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "0 A.D. Empires Ascendant/binaries/data/mods/public")
    result = []
    for candidate in candidates:
        if candidate.is_dir() and candidate not in result:
            result.append(candidate)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        # These are ordinary read-only copies of public game data, not a live
        # MCP connection. Version checks and provenance still apply.
        runtime = Path(local) / "pyrogenesis-mcp/host-runtime"
        if runtime.is_dir():
            for entry in sorted(runtime.iterdir()):
                candidate = entry / "binaries/data/mods/public"
                if candidate.is_dir() and candidate not in result:
                    result.append(candidate)
    return result[:32]


def _safe_id(template_id: str) -> str:
    if not isinstance(template_id, str) or not 0 < len(template_id) <= 512:
        raise TemplateError("Invalid template identifier")
    if template_id.startswith("actor|"):
        if not _ACTOR.fullmatch(template_id[6:]):
            raise TemplateError("Invalid actor template identifier")
    elif len(template_id.split("|")) > 16 or any(
            _PART.fullmatch(part) is None for part in template_id.split("|")):
        raise TemplateError("Invalid template identifier")
    return template_id


def _parse_xml(data: bytes) -> ET.Element:
    if len(data) > MAX_XML_BYTES:
        raise TemplateError("Template XML exceeds the size limit")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TemplateError("Template XML must use UTF-8") from exc
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        raise TemplateError("Template DTDs and entities are not supported")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise TemplateError("Malformed template XML") from exc
    if root.tag != "Entity":
        raise TemplateError("A template must have an Entity root")
    pending = [(root, 0)]
    count = 0
    while pending:
        element, depth = pending.pop()
        count += 1
        if count > MAX_XML_NODES or depth > MAX_XML_DEPTH:
            raise TemplateError("Template XML exceeds structural limits")
        if not isinstance(element.tag, str) or "{" in element.tag:
            raise TemplateError("Template XML namespaces are unsupported")
        pending.extend((child, depth + 1) for child in element)
    return root


def _fixed(value: str | None) -> int:
    """0 A.D. template arithmetic uses signed 16.16 fixed-point numbers."""
    value = (value or "0").strip()
    if _DECIMAL.fullmatch(value) is None:
        raise TemplateError("Unsupported numeric template operation")
    # Native FromString consumes at most five fractional decimal digits and
    # truncates the positive magnitude before applying the sign.
    negative = value.startswith("-")
    integer, _, fraction = value.lstrip("+-").partition(".")
    fraction = fraction[:5]
    result = int(integer or "0") * 65536
    if fraction:
        result += int(fraction) * 65536 // 10**len(fraction)
    if negative:
        result = -result
    if not -(2**31) <= result < 2**31:
        raise TemplateError("Template arithmetic exceeds fixed-point range")
    return result


def _apply_numeric(parent: ET.Element, incoming: ET.Element) -> None:
    """Wrap recursive layering so numeric operations see inherited values."""
    operation = incoming.get("op")
    if operation is not None and "disable" not in incoming.attrib:
        if operation not in ("add", "mul", "mul_round"):
            raise TemplateError(f"Unsupported template operation: {operation}")
        if incoming.get("datatype") == "tokens":
            raise TemplateError("Combining token and numeric template operations is unsupported")
        previous = parent.find(incoming.tag)
        if "merge" in incoming.attrib and previous is None:
            return
        old = 0 if "replace" in incoming.attrib or previous is None else _fixed(previous.text)
        modifier = _fixed(incoming.text)
        result = old + modifier if operation == "add" else (old * modifier) >> 16
        if operation == "mul_round":
            result = ((result + 32768) >> 16) << 16
        if not -(2**31) <= result < 2**31:
            raise TemplateError("Template arithmetic exceeds fixed-point range")
        layer = deepcopy(incoming)
        del layer.attrib["op"]
        layer.attrib.pop("merge", None)
        # Native ToString emits a short decimal that reparses to this exact
        # fixed value. Five digits suffice; emitting exact binary fractions
        # would change later operations because FromString truncates at five.
        decimal_units = (abs(result) * 100000 + 65535) // 65536
        layer.text = ("-" if result < 0 else "") + format(Decimal(decimal_units) / 100000, "f")
        _apply_numeric(parent, layer)
        return
    # Ordinary recursive layering.
    existing = parent.find(incoming.tag)
    if "disable" in incoming.attrib:
        if existing is not None:
            parent.remove(existing)
        return
    if "replace" in incoming.attrib and existing is not None:
        parent.remove(existing)
        existing = None
    if "merge" in incoming.attrib and existing is None:
        return
    if existing is None:
        existing = ET.SubElement(parent, incoming.tag)
    if incoming.get("datatype") == "tokens":
        old = (existing.text or "").split()
        tokens = list(old)
        for token in (incoming.text or "").split():
            if token.startswith("-"):
                if token[1:] in tokens:
                    tokens.remove(token[1:])
            elif token not in old:
                tokens.append(token)
        existing.text = " ".join(tokens)
    elif "merge" not in incoming.attrib:
        existing.text = (incoming.text or "").strip()
    kept = set()
    for child in incoming:
        _apply_numeric(existing, child)
        kept.add(child.tag)
    if "filtered" in incoming.attrib:
        for child in list(existing):
            if child.tag not in kept:
                existing.remove(child)
    for key, value in incoming.attrib.items():
        if key not in _CONTROLS:
            existing.set(key, value)


class _Source:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.archive = None
        self.version = None
        if self.path.is_file() and self.path.suffix.lower() == ".zip":
            try:
                self.archive = zipfile.ZipFile(self.path)
            except (OSError, zipfile.BadZipFile) as exc:
                raise TemplateError(f"Cannot read template archive {self.path}: {exc}") from exc
        elif not self.path.is_dir():
            raise TemplateError(f"Template source does not exist: {self.path}")
        manifest = self.read("mod.json", limit=32768)
        if manifest is not None:
            try:
                data = json.loads(manifest)
                self.version = data.get("version") if isinstance(data, dict) else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass

    def read(self, name: str, *, limit=MAX_XML_BYTES) -> bytes | None:
        if self.archive is not None:
            try:
                info = self.archive.getinfo(name)
            except KeyError:
                return None
            if info.file_size > limit or info.flag_bits & 1:
                raise TemplateError("Template archive entry exceeds limits or is encrypted")
            try:
                with self.archive.open(info) as stream:
                    data = stream.read(limit + 1)
            except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
                raise TemplateError(f"Cannot read template archive entry {name}") from exc
        else:
            # A caller may give either a mod root or simulation/templates itself.
            relative = name
            if name.startswith(_PREFIX) and self.path.name == "templates":
                relative = name[len(_PREFIX):]
            target = (self.path / relative).resolve()
            if not target.is_relative_to(self.path):
                raise TemplateError("Template path escaped its source")
            try:
                with target.open("rb") as stream:
                    data = stream.read(limit + 1)
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise TemplateError(f"Cannot read template file {target}: {exc}") from exc
        if len(data) > limit:
            raise TemplateError("Template source entry exceeds the size limit")
        return data

    def close(self):
        if self.archive is not None:
            self.archive.close()


class TemplateCatalog:
    """Resolve optional template sources without consulting any live game state.

    ``sources`` are mod roots, template roots or ZIPs, in low-to-high precedence.
    Directory public.zip data is below the same directory's loose overrides.
    ``expected_version`` rejects identified source-version mismatches; unversioned
    sources are retained as such rather than asserted to match.
    """

    def __init__(self, sources=None, *, expected_version: str | None = None):
        self.sources = []
        self._cache = {}
        self._layers = {}
        self.discovery_notes = []
        paths = discover_public_sources() if sources is None else list(sources)
        if not paths:
            raise TemplateError("No installed template source was found; supply an explicit directory or ZIP")
        if len(paths) > 32:
            raise TemplateError("Too many template sources")
        groups = ([path] for path in paths) if sources is None else [paths]
        for group in groups:
            try:
                for path in group:
                    path = Path(path).expanduser()
                    packed = path / "public.zip"
                    if path.is_dir() and packed.is_file():
                        self.sources.append(_Source(packed))
                    self.sources.append(_Source(path))
                if expected_version:
                    for source in self.sources:
                        if source.version is not None and source.version != expected_version:
                            raise TemplateError(f"Template source version {source.version} does not match {expected_version}")
                break
            except TemplateError as exc:
                self.close()
                self.sources = []
                if sources is not None:
                    raise
                self.discovery_notes.append(str(exc))
        if not self.sources:
            raise TemplateError("No readable version-compatible template source: " + "; ".join(self.discovery_notes))

    def close(self):
        for source in self.sources:
            source.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _layer(self, name):
        for suffix in (f"special/filter/{name}.xml", f"mixins/{name}.xml", f"{name}.xml"):
            filename = _PREFIX + suffix
            for source in reversed(self.sources):
                data = source.read(filename)
                if data is not None:
                    return _parse_xml(data), {"template": name, "source": str(source.path),
                                              "file": filename, "version": source.version}
        raise TemplateNotFound(f"Template not found: {name}")

    def _load(self, container, name, active, layers):
        _safe_id(name)
        if len(active) >= MAX_INHERITANCE_DEPTH or name in active:
            raise TemplateError("Template inheritance is cyclic or too deep")
        if len(layers) >= MAX_LAYERS:
            raise TemplateError("Template inheritance exceeds the layer limit")
        active = (*active, name)
        if name.startswith("actor|"):
            self._load(container, "special/actor", active, layers)
            actor = ET.fromstring("<Entity><VisualActor><Actor/></VisualActor></Entity>")
            actor.find("VisualActor/Actor").text = name[6:]
            _apply_numeric(container, actor)
            return
        if "|" in name:
            overlay, parent = name.split("|", 1)
            self._load(container, parent, active, layers)
            self._load(container, overlay, active, layers)
            return
        element, origin = self._layer(name)
        parent = element.get("parent")
        if parent:
            self._load(container, parent, active, layers)
        _apply_numeric(container, element)
        layers.append(origin)

    def resolve(self, template_id: str) -> ET.Element:
        """Return an independent merged Entity element; errors never yield guesses."""
        _safe_id(template_id)
        if template_id not in self._cache:
            container = ET.Element("Templates")
            layers = []
            self._load(container, template_id, (), layers)
            entity = container.find("Entity")
            if entity is None:
                raise TemplateError("Template did not produce an Entity")
            count = 0
            pending = [(entity, 0)]
            while pending:
                node, depth = pending.pop()
                count += 1
                if count > MAX_XML_NODES or depth > MAX_XML_DEPTH:
                    raise TemplateError("Merged template exceeds structural limits")
                pending.extend((child, depth + 1) for child in node)
            if len(self._cache) >= 128:
                self._cache.clear()
                self._layers.clear()
            self._cache[template_id] = entity
            self._layers[template_id] = layers
        return deepcopy(self._cache[template_id])

    def obstruction_type(self, template_id: str) -> str | None:
        """Native Obstruction serialization shape, required to decode its tail."""
        entity = self.resolve(template_id)
        obstruction = entity.find("Obstruction")
        if obstruction is None:
            return None
        if obstruction.find("Unit") is not None:
            return "unit"
        if obstruction.find("Static") is not None:
            return "static"
        return "cluster"

    def describe(self, template_id: str) -> dict:
        entity = self.resolve(template_id)

        def text(path):
            node = entity.find(path)
            return None if node is None else (node.text or "").strip()

        def number(path):
            value = text(path)
            if value is None or value == "":
                return None
            try:
                result = float(value)
            except ValueError as exc:
                raise TemplateError(f"Invalid template number at {path}") from exc
            if not math.isfinite(result) or abs(result) > 1e12:
                raise TemplateError(f"Unsupported template number at {path}")
            return int(result) if result.is_integer() else result

        classes = list(dict.fromkeys(((text("Identity/Classes") or "") + " " +
                                     (text("Identity/VisibleClasses") or "")).split()))
        cost = {resource: number(f"Cost/Resources/{resource}")
                for resource in ("food", "wood", "stone", "metal")}
        cost.update(population=number("Cost/Population"), time=number("Cost/BuildTime"))
        garrison = None if entity.find("GarrisonHolder") is None else {
            "capacity": number("GarrisonHolder/Max"),
            "allowed_classes": (text("GarrisonHolder/List") or "").split()}
        packing = None if entity.find("Pack") is None else {
            "state": text("Pack/State"), "time_ms": number("Pack/Time")}
        layers = deepcopy(self._layers[template_id])
        return {"id": template_id, "basis": "unmodified-template", "translation": "source-language",
                "name": {"generic": text("Identity/GenericName"), "specific": text("Identity/SpecificName")},
                "tooltip": text("Identity/Tooltip"), "civ": text("Identity/Civ"),
                "rank": text("Identity/Rank"), "classes": classes,
                "components": sorted(child.tag for child in entity), "cost": cost,
                "population_bonus": number("Population/Bonus"), "max_hitpoints": number("Health/Max"),
                "garrison": garrison, "packing": packing, "source_layers": layers,
                "source_versions": list(dict.fromkeys(layer["version"] for layer in layers))}
