/*
 * This file is loaded by the stock session.xml's gui/session/ script directory.
 * The MCP process receives only the explicit projection below. Never write raw
 * GetSimulationState(), GetEntityState(), simulation commands or rejoin state.
 */
var MCPPlayerBridge = {
    version: 1,
    get commandPath()
    {
        return this.sessionPath("command.json");
    },

    get observationPath()
    {
        return this.sessionPath("observation.json");
    },
    interval: 500,
    lastTick: 0,
    lastCommandId: null,
    lastAction: null,
    mapRequest: null,
    mapIdentity: null,

    session: function()
    {
        const value = Engine.ConfigDB_GetValue("user", "mcp.session");
        return typeof value === "string" && value.length === 32 && /^[a-f0-9]{32}$/.test(value) ? value : "";
    },

    sessionPath: function(filename)
    {
        const session = this.session();
        if (!session)
            throw new Error("A valid unique bridge session is required.");
        return "moddata/mcp_player/" + session + "/" + filename;
    },

    checkPlayer: function()
    {
        const player = Engine.GetPlayerID();
        if (!this.session() || player <= 0 || g_IsReplay)
            throw new Error("Bridge requires the active player's normal game view.");
        const simulation = Engine.GuiInterfaceCall("GetSimulationState");
        const own = simulation && Array.isArray(simulation.players) ? simulation.players[player] : null;
        if (!own || own.controlsAll)
            throw new Error("Bridge requires ordinary control of one player.");
        const outcome = this.projectOutcome(simulation, player);
        if (outcome.outcome === "won" || outcome.outcome === "defeated")
            return { "player": player, "simulation": simulation, "outcome": outcome, "finished": true };
        if (g_IsObserver || g_ViewedPlayer !== player)
            throw new Error("Bridge requires the active player's normal game view.");
        // A host-selected public match rule applies equally to every player.
        // Never serialize g_InitAttributes: it also contains private map data.
        const roomRevealsMap = typeof g_InitAttributes !== "undefined" &&
            g_InitAttributes.settings && g_InitAttributes.settings.RevealMap === true;
        if (Engine.GuiInterfaceCall("IsMapRevealed") && !roomRevealsMap)
            throw new Error("Bridge is disabled while reveal-map is enabled.");
        return { "player": player, "simulation": simulation, "outcome": outcome, "finished": false };
    },

    projectOutcome: function(simulation, player)
    {
        const players = simulation && Array.isArray(simulation.players) ? simulation.players : [];
        const own = players[player];
        const states = players.slice(1).map(item => item && item.state);
        const known = state => state === "active" || state === "won" || state === "defeated";
        let gameOver = null;
        if (states.length)
        {
            if (states.some(state => state === "active"))
                gameOver = false;
            else if (states.every(known))
                gameOver = true;
        }
        return {
            "outcome": own && known(own.state) ? own.state : "unknown",
            "game_over": gameOver
        };
    },

    projectFinished: function(player, simulation, outcome)
    {
        const observation = {
            "version": this.version,
            "session": this.session(),
            "policy": "own-player-only-v1",
            "available": true,
            "player_id": player,
            "outcome": outcome.outcome,
            "game_over": outcome.game_over,
            "emitted_at_ms": Date.now()
        };
        if (typeof simulation.timeElapsed === "number" && Number.isFinite(simulation.timeElapsed))
            observation.game_time_ms = simulation.timeElapsed;
        return observation;
    },

    ownStates: function(player)
    {
        const result = [];
        // GetPlayerEntities uses the caller's player ID in GuiInterface. Do not
        // replace this with GetNonGaiaEntities or scan numeric entity IDs.
        const ids = Engine.GuiInterfaceCall("GetPlayerEntities");
        for (const id of ids)
        {
            const state = Engine.GuiInterfaceCall("GetEntityState", id);
            if (state && state.player === player)
            {
                state.mcpProperties = Engine.GuiInterfaceCall("MCPGetEntityProperties", id);
                result.push(state);
            }
        }
        return result;
    },

    projectEntity: function(state)
    {
        const entity = {
            "id": state.id,
            "owner": state.player,
            "template": state.template,
            "classes": state.identity ? state.identity.classes : [],
            "movable": !!(state.speed && state.unitAI && state.position),
            "idle": !!(state.unitAI && state.unitAI.isIdle)
        };
        if (state.position)
            entity.position = { "x": state.position.x, "z": state.position.z };
        if (typeof state.hitpoints === "number")
            entity.hitpoints = state.hitpoints;
        if (typeof state.maxHitpoints === "number")
            entity.max_hitpoints = state.maxHitpoints;
        if (state.mcpProperties)
            entity.properties = state.mcpProperties;
        return entity;
    },

    projectScoreboard: function(player, own, states)
    {
        const resources = ["food", "wood", "stone", "metal"];
        const gatherers = {};
        for (const resource of resources)
            if (own.resourceGatherers && typeof own.resourceGatherers[resource] === "number")
                gatherers[resource] = Math.max(0, Math.floor(own.resourceGatherers[resource]));

        let idleCount = 0;
        let idleWorkerCount = 0;
        const construction = [];
        const busyBuildings = [];
        for (const state of states)
        {
            const classes = state.identity && Array.isArray(state.identity.classes) ? state.identity.classes : [];
            if (state.speed && state.unitAI && state.position && state.unitAI.isIdle)
            {
                ++idleCount;
                if (classes.indexOf("Worker") !== -1)
                    ++idleWorkerCount;
            }
            if (state.foundation && construction.length < 2000)
                construction.push({
                    "id": state.id,
                    "template": state.template,
                    "progress": typeof state.hitpoints === "number" && typeof state.maxHitpoints === "number" &&
                        state.maxHitpoints > 0 ? Math.max(0, Math.min(1, state.hitpoints / state.maxHitpoints)) : null,
                    "builders": state.foundation.numBuilders
                });

            const queue = state.production && Array.isArray(state.production.queue) ?
                state.production.queue.slice(0, 16).map(item => ({
                    "template_name": typeof item.templateName === "string" ? item.templateName : null,
                    "unit_template": typeof item.unitTemplate === "string" ? item.unitTemplate : null,
                    "technology_template": typeof item.technologyTemplate === "string" ? item.technologyTemplate : null,
                    "count": typeof item.count === "number" ? item.count : null,
                    "progress": typeof item.progress === "number" ? Math.max(0, Math.min(1, item.progress)) : null,
                    "time_remaining_ms": typeof item.timeRemaining === "number" ? Math.max(0, item.timeRemaining) : null,
                    "paused": item.paused === true
                })) : [];
            if (queue.length && classes.indexOf("Structure") !== -1 && busyBuildings.length < 2000)
                busyBuildings.push({ "id": state.id, "template": state.template, "queue": queue });
        }

        const research = [];
        try
        {
            const started = Engine.GuiInterfaceCall("GetStartedResearch") || {};
            for (const technology of Object.keys(started).slice(0, 64))
            {
                const item = started[technology];
                if (!item || typeof item !== "object")
                    continue;
                research.push({
                    "technology": technology,
                    "progress": typeof item.progress === "number" ? Math.max(0, Math.min(1, item.progress)) : null,
                    "time_remaining_ms": typeof item.timeRemaining === "number" ? Math.max(0, item.timeRemaining) : null,
                    "researcher_id": Number.isInteger(item.researcher) && item.researcher > 0 ? item.researcher : null,
                    "paused": item.paused === true
                });
            }
        }
        catch (error)
        {
            // Research progress is optional in game modes without the standard API.
        }

        let territoryPercent = null;
        try
        {
            const value = Engine.GuiInterfaceCall("MCPGetOwnTerritoryPercent");
            if (typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100)
                territoryPercent = value;
        }
        catch (error)
        {
            // A missing optional GUI API must not disable ordinary observations.
        }

        return {
            "phase": ["village", "town", "city"].indexOf(own.phase) !== -1 ? own.phase : null,
            "population": { "count": own.popCount, "limit": own.popLimit, "max": own.popMax },
            "resources": resources.reduce((result, key) => {
                if (own.resourceCounts && typeof own.resourceCounts[key] === "number")
                    result[key] = own.resourceCounts[key];
                return result;
            }, {}),
            "gatherers": gatherers,
            "idle_units": { "count": idleCount, "worker_count": idleWorkerCount },
            "territory_percent": territoryPercent,
            "current_research": research,
            "construction": construction,
            "busy_buildings": busyBuildings
        };
    },

    project: function(player, states, simulation, outcome)
    {
        // GetSimulationState includes every player's private economy. Only
        // the active player's own whitelisted fields may leave this process.
        const own = simulation.players[player];
        if (!own || own.controlsAll)
            throw new Error("Bridge requires ordinary control of one player.");
        const resources = {};
        for (const key of Object.keys(own.resourceCounts || {}))
            if (typeof own.resourceCounts[key] === "number")
                resources[key] = own.resourceCounts[key];
        return {
            "version": this.version,
            "session": this.session(),
            "policy": "own-player-only-v1",
            "available": true,
            "player_id": player,
            "outcome": outcome.outcome,
            "game_over": outcome.game_over,
            "game_time_ms": simulation.timeElapsed,
            "map_size": simulation.mapSize,
            "resources": resources,
            "population": {
                "count": own.popCount,
                "limit": own.popLimit,
                "max": own.popMax
            },
            "scoreboard": this.projectScoreboard(player, own, states),
            "own_entities": states.map(state => this.projectEntity(state)),
            "last_action": this.lastAction,
            "emitted_at_ms": Date.now()
        };
    },

    validateKeys: function(command, allowed)
    {
        for (const key of Object.keys(command))
            if (allowed.indexOf(key) === -1)
                throw new Error("Unsupported command field.");
    },

    execute: function(command, states, mapSize)
    {
        if (!command || command.version !== this.version ||
            command.session !== this.session() ||
            typeof command.id !== "string" || !command.id || command.id.length > 128)
            throw new Error("Invalid bridge command envelope.");
        if (["observe", "observe_map", "move", "stop", "orders", "abandon_target", "stance",
            "destroy_own_units", "destroy_own_buildings", "train", "research", "packing",
            "cancel_packing", "unload", "leave_turret", "cancel_production", "describe_options",
            "set_rally", "clear_rally"].indexOf(command.action) === -1)
            throw new Error("Unsupported action.");
        const baseKeys = ["version", "session", "id", "action"];
        if (command.action === "observe_map")
        {
            this.validateKeys(command, baseKeys.concat(["region", "include_objects"]));
            if (command.include_objects !== undefined && typeof command.include_objects !== "boolean")
                throw new Error("Invalid map object selection flag.");
            MCPMapMemory.validateRegion(command.region, mapSize);
            this.mapRequest = { "id": command.id, "region": command.region,
                "include_objects": command.include_objects !== false };
            return { "entity_count": 0 };
        }
        if (command.action === "observe")
        {
            this.validateKeys(command, baseKeys);
            return { "entity_count": 0 };
        }
        if (command.action === "describe_options")
        {
            this.validateKeys(command, baseKeys.concat(["entity_id", "kind", "ids"]));
            return { "entity_count": 0,
                "metadata": MCPMetadata.describe(Engine.GetPlayerID(), states, command) };
        }
        if (command.action === "set_rally" || command.action === "clear_rally")
            return MCPRally.execute(command, states, mapSize, this);
        const actionKeys = command.action === "move" ? ["entities", "x", "z", "queued"] :
            command.action === "orders" ? ["entities", "orders", "queued"] :
            command.action === "stance" ? ["entities", "stance"] :
            command.action === "train" ? ["entities", "template", "count"] :
            command.action === "research" ? ["entities", "template"] :
            command.action === "packing" ? ["entities", "packed", "queued"] :
            command.action === "unload" ? ["entities", "holder_id"] :
            command.action === "cancel_production" ? ["entities", "queue_id"] : ["entities"];
        this.validateKeys(command, baseKeys.concat(actionKeys));
        if (!Array.isArray(command.entities) || !command.entities.length || command.entities.length > 200)
            throw new Error("Select between one and 200 owned entities.");
        const player = Engine.GetPlayerID();
        const deleting = command.action === "destroy_own_units" || command.action === "destroy_own_buildings";
        const producing = command.action === "train" || command.action === "research" || command.action === "cancel_production";
        const packing = command.action === "packing" || command.action === "cancel_packing";
        if (producing && command.entities.length !== 1)
            throw new Error("Choose one owned production building.");
        const ownById = new Map(states.map(state => [state.id, state]));
        const selected = [];
        for (const id of command.entities)
        {
            if (!Number.isInteger(id) || !ownById.has(id) || ownById.get(id).player !== player || selected.indexOf(id) !== -1)
                throw new Error("Entity is unavailable or is not owned by this player.");
            const state = ownById.get(id);
            const classes = state.identity && Array.isArray(state.identity.classes) ? state.identity.classes : [];
            if (deleting)
            {
                const expectedClass = command.action === "destroy_own_units" ? "Unit" : "Structure";
                if (classes.indexOf(expectedClass) === -1 || state.identity.canDelete === false)
                    throw new Error("Entity cannot be deleted by this action.");
            }
            else if (producing)
            {
                if (classes.indexOf("Structure") === -1 || state.foundation || !state.production)
                    throw new Error("Choose a completed owned production building.");
            }
            else if (packing)
            {
                const profile = state.mcpProperties && state.mcpProperties.packing;
                if (classes.indexOf("Unit") === -1 || !state.pack || !profile ||
                    typeof profile.packed !== "boolean" || typeof profile.changing !== "boolean" ||
                    state.pack.packed !== profile.packed)
                    throw new Error("Choose owned units with a packing capability.");
                if (command.action === "cancel_packing" ? !profile.changing :
                    profile.changing || profile.packed === command.packed)
                    throw new Error("Packing state does not allow this action.");
            }
            else if (command.action === "unload")
            {
                if (classes.indexOf("Unit") === -1 || !state.garrisonable)
                    throw new Error("Choose your garrisoned units.");
            }
            else if (command.action === "leave_turret")
            {
                if (classes.indexOf("Unit") === -1 || !state.turretable ||
                    !Number.isInteger(state.turretable.holder) || state.turretable.holder <= 0 ||
                    state.turretable.ejectable !== true)
                    throw new Error("Choose your ejectable turret occupants.");
            }
            else if (!state.unitAI || (command.action === "move" || command.action === "orders") &&
                (!state.speed || !state.position))
                throw new Error("Entity cannot perform this action.");
            selected.push(id);
        }
        const commands = [];
        function movement(x, z)
        {
            return Number.isFinite(x) && Number.isFinite(z) && x >= 0 && z >= 0 && x <= mapSize && z <= mapSize;
        }
        if (command.action === "packing")
        {
            if (typeof command.packed !== "boolean" ||
                command.queued !== undefined && typeof command.queued !== "boolean")
                throw new Error("Invalid packing state or queue flag.");
            commands.push({ "type": "pack", "entities": selected, "pack": command.packed,
                "queued": command.queued === true, "pushFront": false });
        }
        else if (command.action === "cancel_packing")
        {
            for (const direction of [true, false])
            {
                const group = selected.filter(id => !ownById.get(id).mcpProperties.packing.packed === direction);
                if (group.length)
                    commands.push({ "type": "cancel-pack", "entities": group, "pack": direction,
                        "queued": false, "pushFront": false });
            }
        }
        else if (command.action === "unload")
        {
            const holder = ownById.get(command.holder_id);
            if (!Number.isInteger(command.holder_id) || command.holder_id <= 0 ||
                command.holder_id >= 2147483648 ||
                !holder || holder.player !== player || !holder.garrisonHolder ||
                !Array.isArray(holder.garrisonHolder.entities) || selected.some(id =>
                    holder.garrisonHolder.entities.indexOf(id) === -1 ||
                    ownById.get(id).garrisonable.holder !== command.holder_id))
                throw new Error("Choose your current occupants of an owned garrison holder.");
            commands.push({ "type": "unload", "garrisonHolder": command.holder_id, "entities": selected });
        }
        else if (command.action === "cancel_production")
        {
            const queue = ownById.get(selected[0]).production.queue;
            if (!Number.isInteger(command.queue_id) || command.queue_id < 0 || command.queue_id >= 4294967296 ||
                !Array.isArray(queue) || !queue.some(item => item.id === command.queue_id))
                throw new Error("Choose a current item of your production queue.");
            commands.push({ "type": "stop-production", "entity": selected[0], "id": command.queue_id });
        }
        else if (command.action === "leave_turret")
            commands.push({ "type": "leave-turret", "entities": selected });
        else if (producing)
        {
            if (typeof command.template !== "string" || command.template.length > 200 ||
                !/^[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*$/.test(command.template))
                throw new Error("Invalid production template.");
            const producer = ownById.get(selected[0]);
            if (command.action === "train")
            {
                if (!Number.isInteger(command.count) || command.count < 1 || command.count > 100 ||
                    !producer.trainer || !Array.isArray(producer.trainer.entities) ||
                    producer.trainer.entities.indexOf(command.template) === -1)
                    throw new Error("The building cannot train this unit or count.");
                commands.push({ "type": "train", "entities": selected, "template": command.template,
                    "count": command.count, "pushFront": false });
            }
            else
            {
                const options = producer.researcher && producer.researcher.technologies;
                if (!Array.isArray(options) || !options.some(item => item === command.template ||
                    item && item.pair === true && (item.top === command.template || item.bottom === command.template)))
                    throw new Error("The building does not offer this technology.");
                if (!Engine.GuiInterfaceCall("CheckTechnologyRequirements", { "tech": command.template }))
                    throw new Error("Technology requirements are not met or it is already queued.");
                commands.push({ "type": "research", "entity": selected[0], "template": command.template,
                    "pushFront": false });
            }
        }
        else if (command.action === "move")
        {
            if (!movement(command.x, command.z) ||
                (command.queued !== undefined && typeof command.queued !== "boolean"))
                throw new Error("Invalid movement coordinates or queue flag.");
            // A player can normally order exploration into black terrain. This
            // uses no information about terrain or entities at the destination.
            commands.push({
                "type": "walk", "entities": selected,
                "x": command.x, "z": command.z,
                "queued": command.queued === true,
                "pushFront": false,
                "formation": "special/formations/null"
            });
        }
        else if (command.action === "orders")
        {
            if (!Array.isArray(command.orders) || !command.orders.length || command.orders.length > 100 ||
                (command.queued !== undefined && typeof command.queued !== "boolean"))
                throw new Error("Choose between one and 100 valid orders.");
            const statesById = selected.map(id => ownById.get(id));
            for (const [index, order] of command.orders.entries())
            {
                if (!order || typeof order !== "object" || Array.isArray(order) ||
                    ["attack", "capture", "gather", "repair", "garrison", "build", "heal", "guard",
                     "collect_treasure", "return_resource", "trade", "patrol", "attack_move", "occupy_turret"].indexOf(order.type) === -1)
                    throw new Error("Unsupported unit order.");
                const positional = order.type === "patrol" || order.type === "attack_move";
                this.validateKeys(order, order.type === "build" ? ["type", "template", "x", "z", "angle"] :
                    positional ? ["type", "x", "z"] : ["type", "target"]);
                const native = { "entities": selected, "queued": index > 0 || command.queued === true,
                    "pushFront": false, "formation": "special/formations/null" };
                if (positional)
                {
                    if (!movement(order.x, order.z) || statesById.some(state => order.type === "patrol" ?
                        state.unitAI.canPatrol !== true : !state.attack))
                        throw new Error("The selected units cannot perform this position order.");
                    Object.assign(native, { "type": order.type === "patrol" ? "patrol" : "attack-walk",
                        "x": order.x, "z": order.z, "allowCapture": false });
                }
                else if (order.type === "build")
                {
                    if (typeof order.template !== "string" || !/^structures\/[a-zA-Z0-9_/-]+$/.test(order.template) ||
                        order.template.length > 200 || !movement(order.x, order.z) ||
                        (order.angle !== undefined && (!Number.isFinite(order.angle) || Math.abs(order.angle) > Math.PI * 2)))
                        throw new Error("Invalid building template or placement.");
                    if (statesById.some(state => !state.builder || !state.mcpProperties || !state.mcpProperties.construction ||
                        state.mcpProperties.construction.templates.indexOf(order.template) === -1))
                        throw new Error("The selected builders cannot construct this template.");
                    Object.assign(native, { "type": "construct", "template": order.template,
                        "x": order.x, "z": order.z, "angle": order.angle === undefined ? 0 : order.angle,
                        "autorepair": true, "autocontinue": true });
                }
                else
                {
                    if (!Number.isInteger(order.target) || order.target <= 0 || order.target >= 2147483648 || selected.indexOf(order.target) !== -1)
                        throw new Error("Invalid target entity.");
                    let target = ownById.get(order.target);
                    if (!target)
                    {
                        if (["repair", "garrison", "heal", "guard", "occupy_turret", "return_resource", "trade"].indexOf(order.type) !== -1)
                            throw new Error("This order requires a currently owned target.");
                        // The simulation properties entry point checks LOS before
                        // reading GetEntityState. Discard the result; this call is
                        // only an authorization guard for the explicit target ID.
                        Engine.GuiInterfaceCall("MCPGetEntityProperties", order.target);
                        target = Engine.GuiInterfaceCall("GetEntityState", order.target);
                    }
                    // GetEntityState may still return a mirage; do not use its
                    // components for a target outside the current normal view.
                    if (!target || target.mirage || target.player !== player && target.visibility !== "visible")
                        throw new Error("The target is unavailable in the current player view.");
                    const attack = order.type === "attack" || order.type === "capture";
                    if (attack)
                    {
                        if (target.player === player || order.type === "capture" && !target.capturePoints ||
                            order.type === "attack" && !(target.hitpoints > 0))
                            throw new Error("The target cannot receive this attack.");
                        // Check every attacker against the active player's
                        // owned state before asking the simulation's CanAttack
                        // helper about any entity IDs. This keeps the GUI query
                        // from becoming a capability oracle for stale/forged
                        // selection state and avoids partial checks on batches.
                        if (statesById.some((state, stateIndex) => !state || state.id !== selected[stateIndex] ||
                            state.player !== player || !state.attack))
                            throw new Error("The selected units cannot attack this target.");
                        const types = [order.type === "capture" ? "Capture" : "!Capture"];
                        if (statesById.some(state => !Engine.GuiInterfaceCall("CanAttack", {
                            "entity": state.id, "target": order.target, "types": types })))
                            throw new Error("The selected units cannot attack this target.");
                        Object.assign(native, { "type": "attack", "target": order.target, "allowCapture": order.type === "capture" });
                    }
                    else if (order.type === "gather")
                    {
                        const supply = target.resourceSupply;
                        if (target.player !== 0 && target.player !== player || !supply || !supply.type ||
                            statesById.some(state => !state.resourceGatherRates ||
                            !(state.resourceGatherRates[supply.type.generic + "." + supply.type.specific] > 0 ||
                              state.resourceGatherRates[supply.type.generic] > 0)))
                            throw new Error("The selected units cannot gather this resource.");
                        Object.assign(native, { "type": "gather", "target": order.target });
                    }
                    else if (order.type === "repair")
                    {
                        if (target.player !== player || (!target.foundation && !target.needsRepair) ||
                            statesById.some(state => !state.builder))
                            throw new Error("The selected builders cannot repair this target.");
                        Object.assign(native, { "type": "repair", "target": order.target, "autocontinue": true });
                    }
                    else if (order.type === "garrison")
                    {
                        if (target.player !== player || !target.garrisonHolder ||
                            statesById.some(state => !state.garrisonable))
                            throw new Error("The selected units cannot enter this target.");
                        Object.assign(native, { "type": "garrison", "target": order.target });
                    }
                    else if (order.type === "heal")
                    {
                        const classes = target.identity && target.identity.classes || [];
                        if (target.player !== player || !target.needsHeal || classes.indexOf("Unit") === -1 ||
                            statesById.some(state => !state.heal ||
                                MatchesClassList(classes, state.heal.unhealableClasses) ||
                                !MatchesClassList(classes, state.heal.healableClasses)))
                            throw new Error("The selected healers cannot heal this target.");
                        Object.assign(native, { "type": "heal", "target": order.target });
                    }
                    else if (order.type === "guard")
                    {
                        if (target.player !== player || !target.guard ||
                            statesById.some(state => state.unitAI.canGuard !== true))
                            throw new Error("The selected units cannot guard this target.");
                        Object.assign(native, { "type": "guard", "target": order.target });
                    }
                    else if (order.type === "occupy_turret")
                    {
                        const points = target.turretHolder && target.turretHolder.turretPoints;
                        if (target.player !== player || !Array.isArray(points) || statesById.some(state =>
                            !state.turretable || !points.some(point => !point.allowedClasses ||
                                MatchesClassList(state.identity && state.identity.classes || [], point.allowedClasses))))
                            throw new Error("The selected units cannot occupy this turret holder.");
                        Object.assign(native, { "type": "occupy-turret", "target": order.target });
                    }
                    else if (order.type === "collect_treasure")
                    {
                        if (target.player !== 0 || !target.treasure ||
                            statesById.some(state => state.treasureCollector !== true))
                            throw new Error("The selected units cannot collect this treasure.");
                        Object.assign(native, { "type": "collect-treasure", "target": order.target });
                    }
                    else if (order.type === "return_resource")
                    {
                        const types = target.resourceDropsite && target.resourceDropsite.types;
                        if (target.player !== player || target.foundation || !Array.isArray(types) ||
                            statesById.some(state => !Array.isArray(state.resourceCarrying) ||
                                !state.resourceCarrying.some(item => item.amount > 0 && types.indexOf(item.type) !== -1)))
                            throw new Error("The selected units cannot return resources here.");
                        Object.assign(native, { "type": "returnresource", "target": order.target });
                    }
                    else if (order.type === "trade")
                    {
                        if (target.player !== player || target.foundation || !target.market || statesById.some(state =>
                            !state.trader || !(target.market.land && state.identity && state.identity.classes.indexOf("Organic") !== -1 ||
                                              target.market.naval && state.identity && state.identity.classes.indexOf("Ship") !== -1)))
                            throw new Error("The selected traders cannot use this market.");
                        Object.assign(native, { "type": "setup-trade-route", "target": order.target,
                            "source": null, "route": null });
                    }
                }
                commands.push(native);
            }
        }
        else if (command.action === "stance")
        {
            if (["violent", "aggressive", "defensive", "passive", "standground", "skittish"].indexOf(command.stance) === -1)
                throw new Error("Invalid unit stance.");
            if (selected.some(id => ownById.get(id).turretable && ownById.get(id).turretable.holder))
                throw new Error("Turret occupants cannot change stance.");
            commands.push({ "type": "stance", "entities": selected, "name": command.stance });
        }
        else if (deleting)
            commands.push({ "type": "delete-entities", "entities": selected });
        else if (command.action === "abandon_target")
        {
            // Stop alone retains UnitAI's persistent guard association and
            // IDLE can resume it. RemoveGuard is a safe no-op for non-guards.
            commands.push({ "type": "remove-guard", "entities": selected });
            commands.push({ "type": "stop", "entities": selected, "queued": false });
        }
        else
            commands.push({ "type": "stop", "entities": selected, "queued": false });
        // Validate the complete order list before producing any side effect.
        for (const native of commands)
            Engine.PostNetworkCommand(native);
        return { "entity_count": selected.length };
    },

    tick: function()
    {
        // Invalid configuration must not read or overwrite a legacy/shared path.
        if (!this.session())
            return;
        const now = Date.now();
        if (now - this.lastTick < this.interval || !g_Settings)
            return;
        this.lastTick = now;
        try
        {
            const context = this.checkPlayer();
            const player = context.player;
            const identity = this.session() + ":" + player;
            if (this.mapIdentity !== identity)
            {
                this.mapIdentity = identity;
                this.mapRequest = null;
                this.lastCommandId = null;
                this.lastAction = null;
                if (typeof MCPMapMemory !== "undefined")
                    MCPMapMemory.clear();
            }
            const states = context.finished ? [] : this.ownStates(player);
            let observation = context.finished ?
                this.projectFinished(player, context.simulation, context.outcome) :
                this.project(player, states, context.simulation, context.outcome);
            if (context.finished)
            {
                this.mapRequest = null;
                if (typeof MCPMapMemory !== "undefined")
                    MCPMapMemory.clear();
            }
            if (!context.finished && Engine.FileExists(this.commandPath))
            {
                const command = Engine.ReadJSONFile(this.commandPath);
                if (command && command.session === this.session() && command.id !== this.lastCommandId)
                {
                    this.lastCommandId = command.id;
                    const ack = {
                        "id": typeof command.id === "string" ? command.id : null,
                        "action": typeof command.action === "string" ? command.action : null,
                        "ok": false,
                        "issued_at_ms": now
                    };
                    try
                    {
                        const result = this.execute(command, states, observation.map_size);
                        ack.ok = true;
                        ack.entity_count = result.entity_count;
                        if (result.metadata)
                            ack.metadata = result.metadata;
                    }
                    catch (error)
                    {
                        ack.error = String(error.message || "Command rejected.");
                    }
                    this.lastAction = ack;
                    observation.last_action = ack;
                }
            }
            if (this.mapRequest && !context.finished)
            {
                try
                {
                    observation.map_region = MCPMapMemory.observe(this.mapRequest.region, player,
                        this.session(), this.mapRequest.include_objects);
                    observation.map_request_id = this.mapRequest.id;
                }
                catch (error)
                {
                    if (this.lastAction && this.lastAction.id === this.mapRequest.id)
                    {
                        this.lastAction.ok = false;
                        this.lastAction.error = "Player-view map observation is unavailable.";
                    }
                    this.mapRequest = null;
                }
            }
            Engine.WriteJSONFile(this.observationPath, observation);
        }
        catch (error)
        {
            this.mapRequest = null;
            if (typeof MCPMapMemory !== "undefined")
                MCPMapMemory.clear();
            // Fail closed: never retain or serialize a raw state in errors.
            Engine.WriteJSONFile(this.observationPath, {
                "version": this.version,
                "session": this.session(),
                "policy": "own-player-only-v1",
                "available": false,
                "error": "Normal active-player observation is unavailable.",
                "emitted_at_ms": now
            });
        }
    }
};

var MCPPlayerOriginalOnTick = onTick;
onTick = function()
{
    MCPPlayerOriginalOnTick();
    if (MCPPlayerBridge.session())
        MCPPlayerBridge.tick();
};
