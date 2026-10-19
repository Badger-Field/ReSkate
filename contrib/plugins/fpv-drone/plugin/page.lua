-- The drone's menu panels on ADDONS > PLUGINS, as UI/Overlay/drone_page.cpp's DRONE page had them:
-- flying, flight and rates, the display, and the controller with its guided setup. Every change
-- takes effect at once and is saved (settings.set).
local settings = require("settings")
local radio = require("radio")
local page = {}

local eyewear_labels = {"None", "Large visor 1", "Large visor 2", "Large visor 3", "Large visor 4", "Shield aviators",
                        "Wraparounds", "Slim wraparounds", "Speed shades"}

-- A slider hands its value back as a float: only a real change is one.
local function moved(got, value)
    return math.abs(got - value) > 1e-5 * math.max(1, math.abs(value))
end
local function slider(ui, name, text)
    local info = settings.info[name]
    local value = settings.get(name)
    local got = ui:slider(name, text, value, info.lo, info.hi)
    if moved(got, value) then settings.set(name, got) end
end
local function whole(ui, name, text)
    local info = settings.info[name]
    local value = settings.get(name)
    local got = math.round(ui:slider(name, text, value, info.lo, info.hi))
    if got ~= value then settings.set(name, got) end
end
local function toggle(ui, name, text)
    local on = settings.on(name)
    local got = ui:toggle(name, text, on)
    if got ~= on then settings.set(name, got and 1 or 0) end
end
local function choice(ui, name, text, choices)
    local value = math.clamp(math.round(settings.get(name)), 0, #choices - 1)
    local got = ui:dropdown(name, text, choices, value + 1) - 1
    if got ~= value then settings.set(name, got) end
end

-- `drone` is main.lua's: mode, armed, state, and what the page asks of it (start, stop, follow,
-- arm, reset).
function page.install(drone)
    reskate.menu.panel("FPV Drone", function(ui)
        local flying, following = drone.mode == "fly", drone.mode == "follow"
        ui:heading("FLY")
        if ui:button("fly", flying and "STOP FLYING" or "START FLYING") then
            if flying then drone.stop() else drone.start("fly") end
        end
        if drone.problem then ui:label(drone.problem) end
        local controller = settings.controller.enabled
        if flying then
            if not controller then ui:label("Flying with the keyboard and mouse. Close the menu to fly.")
            elseif settings.on("gamepad") then
                ui:label(reskate.input.gamepad() and "Flying with the gamepad. Close the menu to fly."
                         or "No gamepad found: flying with the keyboard and mouse. Close the menu to fly.")
            else ui:label("Flying with the controller. Close the menu to fly.") end
        else
            ui:label("Keyboard: W/S tilt forward/back, A/D roll, E/Q throttle up/down, mouse turns, Shift boosts.")
        end
        local wanted = ui:toggle("controller", "Fly with a controller", controller)
        if wanted ~= controller then settings.set_controller(wanted) end
        if controller then
            choice(ui, "gamepad", "Controller", {"RC radio", "Gamepad"})
            if settings.on("gamepad") then
                ui:label("Left stick: throttle (the middle hovers) and turning. Right stick: tilt forward, back and sideways. "
                         .. "A (cross) arms and disarms, Menu (Options) puts the drone back.")
            end
        elseif reskate.input.gamepad() or reskate.input.joystick() then
            ui:label("A controller is connected. Turn on Fly with a controller to use it.")
        end
        toggle(ui, "hideui", "Hide the game's interface while flying or following")

        ui:heading("FOLLOW ME")
        if ui:button("follow", following and "STOP FOLLOWING" or "FOLLOW ME") then
            if following then drone.stop() else drone.start("follow") end
        end
        ui:label(following and "Skate as normal: the drone flies itself behind you, as a pilot would."
                 or "The drone takes off behind you and flies itself, keeping you in shot while you skate. "
                    .. "It uses your drone's settings, flown self-levelling.")
        slider(ui, "followdistance", "Follow distance (m)")
        slider(ui, "followaggression", "Aggressiveness")

        ui:heading("DRONE")
        local current = settings.matching_preset()
        local names = {"Custom"}
        for _, preset in settings.presets do table.insert(names, preset.name) end
        local picked = ui:dropdown("preset", "Preset", names, (current or 0) + 1)
        if picked ~= (current or 0) + 1 and picked > 1 then
            settings.apply_preset(picked - 1)
            current = picked - 1
        end
        ui:label(current and settings.presets[current].description or "Your own settings. Pick a preset to start from one of them.")
        local worn = math.clamp(math.round(settings.get("goggles")), 0, 8)
        local goggles = ui:dropdown("goggles", "Goggles", eyewear_labels, worn + 1) - 1
        if goggles ~= worn then settings.set("goggles", goggles) end
        slider(ui, "topspeed", "Top speed")
        ui:label(string.format("Flat out at about %.0f km/h (a little faster with boost).", settings.get("topspeed") * 4 * 3.6))

        ui:heading("MOTORS")
        local armed = drone.armed
        ui:label("Motors: " .. (armed and "ARMED" or "DISARMED"))
        local s = drone.state
        if drone.mode ~= "off" and s then
            ui:label(string.format("%.0f%% throttle, %.0f km/h, %.1f m up, %.0f s", s.throttle * 100, s.speed * 3.6, s.altitude, s.flight_time))
        end
        if ui:button("arm", armed and "Disarm" or "Arm") then drone.armed = not armed end
        if ui:button("takeoff", "Back to takeoff") then drone.reset = true end
        if ui:button("reset", "Reset settings") then settings.reset() end
        ui:label("Created by neekgate for ReSkate")
    end)

    reskate.menu.panel("Drone flight", function(ui)
        ui:heading("FLIGHT MODE")
        choice(ui, "mode", "Mode", {"Acro", "Angle (self-levelling)"})
        if settings.on("mode") then
            slider(ui, "maxangle", "Most lean (degrees)")
            ui:label("The sticks set how far the drone leans; let go and it levels out. Easier to learn on, but no flips.")
        else
            ui:label("The sticks set how fast the drone turns, and it stays at whatever angle you leave it: how real FPV pilots fly.")
        end
        toggle(ui, "althold", "Hold height with a springing throttle")
        slider(ui, "climbrate", "Climb speed (m/s)")
        ui:label("For gamepads and the keyboard, whose throttle springs back. Off: the middle of the stick is hover power. "
                 .. "On: the middle holds your height, adding throttle as you lean to keep it. An RC radio's throttle is not affected.")
        ui:heading("FLIGHT")
        slider(ui, "gravity", "Gravity")
        slider(ui, "hover", "Hover throttle")
        slider(ui, "drag", "Air drag")
        slider(ui, "uptilt", "Camera tilt (degrees)")
        ui:heading("COLLISIONS")
        toggle(ui, "collide", "Collide with the world")
        slider(ui, "radius", "Collision size (m)")
        slider(ui, "floor", "Invisible floor (m)")
        toggle(ui, "disarmfreeze", "Hang in the air when disarmed")
        ui:heading("CRASHES")
        toggle(ui, "crash", "Crashes")
        if settings.on("crash") then
            slider(ui, "crashspeed", "Hit that cuts the motors (m/s)")
            if settings.get("crashspeed") > 0 then
                ui:label(string.format("Bumps bounce the drone off and knock it about. Hitting something at %.0f km/h or more cuts the "
                    .. "motors: it tumbles down, and you arm again to fly on.", settings.get("crashspeed") * 3.6))
            else
                ui:label("Bumps bounce the drone off and knock it about, but never cut the motors.")
            end
        else
            ui:label("Off: the drone just stops against whatever it meets.")
        end
        slider(ui, "propwash", "Prop wash shake")
        ui:heading("RANGE")
        local limited = settings.get("range") > 0
        local want = ui:toggle("limited", "Limited range", limited)
        if want ~= limited then settings.set("range", want and 300 or 0) end
        if want then
            local range = settings.get("range")
            local got = ui:slider("range", "Range (m)", range, 50, 5000)
            if moved(got, range) then settings.set("range", got) end
            range = settings.get("range")
            ui:label(string.format("Measured from where you took off. The picture starts to break up at %.0f m and is lost at %.0f m. "
                .. "Stay without a picture for 5 seconds, or go past %.0f m, and the motors cut.", range * 0.5, range, range * 1.5))
        else
            ui:label("Unlimited: fly anywhere on the map.")
        end
    end)

    reskate.menu.panel("Drone rates", function(ui)
        ui:label("How fast the drone turns, set like Betaflight's \"Actual\" rates. Centre: the speed near the middle of the stick. "
                 .. "Max: the speed at full stick. Expo: how much softer the middle is.")
        for _, axis in {"roll", "pitch", "yaw"} do
            ui:heading(string.upper(axis))
            slider(ui, axis .. "center", "Centre sensitivity (deg/s)")
            slider(ui, axis .. "max", "Max rate (deg/s)")
            slider(ui, axis .. "expo", "Expo")
        end
    end)

    reskate.menu.panel("Drone display", function(ui)
        ui:heading("GOGGLE DISPLAY")
        toggle(ui, "osd", "Show the goggle display")
        slider(ui, "osdscale", "Text size")
        choice(ui, "osdcolor", "Colour", {"White", "Green", "Yellow", "Cyan", "Orange", "Pink"})
        choice(ui, "osdfont", "Font", {"Pixel", "Smooth"})
        slider(ui, "osdmargin", "Distance from the edge")
        toggle(ui, "crosshair", "Crosshair")
        ui:heading("SHOW")
        toggle(ui, "osdarmed", "Armed / disarmed")
        toggle(ui, "osdbattery", "Battery")
        toggle(ui, "osdtimer", "Timer")
        toggle(ui, "osdthrottle", "Throttle")
        toggle(ui, "osdalt", "Altitude")
        toggle(ui, "osdspeed", "Speed")
        toggle(ui, "sticks", "My sticks on screen")
        slider(ui, "sticksize", "Sticks size")
        ui:heading("ANALOGUE VIDEO")
        toggle(ui, "analog", "Analogue video look")
        slider(ui, "analognoise", "Static")
        slider(ui, "analogscan", "Scanlines")
        slider(ui, "analogvignette", "Dark corners")
        slider(ui, "analogglitch", "Rolling glitches")
        ui:heading("SOUND")
        slider(ui, "volume", "Motor volume")
        slider(ui, "motorpitch", "Motor pitch")
        ui:heading("OTHER PLAYERS")
        toggle(ui, "beacon", "Let other players see my drone")
        toggle(ui, "showbeacons", "Show other players' drones")
        ui:label("Other players see your drone (a marker, FLYING DRONE on your nametag and its motor) if they have this mod too.")
    end)

    reskate.menu.panel("Drone controller", function(ui)
        page.controller(ui, drone)
    end)
end

-- ---- the controller ---------------------------------------------------------------------------
local function axis_text(signed)
    if signed == 0 then return "none" end
    return "axis " .. math.abs(signed) .. (signed < 0 and " (reversed)" or "")
end
-- Live bars for the six axes; `highlight` (1-6) is the one being picked.
local function axis_bars(ui, axes, highlight)
    for i = 1, 6 do
        local v = axes[i] or 0
        ui:bar(string.format("Axis %d  %+.2f%s", i, v, i == highlight and "  <" or ""), v, -1, 1)
    end
end

local step_text = {
    rest = {"STEP 1 OF 7: STICKS AT REST", "Let go of both sticks so they centre, and put the throttle all the way down (a gamepad's sticks just centre). Then press Ready."},
    throttle = {"STEP 2 OF 7: THROTTLE", "Push the throttle all the way up and hold it there. On a gamepad, that's the left stick, up.", "Got it. Now let the throttle back down."},
    yaw = {"STEP 3 OF 7: YAW", "Push the yaw stick (left stick, sideways) fully right and hold it.", "Got it. Let the stick go."},
    pitch = {"STEP 4 OF 7: PITCH", "Push the right stick fully forward (away from you) and hold it.", "Got it. Let the stick go."},
    roll = {"STEP 5 OF 7: ROLL", "Push the right stick fully right and hold it.", "Got it. Let the stick go."},
    arm = {"STEP 6 OF 7: ARM SWITCH (OPTIONAL)", "Flip the switch you want to arm the motors with to its ARMED position. No arm switch? Press Skip."},
    reset = {"STEP 7 OF 7: RESET SWITCH (OPTIONAL)", "Flick the switch or press the button you want to put the drone back at its start. No reset switch? Press Skip.", "Got it. Let the switch go back."},
}

local last_build = nil
local saved = false
function page.wizard(ui, drone, sample)
    local c = drone.calibration
    local now = reskate.time.now()
    local seconds = last_build and math.clamp((now - last_build) / 1000, 0, 0.25) or 0
    last_build = now
    local bits = sample and radio.button_bits(sample.buttons) or 0
    if sample then c:update(sample.axes, bits, seconds) end
    if c:active() then drone.calibration_seen = now end
    if c.step == "done" then
        -- Saved once, then what was found shown.
        if not saved then
            settings.set_controller(true, c.map)
            settings.set("armaxis", c.arm_axis)
            settings.set("armbutton", c.arm_button)
            settings.set("resetaxis", c.reset_axis)
            settings.set("resetbutton", c.reset_button)
            settings.set("throttlecentre", c.throttle_centred and 1 or 0)
            -- Gamepad sticks drift more than a radio's gimbals.
            if c.throttle_centred and settings.get("deadzone") < 0.12 then settings.set("deadzone", 0.12) end
            saved = true
        end
        ui:heading("SETUP SAVED: the drone now flies with this controller")
        local names = {"Roll", "Pitch", "Throttle", "Yaw"}
        for i = 1, 4 do ui:label(names[i] .. ": " .. axis_text(c.map[i])) end
        local function switch_text(axis, button)
            if button > 0 then return "button " .. button end
            return axis_text(axis)
        end
        ui:label("Arm switch: " .. switch_text(c.arm_axis, c.arm_button))
        ui:label("Reset switch: " .. switch_text(c.reset_axis, c.reset_button))
        if c.throttle_centred then
            ui:label("Your throttle springs back to the middle, so the middle now holds the drone up: push up to climb, down to descend.")
        end
        if ui:button("done", "DONE") then
            c:cancel()
            saved = false
        end
        return
    end
    saved = false
    local text = step_text[c.step]
    ui:heading(text[1])
    ui:label(c.returning and text[3] or text[2])
    if not sample then ui:label("The controller is not connected.")
    else axis_bars(ui, sample.axes, not c.returning and c.candidate or -1) end
    if c.step == "rest" then
        if ui:button("ready", "Ready") and sample then c:capture_rest(sample.axes, bits) end
    elseif (c.step == "arm" or c.step == "reset") and not c.returning then
        if ui:button("skip", "Skip") then c:skip() end
    end
    if ui:button("cancel", "Cancel") then c:cancel() end
end

function page.controller(ui, drone)
    local c = drone.calibration
    if settings.on("gamepad") then
        choice(ui, "gamepad", "Controller", {"RC radio", "Gamepad"})
        local pad = reskate.input.gamepad()
        ui:heading("GAMEPAD")
        if not pad then
            ui:label("No gamepad found. Plug in an Xbox or PlayStation controller.")
        else
            ui:label("Connected: " .. (pad.playstation and "PlayStation controller" or "Xbox controller"))
            local s = radio.gamepad_sticks(pad, settings.get("deadzone"), settings.get("padrate"), settings.get("padexpo"),
                settings.get("hover"), settings.on("padinvert"), settings.on("mode"))
            ui:bar(string.format("Throttle  %+.2f", s.climb), s.climb, -1, 1)
            ui:bar(string.format("Yaw  %+.2f", s.yaw), s.yaw, -1, 1)
            ui:bar(string.format("Pitch  %+.2f", s.pitch), s.pitch, -1, 1)
            ui:bar(string.format("Roll  %+.2f", s.roll), s.roll, -1, 1)
            ui:label("What the drone gets from each stick, after the dead zone and sensitivity below.")
        end
        ui:label("Left stick: throttle up and down, turning left and right. Right stick: tilt (pitch, see Invert below) and roll. "
                 .. "A (cross) arms and disarms, Menu (Options) puts the drone back where it took off.")
        ui:heading("STICKS")
        slider(ui, "padrate", "Sensitivity")
        slider(ui, "padexpo", "Softer middle")
        slider(ui, "deadzone", "Stick dead zone")
        toggle(ui, "padinvert", "Invert the right stick (up tips the nose up)")
        ui:label("Sensitivity scales how fast the drone can turn at full stick (your rates still shape it). A softer middle makes "
                 .. "small corrections gentler without slowing full-stick flips. Gamepads use a dead zone of at least 0.08.")
        return
    end
    local sample = reskate.input.joystick()
    if c.step ~= "idle" then
        page.wizard(ui, drone, sample)
        return
    end
    last_build = nil
    choice(ui, "gamepad", "Controller", {"RC radio", "Gamepad"})
    ui:heading("CONTROLLER")
    if not sample then
        ui:label("No controller found. Plug in an RC radio with its USB mode set to Joystick (on EdgeTX radios, pick "
                 .. "\"USB Joystick (HID)\" when you plug it in).")
    else
        ui:label("Connected: " .. sample.name)
        if ui:button("setup", "SET UP CONTROLLER") then c:start() end
        ui:label("Goes through each stick and switch and saves which is which. The drone holds still meanwhile.")
    end
    ui:heading("MANUAL SETUP")
    if sample then
        axis_bars(ui, sample.axes, -1)
        local pressed = {}
        for n = 1, 32 do
            if sample.buttons[n] then table.insert(pressed, tostring(n)) end
        end
        ui:label("Buttons pressed: " .. (#pressed > 0 and table.concat(pressed, " ") or "none"))
        ui:label("Move a stick or flip a switch to see which axis or button it is.")
    end
    local names = {"Roll", "Pitch", "Throttle", "Yaw"}
    local axes = {"Axis 1", "Axis 2", "Axis 3", "Axis 4", "Axis 5", "Axis 6"}
    local map = table.clone(settings.controller.map)
    local changed = false
    for i = 1, 4 do
        local axis = math.clamp(math.abs(map[i]), 1, 6)
        local flip = map[i] < 0
        local picked = ui:dropdown("axis" .. i, names[i], axes, axis)
        local reversed = ui:toggle("flip" .. i, names[i] .. " reversed", flip)
        if picked ~= axis or reversed ~= flip then
            map[i] = picked * (reversed and -1 or 1)
            changed = true
        end
    end
    if changed then settings.set_controller(nil, map) end
    toggle(ui, "throttlecentre", "Throttle springs back to the middle")
    slider(ui, "deadzone", "Stick dead zone")
    ui:heading("SWITCHES: an axis or button no stick uses; 0 = none")
    whole(ui, "armaxis", "Arm switch axis")
    whole(ui, "armbutton", "Arm button")
    whole(ui, "resetaxis", "Reset switch axis")
    whole(ui, "resetbutton", "Reset button")
end

return page
