"""Small, ownership-safe ctypes binding to the ENet 1.3 C API.

Use the ENet library shipped with the installed game so that native transport and
range-coder compression agree with the server. Game messages are handled by the
protocol module, never by this module. The host is intentionally synchronous;
callers must pump ``service`` to make network progress.
"""

from __future__ import annotations

import ctypes as C
import ctypes.util
from dataclasses import dataclass, field
from enum import IntEnum
import os
from pathlib import Path
import threading
from typing import Any


class EnetError(RuntimeError):
    """An ENet operation failed."""


class EventType(IntEnum):
    NONE = 0
    CONNECT = 1
    DISCONNECT = 2
    RECEIVE = 3


class _Address(C.Structure):
    _fields_ = [("host", C.c_uint32), ("port", C.c_uint16)]


class _Packet(C.Structure):
    _fields_ = [
        ("reference_count", C.c_size_t),
        ("flags", C.c_uint32),
        ("data", C.POINTER(C.c_ubyte)),
        ("data_length", C.c_size_t),
        ("free_callback", C.c_void_p),
        ("user_data", C.c_void_p),
    ]


class _Event(C.Structure):
    _fields_ = [
        ("type", C.c_int),
        ("peer", C.c_void_p),
        ("channel_id", C.c_uint8),
        ("data", C.c_uint32),
        ("packet", C.POINTER(_Packet)),
    ]


@dataclass(frozen=True)
class NativeEnetPeer:
    """Opaque peer handle, valid only with the host that created it."""

    _pointer: int = field(repr=False)
    _owner: object = field(repr=False, compare=True)


@dataclass(frozen=True)
class EnetEvent:
    type: EventType
    peer: NativeEnetPeer
    channel: int = 0
    data: int = 0
    payload: bytes = b""


def find_enet_library(explicit: str | Path | None = None) -> str:
    """Find an installed ENet library; no binaries are downloaded."""
    if explicit is not None:
        candidate = Path(explicit).expanduser().resolve()
        if not candidate.is_file():
            raise FileNotFoundError(f"ENet library does not exist: {candidate}")
        return str(candidate)
    configured = (os.environ.get("PYROGENESIS_ENET_LIBRARY")
                  or os.environ.get("PYROGENESIS_ENET_DLL") or os.environ.get("ENET_DLL"))
    if configured:
        return find_enet_library(configured)
    candidates: list[Path] = []
    if os.name == "nt":
        for variable in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(variable)
            if root:
                candidates.extend(
                    Path(root) / name / "binaries" / "system" / "enet.dll"
                    for name in ("0 A.D. Empires Ascendant", "0 A.D.")
                )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    found = ctypes.util.find_library("enet")
    if found:
        return found
    raise FileNotFoundError(
        "ENet was not found. Set PYROGENESIS_ENET_LIBRARY to the system libenet path "
        "on Linux, or PYROGENESIS_ENET_DLL to the game's enet.dll on Windows."
    )


_API_LOCK = threading.RLock()
_APIS: dict[str, "_Api"] = {}


class _Api:
    def __init__(self, path: str):
        # ENET_API is cdecl, including on 32-bit Windows.
        self.library = C.CDLL(path)
        self.references = 0
        signatures: dict[str, tuple[Any, list[Any]]] = {
            "enet_initialize": (C.c_int, []),
            "enet_deinitialize": (None, []),
            "enet_linked_version": (C.c_uint32, []),
            "enet_address_set_host": (C.c_int, [C.POINTER(_Address), C.c_char_p]),
            "enet_host_create": (
                C.c_void_p,
                [C.POINTER(_Address), C.c_size_t, C.c_size_t, C.c_uint32, C.c_uint32],
            ),
            "enet_host_destroy": (None, [C.c_void_p]),
            "enet_host_connect": (
                C.c_void_p,
                [C.c_void_p, C.POINTER(_Address), C.c_size_t, C.c_uint32],
            ),
            "enet_host_service": (C.c_int, [C.c_void_p, C.POINTER(_Event), C.c_uint32]),
            "enet_host_flush": (None, [C.c_void_p]),
            "enet_host_compress_with_range_coder": (C.c_int, [C.c_void_p]),
            "enet_packet_create": (C.POINTER(_Packet), [C.c_void_p, C.c_size_t, C.c_uint32]),
            "enet_packet_destroy": (None, [C.POINTER(_Packet)]),
            "enet_peer_send": (C.c_int, [C.c_void_p, C.c_uint8, C.POINTER(_Packet)]),
            "enet_peer_disconnect": (None, [C.c_void_p, C.c_uint32]),
            "enet_peer_disconnect_now": (None, [C.c_void_p, C.c_uint32]),
            "enet_peer_reset": (None, [C.c_void_p]),
        }
        for name, (result_type, arguments) in signatures.items():
            function = getattr(self.library, name)
            function.restype = result_type
            function.argtypes = arguments
        version = self.library.enet_linked_version()
        self.version = ((version >> 16) & 255, (version >> 8) & 255, version & 255)
        if self.version[:2] != (1, 3):
            raise EnetError(f"Expected ENet 1.3 ABI, found {self.version}")

    @classmethod
    def acquire(cls, path: str) -> "_Api":
        with _API_LOCK:
            api = _APIS.get(path)
            if api is None:
                api = cls(path)
                _APIS[path] = api
            if api.references == 0 and api.library.enet_initialize() != 0:
                raise EnetError("enet_initialize failed")
            api.references += 1
            return api

    def release(self) -> None:
        with _API_LOCK:
            self.references -= 1
            if self.references == 0:
                self.library.enet_deinitialize()


class NativeEnetHost:
    """Own one native ENet host and all packets crossing its boundary.

    ``bind`` is an optional ``(IPv4 hostname, UDP port)`` for a listening host.
    Use ``connect`` for clients. Optional range coding is off for stock 0 A.D.
    Methods are serialized so closing from another thread cannot free live data.
    """

    def __init__(
        self,
        dll_path: str | Path | None = None,
        *,
        bind: tuple[str, int] | None = None,
        peer_count: int = 1,
        channel_limit: int = 1,
        compress: bool = False,
    ):
        if not 1 <= peer_count <= 4095:
            raise ValueError("peer_count must be between 1 and 4095")
        if not 1 <= channel_limit <= 255:
            raise ValueError("channel_limit must be between 1 and 255")
        self._lock = threading.RLock()
        self._identity = object()
        self._peers: dict[int, NativeEnetPeer] = {}
        self._pointer: int | None = None
        self._api: _Api | None = None
        self.library_path = find_enet_library(dll_path)
        self._api = _Api.acquire(self.library_path)
        try:
            address = self._address(*bind) if bind else None
            pointer = self._api.library.enet_host_create(
                C.byref(address) if address is not None else None,
                peer_count,
                channel_limit,
                0,
                0,
            )
            if not pointer:
                raise EnetError("enet_host_create failed (the UDP port may be in use)")
            self._pointer = pointer
            if compress and self._api.library.enet_host_compress_with_range_coder(pointer) != 0:
                raise EnetError("ENet range-coder initialization failed")
        except BaseException:
            self.destroy()
            raise

    @property
    def version(self) -> tuple[int, int, int]:
        with self._lock:
            self._require_open()
            assert self._api is not None
            return self._api.version

    @property
    def closed(self) -> bool:
        return self._pointer is None

    def _require_open(self) -> tuple[_Api, int]:
        if self._pointer is None or self._api is None:
            raise EnetError("ENet host is closed")
        return self._api, self._pointer

    def _address(self, host: str, port: int) -> _Address:
        if not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        if not host or "\x00" in host:
            raise ValueError("a nonempty hostname without NUL characters is required")
        assert self._api is not None
        address = _Address(0, port)
        if self._api.library.enet_address_set_host(C.byref(address), host.encode("idna")) != 0:
            raise EnetError(f"Unable to resolve ENet IPv4 host: {host}")
        return address

    def _peer(self, pointer: int) -> NativeEnetPeer:
        if pointer not in self._peers:
            self._peers[pointer] = NativeEnetPeer(pointer, self._identity)
        return self._peers[pointer]

    def _check_peer(self, peer: NativeEnetPeer) -> int:
        if (
            not isinstance(peer, NativeEnetPeer)
            or peer._owner is not self._identity
            or self._peers.get(peer._pointer) is not peer
        ):
            raise EnetError("Peer is disconnected or belongs to another host")
        return peer._pointer

    def connect(self, host: str, port: int, channels: int = 1, data: int = 0) -> NativeEnetPeer:
        if not 1 <= channels <= 255:
            raise ValueError("channels must be between 1 and 255")
        if not 0 <= data <= 0xFFFFFFFF:
            raise ValueError("connection data must be an unsigned 32-bit integer")
        with self._lock:
            api, pointer = self._require_open()
            address = self._address(host, port)
            peer_pointer = api.library.enet_host_connect(pointer, C.byref(address), channels, data)
            if not peer_pointer:
                raise EnetError("enet_host_connect failed (no peer slot is available)")
            return self._peer(peer_pointer)

    def service(self, timeout_ms: int = 0) -> EnetEvent | None:
        if not isinstance(timeout_ms, int) or not 0 <= timeout_ms <= 60_000:
            raise ValueError("timeout_ms must be between 0 and 60000")
        with self._lock:
            api, pointer = self._require_open()
            native = _Event()
            result = api.library.enet_host_service(pointer, C.byref(native), timeout_ms)
            if result < 0:
                raise EnetError("enet_host_service failed")
            if result == 0 or native.type == EventType.NONE:
                return None
            if not native.peer:
                raise EnetError("ENet returned an event without a peer")
            peer = self._peer(native.peer)
            payload = b""
            if native.type == EventType.RECEIVE:
                if not native.packet:
                    raise EnetError("ENet receive event contains no packet")
                try:
                    size = native.packet.contents.data_length
                    if size > 32 * 1024 * 1024:
                        raise EnetError("ENet packet exceeds 32 MiB")
                    payload = C.string_at(native.packet.contents.data, size)
                finally:
                    api.library.enet_packet_destroy(native.packet)
            event = EnetEvent(EventType(native.type), peer, native.channel_id, native.data, payload)
            if event.type == EventType.DISCONNECT:
                self._peers.pop(native.peer, None)
            return event

    def send(
        self,
        peer: NativeEnetPeer,
        payload: bytes,
        channel: int = 0,
        reliable: bool = True,
    ) -> None:
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        if len(payload) > 32 * 1024 * 1024:
            raise ValueError("payload exceeds 32 MiB")
        if not isinstance(channel, int) or not 0 <= channel <= 254:
            raise ValueError("channel must be between 0 and 254")
        with self._lock:
            api, _ = self._require_open()
            peer_pointer = self._check_peer(peer)
            buffer = C.create_string_buffer(payload)
            packet = api.library.enet_packet_create(buffer, len(payload), 1 if reliable else 0)
            if not packet:
                raise EnetError("enet_packet_create failed")
            # Ownership transfers to ENet only after successful enqueue.
            if api.library.enet_peer_send(peer_pointer, channel, packet) != 0:
                api.library.enet_packet_destroy(packet)
                raise EnetError("enet_peer_send failed")

    def flush(self) -> None:
        with self._lock:
            api, pointer = self._require_open()
            api.library.enet_host_flush(pointer)

    def disconnect(self, peer: NativeEnetPeer, data: int = 0, *, immediate: bool = False) -> None:
        if not 0 <= data <= 0xFFFFFFFF:
            raise ValueError("disconnect data must be an unsigned 32-bit integer")
        with self._lock:
            api, _ = self._require_open()
            peer_pointer = self._check_peer(peer)
            if immediate:
                api.library.enet_peer_disconnect_now(peer_pointer, data)
                self._peers.pop(peer_pointer, None)
            else:
                api.library.enet_peer_disconnect(peer_pointer, data)

    def reset(self, peer: NativeEnetPeer) -> None:
        with self._lock:
            api, _ = self._require_open()
            peer_pointer = self._check_peer(peer)
            api.library.enet_peer_reset(peer_pointer)
            self._peers.pop(peer_pointer, None)

    def destroy(self) -> None:
        lock = getattr(self, "_lock", None)
        if lock is None:
            return
        with lock:
            api = self._api
            if api is None:
                return
            if self._pointer is not None:
                api.library.enet_host_destroy(self._pointer)
                self._pointer = None
            self._peers.clear()
            self._api = None
            api.release()

    close = destroy

    def __enter__(self) -> "NativeEnetHost":
        self._require_open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.destroy()

    def __del__(self) -> None:
        self.destroy()
