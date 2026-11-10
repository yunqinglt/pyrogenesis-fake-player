/* Building/holder defaults. Only native RallyPoint commands; never redirect existing units. */
var MCPRally = {
    execute: function(command, states, mapSize, bridge)
    {
        const clearing = command.action === "clear_rally";
        bridge.validateKeys(command, ["version", "session", "id", "action", "entities"].concat(
            clearing ? [] : ["orders", "queued"]));
        if (!Array.isArray(command.entities) || !command.entities.length || command.entities.length > 200)
            throw new Error("Choose between one and 200 rally point owners.");
        if (!clearing && (!Array.isArray(command.orders) || !command.orders.length || command.orders.length > 100 ||
            command.queued !== undefined && typeof command.queued !== "boolean"))
            throw new Error("Choose between one and 100 rally orders and a boolean queue flag.");
        const player = Engine.GetPlayerID();
        const ownById = new Map(states.map(state => [state.id, state]));
        const selected = [];
        for (const id of command.entities)
        {
            const state = ownById.get(id);
            if (!Number.isInteger(id) || id <= 0 || id >= 2147483648 || selected.includes(id) ||
                !state || state.player !== player || state.foundation || !state.rallyPoint)
                throw new Error("Choose completed owned entities with a rally point capability.");
            if (!clearing && command.queued === true)
            {
                const rally = state.mcpProperties && state.mcpProperties.rally_point;
                if (!rally || rally.truncated !== false || !Array.isArray(rally.points) ||
                    rally.points.length + command.orders.length > 100)
                    throw new Error("The appended rally queue exceeds 100 points or is truncated.");
            }
            selected.push(id);
        }
        const commands = [];
        const positionOK = (x, z) => Number.isFinite(x) && Number.isFinite(z) &&
            x >= 0 && z >= 0 && x <= mapSize && z <= mapSize;
        if (clearing)
            commands.push({"type": "unset-rallypoint", "entities": selected});
        else
            for (const [index, order] of command.orders.entries())
            {
                if (!order || typeof order !== "object" || Array.isArray(order))
                    throw new Error("Invalid rally order.");
                const positional = ["move", "attack_move", "patrol"].includes(order.type);
                if (!positional && !["attack", "capture", "gather", "repair", "garrison", "occupy_turret",
                    "trade", "collect_treasure"].includes(order.type))
                    throw new Error("Unsupported rally order.");
                bridge.validateKeys(order, positional ? ["type", "x", "z"] : ["type", "target"]);
                let x = order.x, z = order.z;
                const data = {};
                if (positional)
                {
                    data.command = order.type === "move" ? "walk" : order.type === "attack_move" ? "attack-walk" : "patrol";
                    if (order.type !== "move")
                        data.targetClasses = {"attack": ["Unit", "Structure"]};
                }
                else
                {
                    if (!Number.isInteger(order.target) || order.target <= 0 || order.target >= 2147483648)
                        throw new Error("Invalid rally target identity.");
                    let target = ownById.get(order.target);
                    if (!target)
                    {
                        if (["repair", "garrison", "occupy_turret", "trade"].includes(order.type))
                            throw new Error("This rally order requires an owned target.");
                        // The simulation entry point authorizes current LOS before reading state.
                        Engine.GuiInterfaceCall("MCPGetEntityProperties", order.target);
                        target = Engine.GuiInterfaceCall("GetEntityState", order.target);
                    }
                    if (!target || target.mirage || target.player !== player && target.visibility !== "visible" || !target.position)
                        throw new Error("The rally target is unavailable in the current player view.");
                    x = target.position.x;
                    z = target.position.z;
                    data.target = order.target;
                    data.command = order.type.replace(/_/g, "-");
                    if (order.type === "attack" || order.type === "capture")
                    {
                        if (target.player === player || (order.type === "capture" ? !target.capturePoints : !(target.hitpoints > 0)))
                            throw new Error("The target cannot receive this rally attack.");
                        data.command = "attack";
                        data.allowCapture = order.type === "capture";
                        data.targetClasses = {"attack": ["Unit", "Structure"]};
                    }
                    else if (order.type === "gather")
                    {
                        const supply = target.resourceSupply;
                        if (target.player !== 0 && target.player !== player || !supply || !supply.type ||
                            !["food", "wood", "stone", "metal"].includes(supply.type.generic) ||
                            typeof supply.type.specific !== "string" || !/^[A-Za-z0-9_-]{1,80}$/.test(supply.type.specific) ||
                            typeof target.template !== "string" || target.template.length > 200)
                            throw new Error("Choose a currently visible Gaia or owned resource supply.");
                        data.resourceType = {"generic": supply.type.generic, "specific": supply.type.specific};
                        data.resourceTemplate = target.template;
                        if (target.speed)
                        {
                            data.command = "gather-near-position";
                            delete data.target;
                        }
                    }
                    else if (order.type === "collect_treasure")
                    {
                        if (target.player !== 0 || !target.treasure)
                            throw new Error("Choose a currently visible Gaia treasure.");
                        if (target.speed)
                        {
                            data.command = "collect-treasure-near-position";
                            delete data.target;
                        }
                    }
                    else if (target.player !== player ||
                        order.type === "repair" && !target.foundation && !target.needsRepair ||
                        order.type === "garrison" && !target.garrisonHolder ||
                        order.type === "occupy_turret" && !target.turretHolder ||
                        order.type === "trade" && !target.market)
                        throw new Error("The owned target does not support this rally order.");
                    if (order.type === "trade")
                    {
                        // Each selected market must be the native source of its own route.
                        if (selected.some(id => id === order.target || !ownById.get(id).market ||
                            !(ownById.get(id).market.land && target.market.land ||
                              ownById.get(id).market.naval && target.market.naval)))
                            throw new Error("Choose compatible owned source and destination markets.");
                    }
                }
                if (!positionOK(x, z))
                    throw new Error("Rally coordinates must be inside the world bounds.");
                const base = {"type": "set-rallypoint", "x": x, "z": z,
                    "queued": index > 0 || command.queued === true};
                if (order.type === "trade")
                    for (const id of selected)
                        commands.push(Object.assign({}, base, {"entities": [id], "data": Object.assign({}, data, {"source": id})}));
                else
                    commands.push(Object.assign({}, base, {"entities": selected, "data": data}));
            }
        // Validate the entire batch before posting its first native command.
        for (const native of commands)
            Engine.PostNetworkCommand(native);
        return {"entity_count": selected.length};
    }
};
