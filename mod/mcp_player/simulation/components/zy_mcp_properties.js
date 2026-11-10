/* Read-only, bounded entity properties for the normal player's GUI projection. */
(function()
{
    const RESOURCES = ["food", "wood", "stone", "metal"];
    const POSITION_ORDERS = ["Walk", "WalkAndFight", "Patrol", "WalkToPointRange", "MoveIntoFormation"];

    function text(value, limit = 200)
    {
        return typeof value === "string" ? value.slice(0, limit) : "";
    }

    function amount(value, fallback = 0)
    {
        return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1e12 ? value : fallback;
    }

    function tokens(value)
    {
        const values = Array.isArray(value) ? value :
            typeof value === "string" ? value.split(/\s+/) :
            value && typeof value._string === "string" ? value._string.split(/\s+/) : [];
        return values.filter(item => typeof item === "string" && item.length).slice(0, 64).map(item => text(item, 80));
    }

    function numericMap(value, limit = 32)
    {
        const result = {};
        if (!value || typeof value !== "object" || Array.isArray(value))
            return result;
        for (const key of Object.keys(value).slice(0, limit))
            if (/^[A-Za-z][A-Za-z0-9_.-]{0,79}$/.test(key) &&
                typeof value[key] === "number" && Number.isFinite(value[key]) && value[key] >= 0 && value[key] <= 1e12)
                result[key] = value[key];
        return result;
    }

    function owned(id, player)
    {
        const ownership = Engine.QueryInterface(id, IID_Ownership);
        return ownership && ownership.GetOwner() === player;
    }

    function knownID(id, player, range)
    {
        return Number.isInteger(id) && id > 0 && id < 2147483648 &&
            (range.GetLosVisibility(id, player) === "visible" || owned(id, player));
    }

    function bonuses(value, entity, path)
    {
        if (!value || typeof value !== "object" || Array.isArray(value))
            return [];
        const result = [];
        for (const name of Object.keys(value).slice(0, 64))
        {
            const bonus = value[name];
            if (!bonus || typeof bonus !== "object")
                continue;
            const multiplier = ApplyValueModificationsToEntity(path + "/Bonuses/" + name + "/Multiplier", +bonus.Multiplier, entity);
            if (!Number.isFinite(multiplier) || multiplier < 0 || multiplier > 1e6)
                continue;
            result.push({
                "name": text(name, 80), "classes": tokens(bonus.Classes),
                "civs": tokens(bonus.Civ), "multiplier": multiplier
            });
        }
        return result;
    }

    function statusEffects(value)
    {
        const result = [];
        if (!value || typeof value !== "object" || Array.isArray(value))
            return result;
        for (const name of Object.keys(value).slice(0, 32))
        {
            const effect = value[name];
            if (!effect || typeof effect !== "object")
                continue;
            // AttackHelper already applied entity/technology modifiers to
            // these values. Do not apply them a second time.
            result.push({
                "name": text(name), "duration_ms": amount(effect.Duration),
                "interval_ms": amount(effect.Interval), "stackability": text(effect.Stackability, 80),
                "damage": numericMap(effect.Damage), "capture": amount(effect.Capture)
            });
        }
        return result;
    }

    function statusResistance(value)
    {
        const result = {};
        if (!value || typeof value !== "object" || Array.isArray(value))
            return result;
        for (const name of Object.keys(value).slice(0, 32))
        {
            const resistance = value[name];
            if (!resistance || typeof resistance !== "object" ||
                !/^[A-Za-z][A-Za-z0-9_.-]{0,79}$/.test(name))
                continue;
            result[name] = {
                "duration_multiplier": amount(resistance.duration, 1),
                "block_chance": Math.min(amount(resistance.blockChance), 1)
            };
        }
        return result;
    }

    function buildingRestrictions(state)
    {
        const templateManager = Engine.QueryInterface(SYSTEM_ENTITY, IID_TemplateManager);
        const template = templateManager && templateManager.GetTemplate(state.template);
        const restrictions = template && template.BuildRestrictions;
        if (!restrictions || typeof restrictions !== "object")
            return null;
        const territory = tokens(restrictions.Territory);
        const placement = text(restrictions.PlacementType, 40);
        const category = text(restrictions.Category, 80);
        const distance = restrictions.Distance && typeof restrictions.Distance === "object" ? {
            "from_class": text(restrictions.Distance.FromClass, 80),
            "min": restrictions.Distance.MinDistance === undefined ? null : amount(
                ApplyValueModificationsToEntity("BuildRestrictions/Distance/MinDistance", +restrictions.Distance.MinDistance, state.id), null),
            "max": restrictions.Distance.MaxDistance === undefined ? null : amount(
                ApplyValueModificationsToEntity("BuildRestrictions/Distance/MaxDistance", +restrictions.Distance.MaxDistance, state.id), null)
        } : null;
        return { "placement": placement || null, "territory": territory,
            "category": category || null, "distance": distance };
    }

    function identityData(state, civ, classes, roles)
    {
        // The caller has already checked ownership/LOS. Read only public
        // template labels; the simulation has no GUI localization context.
        const manager = Engine.QueryInterface(SYSTEM_ENTITY, IID_TemplateManager);
        const template = manager && manager.GetTemplate(state.template);
        const identity = template && template.Identity || {};
        return {
            "civ": civ, "classes": classes,
            "rank": text(state.identity && state.identity.rank, 80), "special_roles": roles,
            "name": { "generic": text(identity.GenericName, 300), "specific": text(identity.SpecificName, 300) },
            "tooltip": text(identity.Tooltip, 4000), "translation": "source-language"
        };
    }

    function researchData(state)
    {
        if (!state.researcher || typeof state.researcher !== "object")
            return null;
        const technologies = [], pairs = [];
        for (const item of (Array.isArray(state.researcher.technologies) ? state.researcher.technologies : []).slice(0, 256))
            if (typeof item === "string")
                technologies.push(text(item, 200));
            else if (item && item.pair === true && typeof item.top === "string" && typeof item.bottom === "string")
                pairs.push({ "top": text(item.top, 200), "bottom": text(item.bottom, 200) });
        return { "technologies": technologies, "pairs": pairs,
            "tech_cost_multiplier": numericMap(state.researcher.techCostMultiplier, 5) };
    }

    function buildingWork(value, privateView)
    {
        if (!privateView || !value || typeof value !== "object")
            return null;
        const time = value.buildTime && typeof value.buildTime === "object" ? value.buildTime : {};
        return { "builders": amount(value.numBuilders),
            "time_remaining_seconds": amount(time.timeRemaining),
            "time_remaining_with_one_more_worker_seconds": amount(time.timeRemainingNew) };
    }

    function buildingData(state, privateView)
    {
        const classes = tokens(state.identity && state.identity.classes);
        const structure = classes.includes("Structure");
        if (!structure)
            return null;
        const researcher = researchData(state);
        const trainer = state.trainer && typeof state.trainer === "object" ? {
            "entities": Array.isArray(state.trainer.entities) ?
                state.trainer.entities.slice(0, 256).filter(value => typeof value === "string").map(value => text(value, 200)) : []
        } : null;
        const production = privateView && state.production && typeof state.production === "object" ? {
            "queue": Array.isArray(state.production.queue) ? state.production.queue.slice(0, 16).map(item => {
                if (!item || typeof item !== "object") return null;
                // ProductionQueue's native basic-info shape may expose the
                // generic templateName for technologies; Trainer batches use
                // unitTemplate and Researcher usually supplies technologyTemplate.
                const unitTemplate = typeof item.unitTemplate === "string" ? item.unitTemplate : "";
                const technologyTemplate = typeof item.technologyTemplate === "string" ? item.technologyTemplate :
                    typeof item.unitTemplate === "string" ? "" : item.templateName;
                return { "id": Number.isInteger(item.id) ? item.id : null,
                    "unit_template": text(unitTemplate, 200), "technology_template": text(technologyTemplate, 200),
                    "count": amount(item.count), "progress": amount(item.progress), "time_remaining": amount(item.timeRemaining),
                    "paused": item.paused === true };
            }).filter(item => item !== null) : [],
            "autoqueue": state.production.autoqueue === true
        } : null;
        const buildingAI = state.buildingAI && typeof state.buildingAI === "object" ? {
            "default_arrows": amount(state.buildingAI.defaultArrowCount),
            "max_arrows": amount(state.buildingAI.maxArrowCount, null),
            "garrison_arrow_multiplier": amount(state.buildingAI.garrisonArrowMultiplier),
            "garrison_arrow_classes": tokens(state.buildingAI.garrisonArrowClasses),
            "arrows": amount(state.buildingAI.arrowCount)
        } : null;
        const resource = state.resourceSupply && typeof state.resourceSupply === "object" ? {
            "infinite": state.resourceSupply.isInfinite === true,
            "type": state.resourceSupply.type && typeof state.resourceSupply.type.generic === "string" ?
                text(state.resourceSupply.type.generic + (state.resourceSupply.type.specific ? "." + state.resourceSupply.type.specific : ""), 80) : null,
            "amount": state.resourceSupply.isInfinite === true ? null : amount(state.resourceSupply.amount, null),
            "max": state.resourceSupply.isInfinite === true ? null : amount(state.resourceSupply.max, null),
            "max_gatherers": amount(state.resourceSupply.maxGatherers, null),
            "gatherers": privateView ? amount(state.resourceSupply.numGatherers, null) : null
        } : null;
        const loyalty = Array.isArray(state.capturePoints) ? {
            "current": state.capturePoints.slice(0, 9).map(value => amount(value)),
            "max": amount(state.maxCapturePoints, null)
        } : null;
        return { "is_building": true, "research": researcher, "training": trainer,
            "production": production, "building_ai": buildingAI, "resource": resource,
            "loyalty": loyalty, "placement": buildingRestrictions(state),
            "owner": Number.isInteger(state.player) && state.player >= -1 && state.player <= 8 ? state.player : null,
            "hitpoints": amount(state.hitpoints, null), "max_hitpoints": amount(state.maxHitpoints, null),
            "foundation": buildingWork(state.foundation, privateView),
            "repair": buildingWork(state.repairable, privateView) };
    }

    function attacks(state, component)
    {
        const result = {};
        for (const type of Object.keys(state.attack || {}).slice(0, 8))
        {
            if (!["Melee", "Ranged", "Capture", "Slaughter"].includes(type))
                continue;
            const attack = state.attack[type];
            const splash = attack.splash;
            result[type] = {
                "damage": numericMap(attack.Damage), "capture": amount(attack.Capture),
                "status_effects": statusEffects(attack.ApplyStatus),
                "bonuses": bonuses(attack.Bonuses, state.id, "Attack/" + type),
                "min_range": amount(attack.minRange), "max_range": amount(attack.maxRange),
                "prepare_ms": amount(attack.prepareTime), "repeat_ms": amount(attack.repeatTime),
                "restricted_classes": component ? tokens(component.GetRestrictedClasses(type)) : [],
                "preferred_classes": component ? tokens(component.GetPreferredClasses(type)) : [],
                "splash": splash ? {
                    "shape": text(splash.shape, 80), "range": amount(splash.radius),
                    "friendly_fire": splash.friendlyFire === true,
                    "damage": numericMap(splash.Damage || splash.attackData && splash.attackData.Damage),
                    "capture": amount(splash.Capture === undefined && splash.attackData ? splash.attackData.Capture : splash.Capture),
                    "status_effects": statusEffects(splash.ApplyStatus || splash.attackData && splash.attackData.ApplyStatus),
                    "bonuses": bonuses(splash.Bonuses || splash.attackData && splash.attackData.Bonuses,
                        state.id, "Attack/" + type + "/Splash")
                } : null
            };
        }
        return result;
    }

    function target(state, player, range)
    {
        const order = state.unitAI && Array.isArray(state.unitAI.orders) && state.unitAI.orders[0];
        if (!order || typeof order.type !== "string")
            return null;
        const data = order.data || {};
        let position = null;
        if (POSITION_ORDERS.includes(order.type) &&
            typeof data.x === "number" && Number.isFinite(data.x) && data.x >= 0 && data.x <= 100000 &&
            typeof data.z === "number" && Number.isFinite(data.z) && data.z >= 0 && data.z <= 100000)
            position = { "x": data.x, "z": data.z };
        return {
            "order": text(order.type, 80),
            "entity_id": knownID(data.target, player, range) ? data.target : null,
            "position": position
        };
    }

    function rallyPoint(state, player, range)
    {
        const rally = Engine.QueryInterface(state.id, IID_RallyPoint);
        if (!rally)
            return null;
        // Native GetPositions follows targets only while visible to the owner;
        // otherwise it retains the player's last known coordinates.
        const positions = rally.GetPositions();
        const data = rally.GetData();
        const names = {"walk": "move", "attack-walk": "attack_move", "patrol": "patrol",
            "attack": "attack", "gather": "gather", "gather-near-position": "gather_near_position",
            "repair": "repair", "build": "repair", "garrison": "garrison", "occupy-turret": "occupy_turret",
            "trade": "trade", "collect-treasure": "collect_treasure",
            "collect-treasure-near-position": "collect_treasure_near_position"};
        return {"points": positions.slice(0, 100).map((position, index) => {
            const item = data[index] || {};
            const command = item.command || "walk";
            return {"order": command === "attack" && item.allowCapture === true ? "capture" :
                    Object.prototype.hasOwnProperty.call(names, command) ? names[command] : "unsupported",
                "entity_id": knownID(item.target, player, range) ? item.target : null,
                "position": position && Number.isFinite(position.x) && Number.isFinite(position.z) ?
                    {"x": position.x, "z": position.z} : null};
        }), "truncated": positions.length > 100};
    }

    GuiInterface.prototype.MCPProjectEntityProperties = function(player, state, publicOnly = false)
    {
        if (!Number.isInteger(player) || player <= 0 || player > 8 ||
            !state || !Number.isInteger(state.id) || state.id <= 0 || state.mirage)
            throw new Error("Unit properties require a normal player entity.");
        const range = Engine.QueryInterface(SYSTEM_ENTITY, IID_RangeManager);
        const isOwn = state.player === player && owned(state.id, player);
        if (!range || !isOwn && range.GetLosVisibility(state.id, player) !== "visible")
            throw new Error("Unit properties are unavailable outside normal player view.");
        const privateView = isOwn && !publicOnly;
        const identity = Engine.QueryInterface(state.id, IID_Identity);
        const classes = tokens(state.identity && state.identity.classes);
        const civ = identity ? text(identity.GetCiv(), 80) : "";
        const roles = [];
        if (civ === "brit" && (classes.includes("Dog") || /(?:^|\/)war_dog$/.test(state.template)))
            roles.push("war_dog");
        if (civ === "han" && classes.includes("Minister"))
            roles.push("minister");
        if (civ === "maur" && classes.includes("Elephant") && state.builder && state.resourceDropsite)
            roles.push("worker_elephant");
        for (const [unitClass, role] of [["Champion", "champion"], ["Mercenary", "mercenary"], ["Hero", "hero"]])
            if (classes.includes(unitClass))
                roles.push(role);
        const gatherer = Engine.QueryInterface(state.id, IID_ResourceGatherer);
        const attackComponent = Engine.QueryInterface(state.id, IID_Attack);
        const pack = Engine.QueryInterface(state.id, IID_Pack);
        const unitAI = privateView ? Engine.QueryInterface(state.id, IID_UnitAI) : null;
        const attackData = attacks(state, attackComponent);
        const rates = numericMap(state.resourceGatherRates, 64);
        const heal = state.heal;
        const auraList = [];
        for (const id of Object.keys(state.auras || {}).slice(0, 64))
        {
            const aura = state.auras[id];
            if (aura && typeof aura === "object")
                auraList.push({ "id": text(id), "name": text(aura.name && aura.name.generic, 200),
                    "description": text(aura.description, 2000), "range": amount(aura.radius, null) });
        }
        const capacities = {};
        if (gatherer)
            for (const resource of RESOURCES)
                capacities[resource] = amount(gatherer.GetCapacity(resource));
        const resources = [];
        if (privateView)
            for (const item of (Array.isArray(state.resourceCarrying) ? state.resourceCarrying : []).slice(0, 4))
                if (item && RESOURCES.includes(item.type))
                    resources.push({ "type": item.type, "amount": amount(item.amount), "capacity": amount(item.max) });
        const goods = privateView && state.trader && state.trader.goods;
        const gains = goods && goods.amount && typeof goods.amount === "object" ? goods.amount : null;
        const builder = privateView && state.builder ? Engine.QueryInterface(state.id, IID_Builder) : null;
        const holder = state.garrisonHolder;
        let garrison = null;
        if (holder)
        {
            garrison = { "capacity": amount(holder.capacity), "allowed_classes": tokens(holder.allowedClasses),
                "occupied_slots": privateView ? amount(holder.occupiedSlots) : null,
                "occupants": privateView ? (Array.isArray(holder.entities) ? holder.entities : [])
                    .slice(0, 256).filter(id => Number.isInteger(id) && id > 0 && id < 2147483648 && owned(id, player)) : null };
        }
        let packing = null;
        if (pack)
        {
            const packed = pack.IsPacked() === true;
            const changing = pack.IsPacking() === true;
            packing = { "packed": packed, "changing": changing, "progress": Math.min(amount(pack.GetProgress()), 1),
                "state": changing ? packed ? "unpacking" : "packing" : packed ? "packed" : "unpacked" };
        }
        const canAttack = Object.keys(attackData).some(type => type !== "Slaughter" && type !== "Capture");
        const canCapture = Object.keys(attackData).some(type => attackData[type].capture > 0);
        return {
            "version": 1,
            "identity": identityData(state, civ, classes, roles),
            "capabilities": {
                "move": !!(state.speed && state.unitAI), "build": state.builder === true, "repair": state.builder === true,
                "gather": !!gatherer, "farm": !!gatherer && rates["food.grain"] > 0,
                "trade": !!state.trader, "heal": !!heal, "attack": canAttack, "capture": canCapture,
                "garrison": !!state.garrisonable, "occupy_turret": !!state.turretable,
                "pack": !!pack, "guard": !!(state.unitAI && state.unitAI.canGuard),
                "patrol": !!(state.unitAI && state.unitAI.canPatrol), "collect_treasure": state.treasureCollector === true,
                "resource_dropsite": !!state.resourceDropsite
            },
            "gathering": gatherer ? { "rates": rates, "capacities": capacities } : null,
            "construction": builder ? { "templates": builder.GetEntitiesList().slice(0, 256)
                .filter(value => typeof value === "string").map(value => text(value)), "rate": amount(builder.GetRate()) } : null,
            "storage": state.resourceDropsite ? { "accepts": tokens(state.resourceDropsite.types).filter(type => RESOURCES.includes(type)) } : null,
            "inventory": privateView ? { "resources": resources,
                "trade_goods": gains ? { "type": RESOURCES.includes(goods.type) ? goods.type : null,
                    "trader_gain": amount(gains.traderGain), "market1_gain": amount(gains.market1Gain),
                    "market2_gain": amount(gains.market2Gain) } : null } : null,
            "current_target": privateView ? target(state, player, range) : null,
            "rally_point": privateView ? rallyPoint(state, player, range) : null,
            "activity": privateView && state.unitAI ? { "state": text(state.unitAI.state, 120),
                "idle": state.unitAI.isIdle === true, "stance": unitAI ? text(unitAI.GetStanceName(), 80) : null } : null,
            "combat": { "can_attack": canAttack, "can_capture": canCapture, "attacks": attackData,
                "resistance": state.resistance ? { "damage": numericMap(state.resistance.Damage), "capture": amount(state.resistance.Capture),
                    "status_effects": statusResistance(state.resistance.ApplyStatus) } : null,
                "healing": heal ? { "health": amount(heal.health), "range": amount(heal.range), "interval_ms": amount(heal.interval),
                    "healable_classes": tokens(heal.healableClasses), "unhealable_classes": tokens(heal.unhealableClasses) } : null,
                "auras": auraList },
            "garrison": garrison,
            "garrisoned_in": privateView && state.garrisonable ? {
                "holder_id": knownID(state.garrisonable.holder, player, range) ? state.garrisonable.holder : null,
                "slot_size": amount(state.garrisonable.size)
            } : null,
            "packing": packing,
            "building": buildingData(state, privateView)
        };
    };

    GuiInterface.prototype.MCPGetEntityProperties = function(player, entity)
    {
        if (!Number.isInteger(player) || player <= 0 || player > 8 ||
            !Number.isInteger(entity) || entity <= 0 || entity >= 2147483648)
            throw new Error("Invalid player entity for properties.");
        const range = Engine.QueryInterface(SYSTEM_ENTITY, IID_RangeManager);
        if (!range || !owned(entity, player) && range.GetLosVisibility(entity, player) !== "visible")
            throw new Error("Unit properties are unavailable outside normal player view.");
        return this.MCPProjectEntityProperties(player, this.GetEntityState(player, entity));
    };
    GuiInterface.prototype.exposedFunctions.MCPGetEntityProperties = 1;

    // ScriptCall supplies the trusted calling player. No requested-player
    // argument is accepted, so a GUI payload cannot select a different tracker.
    GuiInterface.prototype.MCPGetOwnTerritoryPercent = function(player)
    {
        if (!Number.isInteger(player) || player <= 0 || player > 8)
            throw new Error("Territory statistics require a normal player.");
        const ownPlayer = QueryPlayerIDInterface(player, IID_Player);
        if (!ownPlayer || ownPlayer.GetPlayerID() !== player)
            throw new Error("Territory statistics are unavailable.");
        const tracker = QueryPlayerIDInterface(player, IID_StatisticsTracker);
        const value = tracker && tracker.GetPercentMapControlled();
        return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null;
    };
    GuiInterface.prototype.exposedFunctions.MCPGetOwnTerritoryPercent = 1;
})();
