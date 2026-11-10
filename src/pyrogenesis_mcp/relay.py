"""A private ENet relay for a native, deterministic Pyrogenesis client.

The relay never exports packet contents.  It lets the official engine implement
simulation and sync checks while Python owns both ENet connections.
"""

from __future__ import annotations

import threading
from collections import deque

from .enet import NativeEnetHost


class EnetRelay:
    def __init__(self, host: str, port: int, listen_port: int, dll_path: str | None = None):
        self.host = host
        self.port = port
        self.listen_port = listen_port
        self.dll_path = dll_path
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._status = {"state": "stopped", "packets_to_host": 0, "packets_to_client": 0}
        self._error: str | None = None

    def start(self) -> None:
        if self._thread:
            raise RuntimeError("Relay already started")
        self._thread = threading.Thread(target=self._run, name="pyrogenesis-enet-relay", daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            self.close()
            raise TimeoutError("ENet relay did not start")
        if self._error:
            self.close()
            raise RuntimeError(self._error)

    def status(self) -> dict:
        with self._lock:
            result = dict(self._status)
        if self._error:
            result["error"] = self._error
        return result

    def _set(self, **values) -> None:
        with self._lock:
            self._status.update(values)

    def _increment(self, key: str) -> None:
        with self._lock:
            self._status[key] += 1

    def _run(self) -> None:
        try:
            with NativeEnetHost(bind=("127.0.0.1", self.listen_port), peer_count=1,
                                channel_limit=1, dll_path=self.dll_path) as downstream, \
                    NativeEnetHost(peer_count=1, channel_limit=1, dll_path=self.dll_path) as upstream:
                incoming = None
                outgoing = None
                connected = False
                pending = deque()
                self._set(state="listening")
                self._ready.set()
                while not self._stop.is_set():
                    event = downstream.service(2)
                    if event:
                        if int(event.type) == 1:
                            incoming = event.peer
                            outgoing = upstream.connect(self.host, self.port, channels=1, data=event.data)
                            self._set(state="connecting")
                        elif int(event.type) == 2:
                            if outgoing:
                                upstream.disconnect(outgoing, data=event.data, immediate=True)
                            self._set(state="client_disconnected")
                            break
                        elif int(event.type) == 3:
                            if connected:
                                upstream.send(outgoing, event.payload, channel=event.channel)
                                self._increment("packets_to_host")
                            else:
                                if len(pending) >= 128:
                                    raise RuntimeError("ENet connection queue exceeded")
                                pending.append((event.channel, event.payload))
                    event = upstream.service(2) if outgoing else None
                    if event:
                        if int(event.type) == 1:
                            connected = True
                            self._set(state="connected")
                            while pending:
                                channel, payload = pending.popleft()
                                upstream.send(outgoing, payload, channel=channel)
                                self._increment("packets_to_host")
                        elif int(event.type) == 2:
                            if incoming:
                                downstream.disconnect(incoming, data=event.data)
                                downstream.flush()
                            self._set(state="host_disconnected")
                            break
                        elif int(event.type) == 3 and incoming:
                            downstream.send(incoming, event.payload, channel=event.channel)
                            self._increment("packets_to_client")
                    upstream.flush()
                    downstream.flush()
        except Exception as exc:
            # Exceptions describe transport failures only, never packet payloads.
            self._error = f"ENet relay failed: {type(exc).__name__}: {exc}"
            self._set(state="error")
            self._ready.set()
        finally:
            if not self._error:
                self._set(state="stopped")

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=10)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.close()
