-- Fit Check: V on the Board screen turns the board, and V on the Skaters screen (no item grid open)
-- turns the skater, once round, by holding a stand-in pad's stick over; every frame the game draws
-- is captured (before ReSkate's overlay), and once the turn has come round evenly spaced frames of it
-- are saved as a GIF, APNG or WebM, cut out by the game's depth (or by colour), with an info card of
-- the board's parts or the outfit beside it. V again stops it. The files go to this plugin's data
-- folder, fit-checks/ (OPEN FIT CHECKS FOLDER on its page, or `fitcheck open`).
--
-- Pull request 326's Fit Check, as a plugin: the capture, the cut-out and the encoders are ReSkate's
-- (reskate.capture); when the turn has come round, how the stick is eased off, which frames make the
-- animation and what is on the card are here, in turn.lua.
local turn = require("turn")

local folder = "fit-checks"
local mattes = {{"Black", 0x000000}, {"Grey", 0x18191c}, {"White", 0xffffff}, {"Green screen", 0x00b140}}

-- ---- the player's choices, kept --------------------------------------------------------------------
local defaults = {format = "gif", transparent = true, matte = 0x18191c, card = true, seconds = 4, fps = 30, size = 512,
    speed = 50, direction = "right", axis = "right_x"}
local options = {}
do
    local kept = reskate.storage.get("options")
    for key, value in defaults do
        options[key] = if type(kept) == "table" and type(kept[key]) == type(value) then kept[key] else value
    end
end
local axes = {lx = "left_x", ly = "left_y", rx = "right_x", ry = "right_y"}
-- One setting, as `fitcheck set` takes it; what to tell the player, or nil when it is not one.
local function set_option(key, value)
    value = string.lower(value or "")
    local n = tonumber(value)
    local said
    if key == "format" then
        if value == "png" then value = "apng" end
        if value ~= "gif" and value ~= "apng" and value ~= "webm" then return nil end
        options.format = value
        said = "Format: " .. (if value == "gif" then "GIF" elseif value == "apng" then "APNG" else "WebM") .. "."
    elseif key == "background" then
        if value == "clear" or value == "transparent" then options.transparent = true
        elseif value == "matte" then options.transparent = false
        else return nil end
        said = if options.transparent then "Background: transparent." else string.format("Background: matte #%06x.", options.matte)
    elseif key == "matte" then
        local hex = string.match(value, "^#?(%x%x%x%x%x%x)$")
        if not hex then return nil end
        options.matte = tonumber(hex, 16)
        said = string.format("Matte colour: #%06x.", options.matte)
    elseif key == "parts" or key == "card" then
        if value == "on" or value == "1" or value == "true" then options.card = true
        elseif value == "off" or value == "0" or value == "false" then options.card = false
        else return nil end
        said = if options.card then "The parts card is on." else "The parts card is off."
    elseif key == "speed" then
        if not n or n < 5 or n > 100 then return nil end
        options.speed = math.floor(n)
        said = string.format("Spin speed: %d%%.", options.speed)
    elseif key == "direction" then
        if value ~= "left" and value ~= "right" then return nil end
        options.direction = value
        said = "Spins to the " .. value .. "."
    elseif key == "axis" then
        if not axes[value] then return nil end
        options.axis = axes[value]
        said = "Spin stick: " .. value .. "."
    elseif key == "seconds" then
        if not n or n < 1 or n > 12 then return nil end
        options.seconds = math.floor(n)
        said = string.format("One turn takes %d s.", options.seconds)
    elseif key == "size" then
        if not n or n < 64 or n > 2048 then return nil end
        options.size = math.floor(n)
        said = string.format("Size: %d px.", options.size)
    elseif key == "fps" then
        if not n or n < 5 or n > 60 then return nil end
        options.fps = math.floor(n)
        said = string.format("%d frames a second.", options.fps)
    else
        return nil
    end
    reskate.storage.set("options", options)
    return said
end

-- ---- one fit check ---------------------------------------------------------------------------------
-- `job` while one is being made: its subject, the stick, the turn finder, its card, and how far it is.
local job = nil
local last = "" -- what the last one came to, for its page
local function say(text, level)
    last = text
    reskate.ui.toast(text, level)
    reskate.log(text)
end

-- The game's render settings that would come out with the board, off while it turns.
local switched = {}
local function settings_off(subject)
    local ok, names = pcall(reskate.settings.names)
    if not ok or type(names) ~= "table" then return end
    for _, name in turn.switched_off(names, subject) do
        if turn.on(reskate.settings.game(name)) and reskate.settings.set(name, false) then table.insert(switched, name) end
    end
end
local function settings_back()
    for _, name in switched do reskate.settings.set(name, nil) end
    switched = {}
end

local function finish(message, failed)
    if job then
        reskate.room.turn(0)
        settings_back()
        if failed then reskate.capture.discard() end
        job = nil
    end
    if message then say(message, if failed then "warn" else "info") end
end

-- Starts (or, after easing the stick, starts over) the capture of the turn; why not, or nil.
local function record()
    local subject = job.subject
    local ok, why = reskate.capture.start({area = turn.areas[subject], frame = "menu", depth = true,
        height = math.max(480, options.size * 5 // 4), skip = turn.warmup, frames = turn.longest_turn, seconds = 60})
    if not ok then return why end
    job.finder = turn.finder(reskate.capture.compare, turn.moved_enough[subject])
    job.seen = 0
    return nil
end

local function start(subject)
    if job then return "A fit check is already being made.", "warn" end
    local wanted, fps = turn.frames_wanted(options.seconds, options.fps, options.format)
    local spin = options.speed / 100 * (if options.direction == "left" then -1 else 1)
    job = {subject = subject, frames = wanted, fps = fps, stick = turn.stick(spin), slowdowns = 0, saving = false}
    -- The card, read now: on the Board screen the part being browsed shows the item highlighted.
    if subject == "skater" then
        job.title, job.card = "skater", {title = "Outfit", lines = turn.card_lines(reskate.outfit.worn(), nil, reskate.outfit.title)}
    else
        local parts = reskate.outfit.board()
        job.card = {title = "Board", lines = turn.card_lines(parts, reskate.menu.grid(), reskate.outfit.title)}
        job.title = "board"
        for _, line in job.card.lines do
            if line[1] == "Deck" then job.title = line[2] end
        end
    end
    settings_off(subject)
    local why = record()
    if why then
        finish(nil)
        return "The fit check could not start: " .. why, "warn"
    end
    return (if subject == "skater" then "Turning the skater" else "Turning the board") .. "... Hands off for a few seconds."
end

-- Once the turn has come round: its evenly spaced frames saved.
local function save()
    reskate.capture.stop()
    local frames = turn.even_frames(reskate.capture.kept(), job.period, job.frames)
    local stamp = if os and os.time then os.time() else math.floor(reskate.time.now() / 1000)
    job.file = turn.file_name(job.title, job.subject, stamp, turn.extension(options.format))
    local ok, why = reskate.capture.save(folder .. "/" .. job.file, {frames = frames, format = options.format, fps = job.fps, size = options.size,
        background = if options.transparent then "transparent" else options.matte, solid = job.subject == "board",
        card = if options.card and #job.card.lines > 0 then job.card else nil})
    if not ok then return finish("The fit check could not be saved: " .. tostring(why), true) end
    reskate.room.turn(0)
    settings_back()
    job.saving = true
    reskate.log("Saving the fit check...")
end

-- Each tick while one is being made: the stick held, each new frame looked at, and what comes of it.
local function step()
    local status = reskate.capture.state()
    if job.saving then
        if status.state == "saved" or status.state == "failed" then reskate.capture.discard() end -- its pictures let go
        if status.state == "saved" then
            local how = if string.find(status.message or "", "depth", 1, true) then " (cut out by depth)." else " (cut out by colour)."
            job = nil
            say("Saved " .. (status.file and string.match(status.file, "[^/]+$") or "it") .. " in the Fit Checks folder" .. how)
        elseif status.state == "failed" then
            job = nil
            say("The fit check could not be saved: " .. tostring(status.message), "warn")
        end
        return
    end
    if status.state == "recording" then
        local held, why = reskate.room.turn(job.stick:value(), options.axis)
        if not held then return finish("The fit check stopped: " .. tostring(why), true) end
    end
    local noun, moved = turn.noun(job.subject), turn.moved_enough[job.subject]
    for n = job.seen + 1, status.taken do
        job.seen = n
        local period = job.finder:add(n)
        if period <= 0 then
            local still = n > turn.still_after and job.finder.peak <= moved
            if still and job.slowdowns > 0 and job.slowdowns < 9 and job.stick:speed_up() then
                job.slowdowns = 9 -- slowed until it stopped: a little faster, and no slower again
                job.note = "Turning a little faster..."
                reskate.log(job.note)
                local why = record()
                if why then finish("The fit check stopped: " .. why, true) end
                return
            end
            if still then return finish("The " .. noun .. " did not turn. " .. turn.screen_hint(job.subject), true) end
            if n >= turn.longest_turn then return finish("The " .. noun .. " did not come all the way round.", true) end
        elseif period < job.frames * 0.9 and job.slowdowns < 3 and job.stick:slow_down(period / job.frames) then
            -- Too fast for the frames wanted: the same frame would show several times over. Slower, and again.
            job.slowdowns += 1
            job.note = "Turning slower, for every frame to be its own..."
            reskate.log(job.note)
            local why = record()
            if why then finish("The fit check stopped: " .. why, true) end
            return
        else
            job.period = period
            return save()
        end
    end
    -- However it goes, a fit check gives the game back within a minute.
    if status.state ~= "recording" then
        finish("The fit check took too long and was stopped. " .. turn.screen_hint(job.subject), true)
    end
end
reskate.on("tick", function()
    if job then step() end
end)
reskate.on("unload", function()
    if job then finish(nil, true) end
end)

-- V: the board on the Board screen (an item grid of the board's parts open), the skater on the Skaters
-- screen with no grid open; V again stops it. The key still goes to the game.
local function pressed()
    if job then
        if job.saving then return end
        return finish("Fit check cancelled.", true)
    end
    local grid = reskate.menu.grid()
    if grid then
        if not turn.board_item(grid.focused, grid.category) then return end
        return say(start("board"))
    end
    if not reskate.menu.game().skaters then return end
    say(start("skater"))
end
reskate.input.bind("fitcheck", "V")
reskate.on("action", function(name)
    if name == "fitcheck" then pressed() end
end)

-- ---- the banner, over the game while it turns (never in the capture: that is taken before) --------
local function v(x, y) return vector.create(x, y, 0) end
reskate.on("draw", function(d)
    if not job then return end
    local size = d:size()
    if size.x <= 0 or size.y <= 0 then return end
    local text
    if job.saving then
        text = "SAVING FIT CHECK..."
    elseif job.seen > 0 then
        text = string.format("RECORDING FIT CHECK  %d frames  -  V to stop", job.seen)
    else
        text = "RECORDING FIT CHECK  -  hands off  -  V to stop"
    end
    local k = size.y / 1080
    local wide = d:measure(text, {font = "bold", size = 20})
    local w, h = wide.x + 64 * k, 40 * k
    local left, top = (size.x - w) / 2, 24 * k
    d:rect(v(left, top), v(left + w, top + h), {colour = "#141518E6", fill = true, rounding = 6 * k})
    if not job.saving then d:circle(v(left + 20 * k, top + h / 2), 6 * k, {colour = "#E8202FFF", fill = true}) end
    d:text(v(left + 36 * k, top + (h - 20 * k) / 2), text, {font = "bold", size = 20, colour = "#F5F5F5FF"})
end)

-- ---- the console -----------------------------------------------------------------------------------
local usage = "fitcheck [board|skater] | cancel | open | set <key> <value> (format gif|apng|webm, background clear|matte, " ..
    "matte #rrggbb, parts on|off, speed 5-100, direction left|right, axis lx|ly|rx|ry, seconds 1-12, fps 5-60, size 64-2048) | test"
local function open_folder()
    local ok, why = reskate.files.reveal(folder)
    return if ok then "The Fit Checks folder is open." else "The folder could not be opened: " .. tostring(why)
end
reskate.console.command("fitcheck", "Fit Check: turn the board or skater on its menu screen and save it (V there does the same)", function(args)
    local function out(text) reskate.console.print("fitcheck: " .. text) end
    local verb = args[1]
    if verb == nil then
        return out(if job then "making one (" .. job.subject .. ")" else (if last ~= "" then last else "ready: V on the Board or Skaters screen"))
    elseif verb == "board" or verb == "skater" then
        return out(start(verb))
    elseif verb == "cancel" then
        if not job or job.saving then return out("none is being made") end
        finish("Fit check cancelled.", true)
        return out("cancelled")
    elseif verb == "open" then
        return out(open_folder())
    elseif verb == "set" then
        return out(set_option(args[2], args[3]) or usage)
    elseif verb == "test" then
        local passed, failures = require("tests").run()
        for _, failure in failures do out("FAIL " .. failure) end
        return out(string.format("%d passed, %d failed", passed, #failures))
    end
    out(usage)
end)

-- ---- its page in the menu's PLUGINS section --------------------------------------------------------
local function choice(ui, id, text, choices, chosen, help) return ui:dropdown(id, text, choices, chosen, help) end
reskate.menu.page("Fit Check", function(ui)
    ui:card("FIT CHECK", "Spin your board or skater and save it, to show off your mods or your setup")
    ui:note("Press V on the Board screen, or on the Skaters screen with no item grid open. It spins once by itself (hands off) " ..
        "and is saved to the Fit Checks folder. V again stops it.")
    if job then ui:info("Now", if job.saving then "Saving..." else string.format("%s %d frames", job.note or "Recording...", job.seen))
    elseif last ~= "" then ui:info("Last", last) end
    if ui:button("open", "OPEN FIT CHECKS FOLDER", true) then reskate.ui.toast(open_folder()) end
    if job and not job.saving and ui:button("cancel", "CANCEL") then finish("Fit check cancelled.", true) end

    ui:card("OPTIONS")
    local formats = {"gif", "apng", "webm"}
    local format = choice(ui, "format", "File", {"GIF", "APNG", "WebM"}, table.find(formats, options.format) or 1,
        "GIF plays anywhere, its edges a little rough. APNG is the best quality, with smooth edges (Discord and browsers). " ..
        "WebM is the smallest file, with smooth edges (Chrome, Firefox and Discord).")
    if formats[format] ~= options.format then set_option("format", formats[format]) end

    -- Transparent, or one of the solid colours (a colour set with `fitcheck set matte` shows as none of them).
    local background = 1
    if not options.transparent then
        background = 2
        for i, matte in mattes do
            if matte[2] == options.matte then background = i + 1 end
        end
    end
    local names = {"Transparent"}
    for _, matte in mattes do table.insert(names, matte[1]) end
    local picked = choice(ui, "background", "Background", names, background)
    if picked ~= background then
        if picked == 1 then set_option("background", "clear")
        else
            set_option("matte", string.format("%06x", mattes[picked - 1][2]))
            set_option("background", "matte")
        end
    end

    local card = ui:toggle("card", "Info card", options.card, "The deck, grip, trucks and wheels (or the outfit) by name beside it.")
    if card ~= options.card then set_option("parts", if card then "on" else "off") end

    -- Steps of a setting that `fitcheck set` can put anywhere: the one at or above it shows.
    local function stepped(id, text, key, labels, values, help)
        local shown = #values
        for i = #values, 1, -1 do
            if options[key] <= values[i] then shown = i end
        end
        local picked = choice(ui, id, text, labels, shown, help)
        if picked ~= shown then set_option(key, tostring(values[picked])) end
    end
    stepped("seconds", "Length", "seconds", {"2 s", "4 s", "6 s"}, {2, 4, 6}, "How long one turn takes to play.")
    stepped("fps", "Smoothness", "fps", {"Normal (30 fps)", "Smooth (60 fps)"}, {30, 60},
        "Normal: smaller files, quicker to make. Smooth: silky, and a slower spin to make it (a GIF plays at 50).")
    stepped("size", "Size", "size", {"Small", "Medium", "Large"}, {384, 512, 768}, "Small is 384 pixels high, Medium 512, Large 768 (bigger files).")
end)

reskate.log("Fit Check loaded: V on the Board or Skaters screen")
