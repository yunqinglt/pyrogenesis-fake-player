/* Public option descriptions from the running game's native templates.
 * Only this player's currently advertised options can be requested. This file
 * never enumerates entity IDs, reads another player's state or posts commands.
 */
var MCPMetadata = (function() {
    const LIMIT = 16;
    const RULE_KEYS = new Set(["all", "any", "not", "tech", "techs", "civ", "entity", "entities",
        "class", "number", "check", "count", "variants", "Techs", "Entities", "All", "Any",
        "Not", "Class", "Count", "Variants", "Number", "_string"]);
    const identifier = value => typeof value === "string" && value.length > 0 && value.length <= 200 &&
        /^[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*$/.test(value);
    const text = (value, limit = 4000) => typeof value === "string" ? value.slice(0, limit) : null;
    const numeric = value => typeof value === "number" && Number.isFinite(value) &&
        Math.abs(value) <= 1e12 ? value : null;
    const tokens = value => (Array.isArray(value) ? value : typeof value === "string" ? value.split(/\s+/) : [])
        .filter(item => typeof item === "string").slice(0, 64).map(item => item.slice(0, 200));
    function cost(value, multiplier) {
        const result = {};
        for (const key of ["food", "wood", "stone", "metal", "population", "time"])
            if (value && numeric(value[key]) !== null && value[key] >= 0) {
                const factor = multiplier && numeric(multiplier[key]) !== null ? multiplier[key] : 1;
                const current = value[key] * factor;
                if (numeric(current) === null || current < 0)
                    throw new Error("Unsupported native option cost.");
                result[key] = current;
            }
        return result;
    }
    function rules(value, status, depth = 0) {
        if (++status.nodes > 256 || depth > 8)
            throw new Error("Native requirements exceed supported bounds.");
        if (value === null || value === undefined)
            return null;
        if (typeof value === "boolean")
            return value;
        if (typeof value === "number")
            return numeric(value);
        if (typeof value === "string")
            return text(value, 1000);
        if (Array.isArray(value)) {
            if (value.length > 64)
                throw new Error("Native requirement list exceeds supported bounds.");
            return value.map(item => rules(item, status, depth + 1));
        }
        if (typeof value !== "object" || Object.keys(value).length > 32)
            throw new Error("Unsupported native requirement structure.");
        const result = {};
        for (const key of Object.keys(value))
            if (RULE_KEYS.has(key))
                result[key] = rules(value[key], status, depth + 1);
            else
                status.complete = false;
        return result;
    }
    function modifications(value, status) {
        if (!Array.isArray(value))
            return [];
        if (value.length > 64)
            throw new Error("Native effect list exceeds supported bounds.");
        return value.map(item => {
            if (!item || typeof item.value !== "string")
                throw new Error("Unsupported native technology effect.");
            const result = { "value": text(item.value, 300), "affects": tokens(item.affects) };
            if (Object.keys(item).some(key => ["value", "affects", "add", "multiply", "replace"].indexOf(key) === -1))
                status.complete = false;
            for (const key of ["add", "multiply"])
                if (key in item) {
                    if (numeric(item[key]) === null)
                        throw new Error("Unsupported native numeric effect.");
                    result[key] = item[key];
                }
            if ("replace" in item) {
                if (typeof item.replace === "string")
                    result.replace = text(item.replace, 1000);
                else if (typeof item.replace === "boolean" || numeric(item.replace) !== null)
                    result.replace = item.replace;
                else
                    throw new Error("Unsupported native replacement effect.");
            }
            return result;
        });
    }
    function stats(data) {
        return {
            "max_hitpoints": numeric(data.health),
            "population_bonus": numeric(data.population && data.population.bonus),
            "classes": tokens(data.visibleIdentityClasses),
            "speed": data.speed ? { "walk": numeric(data.speed.walk), "run": numeric(data.speed.run),
                "acceleration": numeric(data.speed.acceleration) } : null,
            "garrison": data.garrisonHolder ? { "capacity": numeric(data.garrisonHolder.capacity),
                "heal_rate": numeric(data.garrisonHolder.buffHeal) } : null,
            "packing": data.pack ? { "state": data.pack.state,
                "time_ms": numeric(data.pack.time) } : null
        };
    }
    function advertised(state, kind) {
        const result = [];
        const pairs = {};
        const add = item => {
            if (!identifier(item))
                throw new Error("Unsupported advertised option identifier.");
            if (result.indexOf(item) === -1)
                result.push(item);
        };
        if (kind === "self") {
            // Only a known native filter is normalized; never accept an
            // arbitrary caller-provided template or embedded filter language.
            const template = typeof state.template === "string" && state.template.indexOf("foundation|") === 0 ?
                state.template.slice("foundation|".length) : state.template;
            add(template);
        }
        else if (kind === "research") {
            const research = state.researcher && state.researcher.technologies;
            for (const item of Array.isArray(research) ? research : [])
                if (typeof item === "string")
                    add(item);
                else if (item && item.pair === true) {
                    add(item.top);
                    add(item.bottom);
                    pairs[item.top] = { "position": "top", "other": item.bottom };
                    pairs[item.bottom] = { "position": "bottom", "other": item.top };
                }
        }
        else if (kind === "training") {
            const training = state.trainer && state.trainer.entities;
            for (const item of Array.isArray(training) ? training : [])
                add(item);
        }
        else {
            const construction = state.mcpProperties && state.mcpProperties.construction;
            for (const item of construction && Array.isArray(construction.templates) ? construction.templates : [])
                add(item);
        }
        if (result.length > 1024)
            throw new Error("Advertised options exceed supported bounds.");
        return { "ids": result, "pairs": pairs };
    }
    function describe(player, states, request) {
        if (!Number.isInteger(player) || player <= 0 || !Number.isInteger(request.entity_id) ||
            request.entity_id <= 0 || request.entity_id >= 2147483648 ||
            ["research", "training", "construction", "self"].indexOf(request.kind) === -1)
            throw new Error("Invalid native option request.");
        // Check ownership before any native template or requirements query.
        const state = states.find(item => item.id === request.entity_id && item.player === player);
        if (!state)
            throw new Error("Describe only a currently owned entity.");
        const available = advertised(state, request.kind);
        let ids = request.ids;
        if (ids !== null && ids !== undefined && (!Array.isArray(ids) || !ids.length ||
            ids.length > LIMIT || new Set(ids).size !== ids.length ||
            ids.some(item => !identifier(item) || available.ids.indexOf(item) === -1)))
            throw new Error("Describe only distinct currently advertised options.");
        ids = ids === null || ids === undefined ? available.ids.slice(0, LIMIT) : ids;
        let engineVersion = null;
        if (typeof Engine.GetEngineInfo === "function")
            engineVersion = text(Engine.GetEngineInfo().engine_version, 100);
        const result = { "entity_id": state.id, "player": player, "kind": request.kind,
            "source": "running-game-native-templates", "engine_version": engineVersion,
            "locale": typeof Engine.GetCurrentLocale === "function" ? text(Engine.GetCurrentLocale(), 100) : null,
            "translation": "game-locale-or-source-fallback", "total_available": available.ids.length,
            "truncated": ids.length < available.ids.length, "options": [] };
        for (const id of ids) {
            const technology = request.kind === "research";
            let data, native = null, pair = null;
            if (technology) {
                const civ = typeof g_Players !== "undefined" && g_Players[player] && g_Players[player].civ;
                if (!civ || typeof GetTechnologyData !== "function" || typeof TechnologyTemplates === "undefined")
                    throw new Error("Native technology descriptions are unavailable.");
                native = TechnologyTemplates.Get(id);
                data = GetTechnologyData(id, civ);
                if (available.pairs[id]) {
                    const group = native && identifier(native.pair) ? native.pair : null;
                    const pairData = group ? GetTechnologyData(group, civ) : null;
                    const pairStatus = { "nodes": 0, "complete": !!pairData };
                    pair = Object.assign({ "group": group,
                        "requirements": rules(pairData && pairData.reqs, pairStatus) }, available.pairs[id]);
                    pair.requirements_complete = pairStatus.complete;
                }
            }
            else {
                // No caller-selectable player: this is the active player's
                // native, inheritance-resolved and technology-modified view.
                data = Engine.GuiInterfaceCall("GetTemplateData", { "templateName": id });
                if (data && typeof translateObjectKeys === "function")
                    translateObjectKeys(data, ["specific", "generic", "tooltip"]);
            }
            if (!data || typeof data !== "object")
                throw new Error("Native option template is unavailable.");
            const requirementStatus = { "nodes": 0, "complete": true };
            const requirements = rules(technology ? data.reqs : data.requirements, requirementStatus);
            const modificationStatus = { "complete": true };
            const nativeCost = cost(data.cost);
            const multiplier = technology && state.researcher && state.researcher.techCostMultiplier;
            const adjustedCost = cost(data.cost, multiplier);
            const met = technology ? Engine.GuiInterfaceCall("CheckTechnologyRequirements", { "tech": id }) :
                Engine.GuiInterfaceCall("AreRequirementsMet", { "requirements": data.requirements || null });
            const templateStats = stats(data);
            if (request.kind === "self" && numeric(state.maxHitpoints) !== null)
                templateStats.max_hitpoints = state.maxHitpoints;
            result.options.push({
                "id": id, "name": { "generic": text(data.name && data.name.generic, 300),
                    "specific": text(data.name && data.name.specific, 300) },
                "description": text(data.description), "tooltip": text(data.tooltip),
                "requirements_tooltip": text(data.requirementsTooltip),
                "cost": adjustedCost, "base_cost": nativeCost,
                "cost_scope": technology ? "producer-adjusted-research" : "player-modified-template",
                "cost_notes": technology ? "time is research seconds; base_cost is before producer multipliers" :
                    "Player-modified template reference; time is single-template seconds. Producer, batch, queue and builder effects are not included; this is not a quoted completion time.",
                "requirements": requirements, "requirements_complete": requirementStatus.complete &&
                    (!pair || pair.requirements_complete),
                "requirements_met": typeof met === "boolean" ? met : null,
                "affects": tokens(data.affects), "modifications": modifications(data.modifications, modificationStatus),
                "modifications_complete": modificationStatus.complete,
                "pair": pair, "supersedes": text(native && native.supersedes, 200),
                "replaces": tokens(data.replaces), "stats": templateStats
            });
        }
        return result;
    }
    return { "describe": describe };
})();
