-- Checks of Fit Check's rules (turn.lua), run by `fitcheck test` (and by dingosdk_plugin_tests,
-- without the game): a turn found where it comes round, the stick eased off as pull request 326 did,
-- the frames picked, the card, the file's name and the settings switched off.
local turn = require("turn")

local checks = {}
local function check(title, fn) table.insert(checks, {title = title, fn = fn}) end
local function near(a, b, within) return math.abs(a - b) <= (within or 1e-6) end

-- Frames of something turning once every `period` frames: how unlike two of them look grows with how
-- far apart their angles are, nothing when they are the same.
local function turning(period, scale)
    return function(a, b) return (scale or 100) * math.abs(math.sin(math.pi * (a - b) / period)) end
end
local function first_round(finder, most)
    for n = 1, most do
        local period = finder:add(n)
        if period > 0 then return period, n end
    end
    return 0, most
end

check("a turn is found where it comes round", function()
    for _, period in {100, 73.4, 240} do
        local found, at = first_round(turn.finder(turning(period), turn.moved_enough.board), 1000)
        assert(near(found, period, 0.5), string.format("a turn of %g frames was found at %g", period, found))
        assert(at < period + 12, "and soon after it has: " .. at)
    end
end)
check("nothing turning never comes round, and says it has not moved", function()
    local finder = turn.finder(function() return 0.5 end, turn.moved_enough.board)
    local found = first_round(finder, 700)
    assert(found == 0 and finder.peak <= turn.moved_enough.board, "still")
end)
check("too little change is not a turn", function()
    -- A skater's turn is matched from 2.5 up; a board's from 6.
    local finder = turn.finder(turning(100, 4), turn.moved_enough.board)
    assert(first_round(finder, 400) == 0, "under the board's 6")
    local found = first_round(turn.finder(turning(100, 4), turn.moved_enough.skater), 400)
    assert(near(found, 100, 0.5), "over the skater's 2.5: " .. found)
end)
check("the stick eases off by at most half, and never into its dead zone", function()
    local stick = turn.stick(0.5)
    assert(near(stick:value(), 0.5))
    assert(stick:slow_down(0.6) and near(stick:value(), 0.33, 1e-4), "0.5 x 0.66: " .. stick:value())
    assert(stick:slow_down(0.2) and near(stick:value(), 0.3, 1e-4), "half would be under 0.3: " .. stick:value())
    assert(not stick:slow_down(0.2), "and no slower")
    assert(stick:speed_up() and near(stick:value(), 0.33, 1e-4), "back to the last speed that turned it")
    assert(not stick:speed_up(), "once")
    local left = turn.stick(-1)
    assert(left:slow_down(0.5) and near(left:value(), -0.55, 1e-4), "to the left too: " .. left:value())
end)
check("the frames nearest evenly spaced points of the turn", function()
    local kept = {}
    for n = 1, 199, 2 do table.insert(kept, n) end
    local frames = turn.even_frames(kept, 100, 4)
    assert(#frames == 4 and frames[1] == 1 and frames[2] == 25 and frames[3] == 51 and frames[4] == 75, table.concat(frames, " "))
end)
check("a turn's length in frames", function()
    local frames, fps = turn.frames_wanted(4, 60, "gif")
    assert(frames == 200 and fps == 50, "a GIF plays at 50 a second at most")
    assert(turn.frames_wanted(6, 60, "webm") == 360 and turn.frames_wanted(0.1, 30, "apng") == 8, "8 to 360")
end)
check("the card shows the part being browsed", function()
    local parts = {{slot = "Deck", item = "d1", title = "Old Deck"}, {slot = "Wheels", item = "w1", title = "Wheels A"}}
    local lines = turn.card_lines(parts, {focused = "d2", title = "New Deck", items = {"d1", "d2"}}, function(key) return key end)
    assert(lines[1][1] == "Deck" and lines[1][2] == "New Deck" and lines[2][2] == "Wheels A", "the deck browsed, the wheels worn")
    lines = turn.card_lines(parts, {focused = "d2", items = {"d1", "d2"}}, function(key) return "title of " .. key end)
    assert(lines[1][2] == "title of d2", "its title looked up when the grid has none")
    lines = turn.card_lines(parts, nil, function(key) return key end)
    assert(lines[1][2] == "Old Deck", "the worn one with no grid open")
end)
check("the Board screen's items", function()
    assert(turn.board_item("Customization/Board/Deck/x", ""), "by key")
    assert(turn.board_item("x", "Wheels") and turn.board_item("x", "griptape"), "by category")
    assert(not turn.board_item("Customization/Tops/x", "tops"), "not clothes")
end)
check("the file is named after the deck", function()
    assert(turn.file_name("Baker Brand Logo / Red", "board", 12, "gif") == "Baker-Brand-Logo-Red-12.gif")
    assert(turn.file_name("", "skater", 3, turn.extension("apng")) == "skater-3.png")
    assert(turn.file_name("--", "board", 3, turn.extension("webm")) == "board-3.webm")
end)
check("what is switched off while it turns", function()
    local names = {"Render.ScreenSpaceContactShadowsEnable", "Render.ScreenSpaceContactShadowsCacheEnable", "PostProcess.BloomEnable",
        "fog.BloomEnable", "PostProcess.ScreenSpaceRaytraceEnable", "Render.ShadowsEnable", "PostProcess.BloomIntensity"}
    local board = turn.switched_off(names, "board")
    assert(#board == 2 and board[1] == names[1] and board[2] == names[3], table.concat(board, " "))
    local skater = turn.switched_off(names, "skater")
    assert(#skater == 3 and skater[3] == names[5], table.concat(skater, " "))
    assert(turn.on("True") and turn.on("1") and not turn.on("false") and not turn.on(nil))
end)

local tests = {}
function tests.run()
    local passed, failures = 0, {}
    for _, c in checks do
        local ok, why = pcall(c.fn)
        if ok then passed += 1 else table.insert(failures, c.title .. ": " .. tostring(why)) end
    end
    return passed, failures
end
return tests
