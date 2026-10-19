// The plugin host apart from the game: manifests, the reskate API, storage, modules, hot reload,
// and the sandbox against plugins written to break it (loops, memory, error floods, escapes).
#include "Extension/Plugins/plugin_discovery.h"
#include "Extension/Plugins/plugin_host.h"
#include "Extension/Plugins/plugin_manifest.h"
#include "Engine/Vfs/mod_list.h"
#include <algorithm>
#include <chrono>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>

using namespace dingosdk::plugins;
namespace {
void check(bool value, const std::string& message) { if (!value) throw std::runtime_error(message); }

std::filesystem::path scratch;

struct TestEnvironment final : Environment {
    std::vector<std::string> logs, lines;
    double now = 1000;
    void log(LogLevel, std::string_view text) override { logs.emplace_back(text); }
    void console(std::string_view text) override { lines.emplace_back(text); }
    double now_ms() override { return now; }
    std::filesystem::path storage_root() override { return scratch / "storage"; }
    bool builtin_command(std::string_view word) override { return word == "perf" || word == "load"; }
    // A skater, when there is one, on flat ground at y = 0.
    std::optional<SkaterState> local_skater;
    bool teleport_forbidden{}, teleport_unavailable{};
    std::vector<std::pair<Vec3, float>> teleports;
    int rays{};
    std::optional<SkaterState> skater() override { return local_skater; }
    std::optional<ScoreState> score_now;
    std::optional<ScoreState> score() override { return score_now; }
    Teleport teleport(const Vec3& position, float heading) override {
        if (teleport_forbidden) return Teleport::forbidden;
        if (teleport_unavailable || !local_skater) return Teleport::unavailable;
        teleports.emplace_back(position, heading);
        return Teleport::sent;
    }
    std::optional<Vec3> raycast(const Vec3& from, const Vec3& to) override {
        ++rays;
        if ((from[1] > 0) == (to[1] > 0)) return std::nullopt;
        const float f = from[1] / (from[1] - to[1]);
        return Vec3{from[0] + (to[0] - from[0]) * f, 0, from[2] + (to[2] - from[2]) * f};
    }
    bool logged(std::string_view part) const {
        return std::any_of(logs.begin(), logs.end(), [&](const auto& line) { return line.find(part) != std::string::npos; });
    }
    bool printed(std::string_view part) const {
        return std::any_of(lines.begin(), lines.end(), [&](const auto& line) { return line.find(part) != std::string::npos; });
    }
    std::string all() const {
        std::string text;
        for (const auto& line : logs) text += line + "\n";
        return text;
    }
};

std::string manifest(std::string permissions = "", std::string side = "client") {
    return R"({"api": 1, "entry": "plugin/main.lua", "side": ")" + side + R"(", "permissions": [)" + permissions + "]}";
}

void write(const std::filesystem::path& file, const std::string& text) {
    std::filesystem::create_directories(file.parent_path());
    std::ofstream(file, std::ios::binary | std::ios::trunc) << text;
}

// A mod folder holding a plugin: its reskate-plugin.json and files.
Candidate package(const std::string& id, const std::map<std::string, std::string>& files, std::string fingerprint = "1") {
    const auto directory = scratch / "mods" / id;
    std::filesystem::remove_all(directory);
    for (const auto& [name, text] : files) write(directory / name, text);
    return {id, directory, std::move(fingerprint), {}};
}
Candidate script(const std::string& id, const std::string& main, const std::string& permissions = "") {
    return package(id, {{manifest_file, manifest(permissions)}, {"plugin/main.lua", main}});
}

Status status_of(const Host& host, std::string_view id) {
    for (const auto& status : host.status()) if (status.id == id) return status;
    throw std::runtime_error("no plugin " + std::string(id));
}
void ticks(Host& host, TestEnvironment& environment, int count) {
    for (int i = 0; i < count; ++i) {
        environment.now += 16.7;
        host.tick(16.7);
    }
}
void running(const Host& host, std::string_view id, const TestEnvironment& environment) {
    const auto status = status_of(host, id);
    check(status.state == "running", std::string(id) + " should run, and is " + status.state + ": " + status.reason + "\n" + environment.all());
}
void stopped(const Host& host, std::string_view id, std::string_view because) {
    const auto status = status_of(host, id);
    check(status.state == "stopped", std::string(id) + " should have stopped, and is " + status.state);
    check(status.reason.find(because) != std::string::npos, std::string(id) + " stopped for \"" + status.reason + "\", not for " + std::string(because));
}

void a_manifest_says_what_a_plugin_is() {
    const auto good = parse_manifest(manifest(R"("console", "storage")"));
    check(good.api == 1 && good.entry == "plugin/main.lua" && good.side == Side::client, "A manifest is read");
    check(good.permissions.contains("console") && good.permissions.contains("storage"), "with its permissions");
    const auto refused = [](const std::string& text, std::string_view because) {
        try {
            parse_manifest(text);
        } catch (const std::exception& error) {
            check(std::string(error.what()).find(because) != std::string::npos, "Refused for \"" + std::string(error.what()) + "\", not " + std::string(because));
            return;
        }
        throw std::runtime_error("A manifest that should be refused was read: " + text);
    };
    refused(R"({"entry": "main.lua"})", "\"api\"");
    refused(R"({"api": 2, "entry": "main.lua"})", "update ReSkate");
    refused(R"({"api": 1, "entry": "../main.lua"})", "inside the mod folder");
    refused(R"({"api": 1, "entry": "C:/main.lua"})", "inside the mod folder");
    refused(R"({"api": 1, "entry": "a\\main.lua"})", "inside the mod folder");
    refused(R"({"api": 1, "entry": "main.txt"})", ".lua");
    refused(R"({"api": 1, "entry": "main.lua", "side": "moon"})", "\"side\"");
    refused(R"({"api": 1, "entry": "main.lua", "permissions": ["root"]})", "unknown permission");
    refused("not json", "not valid JSON");
    check(safe_relative_path("a/b.lua") == "a/b.lua", "A path inside the package is kept");
    for (const auto* bad : {"", "/a", "a//b", "./a", "a/../b", "..", "a\\b", "c:a", "a.", "a /b", "a\nb"})
        check(safe_relative_path(bad).empty(), std::string("A path that could leave the package is refused: ") + bad);
}

void a_plugin_loads_ticks_runs_its_commands_and_unloads() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({script("hello", R"(
        local ticks = 0
        reskate.on("load", function() reskate.log("loaded " .. reskate.id .. " api " .. reskate.api_version) end)
        reskate.on("tick", function(dt) ticks += 1 end)
        reskate.on("unload", function() reskate.log("bye after " .. ticks) end)
        reskate.console.command("hello there", "Says hello", function(args)
            reskate.console.print("hello " .. table.concat(args, ",") .. " after " .. ticks)
        end)
        print("printed", 1, true)
    )", R"("console")")});
    running(host, "hello", environment);
    check(environment.logged("[hello] loaded hello api 1"), "The load handler ran\n" + environment.all());
    check(environment.logged("[hello] printed\t1\ttrue"), "print goes to the log");
    ticks(host, environment, 3);
    check(host.run_console("hello there a b"), "Its command runs");
    check(environment.printed("hello a,b after 3"), "with its arguments, after its ticks");
    check(host.run_console("HELLO There"), "whatever the case");
    check(!host.run_console("nothing here"), "and other text is left to ReSkate");
    check(status_of(host, "hello").commands.size() == 1, "Its command is listed");
    host.apply({});
    check(environment.logged("[hello] bye after 3"), "Unloading runs its unload handler");
    check(host.status().empty(), "and it is gone");
    check(!host.run_console("hello there"), "with its command");
}

void a_permission_must_be_declared() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({script("bare", R"(
        local ok, err = pcall(reskate.console.command, "x", "x", function() end)
        assert(not ok and err:find("needs the \"console\" permission"), err)
        ok, err = pcall(reskate.storage.get, "x")
        assert(not ok and err:find("needs the \"storage\" permission"), err)
        reskate.console.print("never")
    )")});
    stopped(host, "bare", "raised an error");
    check(environment.logged("needs the \"console\" permission"), "The error names the permission\n" + environment.all());
}

void command_names_are_checked() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({script("first", R"(
        reskate.console.command("dom start", "Starts", function() end)
        assert(not pcall(reskate.console.command, "perf mine", "x", function() end), "ReSkate's own")
        assert(not pcall(reskate.console.command, "plugin mine", "x", function() end), "the plugin command")
        assert(not pcall(reskate.console.command, "Bad Name", "x", function() end), "uppercase")
        assert(not pcall(reskate.console.command, "a b c d", "x", function() end), "four words")
        assert(not pcall(reskate.console.command, "dom start", "x", function() end), "twice")
    )", R"("console")"),
        script("second", R"(
        local ok, err = pcall(reskate.console.command, "dom", "x", function() end)
        assert(not ok and err:find("first"), err)
        reskate.console.command("other", "x", function() end)
    )", R"("console")")});
    running(host, "first", environment);
    running(host, "second", environment);
}

void storage_is_kept_across_loads_and_has_a_quota() {
    TestEnvironment environment;
    Limits limits;
    limits.storage_bytes = 300;
    std::filesystem::remove_all(scratch / "storage" / "keeper");
    const auto keeper = script("keeper", R"(
        local runs = reskate.storage.add("runs")
        reskate.storage.set("table", {a = 1, list = {1, 2, 3}, name = "x"})
        local back = reskate.storage.get("table")
        assert(back.a == 1 and back.list[3] == 3 and back.name == "x")
        reskate.storage.set("gone", true)
        reskate.storage.set("gone", nil)
        assert(reskate.storage.get("gone") == nil)
        local ok, err = pcall(reskate.storage.set, "big", string.rep("x", 1000))
        assert(not ok and err:find("full"), err)
        assert(reskate.storage.get("big") == nil)
        assert(not pcall(reskate.storage.set, "f", print), "a function is not JSON")
        assert(not pcall(reskate.storage.set, "", 1), "an empty key")
        local cycle = {} cycle.me = cycle
        assert(not pcall(reskate.storage.set, "c", cycle), "a cycle")
        reskate.log("runs " .. runs)
    )", R"("storage")");
    {
        Host host(environment, Side::client, limits);
        host.apply({keeper});
        running(host, "keeper", environment);
        host.apply({});
        host.apply({keeper});
        running(host, "keeper", environment);
    }
    check(environment.logged("[keeper] runs 1") && environment.logged("[keeper] runs 2"), "Storage is kept from one load to the next\n" + environment.all());
    check(std::filesystem::exists(scratch / "storage" / "keeper" / "storage.json"), "in the plugin's own file");
}

void packages_json_and_modules() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({package("modules", {
        {manifest_file, manifest()},
        {"data/parks.json", R"({"parks": [{"name": "bowl", "spots": 3}]})"},
        {"data/notes.txt", "hello"},
        {"plugin/lib/util.lua", "loads = (loads or 0) + 1\nreturn {double = function(x) return x * 2 end}"},
        {"plugin/rules.luau", "return {name = 'rules'}"},
        {"plugin/cycle_a.lua", "return require('cycle_b')"},
        {"plugin/cycle_b.lua", "return require('cycle_a')"},
        {"plugin/main.lua", R"(
            assert(reskate.package.read("data/notes.txt") == "hello")
            assert(reskate.package.read("data/missing.txt") == nil)
            local parks = reskate.package.read_json("data/parks.json")
            assert(parks.parks[1].name == "bowl" and parks.parks[1].spots == 3)
            local listing = table.concat(reskate.package.list("data"), ",")
            assert(listing == "notes.txt,parks.json", listing)
            assert(table.concat(reskate.package.list(), ","):find("plugin/"), "folders end in /")
            for _, bad in {"../x", "/x", "C:/x", "data\\notes.txt", "data/../../x"} do
                assert(not pcall(reskate.package.read, bad), bad)
            end
            local util = require("./lib/util")
            assert(util.double(4) == 8)
            assert(require("lib/util") == util and loads == 1, "a module runs once")
            assert(require("rules").name == "rules", ".luau too")
            local ok, err = pcall(require, "cycle_a")
            assert(not ok and err:find("requires itself"), err)
            assert(not pcall(require, "missing"))
            assert(not pcall(require, "../data/notes"))
            local text = reskate.json.encode({a = {1, 2}, b = "x", c = true})
            local back = reskate.json.decode(text)
            assert(back.a[2] == 2 and back.b == "x" and back.c == true, text)
            assert(not pcall(reskate.json.decode, "{nope"))
            assert(not pcall(reskate.json.encode, {[{}] = 1}))
            assert(not pcall(reskate.json.encode, 0/0))
            reskate.log("modules fine")
        )"}})});
    running(host, "modules", environment);
    check(environment.logged("modules fine"), "Packages, JSON and modules work\n" + environment.all());
}

// What a plugin must not be able to reach or change. Each line checks one way out.
void the_sandbox_holds() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({script("escape", R"(
        assert(io == nil and package == nil and dofile == nil and loadfile == nil, "no files")
        assert(loadstring == nil and load == nil, "no loading code, bytecode or text")
        assert(os.execute == nil and os.remove == nil and os.rename == nil and os.getenv == nil and os.exit == nil, "no os")
        assert(not pcall(function() reskate.log = nil end), "reskate is read only")
        assert(not pcall(function() reskate.storage.get = print end), "and its tables")
        assert(not pcall(rawset, reskate, "x", 1), "even by rawset")
        assert(not pcall(setmetatable, reskate, {}), "or a metatable")
        assert(not pcall(function() string.rep = nil end), "the libraries are read only")
        assert(not pcall(function() getmetatable("").__index = {} end), "and the string metatable")
        assert(not pcall(function() _G.print = nil end) or print ~= nil, "print stays")
        mine = 1
        assert(mine == 1, "its own globals work")
        local function deep(n) return deep(n + 1) + 1 end
        assert(not pcall(deep, 1), "endless recursion is an error, not a crash")
        assert(not pcall(require, "C:/Windows/win.ini"))
        assert(not pcall(reskate.package.read, "../../../Windows/win.ini"))
        assert(not pcall(reskate.time.after, -1, print))
        assert(not pcall(reskate.time.every, 0, print))
        assert(not pcall(reskate.on, "made_up", print))
        assert(not pcall(reskate.log, "x", "loud"))
        reskate.log("sandbox fine")
    )")});
    running(host, "escape", environment);
    check(environment.logged("sandbox fine"), "Every way out is closed\n" + environment.all());
}

void a_runaway_plugin_is_stopped_and_the_rest_go_on() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    const auto start = std::chrono::steady_clock::now();
    host.apply({
        script("bystander", R"(local n = 0 reskate.on("tick", function() n += 1 if n == 30 then reskate.log("bystander ticked 30") end end))"),
        script("loop", "while true do end"),
        script("catches", "while true do pcall(function() while true do end end) end"),
        script("tick_loop", R"(reskate.on("tick", function() while true do end end))"),
        script("memory", R"(local t = {} while true do t[#t + 1] = string.rep("x", 8000000) end)"),
        script("memory_caught", R"(
            pcall(function() local t = {} while true do t[#t + 1] = string.rep("y", 8000000) end end)
            reskate.log("survived the memory bomb")
        )"),
        script("huge", R"(local s = string.rep("x", 100000000))"),
        script("buffer", R"(local b = buffer.create(1e9))"),
        script("errors", R"(reskate.on("tick", function() error("boom") end))"),
        script("timers", R"(
            local ok, err = pcall(function() for i = 1, 2000 do reskate.time.after(1000, function() end) end end)
            assert(not ok and err:find("timers"), err)
            reskate.log("timers capped")
        )"),
        script("chatty", R"(for i = 1, 1000 do reskate.log("line " .. i) end)"),
        script("coroutines", R"(while true do coroutine.wrap(function() end)() end)"),
        script("slow_unload", R"(reskate.on("unload", function() while true do end end))"),
    });
    for (const auto* id : {"loop", "catches"}) stopped(host, id, "ran longer than 50 ms");
    stopped(host, "coroutines", ""); // its time or its memory, whichever runs out first
    for (const auto* id : {"memory", "memory_caught", "huge", "buffer"}) stopped(host, id, "more than 64 MB");
    check(!environment.logged("survived the memory bomb"), "A memory bomb cannot be caught and carried on from");
    running(host, "tick_loop", environment);
    ticks(host, environment, 1);
    stopped(host, "tick_loop", "the tick handler ran longer than 50 ms");
    check(environment.logged("[tick_loop] stopped: the tick handler"), "A stop is logged, which the console shows");
    ticks(host, environment, 4); // five errors, with the tick above
    running(host, "errors", environment);
    ticks(host, environment, 1);
    stopped(host, "errors", "more than 5 errors");
    running(host, "timers", environment);
    check(environment.logged("timers capped"), "Timers have a limit");
    const auto chatty = std::count_if(environment.logs.begin(), environment.logs.end(),
        [](const auto& line) { return line.starts_with("[chatty] line "); });
    check(chatty == 50, "50 lines a second, and the rest left out: " + std::to_string(chatty));
    ticks(host, environment, 30);
    running(host, "bystander", environment);
    check(environment.logged("bystander ticked 30"), "The others go on ticking");
    const auto unloading = std::chrono::steady_clock::now();
    host.apply({});
    const auto unload_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - unloading).count();
    check(unload_ms < 500, "An unload handler that never ends is cut off: " + std::to_string(unload_ms) + " ms");
    const auto total = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    check(total < 20, "and none of it hangs the host: " + std::to_string(total) + " s");
}

void a_plugin_over_its_time_budget_is_stopped() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({script("busy", R"(reskate.on("tick", function() local s = os.clock() while os.clock() - s < 0.004 do end end))"),
        script("light", R"(reskate.on("tick", function() local s = 0 for i = 1, 100 do s += i end end))")});
    ticks(host, environment, 60 * 5);
    stopped(host, "busy", "ms a tick for 3 s");
    running(host, "light", environment);
}

void a_plugin_reloads_when_its_files_change() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({script("live", R"(reskate.log("version 1") reskate.on("unload", function() reskate.log("unloading 1") end))")});
    running(host, "live", environment);
    auto changed = script("live", R"(reskate.log("version 2"))");
    changed.fingerprint = "2";
    host.apply({changed});
    check(environment.logged("unloading 1") && environment.logged("version 2"), "A changed plugin unloads and loads again\n" + environment.all());
    host.apply({changed});
    check(std::count_if(environment.logs.begin(), environment.logs.end(), [](const auto& line) { return line.find("version 2") != std::string::npos; }) == 1,
        "and an unchanged one is left alone");
    check(host.switch_off("live").find("switched off") != std::string::npos, "It can be switched off by hand");
    check(status_of(host, "live").state == "switched off", "and stays off");
    changed.fingerprint = "3";
    host.apply({changed});
    check(status_of(host, "live").state == "switched off", "even when its files change");
    check(host.switch_on("live").find("started") != std::string::npos, "until it is switched on");
    running(host, "live", environment);
    check(host.reload("live").find("reloaded") != std::string::npos, "and reloaded by hand");
    check(host.reload("nobody").starts_with("error: "), "A plugin that is not there cannot be");
}

void the_skater_can_be_read_moved_and_the_world_probed() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({script("bare", R"(
        local ok, err = pcall(reskate.skater.get)
        assert(not ok and err:find("skater.read"), err)
        ok, err = pcall(reskate.skater.teleport, vector.create(0, 0, 0))
        assert(not ok and err:find("skater.teleport"), err)
        ok, err = pcall(reskate.world.raycast, vector.create(0, 1, 0), vector.create(0, -1, 0))
        assert(not ok and err:find("world.raycast"), err)
        reskate.log("bare refused")
    )"), script("mover", R"(
        reskate.console.command("probe", "x", function()
            local s = reskate.skater.get()
            if not s then reskate.log("no skater") return end
            reskate.log(string.format("at %.1f %.1f %.1f facing %.2f speed %.1f up %.1f air %s %.1f", s.position.x, s.position.y,
                s.position.z, s.heading, s.speed, s.vertical_speed, tostring(s.airborne), s.air_time))
            assert(reskate.skater.position() == s.position and reskate.skater.heading() == s.heading)
            assert(reskate.skater.speed() == s.speed and reskate.skater.on_ground() == not s.airborne)
            local hit = reskate.world.raycast(s.position + vector.create(0, 2, 0), s.position - vector.create(0, 5, 0))
            assert(hit and math.abs(hit.y) < 0.01, "the ground under the skater")
            assert(reskate.world.raycast(vector.create(0, 5, 0), vector.create(0, 1, 0)) == nil, "a clear ray")
        end)
        reskate.console.command("move", "x", function(args)
            local ok, why = reskate.skater.teleport(vector.create(10, 0, 20), args[1] and tonumber(args[1]) or nil)
            reskate.log("teleport " .. tostring(ok) .. " " .. tostring(why))
        end)
        reskate.console.command("bad", "x", function()
            assert(not pcall(reskate.skater.teleport, vector.create(0 / 0, 0, 0)), "NaN")
            assert(not pcall(reskate.skater.teleport, vector.create(1e7, 0, 0)), "far away")
            assert(not pcall(reskate.skater.teleport, {1, 2, 3}), "a table is not a vector")
            assert(not pcall(reskate.skater.teleport, vector.create(0, 0, 0), 0 / 0), "a NaN heading")
            assert(not pcall(reskate.world.raycast, vector.create(0, 0, 0), vector.create(2000, 0, 0)), "too long")
            reskate.log("bad refused")
        end)
        reskate.console.command("rays", "x", function()
            local count = 0
            local ok, err = pcall(function()
                for i = 1, 100 do reskate.world.raycast(vector.create(0, 1, 0), vector.create(0, -1, 0)) count += 1 end
            end)
            reskate.log("rays " .. count .. " " .. tostring(err):match("more than %d+ raycasts") or "")
        end)
    )", R"("console", "skater.read", "skater.teleport", "world.raycast")")});
    running(host, "bare", environment);
    check(environment.logged("bare refused"), "Each needs its permission\n" + environment.all());
    host.run_console("probe");
    check(environment.logged("[mover] no skater"), "Without a skater there is nothing to read");
    host.run_console("move");
    check(environment.logged("teleport false teleporting is unavailable"), "nor to move");
    environment.local_skater = SkaterState{{1, 0, 2}, 1.5f, 4, 0.5f, true, 0.25f};
    host.run_console("probe");
    check(environment.logged("at 1.0 0.0 2.0 facing 1.50 speed 4.0 up 0.5 air true 0.2"), "The skater is read\n" + environment.all());
    running(host, "mover", environment);
    host.run_console("move 0.5");
    check(environment.teleports.size() == 1 && environment.teleports[0].first == Vec3{10, 0, 20} &&
          std::abs(environment.teleports[0].second - 0.5f) < 1e-6f, "A teleport is sent where it was asked");
    environment.now += 1000;
    host.run_console("move");
    check(environment.teleports.size() == 2 && std::abs(environment.teleports[1].second - 1.5f) < 1e-6f, "facing as the skater does by default");
    host.run_console("move");
    host.run_console("move");
    check(environment.teleports.size() == 3 && environment.logged("asked more than twice in a second"), "Two a second");
    environment.now += 1000;
    environment.teleport_forbidden = true;
    host.run_console("move");
    check(environment.logged("the host has turned teleporting off"), "and none when the host says no");
    host.run_console("bad");
    check(environment.logged("bad refused"), "Bad positions are refused\n" + environment.all());
    ticks(host, environment, 1); // the count is per tick: the probes above used two
    host.run_console("rays");
    check(environment.logged("rays 64 more than 64 raycasts"), "64 raycasts a tick\n" + environment.all());
    ticks(host, environment, 1);
    host.run_console("probe");
    running(host, "mover", environment);
}

void tricks_reach_plugins_that_ask_for_them() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({script("bare", R"(
        local ok, err = pcall(reskate.on, "trick_landed", function() end)
        assert(not ok and err:find("events.tricks"), err)
        ok, err = pcall(reskate.score.current)
        assert(not ok and err:find("events.tricks"), err)
        reskate.log("bare refused")
    )")});
    check(environment.logged("bare refused") && !host.wants_tricks(), "Tricks need events.tricks, and none is read without it");
    host.apply({script("bare", "reskate.log('bare')"), script("tricks", R"(
        reskate.on("trick_started", function(e) reskate.log(string.format("started at %.0f %.0f %.0f", e.at.x, e.at.y, e.at.z)) end)
        reskate.on("trick_landed", function(e)
            reskate.log(string.format("landed %.0f x%.1f from %.0f to %.0f in %.0f ms", e.points, e.multiplier, e.started_at.x, e.landed_at.x, e.ms))
        end)
        reskate.on("line_broken", function(e) reskate.log("broken by a " .. e.reason) end)
        reskate.console.command("score", "x", function()
            local s = reskate.score.current()
            reskate.log(s and string.format("score %.0f x%.1f %s", s.line, s.multiplier, tostring(s.in_trick)) or "no score")
        end)
    )", R"("console", "events.tricks")")});
    check(host.wants_tricks(), "With a plugin that has events.tricks, tricks are read");
    TrickNews started;
    started.started_at = {1, 2, 3};
    host.trick(started);
    TrickNews landed;
    landed.kind = TrickNews::Kind::landed;
    landed.points = 250;
    landed.multiplier = 2;
    landed.started_at = {1, 2, 3};
    landed.landed_at = {9, 2, 3};
    landed.ms = 1200;
    host.trick(landed);
    TrickNews broken;
    broken.kind = TrickNews::Kind::broken;
    broken.reason = "slam";
    host.trick(broken);
    check(environment.logged("[tricks] started at 1 2 3") && environment.logged("[tricks] landed 250 x2.0 from 1 to 9 in 1200 ms") &&
          environment.logged("[tricks] broken by a slam"), "Each trick event reaches it, with its values\n" + environment.all());
    host.run_console("score");
    environment.score_now = ScoreState{708, 2, true};
    host.run_console("score");
    check(environment.logged("[tricks] no score") && environment.logged("[tricks] score 708 x2.0 true"), "and the score HUD as it is");
    running(host, "tricks", environment);
}

void level_events_reach_plugins() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({script("levels", R"(
        assert(reskate.level.current() == nil)
        reskate.on("level_loaded", function(name) reskate.log("loaded " .. name .. " / " .. reskate.level.current().name) end)
        reskate.on("level_unloading", function(name) reskate.log("leaving " .. name) end)
    )")});
    host.set_level("sanvan");
    host.set_level("park");
    host.set_level("");
    check(environment.logged("loaded sanvan / sanvan") && environment.logged("leaving sanvan") &&
          environment.logged("loaded park / park") && environment.logged("leaving park"), "Level events in order\n" + environment.all());
}

void timers_run_when_due() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({script("clock", R"(
        local count = 0
        reskate.time.after(100, function() reskate.log("once") end)
        local id = reskate.time.every(50, function() count += 1 end)
        reskate.time.after(260, function() reskate.time.cancel(id) reskate.log("every ran " .. count) end)
        local cancelled = reskate.time.after(10, function() reskate.log("never") end)
        reskate.time.cancel(cancelled)
    )")});
    ticks(host, environment, 30);
    check(environment.logged("[clock] once") && environment.logged("every ran 5") && !environment.logged("never"),
        "Timers run once, repeat, and stop when cancelled\n" + environment.all());
}

void a_package_with_a_program_or_on_the_wrong_side_is_not_loaded() {
    const auto clean = package("clean", {{manifest_file, manifest()}, {"plugin/main.lua", ""}, {"readme.txt", "A plain file"}});
    check(check_package_files(clean.directory).empty(), "A plain package passes");
    const auto named = package("named", {{manifest_file, manifest()}, {"plugin/main.lua", ""}, {"tools/helper.DLL", "x"}});
    check(check_package_files(named.directory).find("tools/helper.dll") != std::string::npos, "A DLL by its name is refused");
    const auto hidden = package("hidden", {{manifest_file, manifest()}, {"plugin/main.lua", ""}, {"data/picture.png", "MZ\x90\x00"}});
    check(!check_package_files(hidden.directory).empty(), "and a program by its first bytes, whatever its name");
    const auto script_file = package("batch", {{manifest_file, manifest()}, {"plugin/main.lua", ""}, {"run.bat", "echo"}});
    check(!check_package_files(script_file.directory).empty(), "and a script Windows runs");

    TestEnvironment environment;
    Host host(environment, Side::client);
    auto refused = hidden;
    refused.refusal = check_package_files(hidden.directory);
    host.apply({refused, package("server_only", {{manifest_file, manifest("", "server")}, {"plugin/main.lua", ""}}),
        package("broken", {{manifest_file, "{"}, {"plugin/main.lua", ""}}),
        package("syntax", {{manifest_file, manifest()}, {"plugin/main.lua", "local = 1"}})});
    stopped(host, "hidden", "a program");
    check(status_of(host, "server_only").state == "not loaded", "A server plugin waits for a server");
    stopped(host, "broken", "not valid JSON");
    stopped(host, "syntax", "plugin/main.lua");
}

void discovery_finds_the_plugins_among_the_mods_left_on() {
    const auto root = scratch / "game";
    const auto mods = root / "Mods";
    write(mods / "Alpha" / manifest_file, manifest());
    write(mods / "Alpha" / "plugin" / "main.lua", "");
    write(mods / "Beta" / manifest_file, manifest());
    write(mods / "Beta" / "plugin" / "main.lua", "");
    write(mods / "Beta" / "bin" / "tool.exe", "x");
    write(mods / "Gamma" / manifest_file, manifest());
    write(mods / "Gamma" / "plugin" / "main.lua", "");
    write(mods / "JustAMap" / "manifest.json", R"({"name": "Just a map"})");
    check(dingosdk::mods::set_mod_enabled(mods, "Gamma", false), "mods.json switches a mod off");
    FileChecks checks;
    auto found = discover(root, checks);
    std::sort(found.begin(), found.end(), [](const auto& a, const auto& b) { return a.id < b.id; });
    check(found.size() == 2 && found[0].id == "Alpha" && found[1].id == "Beta", "Only the plugins left on are found");
    check(found[0].refusal.empty() && found[1].refusal.find("bin/tool.exe") != std::string::npos, "with what their files say");
    const auto before = found[0].fingerprint;
    write(mods / "Alpha" / "plugin" / "main.lua", "-- changed");
    auto again = discover(root, checks);
    std::sort(again.begin(), again.end(), [](const auto& a, const auto& b) { return a.id < b.id; });
    check(again[0].fingerprint != before, "A changed file changes the fingerprint, which reloads it");
}

// contrib/plugins, as they are in the repository.
Candidate contributed(const std::string& id) {
    const auto directory = std::filesystem::path(RESKATE_SOURCE_DIR) / "contrib" / "plugins" / id;
    return {id, directory, "1", check_package_files(directory)};
}
std::string printed_lines(const TestEnvironment& environment) {
    std::string text;
    for (const auto& line : environment.lines) text += line + "\n";
    return text;
}

void reskate_test_passes_its_own_checks() {
    TestEnvironment environment;
    std::filesystem::remove_all(scratch / "storage" / "reskate-test");
    Host host(environment, Side::client);
    host.apply({contributed("reskate-test")});
    running(host, "reskate-test", environment);
    ticks(host, environment, 5);
    check(host.run_console("ptest all"), "ptest is its command");
    ticks(host, environment, 120); // its later checks run on timers
    const auto output = printed_lines(environment);
    check(output.find("ptest: 47 passed, 0 failed, 7 skipped") != std::string::npos &&
          output.find("FAIL") == std::string::npos, "Every reskate-test check passes:\n" + output + environment.all());
    check(host.run_console("ptest echo a b") && environment.printed("ptest echo: a b"), "and its echo command answers");
    // With a skater on the ground, its skater checks run too.
    environment.local_skater = SkaterState{{5, 0, 5}, 0.3f, 2, 0, false, 0};
    environment.lines.clear();
    check(host.run_console("ptest skater"), "ptest skater");
    check(environment.printed("ptest: 5 passed, 0 failed (skater 5/5)"), "The skater checks pass with a skater:\n" + printed_lines(environment));
    check(host.run_console("ptest tp") && environment.teleports.size() == 1, "and ptest tp teleports it");
    // ptest trick waits for a trick and says what its events held.
    check(host.run_console("ptest trick"), "ptest trick");
    host.trick({TrickNews::Kind::started, 0, 0, {1, 0, 1}});
    host.trick({TrickNews::Kind::landed, 250, 2, {1, 0, 1}, {4, 0, 5}, 900});
    check(environment.printed("ptest trick: started at (1.0, 0.0, 1.0)") &&
          environment.printed("ptest trick: landed 250 points at x2.0 in 900 ms, from (1.0, 0.0, 1.0) to (4.0, 0.0, 5.0)"),
          "ptest trick prints the trick");
    environment.lines.clear();
    check(host.run_console("ptest tricks") && environment.printed("ptest: 3 passed, 0 failed, 1 skipped"),
          "and the tricks checks pass once one landed");
    running(host, "reskate-test", environment);
}

void reskate_hostile_is_stopped_or_refused_every_time() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({contributed("reskate-hostile")});
    running(host, "reskate-hostile", environment);
    struct Case { const char* attack; bool stops; int ticks; bool trick = false; };
    const Case cases[] = {
        {"loop", true, 0}, {"caught-loop", true, 0}, {"tick-loop", true, 1}, {"memory", true, 0},
        {"caught-memory", true, 0}, {"huge-string", true, 0}, {"buffer", true, 0}, {"coroutines", true, 0},
        {"errors", true, 10}, {"slow", true, 60 * 5}, {"log-flood", false, 1}, {"timers", false, 1},
        {"recursion", false, 1}, {"escapes", false, 1}, {"teleport-spam", false, 1}, {"raycast-flood", false, 1}, {"trick-loop", true, 0, true},
    };
    for (const auto& item : cases) {
        check(host.reload("reskate-hostile").find("reloaded") != std::string::npos, std::string("reloads before ") + item.attack);
        check(host.run_console(std::string("hostile ") + item.attack), item.attack);
        ticks(host, environment, item.ticks);
        if (item.trick) host.trick({TrickNews::Kind::landed, 100, 1});
        const auto state = status_of(host, "reskate-hostile").state;
        check((state == "stopped") == item.stops, std::string(item.attack) + " left it " + state + "\n" + environment.all());
    }
    check(!environment.printed("ESCAPED"), "No attack got out:\n" + printed_lines(environment));
    check(environment.printed("hostile escapes: 17 of 17 refused"), "and every escape was tried\n" + printed_lines(environment));
    host.reload("reskate-hostile");
    host.run_console("hostile slow-unload");
    const auto start = std::chrono::steady_clock::now();
    host.switch_off("reskate-hostile");
    const auto ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    check(ms < 500, "An unload handler that never ends is cut off: " + std::to_string(ms) + " ms");
}
}

int main() {
    try {
        scratch = std::filesystem::temp_directory_path() / "reskate-plugin-tests";
        std::filesystem::remove_all(scratch);
        a_manifest_says_what_a_plugin_is();
        a_plugin_loads_ticks_runs_its_commands_and_unloads();
        a_permission_must_be_declared();
        command_names_are_checked();
        storage_is_kept_across_loads_and_has_a_quota();
        packages_json_and_modules();
        the_sandbox_holds();
        a_runaway_plugin_is_stopped_and_the_rest_go_on();
        a_plugin_over_its_time_budget_is_stopped();
        a_plugin_reloads_when_its_files_change();
        the_skater_can_be_read_moved_and_the_world_probed();
        tricks_reach_plugins_that_ask_for_them();
        level_events_reach_plugins();
        timers_run_when_due();
        a_package_with_a_program_or_on_the_wrong_side_is_not_loaded();
        discovery_finds_the_plugins_among_the_mods_left_on();
        reskate_test_passes_its_own_checks();
        reskate_hostile_is_stopped_or_refused_every_time();
        std::filesystem::remove_all(scratch);
        std::cout << "plugin tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "plugin test failed: " << error.what() << '\n';
        return 1;
    }
}
