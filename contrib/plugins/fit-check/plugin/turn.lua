-- Fit Check's rules, with the game passed in so tests.lua can run them without it: where the board or
-- skater is on screen, when one turn has come round, how the stick is eased off when it turns too
-- fast for the frames wanted, which frames make the animation, what is on the card, and which of the
-- game's render settings are switched off while it turns. The numbers are pull request 326's.
local M = {}

-- Where the game shows what is turned, as fractions of its menu frame (the whole width up to 21:9, a
-- 21:9 frame in the middle of anything wider: reskate.capture.start's frame "menu"), {left, top,
-- right, bottom}. Measured from the game at 2000x837 (2026-10-20):
--  - the Board screen's board: right of the item card, below the search bar, left of Back;
--  - the Skaters screen's skater: between the Apparel and Tattoos columns, below the tab icons,
--    down past the feet.
M.areas = {board = {0.655, 0.185, 0.86, 0.985}, skater = {0.27, 0.14, 0.735, 0.985}}
-- How unlike the start (0 to 255 a pixel of the thumbnails) the views must get for it to have turned.
M.moved_enough = {board = 6, skater = 2.5}
M.warmup = 45          -- frames let go first, for the stick to bring it up to speed
M.longest_turn = 2400  -- frames (40 s at 60) to measure a turn in
M.most_frames = 360    -- in the animation
M.still_after = 600    -- frames with nothing turning before it gives up

function M.noun(subject) return if subject == "skater" then "skater" else "board" end
function M.screen_hint(subject)
    return if subject == "skater" then "Is the Skaters screen open, with the whole skater on show?"
        else "Is the Board screen open, with the board on show?"
end

-- An item of the Board screen's grids: its key is under Customization/Board, or its category names a
-- board part.
local board_words = {"deck", "grip", "truck", "wheel", "bearing", "hardware", "riser", "board"}
function M.board_item(key, category)
    key, category = string.lower(key or ""), string.lower(category or "")
    if string.find(key, "/board/", 1, true) or string.find(key, "skateboard", 1, true) then return true end
    for _, word in board_words do
        if string.find(category, word, 1, true) then return true end
    end
    return false
end

-- ---- coming round ---------------------------------------------------------------------------------
-- Come round: the last few views match the first few again, after clearly different ones. Matching a
-- run of views, not one, keeps a board that started edge-on from "coming round" half way, when it is
-- edge-on again but turning to its other face. `compare(a, b)` says how unlike frames a and b look
-- (reskate.capture.compare). add(n) for each frame taken, 1, 2, ...: where the turn came round, in
-- frames from the first (with a parabola through the best match's neighbours), or 0.
local run, long_run, side = 4, 16, 6
function M.finder(compare, moved_enough)
    local f = {difference = {}, long = {}, peak = 0, long_peak = 0, taken = 0}
    local function unlike(a, b) return compare(a, b) or 0 end
    function f:add(n)
        self.taken = n
        if n < run then return 0 end
        local p = n - run -- the second turn's start, counted from 0
        local sum = 0
        for k = 1, run do sum += unlike(p + k, k) end
        table.insert(self.difference, sum / run)
        self.peak = math.max(self.peak, sum / run)
        -- The same over a longer run, for what does not quite come back to how it started (below).
        if n >= long_run then
            local q = n - long_run
            local total = 0
            for k = 1, long_run do total += unlike(q + k, k) end
            table.insert(self.long, total / long_run)
            self.long_peak = math.max(self.long_peak, total / long_run)
        end
        local d = #self.difference
        if d < 12 or self.peak <= moved_enough then return 0 end
        local a, b, c = self.difference[d - 2], self.difference[d - 1], self.difference[d]
        if b < 0.2 * self.peak and b <= a and b < c then
            local curve = a - 2 * b + c
            return (d - 2) + (if curve > 0 then 0.5 * (a - c) / curve else 0)
        end
        -- What turns may not look quite the same when it comes round: a skater sways and breathes as
        -- they stand, and fills less of the area than a board does. Then it is matched over a longer
        -- run of views (so a view that is only the mirror of the start, turning the other way, does not
        -- pass), and has come round at a clear dip, lowest for a few views either side, after it was
        -- well unlike the start.
        local series = self.long
        local e = #series
        if e < 2 * side + 12 then return 0 end
        local m = e - side -- the dip's place in the list; its frame is m - 1
        local v = series[m]
        if v >= 0.4 * self.long_peak then return 0 end
        for k = m - side, m + side do
            if k ~= m and series[k] < v then return 0 end
        end
        local before = 0
        for k = 1, m - 1 do before = math.max(before, series[k]) end
        if before < 0.8 * self.long_peak then return 0 end
        local l, r = series[m - 1], series[m + 1]
        local curve = l - 2 * v + r
        return (m - 1) + (if curve > 0 then 0.5 * (l - r) / curve else 0)
    end
    return f
end

-- ---- the stick ------------------------------------------------------------------------------------
-- The stick held over for the whole turn. Eased off by at most half each time: how the game turns the
-- stick into a speed is not known (a curve), and easing off too far at once leaves it standing still.
-- Never into the stick's dead zone (XInput's is about a quarter of the way): there nothing turns.
function M.stick(spin)
    local s = {spin = spin, strength = 1, faster = 1}
    function s:value() return math.clamp(self.spin * math.clamp(self.strength, 0, 1), -1, 1) end
    -- `ratio`: the turn's length over the frames wanted. Whether it is slower now.
    function s:slow_down(ratio)
        local least = 0.3
        local now = math.abs(self.spin) * self.strength
        local next = math.max(least, now * math.clamp(ratio * 1.1, 0.5, 1))
        if next >= now - 0.005 then return false end
        self.faster = self.strength
        self.strength = next / math.abs(self.spin)
        return true
    end
    -- Back to the last speed that turned it.
    function s:speed_up()
        if self.faster <= self.strength then return false end
        self.strength = self.faster
        return true
    end
    return s
end

-- ---- the animation --------------------------------------------------------------------------------
-- Frames in one turn: its length played at fps (a GIF plays at 50 a second at most: its frame times are
-- in hundredths, and players slow anything under two to a tenth).
function M.frames_wanted(seconds, fps, format)
    if format == "gif" then fps = math.min(fps, 50) end
    return math.clamp(math.floor(seconds * fps + 0.5), 8, M.most_frames), fps
end
-- The kept frames (reskate.capture.kept, numbered from 1) nearest to `count` evenly spaced points of
-- one turn of `period` frames.
function M.even_frames(kept, period, count)
    local out = {}
    for j = 0, count - 1 do
        local at = period * j / count
        local best = kept[1]
        for k = 2, #kept do
            if math.abs((kept[k] - 1) - at) < math.abs((best - 1) - at) then best = kept[k] end
        end
        table.insert(out, best)
    end
    return out
end

-- The card's lines: what reskate.outfit.board or worn says, {slot, item, title} each. On the Board
-- screen the board shows the item highlighted in the open grid, not the one worn: the part whose worn
-- item is in that grid shows the highlighted one. `title(key)` is reskate.outfit.title.
function M.card_lines(parts, grid, title)
    local in_grid = {}
    if grid and grid.focused and grid.items then
        for _, key in grid.items do in_grid[key] = true end
    end
    local lines = {}
    for _, part in parts do
        local name = part.title
        if in_grid[part.item] then name = if grid.title and grid.title ~= "" then grid.title else title(grid.focused) end
        if #lines < 16 then table.insert(lines, {string.sub(part.slot, 1, 64), string.sub(name, 1, 64)}) end
    end
    return lines
end
-- The file's name: the deck's (or "skater"), kept to what a file name may hold, then when, then the
-- format's extension.
function M.file_name(title, subject, stamp, extension)
    local name = ""
    title = title or ""
    for i = 1, #title do
        local c = string.sub(title, i, i)
        if string.byte(c) >= 0x80 or string.match(c, "^%w$") then
            name ..= c
        elseif (c == " " or c == "-" or c == "_") and name ~= "" and string.sub(name, -1) ~= "-" then
            name ..= "-"
        end
    end
    name = string.gsub(name, "%-+$", "")
    if name == "" then name = M.noun(subject) end
    return string.format("%s-%d.%s", name, stamp, extension)
end
function M.extension(format) return if format == "apng" then "png" elseif format == "webm" then "webm" else "gif" end

-- ---- the game's render settings -------------------------------------------------------------------
-- What is switched off while it turns, of the game's settings (reskate.settings.names):
--  - the screen-space contact shadows (the board's shadow where it nears the backdrop), not the shadow
--    maps: without those every light reaches the board unblocked and it is blown out;
--  - post effects that spread the board into the backdrop: bloom (a white deck glows onto it), lens
--    flare and dirt, film grain, chromatic aberration, motion blur;
--  - for a skater, what puts them on the floor: screen-space reflections (the Skaters screen's floor
--    is glossy) and ambient occlusion (the dark smudge round the feet).
local how = {"cache", "16bit", "logspace", "accum", "stencil", "transition", "viewdistance", "fixedmovement", "refine", "culling",
    "displacement", "tessellation", "zbuffer", "expression", "emitter", "quad", "cloud", "volumetric", "participating", "transparent"}
local effects = {"bloom", "lensflare", "lensdirt", "filmgrain", "chromaticaberration", "motionblur"}
local floors = {["postprocess.screenspaceraytraceenable"] = true, ["postprocess.dynamicaoenable"] = true}
local function has(text, part) return string.find(text, part, 1, true) ~= nil end
function M.switched_off(names, subject)
    local out = {}
    for _, name in names do
        local folded = string.lower(name)
        if string.sub(folded, -6) ~= "enable" or string.sub(folded, 1, 4) == "fog." then continue end
        local shadow = has(folded, "screenspacecontactshadows")
        if shadow then
            for _, part in how do
                if has(folded, part) then shadow = false break end
            end
        end
        local effect = false
        for _, part in effects do
            if has(folded, part) then effect = true break end
        end
        if shadow or effect or (subject == "skater" and floors[folded]) then table.insert(out, name) end
    end
    return out
end
function M.on(value)
    value = string.lower(tostring(value or ""))
    return value == "true" or value == "1" or value == "on"
end

return M
