"""Exercise the actual installed ENet DLL over loopback, including ownership."""

import socket
import time

import pytest

from pyrogenesis_mcp.enet import EnetError, EventType, NativeEnetHost, find_enet_library
from pyrogenesis_mcp.relay import EnetRelay


def test_native_round_trip_and_disconnect():
    try:
        library = find_enet_library()
    except FileNotFoundError:
        pytest.skip("An ENet 1.3 library is required for native transport integration")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    with NativeEnetHost(library, bind=("127.0.0.1", port)) as server:
        with NativeEnetHost(library) as client:
            client_peer = client.connect("127.0.0.1", port, data=123)
            connected = set()
            server_peer = None
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and len(connected) < 2:
                for name, host in (("server", server), ("client", client)):
                    event = host.service(5)
                    if event and event.type == EventType.CONNECT:
                        connected.add(name)
                        if name == "server":
                            assert event.data == 123
                            server_peer = event.peer
            assert connected == {"server", "client"}
            assert server_peer is not None
            payload = b"0ad-enet\x00" + b"x" * 9000
            client.send(client_peer, payload)
            client.flush()
            reply = None
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and reply is None:
                client.service(0)
                event = server.service(5)
                if event and event.type == EventType.RECEIVE:
                    assert event.payload == payload
                    server.send(server_peer, b"ack\x00")
                    server.flush()
                event = client.service(5)
                if event and event.type == EventType.RECEIVE:
                    reply = event.payload
            assert reply == b"ack\x00"
            with pytest.raises(EnetError, match="another host"):
                server.send(client_peer, b"wrong owner")
            client.disconnect(client_peer, data=42)
            disconnected = set()
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and len(disconnected) < 2:
                for name, host in (("server", server), ("client", client)):
                    event = host.service(5)
                    if event and event.type == EventType.DISCONNECT:
                        disconnected.add(name)
                        if name == "server":
                            assert event.data == 42
            assert disconnected == {"server", "client"}
            with pytest.raises(EnetError, match="disconnected"):
                client.send(client_peer, b"stale")
        assert client.closed
        client.destroy()
        with pytest.raises(EnetError, match="closed"):
            client.service()


def test_relay_waits_for_client_then_forwards_both_directions_and_disconnect():
    try:
        library = find_enet_library()
    except FileNotFoundError:
        pytest.skip("An ENet 1.3 library is required for native transport integration")
    # Reserve both ports together so they cannot be identical.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as first, \
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        server_port = first.getsockname()[1]
        relay_port = second.getsockname()[1]
    with NativeEnetHost(library, bind=("127.0.0.1", server_port)) as server, \
            EnetRelay("127.0.0.1", server_port, relay_port, library) as relay:
        # An idle upstream ENet host must not be serviced before connect().
        time.sleep(0.05)
        assert relay.status()["state"] == "listening"
        with NativeEnetHost(library) as client:
            peer = client.connect("127.0.0.1", relay_port, data=123)
            received = None
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and received is None:
                event = client.service(2)
                if event and event.type == EventType.CONNECT:
                    client.send(peer, b"request\x00" + b"x" * 9000)
                elif event and event.type == EventType.RECEIVE:
                    received = event.payload
                event = server.service(2)
                if event and event.type == EventType.CONNECT:
                    assert event.data == 123
                elif event and event.type == EventType.RECEIVE:
                    assert event.payload == b"request\x00" + b"x" * 9000
                    server.send(event.peer, b"response\x00")
            assert received == b"response\x00"
            assert relay.status()["packets_to_host"] == 1
            assert relay.status()["packets_to_client"] == 1
            client.disconnect(peer, data=42)
            disconnected = False
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and not disconnected:
                client.service(2)
                event = server.service(2)
                if event and event.type == EventType.DISCONNECT:
                    assert event.data == 42
                    disconnected = True
            assert disconnected
        assert "error" not in relay.status()
