# MCP Player Companion

This mod leaves deterministic simulation to Pyrogenesis. Read-only GUI interface
extensions query the native simulation; actions use ordinary network commands. The Python
companion communicates through JSON files and never receives complete engine
state. Install the `mcp_player` directory in the game's user `mods` directory and
enable it with `-mod=mcp_player -conf=mcp.session:<unique-session-id>`.

The stock session GUI loads `gui/session/zz_mcp_player.js`. Every 500 ms the mod
reads VFS `moddata/mcp_player/command.json` and writes
`moddata/mcp_player/observation.json`. The writable VFS root is the automatic
`user` mod; on Windows the files are normally under
`Documents/My Games/0ad/mods/user/moddata/mcp_player/`.

Commands have `version: 1`, `session`, unique string `id`, and `action`.

```json
{"version":1,"session":"example-session","id":"observe-1","action":"observe"}
```

```json
{"version":1,"session":"example-session","id":"move-1","action":"move","entities":[123],"x":100,"z":100,"queued":false}
```

```json
{"version":1,"session":"example-session","id":"stop-1","action":"stop","entities":[123]}
```

The observation echoes `session` and includes `player_id`, `game_time_ms`,
`map_size`, the player's own `resources`, `population`, and `own_entities`.
Each entity has explicit `owner`, `id`, `template`, `classes`, `movable`, `idle`,
and optional `position`, `hitpoints`, `max_hitpoints`. Version 0.3 also projects
versioned `properties`: capabilities, resource cargo, current target/activity,
attack bonuses, healing and auras, native special roles, mobile garrison holders,
and pack/unpack state. See `../../docs/properties.md` for the full schema.
The player's `scoreboard` includes phase, gathering workers, idle objects,
territory, resources, population, research, foundations and busy building queues.
`last_action` acknowledges
the last processed command with its `id` and `ok` flag. An acknowledgement
means the ordinary command was queued; observe subsequent positions to verify
its effect in simulation.

The own-player projection omits every other player's entities and economy;
the map query includes currently visible public entity profiles. Cargo, orders,
construction lists, activity and garrison occupancy are owned-player only.
Static map properties are copied into the same fog history as other map fields.
It does not expose raw GUI state, order queues, raw network payloads,
rejoin snapshots, debug commands, replay contents, or arbitrary scripts. Own
entity ownership is rechecked for each command. Unknown command fields are
rejected. Observers, replays, another player's view, personal reveal-map and control-all
mode disable the bridge. A public match rule with `RevealMap: true` is allowed,
while observations remain limited to owned entities and economy. Initialization
attributes, including map seeds, are never serialized. Moving into black terrain remains allowed because it
is an ordinary exploration order and reads no destination information.

This is an MCP interface boundary. The engine and the local bridge files remain
trusted components; it does not sandbox an agent that separately has arbitrary
operating-system file or process access.

Open `../tests/bridge_contract.html` in a browser to run the isolated JavaScript
contract test. A real Pyrogenesis session is still required to verify native
API integration and movement.

The fixed action allowlist also supports `orders`, `abandon_target`, `stance`,
`destroy_own_units`, `destroy_own_buildings`, `train` and `research`. Production
requires exactly one completed owned building and an advertised option; research
additionally checks the calling player's native technology requirements. The
native `research` command uses singular `entity`; `train` uses `entities` and an
integer `count` from 1 to 100. See `../../docs/player-actions.md` for the MCP
surface. Eight browser contracts cover observation privacy, native metadata and command validation.

The bounded `describe_options` action reads the owned entity's current template or
its advertised research/training/construction options (at most 16 descriptions).
`zy_mcp_metadata.js` uses the running game's native GUI template helpers and
localization. Same-name technology pairs retain separate IDs, effects and group
requirements. Descriptions label completeness, cost scope and language fallback;
template unit costs are not final batch prices or producer-specific training times.
Public identity names in simulation properties remain source-language labels.

Additional allowlisted actions implement native pack/unpack and cancellation,
owned garrison unloading, turret exit and production cancellation. Target orders
also support healing, guarding, treasure collection, resource return, owned-market
trade and turret occupation; position orders support patrol and `attack-walk`.
Every batch is validated before any network command is posted. No generic script
or arbitrary native-command endpoint is provided. See `../../MCP.md` for all 21 tools
and remaining management gaps.

`abandon_target` removes persistent guard bindings with `remove-guard` before
posting `stop`; plain `stop` preserves those bindings. Neither disables UnitAI
or changes stance. Native gathering deliveries, construction continuation and
combat reactions still run. Reissuing identical work may disrupt pending orders;
queued construction can place and pay for foundations immediately. Model callers
should follow the semi-autonomous coordination rules in `../../MCP.md`.
