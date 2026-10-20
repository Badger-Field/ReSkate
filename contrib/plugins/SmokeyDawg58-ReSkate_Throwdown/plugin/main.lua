-- SmokeyDawg58's ReSkate Throwdowns as a plugin mod: his modes on the game's Throwdowns page, with
-- the logos, flags, gates and HUD art this mod ships. A flag that is down is shown to the other
-- players in the session who have this mod, who join it with the game's own Join. The modes'
-- gameplay comes over from his fork one at a time as ReSkate gains what each needs; until a
-- mode's has, its Start says so.
local modes = require("modes")
local ported = {} -- mode id -> the module that runs it, once it is ported

for _, mode in ipairs(modes) do
    local ok, why = reskate.throwdowns.mode(mode)
    if not ok then reskate.log("could not add " .. mode.name .. ": " .. tostring(why)) end
end

local names = {}
for _, mode in ipairs(modes) do names[mode.id] = mode.name end

local function player_name(id)
    if not id then return "someone" end
    for _, player in ipairs(reskate.session.players()) do
        if tostring(player.id) == id then return player.name end
    end
    return "a player"
end

reskate.on("throwdown_mode", function(e)
    local runner = ported[e.mode]
    if runner then return runner.event(e) end
    local mode = names[e.mode] or e.mode
    if e.action == "joined" then
        reskate.chat.notice(e.host and (player_name(e.player) .. " joined your " .. mode .. ".")
                                   or ("You joined " .. player_name(e.leader) .. "'s " .. mode .. "."))
    elseif e.action == "left" and e.host then
        reskate.chat.notice(player_name(e.player) .. " left your " .. mode .. ".")
    elseif e.action == "closed" then
        reskate.chat.notice(player_name(e.leader) .. " took their " .. mode .. " flag down.")
    elseif e.action == "start" then
        if e.host then reskate.throwdowns.release_flag() end
        reskate.chat.notice(mode .. " is not playable in this version yet (" .. #(e.players or {}) .. " player(s) were ready).")
    end
end)
