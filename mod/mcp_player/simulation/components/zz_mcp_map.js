/*
 * Read-only GUI extension, pinned to 0 A.D. 0.28.0. Do not register another
 * component, change Serialize, retain observations here, or mutate simulation
 * state. The GUI owns its fog-of-war memory outside synchronized simulation.
 */
(function()
{
    const MAX_CELLS = 4096;
    const MAX_OBJECTS = 2000;
    // Official v0.28.0 helpers/Los.h and helpers/MapEdgeTiles.h. These match
    // CCmpRangeManager::LosIsOffWorld, including nearest LOS-vertex rounding.
    const LOS_TILE_SIZE = 4;
    const MAP_EDGE_TILES = 3;

    function finite(value)
    {
        return typeof value === "number" && Number.isFinite(value);
    }

    function keys(value, allowed)
    {
        if (!value || typeof value !== "object" || Array.isArray(value) ||
            Object.keys(value).some(key => allowed.indexOf(key) === -1))
            throw new Error("Invalid map request fields.");
    }

    function inWorld(x, z, size, circular)
    {
        if (!finite(x) || !finite(z) || x < 0 || z < 0 || x > size || z > size)
            return false;
        const vertices = Math.floor(size / LOS_TILE_SIZE) + 1;
        const i = Math.floor(x / LOS_TILE_SIZE + 0.5);
        const j = Math.floor(z / LOS_TILE_SIZE + 0.5);
        if (!circular)
            return i >= MAP_EDGE_TILES && j >= MAP_EDGE_TILES &&
                i < vertices - MAP_EDGE_TILES && j < vertices - MAP_EDGE_TILES;
        const middle = Math.floor(vertices / 2);
        const radius = middle - MAP_EDGE_TILES + 1;
        return (i - middle) * (i - middle) + (j - middle) * (j - middle) < radius * radius;
    }

    function resourceData(state)
    {
        const supply = state.resourceSupply;
        if (supply && supply.type && typeof supply.type.generic === "string" &&
            finite(supply.amount) && supply.amount >= 0)
        {
            const type = supply.type.generic +
                (typeof supply.type.specific === "string" && supply.type.specific ? "." + supply.type.specific : "");
            return {
                "type": type,
                "amount": supply.amount,
                "max": finite(supply.max) && supply.max >= 0 ? supply.max : null
            };
        }
        // Treasure can contain more than one resource. The singular resource
        // field is included only for an unambiguous one-resource treasure.
        const treasure = state.treasure && state.treasure.resources;
        if (treasure)
        {
            const types = ["food", "wood", "stone", "metal"].filter(type =>
                finite(treasure[type]) && treasure[type] > 0);
            if (types.length === 1)
                return { "type": types[0] + ".treasure", "amount": treasure[types[0]], "max": treasure[types[0]] };
        }
        return undefined;
    }

    GuiInterface.prototype.MCPGetMapRegion = function(player, request)
    {
        if (!Number.isInteger(player) || player <= 0)
            throw new Error("Map observation requires an active player.");
        keys(request, ["region", "remembered", "include_objects"]);
        const includeObjects = request.include_objects === undefined ? true : request.include_objects;
        if (typeof includeObjects !== "boolean")
            throw new Error("Invalid object selection flag.");
        const region = request.region;
        keys(region, ["x_min", "z_min", "x_max", "z_max", "cell_size"]);
        const terrain = Engine.QueryInterface(SYSTEM_ENTITY, IID_Terrain);
        const range = Engine.QueryInterface(SYSTEM_ENTITY, IID_RangeManager);
        const timer = Engine.QueryInterface(SYSTEM_ENTITY, IID_Timer);
        if (!terrain || !range || !timer)
            throw new Error("Map observation is not available.");
        const size = terrain.GetMapSize();
        if (!finite(size) || size <= 0 ||
            ![region.x_min, region.z_min, region.x_max, region.z_max, region.cell_size].every(finite) ||
            region.x_min < 0 || region.z_min < 0 || region.x_max > size || region.z_max > size ||
            region.x_min >= region.x_max || region.z_min >= region.z_max ||
            region.cell_size < 4 || region.cell_size > 256)
            throw new Error("Invalid map bounds or cell size.");
        const columns = Math.ceil((region.x_max - region.x_min) / region.cell_size);
        const rows = Math.ceil((region.z_max - region.z_min) / region.cell_size);
        if (columns * rows > MAX_CELLS)
            throw new Error("Map request exceeds 4096 samples.");
        const suppliedRemembered = request.remembered === undefined ? [] : request.remembered;
        if (!Array.isArray(suppliedRemembered) || suppliedRemembered.length > MAX_OBJECTS)
            throw new Error("Too many remembered positions.");
        const rememberedIDs = new Set();
        for (const record of suppliedRemembered)
        {
            keys(record, ["id", "x", "z"]);
            if (!Number.isInteger(record.id) || record.id <= 0 || record.id > 2147483647 ||
                rememberedIDs.has(record.id) || !finite(record.x) || !finite(record.z) ||
                record.x < 0 || record.z < 0 || record.x > size || record.z > size)
                throw new Error("Invalid remembered position.");
            rememberedIDs.add(record.id);
        }
        const remembered = includeObjects ? suppliedRemembered : [];
        const time = timer.GetTime();
        if (!finite(time) || time < 0)
            throw new Error("Map time is not available.");
        const circular = range.GetLosCircular();
        const water = Engine.QueryInterface(SYSTEM_ENTITY, IID_WaterManager);
        function visibility(x, z)
        {
            if (!inWorld(x, z, size, circular))
                return { "in_bounds": false, "explored": false, "currently_visible": false };
            const los = range.GetLosVisibilityPosition(x, z, player);
            return {
                "in_bounds": true,
                "explored": los === "fogged" || los === "visible",
                "currently_visible": los === "visible"
            };
        }
        const cells = [];
        for (let row = 0; row < rows; ++row)
            for (let column = 0; column < columns; ++column)
            {
                const x0 = region.x_min + column * region.cell_size;
                const z0 = region.z_min + row * region.cell_size;
                const x = (x0 + Math.min(region.x_max, x0 + region.cell_size)) / 2;
                const z = (z0 + Math.min(region.z_max, z0 + region.cell_size)) / 2;
                const cell = Object.assign({ "x": x, "z": z }, visibility(x, z), { "terrain": null });
                // Unknown and fogged terrain is never read here. GUI memory may
                // retain terrain that was previously observed while visible.
                if (cell.currently_visible)
                {
                    const height = terrain.GetGroundLevel(x, z);
                    const waterLevel = water ? water.GetWaterLevel(x, z) : null;
                    if (finite(height))
                        cell.terrain = { "height": height, "water_level": finite(waterLevel) ? waterLevel : null };
                }
                cells.push(cell);
            }
        const objects = [];
        let truncated = false;
        // Candidate IDs never leave the simulation. Enumerating this explicit
        // engine list includes Gaia resources without guessing numeric IDs.
        const candidates = includeObjects ? range.GetGaiaAndNonGaiaEntities().slice().sort((a, b) => a - b) : [];
        for (const id of candidates)
        {
            if (!Number.isInteger(id) || id <= 0 || range.GetLosVisibility(id, player) !== "visible")
                continue;
            const positionComponent = Engine.QueryInterface(id, IID_Position);
            if (!positionComponent || !positionComponent.IsInWorld())
                continue;
            const position = positionComponent.GetPosition();
            if (!position || !finite(position.x) || !finite(position.z) ||
                position.x < region.x_min || position.x >= region.x_max ||
                position.z < region.z_min || position.z >= region.z_max ||
                !visibility(position.x, position.z).currently_visible)
                continue;
            const state = this.GetEntityState(player, id);
            if (!state || state.mirage || !Number.isInteger(state.player) || state.player < 0 ||
                typeof state.template !== "string" || !state.template || !state.position ||
                !finite(state.position.x) || !finite(state.position.z))
                continue;
            if (objects.length === MAX_OBJECTS)
            {
                truncated = true;
                break;
            }
            const classes = state.identity && Array.isArray(state.identity.classes) ?
                state.identity.classes.filter(value => typeof value === "string").slice(0, 64) : [];
            const resource = resourceData(state);
            const movable = !!state.speed || classes.indexOf("Unit") !== -1;
            const kind = state.treasure ? "treasure" :
                classes.indexOf("Tree") !== -1 || resource && resource.type === "wood.tree" ? "tree" :
                classes.indexOf("Structure") !== -1 ? "building" : resource ? "resource" : movable ? "unit" : "other";
            const object = {
                "id": id,
                "owner": state.player,
                "template": state.template.slice(0, 200),
                "classes": classes,
                "kind": kind,
                "position": { "x": position.x, "z": position.z },
                "static": !movable,
                "currently_visible": true,
                "last_seen": time
            };
            if (finite(state.hitpoints) && state.hitpoints >= 0)
                object.hitpoints = state.hitpoints;
            if (resource)
                object.resource = resource;
            if (typeof this.MCPProjectEntityProperties === "function" &&
                (state.unitAI || state.pack || state.garrisonHolder ||
                 classes.indexOf("Unit") !== -1 || classes.indexOf("Structure") !== -1))
                object.properties = this.MCPProjectEntityProperties(player, state, true);
            objects.push(object);
        }
        return {
            "time": time,
            "columns": columns,
            "rows": rows,
            "cells": cells,
            "objects": objects,
            "truncated": truncated,
            // Query only saved coordinates, never the entity's new position,
            // current existence, owner, health, or depleted resource amount.
            "remembered": remembered.map(record => {
                const los = visibility(record.x, record.z);
                return { "id": record.id, "explored": los.explored, "currently_visible": los.currently_visible };
            })
        };
    };
    GuiInterface.prototype.exposedFunctions.MCPGetMapRegion = 1;
})();
