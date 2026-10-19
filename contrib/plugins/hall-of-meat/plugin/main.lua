-- Hall of Meat, as in skate. 3: when the local skater bails, the bones it hurt show through the
-- skater, yellow where a hit bruised one and red where one broke it, with the bail's Meat card in the
-- bottom left corner; both stay a second after the skater gets up, then fade out. A bone breaking
-- flashes the screen's edges red and, in single player, slows the game down a moment.
--
-- Each physics step of the local skater (physics_step) feeds the bail (model.lua). The skeleton is
-- skate.'s own skeleton mesh as the renderer draws the skater (reskate.skater.xray, d:skeleton); the
-- card (card.lua, draw.lua) takes the place of skate.'s bottom left HUD while it shows
-- (reskate.hud.corner). Each map's best Meat is kept (reskate.storage).
local model = require("model")
local card = require("card")
local draw = require("draw")

local on = reskate.storage.get("on")
if type(on) ~= "boolean" then on = true end
local tracker = model.tracker()
local level = ""
local best = nil       -- the current map's best Meat; nil while no map is known
local standing = nil   -- where the last finished bail that showed stands against the map's best
local last = nil       -- the last finished bail's summary
local speed_asked = 1  -- the game speed last asked for
local corner_hidden = false

local function best_key(name) return "best." .. string.lower(name) end
local function set_level(name)
    level = name or ""
    best = level ~= "" and (tonumber(reskate.storage.get(best_key(level))) or 0) or nil
    standing = nil
end

-- A bail is over: it stands against the map's best, which a new best replaces.
local function finished(summary)
    if not summary then return end
    last = summary
    standing = summary.shown and best and model.standing(best, summary.tally.score) or nil
    if standing and standing.new_best then
        best = standing.best
        reskate.storage.set(best_key(level), best)
    end
end

local function set_on(value)
    if value == on then return end
    on = value
    reskate.storage.set("on", on)
    reskate.skater.xray(on)
    reskate.hud.corner(false)
    if not on then
        tracker:reset()
        speed_asked = 1
        reskate.time.slow(1)
    end
end

reskate.on("load", function()
    reskate.skater.xray(on)
    reskate.hud.corner(false) -- ready to hide it the moment a card shows
    local current = reskate.level.current()
    if current then set_level(current.name) end
end)
reskate.on("level_loaded", function(name)
    finished(tracker:lose(reskate.time.now()))
    set_level(name)
end)
reskate.on("level_unloading", function() finished(tracker:lose(reskate.time.now())) end)
-- The skater gone (a respawn, a teleport): a bail ends.
reskate.on("skater_state", function(state)
    if state == "none" then finished(tracker:lose(reskate.time.now())) end
end)

reskate.on("physics_step", function(step)
    if not on then return end
    finished(tracker:step(step.at, step))
end)

reskate.on("tick", function()
    if not on then return end
    local now = reskate.time.now()
    local showing = tracker:view(now).phase ~= "riding"
    -- The card takes the place of skate.'s bottom left HUD while it shows: the bail is what counts.
    if showing ~= corner_hidden then
        corner_hidden = showing
        reskate.hud.corner(showing)
    end
    -- A break slows the game down (never in a multiplayer session: ReSkate refuses it there).
    local speed = tracker:game_speed(now)
    if math.abs(speed - speed_asked) > 0.005 or (speed >= 1 and speed_asked < 1) then
        speed_asked = speed
        reskate.time.slow(speed >= 0.999 and 1 or speed)
    end
end)

reskate.on("draw", function(d)
    if not on then return end
    local now = reskate.time.now()
    local view = tracker:view(now)
    draw.break_pulse(d, view.break_pulse)
    if view.phase == "riding" then
        draw.card(d, {opacity = 0, rows = {}, total = 0, badge = ""}, now)
        return
    end
    draw.skeleton(d, view, now / 1000)
    -- The card's bail against the map's best before it: while it goes on, as it would stand; once
    -- over, as it stood (the best already counts it).
    local against
    if view.phase == "getting_up" then
        against = standing
    elseif best then
        against = model.standing(best, view.tally.score)
    end
    draw.card(d, card.make(view, against), now)
end)

-- ---- the console and the menu ----------------------------------------------------------------

local function describe(summary)
    if not summary then return "no bail yet" end
    local t = summary.tally
    return string.format("%s Meat (%d hits, %d broken, %.1f s, %.1f s airtime, fell %.1f m, %.1f m/s top, %.1f rotations, road rash %.1f m)%s",
        card.grouped(t.score), t.impacts, t.broken, t.seconds, t.airtime, t.fallen, t.top_speed, t.rotations, t.scraped,
        summary.shown and "" or ", hurt nothing so it did not show")
end
local function answer(name)
    return function(args)
        local function say(text) reskate.console.print(name .. ": " .. text) end
        local what = args[1]
        if what == "1" or what == "on" then
            set_on(true)
        elseif what == "0" or what == "off" then
            set_on(false)
        elseif what == "test" then
            local passed, failures = require("tests").run()
            for _, failure in failures do say("FAIL " .. failure) end
            return say(string.format("%d passed, %d failed", passed, #failures))
        elseif what ~= nil then
            return say("say " .. name .. " 0|1, or test")
        end
        say(string.format("%s; this map's best %s; last bail: %s", on and "on" or "off", best and card.grouped(best) or "unknown",
            describe(last)))
    end
end
reskate.console.command("hallofmeat", "Your bails show the bones they hurt and score a Meat card: 0|1, test", answer("hallofmeat"))

reskate.menu.panel("Hall of Meat", function(ui)
    set_on(ui:toggle("on", "Hall of Meat", on))
    ui:label("Your bails show the bones they hurt and score a Meat card. A break slows the game in single player. No Bail stops the bails it scores.")
    ui:label("This map's best: " .. (best and card.grouped(best) or "no map yet"))
    ui:label("Last bail: " .. describe(last))
end)

reskate.log("Hall of Meat " .. (on and "on" or "off") .. ": hallofmeat 0|1")
