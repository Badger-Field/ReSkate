-- SmokeyDawg58's throwdown modes, as his ReSkate fork set them up (reskate-custom-mods,
-- Extension/Modes/mode_rules.cpp and the native setup page in one_up_menu.cpp). Each has its logo and
-- its flag in this mod's assets; the setup page shows its players, timer and rows.
local logos = "UI/ReSkate/Throwdowns/"
local function area(text) return {id = "area", kind = "button", caption = "Area", text = text or "Set optional area"} end
local function duration(caption, default)
    return {caption = caption or "Duration", min = 30, max = 3600, step = 10, default = default or 300}
end
local players = {min = 1, max = 6, default = 4}

return {
    {
        id = "skate", name = "R.E.S.K.A.T.E.", flag = "skate", replaces = "skate",
        art = logos .. "img_skate_Reskate_1024",
        description = "Set a trick, everyone copies it or takes a letter. Spell R.E.S.K.A.T.E. and you're out; last one standing wins.",
        players = players,
        timer = {caption = "Turn Timer", min = 10, max = 120, step = 5, default = 30},
        rows = {
            {id = "letters", caption = "Letters", min = 1, max = 7, default = 7},
            {id = "tricks", kind = "choice", caption = "Tricks", options = {"All tricks", "Flips only", "Grinds only", "Grabs only"}},
            {id = "zone", kind = "choice", caption = "Trick Zone", options = {"Anywhere", "Small", "Medium", "Large", "Huge", "Custom"}},
        },
    },
    {
        id = "oneup", name = "1-UP", flag = "oneup", art = "UI/ReSkate/OneUp/img_OneUp_Blue_1024",
        description = "Beat the previous turn total. Three penalties and you're out. Last skater wins!",
        players = players,
        timer = {caption = "Turn", min = 10, max = 120, step = 10, default = 20},
    },
    {
        id = "race", name = "Deathrace", flag = "race", art = logos .. "img_race_Placeholder",
        description = "Cross required gates in order; gray guides are optional. Reach the red finish. Countdown only in the final 30 seconds.",
        players = players,
        timer = {caption = "Finish deadline", min = 30, max = 3600, step = 30, default = 180},
    },
    {
        id = "jam", name = "Spot Jam", flag = "jam", art = logos .. "img_jam_Placeholder",
        description = "Land lines inside the area. Every line adds to your score; highest total wins.",
        players = players, timer = duration(), rows = {area()},
    },
    {
        id = "meat", name = "Hall of Meat", flag = "meat", art = logos .. "img_meat_Placeholder",
        description = "Bail as hard as you can. Speed, drops and tumbling score; most meat wins.",
        players = players, timer = duration(), rows = {area()},
    },
    {
        id = "domination", name = "Domination", flag = "domination", art = logos .. "img_domination_Placeholder",
        description = "Take spots with your best line there. Every second you hold one scores.",
        players = players, timer = duration(),
        rows = {{id = "spots", kind = "button", caption = "Capture Spots", text = "Place spots"}},
    },
    {
        id = "graffiti", name = "Graffiti", flag = "graffiti", art = logos .. "img_graffiti_Placeholder",
        description = "Grind it, manual it, gap it: what you skate takes your colour. A bigger line steals it. Most tags wins.",
        players = players, timer = duration(), rows = {area()},
    },
    {
        id = "tag", name = "Skate Tag", flag = "tag", art = logos .. "img_tag_Placeholder",
        description = "One player is it: get close to tag someone else. No tag-backs. Least time spent it wins.",
        players = players, timer = duration(), rows = {area()},
    },
    {
        id = "infection", name = "Infection", flag = "infection", art = logos .. "img_infection_Placeholder",
        description = "One player starts infected. Everyone they reach is infected too and joins the hunt. Survive longest to win.",
        players = players, timer = duration(), rows = {area()},
    },
    {
        id = "hide", name = "Hide & Seek", flag = "hide", art = logos .. "img_hide_Placeholder",
        description = "Hide while the seeker can't see. Then they hunt: whoever is found seeks too. Stay hidden longest to win.",
        players = players, timer = duration(),
        rows = {{id = "hide_time", caption = "Hide Timer", min = 10, max = 120, step = 5, default = 30}, area()},
    },
}
