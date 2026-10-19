-- Throwdown modes test: two modes on the game's Throwdowns page, through the whole native flow
-- (card, Spot Battle's setup page with the mode's rows, the start flag, the waiting card). It runs
-- no match: it says in chat what it hears, and takes the flag down when the host presses Start.
-- Their logos are textures from SmokeyDawg58's ReSkate_Throwdown assets, by path; without that mod
-- they show Spot Battle's art. Both use Spot Battle's own flag (a mode's `flag` names its mod's own,
-- which only works with that mod installed).
local function say(text)
    reskate.chat.notice(text)
    reskate.log(text)
end

local function register()
    assert(reskate.throwdowns.mode{
        id = "oneup", name = "1-UP", art = "UI/ReSkate/OneUp/img_OneUp_Blue_1024",
        description = "Beat the previous turn total. Three penalties and you're out. Last skater wins!",
        players = {min = 1, max = 6, default = 2},
        timer = {caption = "Turn", min = 10, max = 120, step = 10, default = 20},
    })
    assert(reskate.throwdowns.mode{
        id = "race", name = "Deathrace", art = "UI/ReSkate/Throwdowns/img_race_placeholder",
        description = "Race through the gates. First to the finish wins.",
        players = {min = 1, max = 8, default = 4},
        timer = {caption = "Finish deadline", min = 30, max = 600, step = 30, default = 180},
        rows = {
            {id = "laps", kind = "choice", caption = "Laps", options = {"1", "2", "3"}, default = 1},
            {id = "boost", caption = "Boost", min = 0, max = 5, default = 2},
            {id = "route", kind = "button", caption = "Route", text = "Place gates"},
        },
    })
end
register()

local last = "nothing yet"
reskate.on("throwdown_mode", function(e)
    local settings = {}
    for row, value in pairs(e.settings) do settings[#settings + 1] = row .. "=" .. tostring(value) end
    table.sort(settings)
    local line = e.mode .. ": " .. e.action
    if e.row then line = line .. " " .. e.row .. "=" .. tostring(e.value) end
    if e.position then
        line = line .. string.format(" at %.1f %.1f %.1f facing %.0f", e.position[1], e.position[2], e.position[3], e.heading)
    end
    line = line .. " (" .. table.concat(settings, ", ") .. ")"
    last = line
    say(line)
    if e.action == "start" then
        -- A real mode would start its match here.
        reskate.throwdowns.release_flag()
        say(e.mode .. ": the match would start now")
    end
end)

reskate.console.command("tdmodes", "Throwdown modes test: what it heard last (tdmodes off takes the modes away, tdmodes on puts them back)", function(args)
    if args[1] == "off" then
        reskate.throwdowns.remove_mode("oneup")
        reskate.throwdowns.remove_mode("race")
        reskate.console.print("tdmodes: removed")
    elseif args[1] == "on" then
        register()
        reskate.console.print("tdmodes: registered")
    else
        reskate.console.print("tdmodes: " .. last)
    end
end)
