"""Bounded codec for the 0 A.D. 0.28.0 ENet application protocol.

Ordinary fields use network (big endian) byte order. Simulation/script values
have a separate binary format and deliberately remain opaque here. This module
is an internal transport implementation, never an MCP packet-inspection API.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct
from typing import Any

PROTOCOL_MAGIC = 0x5073013F
PROTOCOL_MAGIC_RESPONSE = 0x50630121
PROTOCOL_VERSION = 0x01010019
ENGINE_VERSION = "0.28.0"
DEFAULT_PORT = 0x5073
REQUIRE_LOBBY_AUTH = 1
MAX_PACKET_SIZE = 65535


class ProtocolError(ValueError):
    pass


class MessageType(IntEnum):
    INVALID = 0
    SERVER_HANDSHAKE = 1
    CLIENT_HANDSHAKE = 2
    SERVER_HANDSHAKE_RESPONSE = 3
    AUTHENTICATE = 4
    AUTHENTICATE_RESULT = 5
    CHAT = 6
    READY = 7
    CLEAR_ALL_READY = 8
    GAME_SETUP = 9
    ASSIGN_PLAYER = 10
    PLAYER_ASSIGNMENT = 11
    FILE_TRANSFER_REQUEST = 12
    FILE_TRANSFER_RESPONSE = 13
    FILE_TRANSFER_DATA = 14
    FILE_TRANSFER_ACK = 15
    JOIN_SYNC_START = 16
    REJOINED = 17
    KICKED = 18
    CLIENT_TIMEOUT = 19
    CLIENT_PERFORMANCE = 20
    CLIENTS_LOADING = 21
    CLIENT_PAUSED = 22
    LOADED_GAME = 23
    GAME_START = 24
    SAVED_GAME_START = 25
    END_COMMAND_BATCH = 26
    SYNC_CHECK = 27
    SYNC_ERROR = 28
    SIMULATION_COMMAND = 29
    FLARE = 30


class AuthenticateResult(IntEnum):
    OK = 0
    OK_SAVED_GAME = 1
    OK_REJOINING = 2
    PASSWORD_INVALID = 3


@dataclass(frozen=True, repr=False)
class Message:
    type: MessageType
    fields: dict[str, Any]

    def __repr__(self) -> str:
        # Avoid exposing private payloads or authentication secrets in logs.
        return f"Message(type={self.type.name})"


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.offset

    def take(self, size: int) -> bytes:
        if size < 0 or size > self.remaining:
            raise ProtocolError("Truncated network message")
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def integer(self, size: int, signed: bool = False) -> int:
        return int.from_bytes(self.take(size), "big", signed=signed)

    def string(self, *, binary: bool = False) -> str | bytes:
        value = self.take(self.integer(4))
        if binary:
            return value
        try:
            return value.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ProtocolError("Invalid UTF-8 string") from exc

    def wstring(self) -> str:
        start = self.offset
        while self.remaining >= 2:
            if self.take(2) == b"\x00\x00":
                try:
                    return self.data[start:self.offset - 2].decode("utf-16-be", errors="strict")
                except UnicodeDecodeError as exc:
                    raise ProtocolError("Invalid UTF-16 string") from exc
        raise ProtocolError("Unterminated wide string")


def encode_string(value: str | bytes) -> bytes:
    data = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    return struct.pack(">I", len(data)) + data


def encode_wstring(value: str) -> bytes:
    if "\x00" in value:
        raise ProtocolError("Wide strings cannot contain NUL")
    return value.encode("utf-16-be", errors="strict") + b"\x00\x00"


def frame(message_type: MessageType | int, payload: bytes = b"") -> bytes:
    size = 3 + len(payload)
    if not 3 <= size <= MAX_PACKET_SIZE:
        raise ProtocolError("Message exceeds the 16-bit packet size")
    try:
        kind = MessageType(message_type)
    except ValueError as exc:
        raise ProtocolError("Unknown message type") from exc
    return struct.pack(">BH", kind, size) + payload


# A trailing tuple is an array with no count prefix: entries consume the packet.
_SCHEMAS: dict[MessageType, tuple] = {
    MessageType.SERVER_HANDSHAKE: (("magic", "u32"), ("protocol_version", "u32"), ("engine_version", "str"), ("mods", (("name", "str"), ("version", "str")))),
    MessageType.CLIENT_HANDSHAKE: (("magic_response", "u32"), ("protocol_version", "u32"), ("engine_version", "str"), ("mods", (("name", "str"), ("version", "str")))),
    MessageType.SERVER_HANDSHAKE_RESPONSE: (("protocol_version", "u32"), ("flags", "u32"), ("guid", "str")),
    MessageType.AUTHENTICATE: (("name", "wstr"), ("password", "str"), ("controller_secret", "str")),
    MessageType.AUTHENTICATE_RESULT: (("code", "u32"), ("host_id", "u16"), ("is_controller", "u8"), ("message", "wstr")),
    MessageType.CHAT: (("sender_guid", "str"), ("message", "wstr"), ("receivers", (("guid", "str"),))),
    MessageType.READY: (("guid", "str"), ("status", "u8")),
    MessageType.CLEAR_ALL_READY: (),
    MessageType.ASSIGN_PLAYER: (("player_id", "i8"), ("guid", "str")),
    MessageType.PLAYER_ASSIGNMENT: (("hosts", (("guid", "str"), ("name", "wstr"), ("player_id", "i8"), ("status", "u8"))),),
    MessageType.FILE_TRANSFER_REQUEST: (("request_type", "i8"), ("request_id", "u32")),
    MessageType.FILE_TRANSFER_RESPONSE: (("request_id", "u32"), ("length", "u32")),
    MessageType.FILE_TRANSFER_DATA: (("request_id", "u32"), ("data", "bytes")),
    MessageType.FILE_TRANSFER_ACK: (("request_id", "u32"), ("num_packets", "u32")),
    MessageType.JOIN_SYNC_START: (("init_attributes", "str"),),
    MessageType.REJOINED: (("guid", "str"),),
    MessageType.KICKED: (("name", "wstr"), ("ban", "u8")),
    MessageType.CLIENT_TIMEOUT: (("guid", "str"), ("last_received_time", "u32")),
    MessageType.CLIENT_PERFORMANCE: (("clients", (("guid", "str"), ("mean_rtt", "u32"))),),
    MessageType.CLIENTS_LOADING: (("clients", (("guid", "str"),)),),
    MessageType.CLIENT_PAUSED: (("guid", "str"), ("pause", "u8")),
    MessageType.LOADED_GAME: (("current_turn", "u32"),),
    MessageType.GAME_START: (("init_attributes", "str"),),
    MessageType.SAVED_GAME_START: (("init_attributes", "str"),),
    MessageType.END_COMMAND_BATCH: (("turn", "u32"), ("turn_length", "u16")),
    MessageType.SYNC_CHECK: (("turn", "u32"), ("hash", "bytes")),
    MessageType.SYNC_ERROR: (("turn", "u32"), ("expected_hash", "bytes"), ("player_names", (("name", "wstr"),))),
    MessageType.FLARE: (("guid", "str"), ("x", "str"), ("y", "str"), ("z", "str")),
}


def _decode_fields(reader: Reader, schema: tuple) -> dict:
    fields = {}
    for name, kind in schema:
        if isinstance(kind, tuple):
            entries = []
            while reader.remaining:
                entries.append(_decode_fields(reader, kind))
            fields[name] = entries
        elif kind == "str":
            fields[name] = reader.string()
        elif kind == "bytes":
            fields[name] = reader.string(binary=True)
        elif kind == "wstr":
            fields[name] = reader.wstring()
        else:
            fields[name] = reader.integer(int(kind[1:]) // 8, kind[0] == "i")
    return fields


def _encode_fields(fields: dict, schema: tuple) -> bytes:
    output = bytearray()
    for name, kind in schema:
        value = fields[name]
        if isinstance(kind, tuple):
            for entry in value:
                output.extend(_encode_fields(entry, kind))
        elif kind in ("str", "bytes"):
            output.extend(encode_string(value))
        elif kind == "wstr":
            output.extend(encode_wstring(value))
        else:
            output.extend(int(value).to_bytes(int(kind[1:]) // 8, "big", signed=kind[0] == "i"))
    return bytes(output)


def decode_message(packet: bytes) -> Message:
    if len(packet) < 3 or len(packet) > MAX_PACKET_SIZE:
        raise ProtocolError("Invalid packet size")
    kind_value, length = struct.unpack_from(">BH", packet)
    if length != len(packet):
        raise ProtocolError("Packet length does not match header")
    try:
        kind = MessageType(kind_value)
    except ValueError as exc:
        raise ProtocolError("Unknown message type") from exc
    reader = Reader(packet[3:])
    if kind in (MessageType.GAME_SETUP, MessageType.SIMULATION_COMMAND):
        # No parsing of world state or any other player's commands.
        fields = {"payload": reader.take(reader.remaining)}
    elif kind in _SCHEMAS:
        fields = _decode_fields(reader, _SCHEMAS[kind])
    else:
        raise ProtocolError("Unsupported message type")
    if reader.remaining:
        raise ProtocolError("Trailing message bytes")
    return Message(kind, fields)


def encode_message(message_type: MessageType | int, **fields: Any) -> bytes:
    kind = MessageType(message_type)
    if kind in (MessageType.GAME_SETUP, MessageType.SIMULATION_COMMAND):
        return frame(kind, fields["payload"])
    return frame(kind, _encode_fields(fields, _SCHEMAS[kind]))


def build_handshake(*, engine_version: str = ENGINE_VERSION, mods=(), protocol_version: int = PROTOCOL_VERSION) -> bytes:
    return encode_message(MessageType.CLIENT_HANDSHAKE,
                          magic_response=PROTOCOL_MAGIC_RESPONSE,
                          protocol_version=protocol_version, engine_version=engine_version,
                          mods=[dict(name=name, version=version) for name, version in mods])


def build_authenticate(name: str, password: str = "", controller_secret: str = "") -> bytes:
    """password is the final engine-compatible hash, not a cleartext password."""
    return encode_message(MessageType.AUTHENTICATE, name=name,
                          password=password, controller_secret=controller_secret)


def build_lan_authenticate(name: str, password: str = "") -> bytes:
    """Match stock StartNetworkJoin: empty password, no controller privilege.

    HashCryptographically explicitly returns an empty input unchanged. Stock
    direct LAN hosting does not configure a password; password derivation and
    verification are part of StartNetworkJoinLobby's XMPP workflow instead.
    """
    if password:
        raise ProtocolError("Password authentication requires the unsupported XMPP lobby workflow")
    return build_authenticate(name)


def build_ready(guid: str, status: int = 1) -> bytes:
    if status not in (0, 1, 2):
        raise ProtocolError("Invalid ready state")
    return encode_message(MessageType.READY, guid=guid, status=status)


def build_chat(message: str, receivers=()) -> bytes:
    return encode_message(MessageType.CHAT, sender_guid="", message=message,
                          receivers=[{"guid": guid} for guid in receivers])


NetMessageType = MessageType
