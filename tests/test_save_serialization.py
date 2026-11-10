"""Binary fixtures follow the upstream 0.28 serialization wire format."""

import json
import struct

import pytest

from pyrogenesis_analysis.serialization import (
    DecodeError, HOLE, UNDEFINED, Reader, decode_simulation, to_jsonable,
)


def u32(value):
    return struct.pack("<I", value)


def native(value):
    data = value.encode("utf-8")
    return u32(len(data)) + data


def js_string(value, latin1=True):
    data = value.encode("latin-1" if latin1 else "utf-16-le", "surrogatepass")
    return bytes([latin1]) + u32(len(data) if latin1 else len(data) // 2) + data


def properties(items=()):
    return u32(len(items)) + b"".join(js_string(key) + value for key, value in items)


def prototype(name, payload=b""):
    return b"\x0b" + native(name) + payload


def simulation(systems=(), components=()):
    return (
        native("42") + u32(10) + u32(len(systems))
        + b"".join(native(name) + payload for name, payload in systems)
        + u32(len(components))
        + b"".join(native(name) + u32(len(items)) + b"".join(u32(entity) + payload for entity, payload in items) for name, items in components)
    )


def test_complete_stream_requires_exact_eof_and_reports_sizes():
    data = simulation([("AIManager", u32(0))])
    result = decode_simulation(data)
    assert result["systems"]["AIManager"] == {"numAIs": 0}
    assert result["consumed_bytes"] == result["total_bytes"] == len(data)
    with pytest.raises(DecodeError, match="trailing"):
        decode_simulation(data + b"extra")
    for end in range(len(data)):
        with pytest.raises(DecodeError):
            decode_simulation(data[:end])


def test_rejects_versions_and_unknown_unframed_components():
    with pytest.raises(DecodeError, match="version"):
        decode_simulation(simulation(), "0.27.1")
    with pytest.raises(DecodeError, match="unsupported component"):
        decode_simulation(simulation([("UnknownModComponent", b"")]))


def test_custom_null_and_property_prototypes_have_distinct_layouts():
    custom = Reader(prototype("Identity", b"\x03" + properties([("rank", b"\x05" + u32(2))]))).script_value()
    assert custom == {"rank": 2}
    assert custom.prototype == "Identity"
    null = Reader(prototype("Sound")).script_value()
    assert null == {} and null.prototype == "Sound"
    standard = Reader(prototype("Health", properties([("hitpoints", b"\x05" + u32(100))]))).script_value()
    assert standard["hitpoints"] == 100
    with pytest.raises(DecodeError, match="unknown JavaScript prototype"):
        Reader(prototype("UnknownModClass", b"\x03" + u32(0))).script_value()


def test_player_native_activity_prefix_is_not_a_js_tag():
    payload = b"\x01" + prototype("Player", b"\x03" + properties([("playerID", b"\x05" + u32(1))]))
    state = decode_simulation(simulation(components=[("Player", [(2, payload)])]))["components"]["Player"][2]
    assert state["playerID"] == 1
    assert state["$nativeIsActive"] is True


def test_component_must_match_its_registered_js_prototype():
    with pytest.raises(DecodeError, match="registered prototype"):
        decode_simulation(simulation(components=[("Health", [(2, prototype("Attack", u32(0)))])]))


def test_sparse_arrays_preserve_holes_undefined_properties_and_cycles():
    raw = b"\x02" + u32(3) + properties([
        ("0", b"\x00"), ("2", b"\x01"), ("self", b"\x08" + u32(1)),
    ])
    value = Reader(raw).script_value()
    assert value[0] is UNDEFINED and value[1] is HOLE and value[2] is None
    assert value.properties["self"] is value
    output = to_jsonable(value)
    assert output["items"] == [{"$type": "undefined"}, {"$type": "array_hole"}, None]
    assert output["properties"]["self"] == {"$ref": "#"}
    json.dumps(output, allow_nan=False)


def test_plain_objects_preserve_self_and_shared_references():
    raw = b"\x03" + properties([("self", b"\x08" + u32(1))])
    value = Reader(raw).script_value()
    assert value["self"] is value
    assert to_jsonable([value, value]) == [{"self": {"$ref": "#/0"}}, {"$ref": "#/0"}]


@pytest.mark.parametrize("raw", [b"\x08" + u32(0), b"\x08" + u32(1), b"\x08" + u32(0xFFFFFFFF), b"\xff", b"\x07\x02"])
def test_rejects_invalid_backrefs_tags_and_booleans(raw):
    with pytest.raises(DecodeError):
        Reader(raw).script_value()


def test_maps_sets_and_typed_array_shared_buffers_are_lossless():
    # Map object #1, ArrayBuffer #2, typed array #3 referring back to #2.
    raw = b"\x0f" + u32(2)
    raw += b"\x04" + js_string("buffer") + b"\x0a" + u32(4) + b"\x01\x02\x03\x04"
    raw += b"\x04" + js_string("view") + b"\x09\x03" + u32(0) + u32(2) + b"\x08" + u32(2)
    value = Reader(raw).script_value()
    assert value["$type"] == "Map"
    assert value["entries"][1][1]["buffer"] is value["entries"][0][1]
    assert value["entries"][1][1]["name"] == "Uint16Array"
    encoded = to_jsonable(value)
    assert encoded["entries"][1][1]["buffer"] == {"$ref": "#/entries/0/1"}
    value = Reader(b"\x10" + u32(1) + b"\x04" + js_string("phase_city")).script_value()
    assert value == {"$type": "Set", "values": ["phase_city"]}


@pytest.mark.parametrize("subtype,offset,length", [(9, 0, 1), (3, 1, 1), (3, 0, 3)])
def test_rejects_invalid_typed_array_bounds(subtype, offset, length):
    raw = b"\x09" + bytes([subtype]) + u32(offset) + u32(length) + b"\x0a" + u32(4) + b"1234"
    with pytest.raises(DecodeError):
        Reader(raw).script_value()


def test_string_encodings_preserve_non_ascii_and_utf16_surrogates():
    assert Reader(b"\x04" + js_string("农田", False)).script_value() == "农田"
    assert Reader(b"\x04" + js_string("\ud800", False)).script_value() == "\ud800"
    assert Reader(b"\x04" + js_string("é")).script_value() == "é"
    with pytest.raises(DecodeError, match="encoding"):
        Reader(u32(1) + b"\xff").text(True)


def test_duplicate_properties_and_invalid_array_length_are_rejected():
    for raw in [
        b"\x03" + properties([("a", b"\x00"), ("a", b"\x01")]),
        b"\x02" + u32(1) + properties([("1", b"\x00")]),
    ]:
        with pytest.raises(DecodeError):
            Reader(raw).script_value()


def test_oversized_and_aggregate_allocations_are_bounded(monkeypatch):
    with pytest.raises(DecodeError, match="exceeds limit"):
        Reader(b"\x02" + u32(0xFFFFFFFF)).script_value()
    monkeypatch.setattr(Reader, "MAX_TOTAL_ITEMS", 3)
    raw = b"\x02" + u32(2) + properties([("0", b"\x02" + u32(2) + u32(0))])
    with pytest.raises(DecodeError, match="aggregate"):
        Reader(raw).script_value()


def test_empty_native_components_also_obey_aggregate_allocation_limit(monkeypatch):
    monkeypatch.setattr(Reader, "MAX_TOTAL_ITEMS", 1)
    raw = simulation(components=[("Footprint", [(5, b""), (6, b"")])])
    with pytest.raises(DecodeError, match="aggregate"):
        decode_simulation(raw)


def test_grid_rle_rejects_zero_run_overflow_and_truncation():
    raw = struct.pack("<HHIH", 2, 2, 4, 7)
    reader = Reader(raw)
    assert reader.grid(reader.u16) == {"width": 2, "height": 2, "encoding": "rle", "runs": [[4, 7]]}
    for count in (0, 5):
        reader = Reader(struct.pack("<HHIH", 2, 2, count, 7))
        with pytest.raises(DecodeError, match="RLE"):
            reader.grid(reader.u16)
    reader = Reader(raw[:-1])
    with pytest.raises(DecodeError, match="truncated"):
        reader.grid(reader.u16)


def test_obstruction_payload_requires_resolved_template_shape():
    templates = u32(1) + native("units/test") + u32(1) + u32(5)
    obstruction = b"\x01\x00" + u32(5) + u32(0) + u32(1) + b"\x00" + struct.pack("<i", 98304)
    raw = simulation([("TemplateManager", templates)], [("Obstruction", [(5, obstruction)])])
    with pytest.raises(DecodeError, match="requires template kind"):
        decode_simulation(raw)
    value = decode_simulation(raw, obstruction_types={"units/test": "unit"})
    assert value["templates"] == {5: "units/test"}
    assert value["components"]["Obstruction"][5]["clearance"] == 1.5


def test_native_ai_worker_has_an_independent_backref_domain():
    # Stock AI Serialize payloads are plain objects. Worker reference #1 is its
    # SharedScript; main stream reference #1 remains the preceding Barter object.
    shared = prototype("SharedScript", b"\x03" + u32(0))
    ai = u32(1) + native("123") + u32(50) + b"\x01" + shared
    ai += native("petra") + struct.pack("<iB", 2, 3) + native("balanced")
    ai += u32(1) + b"\x08" + u32(1) + prototype("BaseAI", b"\x03" + u32(0))
    ai += u32(1) + native("unrestricted") + struct.pack("<H", 4)
    ai += u32(1) + native("default") + struct.pack("<H", 2)
    ai += struct.pack("<HHHH", 2, 1, 0x0102, 0x0304)
    commands = u32(1) + struct.pack("<i", 1) + b"\x08" + u32(1)
    raw = simulation([("Barter", prototype("Barter", u32(0))), ("AIManager", ai), ("CommandQueue", commands)])
    systems = decode_simulation(raw)["systems"]
    worker = systems["AIManager"]
    assert worker["numAIs"] == 1 and worker["turn"] == 50
    assert worker["players"][0]["commands"][0] is worker["sharedData"]
    assert worker["players"][0]["data"].prototype == "BaseAI"
    assert worker["pathfindingPassClasses"] == {"default": 2}
    assert worker["passabilityGrid"]["data"] == bytearray(b"\x02\x01\x04\x03")
    assert systems["CommandQueue"]["commands"][0]["data"] is systems["Barter"]
    json.dumps(to_jsonable(systems), allow_nan=False)


def test_ai_worker_rejects_unknown_custom_ai_prototypes():
    raw = u32(1) + native("123") + u32(0) + b"\x00"
    raw += native("custom") + struct.pack("<iB", 1, 1) + native("balanced")
    raw += u32(0) + prototype("UnknownAIBot")
    with pytest.raises(DecodeError, match="unknown JavaScript prototype"):
        decode_simulation(simulation([("AIManager", raw)]))


def test_deep_objects_rejected_with_decoder_error():
    value = b"\x01"
    for _ in range(Reader.MAX_DEPTH + 2):
        value = b"\x03" + properties([("x", value)])
    with pytest.raises(DecodeError, match="nesting"):
        Reader(value).script_value()


def test_non_finite_numbers_have_explicit_json_representation():
    value = Reader(b"\x06" + struct.pack("<d", float("inf"))).script_value()
    assert to_jsonable(value) == {"$type": "Number", "value": "inf"}
