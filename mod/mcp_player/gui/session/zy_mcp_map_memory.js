/* Player-view history lives in the GUI, never in synchronized simulation state. */
var MCPMapMemory = {
    policy: "player-los-map-v1",
    identity: null,
    time: -1,
    terrain: new Map(),
    objects: new Map(),

    clear: function()
    {
        this.identity = null;
        this.time = -1;
        this.terrain.clear();
        this.objects.clear();
    },

    validateRegion: function(region, mapSize)
    {
        const keys = ["x_min", "z_min", "x_max", "z_max", "cell_size"];
        if (!region || Object.keys(region).length !== keys.length ||
            keys.some(key => !Number.isFinite(region[key])) ||
            region.x_min < 0 || region.z_min < 0 ||
            region.x_max > mapSize || region.z_max > mapSize ||
            region.x_min >= region.x_max || region.z_min >= region.z_max ||
            region.cell_size < 4 || region.cell_size > 256 ||
            Math.ceil((region.x_max - region.x_min) / region.cell_size) *
            Math.ceil((region.z_max - region.z_min) / region.cell_size) > 4096)
            throw new Error("Invalid or excessive map region.");
    },

    inside: function(position, region)
    {
        return position.x >= region.x_min && position.x < region.x_max &&
            position.z >= region.z_min && position.z < region.z_max;
    },

    // Construct independent snapshots; never retain a live state graph.
    snapshot: function(item, time)
    {
        const result = {
            "id": item.id, "owner": item.owner, "kind": item.kind,
            "template": item.template,
            "position": { "x": item.position.x, "z": item.position.z },
            "static": item.static === true, "currently_visible": true,
            "last_seen": time
        };
        if (Array.isArray(item.classes))
            result.classes = item.classes.slice(0, 64).map(value => String(value).slice(0, 80));
        if (Number.isFinite(item.hitpoints))
            result.hitpoints = item.hitpoints;
        if (item.resource)
            result.resource = {
                "type": item.resource.type,
                "amount": item.resource.amount,
                "max": item.resource.max
            };
        // The fixed simulation query already projected these public groups.
        // Clone the complete nested profile so fog never holds live references.
        if (item.properties)
        {
            result.properties = {};
            for (const key of ["version", "identity", "capabilities", "gathering",
                "storage", "combat", "garrison", "packing", "building"])
                if (Object.prototype.hasOwnProperty.call(item.properties, key))
                    result.properties[key] = JSON.parse(JSON.stringify(item.properties[key]));
            for (const key of ["construction", "inventory", "current_target", "rally_point", "activity", "garrisoned_in"])
                result.properties[key] = null;
            if (result.properties.garrison)
            {
                result.properties.garrison.occupied_slots = null;
                result.properties.garrison.occupants = null;
            }
            if (result.properties.building)
            {
                result.properties.building.production = null;
                result.properties.building.foundation = null;
                result.properties.building.repair = null;
                if (result.properties.building.resource)
                    result.properties.building.resource.gatherers = null;
            }
        }
        return result;
    },

    observe: function(region, player, session, includeObjects)
    {
        includeObjects = includeObjects !== false;
        const identity = session + ":" + player;
        if (identity !== this.identity)
        {
            this.clear();
            this.identity = identity;
        }
        const remembered = [];
        if (includeObjects)
            for (const item of this.objects.values())
                if (this.inside(item.position, region))
                    remembered.push({ "id": item.id, "x": item.position.x, "z": item.position.z });
        // At most 2000 cached positions can be verified per request. Unverified
        // memories are omitted this time, not deleted because of hidden state.
        const queriedMemory = remembered.slice(0, 2000);
        const raw = Engine.GuiInterfaceCall("MCPGetMapRegion", {
            "region": region, "remembered": queriedMemory,
            "include_objects": includeObjects
        });
        if (!raw || !Number.isFinite(raw.time))
            throw new Error("Map observation is unavailable.");
        if (raw.time < this.time)
        {
            this.terrain.clear();
            this.objects.clear();
        }
        this.time = raw.time;
        const cells = raw.cells.map(cell => {
            const key = cell.x + ":" + cell.z;
            let terrain = null;
            if (cell.in_bounds && cell.explored)
            {
                if (cell.currently_visible && cell.terrain)
                {
                    terrain = {
                        "height": cell.terrain.height,
                        "water_level": cell.terrain.water_level,
                        "last_seen": raw.time
                    };
                    this.terrain.delete(key);
                    this.terrain.set(key, terrain);
                }
                else
                    terrain = this.terrain.get(key) || null;
            }
            return {
                "x": cell.x, "z": cell.z, "in_bounds": cell.in_bounds === true,
                "explored": cell.explored === true,
                "currently_visible": cell.currently_visible === true,
                "terrain": terrain ? Object.assign({}, terrain) : null
            };
        });
        const visible = new Map();
        const objects = [];
        if (includeObjects)
        {
            for (const item of raw.objects)
            {
                if (item.currently_visible !== true || !this.inside(item.position, region))
                    continue;
                const snapshot = this.snapshot(item, raw.time);
                visible.set(item.id, snapshot);
                if (snapshot.static)
                {
                    this.objects.delete(item.id);
                    this.objects.set(item.id, this.snapshot(item, raw.time));
                }
                else
                    this.objects.delete(item.id);
            }
            objects.push(...visible.values());
            for (const proof of raw.remembered)
            {
                const item = this.objects.get(proof.id);
                if (!item || visible.has(item.id) || !this.inside(item.position, region))
                    continue;
                if (proof.currently_visible)
                {
                    // Absence proves destruction only when the *visible* source
                    // list is complete. Never query the old entity ID under fog.
                    if (!raw.truncated)
                        this.objects.delete(item.id);
                    continue;
                }
                if (proof.explored)
                {
                    const snapshot = this.snapshot(item, item.last_seen);
                    snapshot.currently_visible = false;
                    objects.push(snapshot);
                }
            }
        }
        while (this.terrain.size > 65536)
            this.terrain.delete(this.terrain.keys().next().value);
        while (this.objects.size > 20000)
            this.objects.delete(this.objects.keys().next().value);
        objects.sort((a, b) => a.id - b.id);
        return {
            "policy": this.policy, "player": player, "time": raw.time,
            "region": Object.assign({}, region), "columns": raw.columns, "rows": raw.rows,
            "cells": cells, "objects": objects.slice(0, 2000),
            "truncated": raw.truncated || objects.length > 2000 || remembered.length > 2000
        };
    }
};
