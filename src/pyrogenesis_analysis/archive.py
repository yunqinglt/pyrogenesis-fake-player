"""Read-only, bounded access to a 0 A.D. save archive. No game or MCP imports."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import zipfile
import zlib


class SaveError(ValueError):
    """An archive cannot be analyzed safely or without guessing."""


MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_SIMULATION_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 128


@dataclass(frozen=True)
class SaveArchive:
    path: Path
    sha256: str
    metadata: dict
    simulation: bytes
    entries: tuple[dict, ...]


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SaveError(f"Duplicate metadata key: {key!r}")
        result[key] = value
    return result


def _constant(value):
    raise SaveError(f"Non-finite JSON number in metadata: {value}")


def _metadata(data: bytes) -> dict:
    try:
        result = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_object,
                            parse_constant=_constant)
    except SaveError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise SaveError(f"Invalid metadata.json: {exc}") from exc
    if not isinstance(result, dict):
        raise SaveError("metadata.json must contain an object")
    stack = [(result, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > 64 or count > 100_000:
            raise SaveError("Metadata nesting or item count exceeds supported bounds")
        if isinstance(item, dict):
            stack.extend((value, depth + 1) for value in item.values())
        elif isinstance(item, list):
            stack.extend((value, depth + 1) for value in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise SaveError("Non-finite JSON number in metadata")
    version = result.get("engine_serialization_version")
    if not isinstance(version, str) or not version or len(version) > 100:
        raise SaveError("Missing engine_serialization_version in metadata")
    return result


def read_save(path: str | Path) -> SaveArchive:
    """Read required entries in memory; never extract paths or execute save content."""
    source = Path(path).expanduser().resolve()
    try:
        with source.open("rb") as stream:
            original_stat = os.fstat(stream.fileno())
            length = original_stat.st_size
            if not 0 < length <= MAX_ARCHIVE_BYTES:
                raise SaveError("Save archive exceeds supported size or is empty")
            digest = hashlib.sha256()
            total = 0
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise SaveError("Save archive exceeds supported size")
                digest.update(chunk)
            stream.seek(0)
            with zipfile.ZipFile(stream) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_ARCHIVE_ENTRIES:
                    raise SaveError("Too many archive entries")
                entries = tuple({"name": item.filename, "bytes": item.file_size,
                                 "compressed_bytes": item.compress_size} for item in infos)
                values = {}
                for name, limit in (("metadata.json", MAX_METADATA_BYTES),
                                    ("simulation.dat", MAX_SIMULATION_BYTES)):
                    matches = [item for item in infos if item.filename == name]
                    if len(matches) != 1:
                        raise SaveError(f"Expected exactly one {name} entry")
                    info = matches[0]
                    if not 0 < info.file_size <= limit or info.flag_bits & 1:
                        raise SaveError(f"Invalid size or encryption for {name}")
                    with archive.open(info) as item:
                        data = item.read(limit + 1)
                    if len(data) != info.file_size or len(data) > limit:
                        raise SaveError(f"Unexpected decompressed size for {name}")
                    values[name] = data
            final_stat = os.fstat(stream.fileno())
            if (original_stat.st_size, original_stat.st_mtime_ns) != (final_stat.st_size, final_stat.st_mtime_ns):
                raise SaveError("Save archive changed while reading; retry after saving has finished")
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error) as exc:
        raise SaveError(f"Cannot read save archive {source}: {exc}") from exc
    return SaveArchive(source, digest.hexdigest(), _metadata(values["metadata.json"]),
                       values["simulation.dat"], entries)
