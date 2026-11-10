"""Launch the official simulator and communicate with its restricted GUI mod."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unicodedata
import uuid

from .privacy import ObservationError, number, project_observation, validate_entities
from .map_observation import project_map_region, validate_region
from .terrain_analysis import analyze_terrain
from .metadata import project_options, validate_options_request
from .relay import EnetRelay
from .actions import (STANCES, validate_orders, validate_owned_selection, validate_production,
                      validate_packing, validate_unload, validate_production_cancel,
                      validate_rally_orders, validate_rally_selection)


MOD_ID = "mcp_player"
POLICY = "own-player-only-v1"


def find_game(explicit: str | None = None) -> Path:
    configured = explicit or os.environ.get("PYROGENESIS_EXE")
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError("PYROGENESIS_EXE does not identify a game executable")
        return path
    roots = [os.environ.get(key) for key in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)")]
    for root in filter(None, roots):
        for directory in ("0 A.D. Empires Ascendant", "0 A.D."):
            candidate = Path(root) / directory / "binaries/system/pyrogenesis.exe"
            if candidate.is_file():
                return candidate
    for command in ("pyrogenesis", "0ad"):
        if path := shutil.which(command):
            return Path(path).resolve()
    raise FileNotFoundError("0 A.D. was not found; set PYROGENESIS_EXE")


def user_mods() -> Path:
    if configured := os.environ.get("PYROGENESIS_MODS_DIR"):
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        # Use the actual Documents known folder (it may be redirected).
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) != 0:
            raise RuntimeError("Cannot locate the Documents known folder")
        return Path(buffer.value) / "My Games/0ad/mods"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "0ad/mods"


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def install_mod(mod_id: str = MOD_ID) -> Path:
    source = Path(__file__).resolve().parents[2] / "mod" / MOD_ID
    if not (source / "mod.json").is_file():
        # A wheel keeps the mod next to this package, an editable install uses the repository.
        source = Path(__file__).resolve().parent / "companion"
    if not (source / "mod.json").is_file():
        raise FileNotFoundError("Companion mod is missing; use an editable installation of this repository")
    if mod_id != MOD_ID and not (
        mod_id.startswith(MOD_ID + "_") and len(mod_id) == len(MOD_ID) + 33
        and all(char in "0123456789abcdef" for char in mod_id[len(MOD_ID) + 1:])
    ):
        raise ValueError("Invalid companion instance directory")
    destination = user_mods() / mod_id
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)
    return destination


@contextmanager
def _runtime_lock(path: Path):
    """Serialize publication of a shared Windows template across MCP processes."""
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + 180
            while True:
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Another instance is still preparing game resources") from None
                    time.sleep(0.1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def prepare_host_runtime(exe: Path, cache_root: Path | None = None) -> Path:
    """Cache a second Windows install: its ZIP archives must be separate files.

    0.28's Windows wopen uses _SH_DENYRD for archive readers. Hard links work
    for the executable/DLLs, but would retain that conflict for game archives.
    """
    if os.name != "nt" and cache_root is None:
        return exe
    binaries = exe.parent.parent
    files = sorted(path for path in binaries.rglob("*") if path.is_file())
    signature = [(str(path.relative_to(binaries)), path.stat().st_size,
                  path.stat().st_mtime_ns) for path in files]
    fingerprint = hashlib.sha256(
        (str(binaries) + json.dumps(signature)).encode("utf-8")
    ).hexdigest()[:20]
    root = cache_root or Path(os.environ["LOCALAPPDATA"]) / "pyrogenesis-mcp/host-runtime"
    destination = root / fingerprint / "binaries"
    marker = destination.parent / "ready.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    with _runtime_lock(marker.with_suffix(".lock")):
        if not marker.is_file():
            for source in files:
                target = destination / source.relative_to(binaries)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_file():
                    continue
                if source.suffix.lower() != ".zip":
                    try:
                        target.hardlink_to(source)
                        continue
                    except OSError:
                        pass
                temporary = target.with_name(target.name + ".tmp")
                shutil.copy2(source, temporary)
                os.replace(temporary, target)
            marker.write_text(json.dumps({"source": str(exe)}), encoding="utf-8")
    return destination / "system" / exe.name


class GameSession:
    """One owned process group and one serialized bridge command stream."""

    def __init__(self):
        self._lock = threading.RLock()
        self.processes: list[subprocess.Popen] = []
        self.relay: EnetRelay | None = None
        self.session = ""
        self.mode = "stopped"
        self._bridge: Path | None = None
        self._mod_path: Path | None = None
        self._mod_id = MOD_ID
        self._runtime: Path | None = None
        self._started_at = 0.0

    def _launch(self, exe: Path, arguments: list[str]) -> subprocess.Popen:
        kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                  "stderr": subprocess.DEVNULL, "cwd": str(exe.parent)}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            info = subprocess.STARTUPINFO()
            info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow = 6  # minimized, the visual session still ticks
            kwargs["startupinfo"] = info
        process = subprocess.Popen([str(exe), *arguments], **kwargs)
        self.processes.append(process)
        return process

    def _base_args(self) -> list[str]:
        return ["-mod=public", "-quickstart", "-nosound", "-unique-logs",
                "-conf=windowed:true", "-xres=1024", "-yres=768",
                "-conf=network.duplicateplayernames:false", "-autostart-disable-replay"]

    def _runtime_exe(self, exe: Path, role: str) -> Path:
        """Each active Windows game gets separate archive files, including host/client."""
        if os.name != "nt":
            return exe
        if self._runtime is None:
            root = Path(os.environ["LOCALAPPDATA"]) / "pyrogenesis-mcp/instances"
            root.mkdir(parents=True, exist_ok=True)
            # Explicit cleanup must run after owned processes exit. TemporaryDirectory's
            # weakref finalizer can run before the server's atexit game.close callback.
            self._runtime = Path(tempfile.mkdtemp(prefix=self.session + "-", dir=root))
        # Never launch the shared template itself. Existing templates also allow
        # a second client when a manually launched game locks the original ZIPs.
        template = prepare_host_runtime(exe)
        return prepare_host_runtime(template, self._runtime / role)

    def _prepare(self) -> Path:
        if any(process.poll() is None for process in self.processes):
            raise RuntimeError("A controlled game is already running; stop it before launching another")
        self.close()
        exe = find_game()
        self.session = uuid.uuid4().hex
        # A private mod snapshot avoids hot-reloading a running sibling client.
        self._mod_id = MOD_ID + "_" + self.session
        self._mod_path = user_mods() / self._mod_id
        try:
            install_mod(self._mod_id)
            self._bridge = user_mods() / "user/moddata/mcp_player" / self.session
            self._bridge.mkdir(parents=True, exist_ok=True)
            self._started_at = time.time()
            self._write_command("observe")
        except BaseException:
            self.close()
            raise
        return exe

    def start_local(self, *, networked: bool = True) -> dict:
        with self._lock:
            exe = self._prepare()
            try:
                if networked:
                    host_exe = self._runtime_exe(exe, "host")
                    host_port = free_udp_port()
                    relay_port = free_udp_port()
                    while relay_port == host_port:
                        relay_port = free_udp_port()
                    self._launch(host_exe, [*self._base_args(),
                                       "-autostart=skirmishes/skirmish_demo_2p",
                                       "-autostart-host", "-autostart-host-players=2",
                                       f"-autostart-port={host_port}", "-autostart-playername=LocalHost",
                                       "-autostart-visibility=hidden", "-autostart-victory=endless"])
                    # ENet retransmits while the host initializes. No fixed readiness sleep.
                    self.relay = EnetRelay("127.0.0.1", host_port, relay_port)
                    self.relay.start()
                    self._launch_player(self._runtime_exe(exe, "player"), "127.0.0.1", relay_port, "MCP Player")
                    self.mode = "local_network"
                else:
                    self._launch(self._runtime_exe(exe, "player"), [*self._base_args(), f"-mod={self._mod_id}",
                                       f"-conf=mcp.session:{self.session}",
                                       "-autostart=skirmishes/skirmish_demo_2p",
                                       "-autostart-player=1", "-autostart-ai=2:petra",
                                       "-autostart-aidiff=2:0", "-autostart-visibility=hidden",
                                       "-autostart-victory=endless"])
                    self.mode = "local_singleplayer"
            except BaseException:
                self.close()
                raise
            return self.status()

    def _launch_player(self, exe: Path, host: str, port: int, name: str) -> None:
        self._launch(exe, [*self._base_args(), f"-mod={self._mod_id}",
                           f"-conf=mcp.session:{self.session}", f"-autostart-client={host}",
                           f"-autostart-port={port}", f"-autostart-playername={name}"])

    def join(self, host: str, port: int = 20595, name: str = "MCP Player") -> dict:
        if not host or len(host) > 253 or any(c in host for c in "\x00\r\n"):
            raise ValueError("Invalid LAN host")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid UDP port")
        if (not isinstance(name, str) or not name.strip() or len(name) > 32
                or any(unicodedata.category(c) in {"Cc", "Cs", "Cf"} for c in name)):
            raise ValueError("Use a player name of 1 to 32 characters")
        with self._lock:
            exe = self._prepare()
            try:
                # A manually hosted room may already hold the installation's ZIPs.
                exe = self._runtime_exe(exe, "player")
                relay_port = free_udp_port()
                self.relay = EnetRelay(host, port, relay_port)
                self.relay.start()
                self._launch_player(exe, "127.0.0.1", relay_port, name)
                self.mode = "lan_client"
            except BaseException:
                self.close()
                raise
            return self.status()

    def _read_raw(self, *, max_age: float = 8) -> dict:
        if not self._bridge:
            raise RuntimeError("Start or join a game first")
        path = self._bridge / "observation.json"
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                raise ObservationError("Observation exceeds the size limit")
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise RuntimeError("Player observation is not ready; the game may still be loading") from None
        if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1 or raw.get("session") != self.session:
            raise ObservationError("Observation belongs to another game session")
        if raw.get("policy") != POLICY or raw.get("available") is not True:
            raise ObservationError("Normal player view is unavailable")
        stamp = number(raw.get("emitted_at_ms", 0), high=10**15) / 1000
        if abs(time.time() - stamp) > max_age:
            raise ObservationError("Player observation is stale")
        return raw

    def observe(self) -> dict:
        with self._lock:
            raw = self._read_raw()
            entities = []
            for entity in raw.get("own_entities", []):
                if not isinstance(entity, dict):
                    raise ObservationError("Invalid entity record")
                projected = {key: entity[key] for key in
                             ("id", "owner", "template", "position", "hitpoints", "movable", "properties") if key in entity}
                if "max_hitpoints" in entity:
                    projected["maxHitpoints"] = entity["max_hitpoints"]
                entities.append(projected)
            population = raw.get("population", {})
            normalized = {"player": raw.get("player_id"), "time": raw.get("game_time_ms", 0),
                          "resources": raw.get("resources", {}), "entities": entities}
            if "outcome" in raw:
                normalized["outcome"] = raw["outcome"]
            if "game_over" in raw:
                normalized["game_over"] = raw["game_over"]
            if "scoreboard" in raw:
                normalized["scoreboard"] = raw["scoreboard"]
            if "count" in population:
                normalized["population"] = population["count"]
            if "limit" in population:
                normalized["populationLimit"] = population["limit"]
            result = project_observation(normalized)
            result["policy"] = POLICY
            result["mapSize"] = number(raw.get("map_size", 0), high=100000)
            return result

    def _write_command(self, action: str, **fields) -> str:
        if not self._bridge:
            raise RuntimeError("Bridge has not been initialized")
        command_id = uuid.uuid4().hex
        value = {"version": 1, "session": self.session, "id": command_id, "action": action, **fields}
        temp = self._bridge / f"command.{command_id}.tmp"
        temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        os.replace(temp, self._bridge / "command.json")
        return command_id

    def observe_map_region(self, x_min: float, z_min: float, x_max: float,
                           z_max: float, cell_size: float = 16, *,
                           include_objects: bool = True) -> dict:
        """Request a bounded player-view map, never a raw simulation dump."""
        if type(include_objects) is not bool:
            raise ValueError("include_objects must be a boolean")
        with self._lock:
            observation = self.observe()
            request = validate_region(x_min, z_min, x_max, z_max, cell_size, observation["mapSize"])
            fields = {"region": request}
            if not include_objects:
                fields["include_objects"] = False
            command_id = self._write_command("observe_map", **fields)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                raw = self._read_raw()
                ack = raw.get("last_action")
                if isinstance(ack, dict) and ack.get("id") == command_id:
                    if ack.get("ok") is not True or ack.get("action") != "observe_map":
                        raise RuntimeError("The player-view map request was rejected")
                    if raw.get("map_request_id") != command_id:
                        raise ObservationError("Map response does not match this request")
                    result = project_map_region(raw.get("map_region"), request,
                                                observation["player"], observation["mapSize"],
                                                include_objects=include_objects)
                    if result["time"] != raw.get("game_time_ms"):
                        raise ObservationError("Map response does not match the current game time")
                    return result
                time.sleep(0.1)
            raise TimeoutError("The game did not acknowledge the map request")

    def analyze_terrain_region(self, x_min: float, z_min: float, x_max: float,
                               z_max: float, footprint_width: float,
                               footprint_depth: float | None = None,
                               cell_size: float = 16) -> dict:
        """Analyze sampled terrain for approximate building-site suitability."""
        sampled = self.observe_map_region(
            x_min, z_min, x_max, z_max, cell_size, include_objects=False)
        return analyze_terrain(sampled, footprint_width, footprint_depth)

    def act(self, action: str, entities: list[int], *, x: float | None = None,
            z: float | None = None, queued: bool = False) -> dict:
        with self._lock:
            observation = self.observe()
            selected = validate_entities(entities, observation)
            if action == "move":
                number(x, high=observation["mapSize"])
                number(z, high=observation["mapSize"])
                if type(queued) is not bool:
                    raise ValueError("queued must be a boolean")
                command_id = self._write_command("move", entities=selected, x=x, z=z, queued=queued)
            elif action == "stop":
                command_id = self._write_command("stop", entities=selected)
            else:
                raise ValueError("Unsupported player action")
            return self._action_ack(command_id, action, len(selected))

    def _action_ack(self, command_id: str, action: str, entity_count: int) -> dict:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            raw = self._read_raw()
            ack = raw.get("last_action")
            if isinstance(ack, dict) and ack.get("id") == command_id:
                if ack.get("ok") is not True:
                    raise RuntimeError("The player bridge rejected this action")
                return {"accepted": True, "action": action, "entityCount": entity_count}
            time.sleep(0.1)
        raise TimeoutError("The game did not acknowledge this action; observe before retrying")

    def order_units(self, entities: list[int], orders: list, *, queued: bool = False) -> dict:
        with self._lock:
            observation = self.observe()
            selected = validate_owned_selection(entities, observation)
            if type(queued) is not bool:
                raise ValueError("queued must be a boolean")
            safe_orders = validate_orders(orders, observation["mapSize"])
            command_id = self._write_command("orders", entities=selected, orders=safe_orders, queued=queued)
            result = self._action_ack(command_id, "orders", len(selected))
            result["orderCount"] = len(safe_orders)
            return result

    def set_rally_point(self, entities: list[int], orders: list, *, queued: bool = False) -> dict:
        with self._lock:
            observation = self.observe()
            safe_orders = validate_rally_orders(orders, observation["mapSize"])
            selected = validate_rally_selection(entities, observation, additional=len(safe_orders), queued=queued)
            command_id = self._write_command("set_rally", entities=selected, orders=safe_orders, queued=queued)
            result = self._action_ack(command_id, "set_rally", len(selected))
            result["orderCount"] = len(safe_orders)
            return result

    def clear_rally_point(self, entities: list[int]) -> dict:
        with self._lock:
            selected = validate_rally_selection(entities, self.observe())
            command_id = self._write_command("clear_rally", entities=selected)
            return self._action_ack(command_id, "clear_rally", len(selected))

    def describe_options(self, entity_id: int, kind: str = "research", ids: list[str] | None = None) -> dict:
        with self._lock:
            observation = self.observe()
            request = validate_options_request(entity_id, kind, ids, observation)
            command_id = self._write_command("describe_options", **request)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                raw = self._read_raw()
                ack = raw.get("last_action")
                if isinstance(ack, dict) and ack.get("id") == command_id:
                    if ack.get("ok") is not True or ack.get("action") != "describe_options":
                        raise RuntimeError("The player bridge rejected this description request")
                    return project_options(ack.get("metadata"), request, observation["player"])
                time.sleep(0.1)
            raise TimeoutError("The game did not acknowledge the description request")

    def abandon_targets(self, entities: list[int]) -> dict:
        with self._lock:
            selected = validate_owned_selection(entities, self.observe())
            command_id = self._write_command("abandon_target", entities=selected)
            return self._action_ack(command_id, "abandon_target", len(selected))

    def set_stance(self, entities: list[int], stance: str) -> dict:
        with self._lock:
            selected = validate_owned_selection(entities, self.observe())
            if not isinstance(stance, str) or stance not in STANCES:
                raise ValueError("Unsupported unit stance")
            command_id = self._write_command("stance", entities=selected, stance=stance)
            return self._action_ack(command_id, "stance", len(selected))

    def set_unit_packing(self, entities: list[int], packed: bool, *, queued: bool = False) -> dict:
        with self._lock:
            selected = validate_packing(entities, self.observe(), packed=packed)
            if type(queued) is not bool:
                raise ValueError("queued must be a boolean")
            command_id = self._write_command("packing", entities=selected, packed=packed, queued=queued)
            return self._action_ack(command_id, "packing", len(selected))

    def cancel_unit_packing(self, entities: list[int]) -> dict:
        with self._lock:
            selected = validate_packing(entities, self.observe(), cancel=True)
            command_id = self._write_command("cancel_packing", entities=selected)
            return self._action_ack(command_id, "cancel_packing", len(selected))

    def unload_garrison(self, holder_id: int, entities: list[int] | None = None) -> dict:
        with self._lock:
            selected = validate_unload(holder_id, entities, self.observe())
            command_id = self._write_command("unload", holder_id=holder_id, entities=selected)
            return self._action_ack(command_id, "unload", len(selected))

    def leave_turret(self, entities: list[int]) -> dict:
        with self._lock:
            observation = self.observe()
            selected = validate_owned_selection(entities, observation, entity_class="Unit")
            by_id = {item["id"]: item for item in observation["entities"]}
            if any(by_id[entity_id].get("properties", {}).get("capabilities", {}).get("occupy_turret") is not True
                   for entity_id in selected):
                raise ValueError("Select your units with a turret occupancy capability")
            command_id = self._write_command("leave_turret", entities=selected)
            return self._action_ack(command_id, "leave_turret", len(selected))

    def delete_owned(self, entities: list[int], *, buildings: bool = False) -> dict:
        with self._lock:
            selected = validate_owned_selection(entities, self.observe(),
                                                entity_class="Structure" if buildings else "Unit")
            action = "destroy_own_buildings" if buildings else "destroy_own_units"
            command_id = self._write_command(action, entities=selected)
            return self._action_ack(command_id, action, len(selected))

    def train_units(self, building_id: int, template: str, count: int = 1) -> dict:
        with self._lock:
            if type(count) is not int or not 1 <= count <= 100:
                raise ValueError("Train between one and 100 units")
            producer = validate_production(building_id, template, self.observe())
            command_id = self._write_command("train", entities=[producer], template=template, count=count)
            result = self._action_ack(command_id, "train", 1)
            result["requestedCount"] = count
            return result

    def research_technology(self, building_id: int, technology: str) -> dict:
        with self._lock:
            producer = validate_production(building_id, technology, self.observe(), research=True)
            command_id = self._write_command("research", entities=[producer], template=technology)
            return self._action_ack(command_id, "research", 1)

    def cancel_production(self, building_id: int, queue_id: int) -> dict:
        with self._lock:
            producer = validate_production_cancel(building_id, queue_id, self.observe())
            command_id = self._write_command("cancel_production", entities=[producer], queue_id=queue_id)
            return self._action_ack(command_id, "cancel_production", 1)

    def status(self) -> dict:
        with self._lock:
            running = [process for process in self.processes if process.poll() is None]
            result = {"mode": self.mode, "running": bool(running), "ready": False,
                      "policy": POLICY}
            network = None
            if self.relay:
                # Do not expose traffic volume: it can reveal hidden opponent activity.
                network = self.relay.status()["state"]
                result["network"] = network
            # Preserve the compact idle contract. A terminal snapshot belongs to
            # the active session and is never reported after an explicit close.
            if not running and self.mode == "stopped":
                return result
            can_observe = bool(running and (not self.relay or network == "connected"))
            if can_observe:
                try:
                    observation = self.observe()
                    result["player"] = observation["player"]
                    for key in ("outcome", "gameOver"):
                        if key in observation:
                            result[key] = observation[key]
                    outcome = observation.get("outcome")
                    if outcome in ("won", "defeated"):
                        # Terminal observations remain readable, but no further
                        # player commands should be advertised as actionable.
                        result["state"] = outcome
                        result["reason"] = "game_over"
                    else:
                        result["ready"] = True
                        result["state"] = outcome or "ready"
                except (ValueError, RuntimeError):
                    result["state"] = "loading" if running else "stopped"
                    result["reason"] = "observation_unavailable"
            elif running:
                result["state"] = network or "running"
                result["reason"] = "network_unavailable" if self.relay else "observation_unavailable"
                if self.relay:
                    # A recent terminal frame remains meaningful after a relay
                    # disconnect; stale or merely active frames must not imply
                    # defeat, and the bridge parser still enforces session/policy.
                    try:
                        terminal = self._read_raw()
                        if terminal.get("outcome") in ("won", "defeated"):
                            projected = self.observe()
                            result.update({
                                "player": projected["player"],
                                "outcome": projected["outcome"],
                                "gameOver": projected.get("gameOver"),
                                "state": projected["outcome"],
                                "reason": f"network_{network}",
                            })
                    except (ValueError, RuntimeError):
                        pass
            else:
                result["state"] = "stopped"
                result["reason"] = "game_not_running"
            return result

    def close(self) -> dict:
        with self._lock:
            # Only processes launched by this object are terminated.
            for process in reversed(self.processes):
                if process.poll() is None:
                    process.terminate()
            for process in reversed(self.processes):
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            self.processes.clear()
            if self.relay:
                self.relay.close()
                self.relay = None
            if self._runtime is not None:
                expected_root = Path(os.environ["LOCALAPPDATA"]) / "pyrogenesis-mcp/instances"
                if (self._runtime.parent.resolve() == expected_root.resolve()
                        and self._runtime.name.startswith(self.session + "-")):
                    try:
                        shutil.rmtree(self._runtime)
                    except FileNotFoundError:
                        # Preparation may fail before creating every owned directory.
                        pass
                self._runtime = None
            if self._mod_path is not None:
                # Only remove the private snapshot made by this GameSession.
                if (self._mod_path.name == MOD_ID + "_" + self.session
                        and self._mod_path.parent.resolve() == user_mods().resolve()):
                    try:
                        shutil.rmtree(self._mod_path)
                    except FileNotFoundError:
                        # Also handle removal between validation and cleanup.
                        pass
                self._mod_path = None
            self.mode = "stopped"
            return {"running": False, "mode": "stopped"}
