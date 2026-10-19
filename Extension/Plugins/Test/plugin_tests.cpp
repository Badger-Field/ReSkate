// The plugin host apart from the game: manifests, the reskate API, storage, modules, hot reload,
// and the sandbox against plugins written to break it (loops, memory, error floods, escapes).
#include "Extension/Plugins/plugin_discovery.h"
#include "Extension/Plugins/plugin_host.h"
#include "Extension/Plugins/plugin_manifest.h"
#include "Extension/Plugins/plugin_wire.h"
#include "Engine/Vfs/mod_list.h"
#include <algorithm>
#include <chrono>
#include <format>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>

using namespace dingosdk::plugins;
namespace plugin_ui = dingosdk::plugin_ui;
namespace {
void check(bool value, const std::string& message) { if (!value) throw std::runtime_error(message); }

std::filesystem::path scratch;


// The wider set's game, faked: it records what plugins asked of it and answers as told.
struct FakeGame final : Game {
    std::optional<std::uint32_t> state;
    std::uint64_t wipe{};
    std::vector<std::string> calls;
    std::vector<ChatLine> lines;
    Track now_playing;
    ThrowdownState throwdown;
    std::vector<std::pair<std::string, std::string>> presses;
    std::map<std::string, std::uint32_t> binds;
    std::vector<std::string> released;
    std::optional<std::uint32_t> physics_state() override { return state; }
    std::uint64_t wipeouts() override { return wipe; }
    std::string outfit_set(std::string_view plugin, std::string_view slot, std::string_view item, std::vector<float> placement, bool covers) override {
        calls.push_back(std::format("outfit {} {} {} {} {}", plugin, slot, item, placement.size(), covers));
        return {};
    }
    void outfit_clear(std::string_view plugin, std::string_view slot) override { calls.push_back(std::format("outfit clear {} {}", plugin, slot)); }
    std::set<std::string, std::less<>> installed; // items found by their whole ending, as Pack/<ending>
    std::string outfit_find(std::string_view ending) override {
        if (installed.contains(ending)) return "Pack/" + std::string(ending);
        return ending == "roadrash_leg1" ? "RoadRash_Leg1" : "";
    }
    std::vector<OutfitSlot> outfit_slots() override { return {{"tattoo_legl", "", {0.5f, 1}}, {"moles", "moles_empty", {}}}; }
    std::optional<std::array<BoardBone, 16>> board_bones() override {
        std::array<BoardBone, 16> bones{};
        bones[3].position = {0, 0.1f, 0};
        return bones;
    }
    std::string board_set(std::string_view plugin, int bone, const BoardBone& value, bool world) override {
        calls.push_back(std::format("board {} {} {:.1f} {}", plugin, bone, value.position[1], world ? "world" : "local"));
        return {};
    }
    std::string board_hide(std::string_view plugin, int bone) override { calls.push_back(std::format("board hide {} {}", plugin, bone)); return {}; }
    void board_park(std::string_view plugin, std::vector<int> bones) override { calls.push_back(std::format("board park {} {}", plugin, bones.size())); }
    std::string board_toss(std::string_view plugin, std::vector<int> bones, const Vec3& velocity, const Vec3&, float ground) override {
        calls.push_back(std::format("board toss {} {} {:.0f} {:.0f}", plugin, bones.size(), velocity[1], ground));
        return {};
    }
    SkaterNews news;
    SkaterNews take_skater_news() override { return std::exchange(news, {}); }
    std::vector<PhysicsStep> steps;
    std::vector<PhysicsStep> take_physics_steps() override { return std::exchange(steps, {}); }
    std::string xray(std::string_view plugin, bool on) override {
        calls.push_back(std::format("xray {} {}", plugin, on ? "on" : "off"));
        return {};
    }
    std::string hud_corner(std::string_view plugin, bool hidden) override {
        calls.push_back(std::format("corner {} {}", plugin, hidden ? "on" : "off"));
        return {};
    }
    std::string game_speed(std::string_view plugin, float speed) override {
        calls.push_back(std::format("slow {} {:.2f}", plugin, speed));
        return {};
    }
    std::string grind_exit(std::string_view plugin, std::optional<GrindExitWish> wish) override {
        calls.push_back(wish ? std::format("grind {} {} {} {:.2f}", plugin, wish->side, wish->stance, wish->strength) : std::format("grind {} none", plugin));
        return {};
    }
    std::vector<GrindPop> pops;
    std::vector<GrindPop> take_grind_pops(std::string_view) override { return std::exchange(pops, {}); }
    std::string hide_pad(std::string_view plugin, bool lt, bool rt, bool left_x) override {
        calls.push_back(std::format("hide {} {} {} {}", plugin, lt, rt, left_x));
        return {};
    }
    std::optional<Vec3> board_velocity() override { return Vec3{1, -2, 3}; }
    std::optional<SlamNews> last_slam() override { return SlamNews{7.5f, 12, 100}; }
    std::string wipeout(float force) override {
        calls.push_back(std::format("wipeout {:.1f}", force));
        return {};
    }
    std::string control(std::string_view action) override { calls.push_back("control " + std::string(action)); return {}; }
    void hide_hud(std::string_view plugin, bool hidden) override { calls.push_back(std::string("hud ") + std::string(plugin) + (hidden ? " on" : " off")); }
    std::vector<CatalogItem> object_catalog() override { return {{"bench_01", "Bench", "Benches"}}; }
    std::vector<GameObject> objects() override { return {{42, "bench_01", {1, 0, 2}, {0, 0, 0, 1}, 1}}; }
    std::string edit_object(std::string_view op, const GameObject& o) override {
        calls.push_back(std::format("{} {} {} {:.1f}", op, o.id, o.item, o.position[0]));
        return {};
    }
    std::string set_environment(std::string_view kind, std::string_view key, std::string_view value) override {
        calls.push_back(std::format("env {} {} {}", kind, key, value));
        return key == "never" ? "refused" : "";
    }
    std::vector<Destination> destinations() override { return {{"bam", "BAM", "bam"}}; }
    std::vector<LevelInfo> levels() override { return {{"Levels/Game/X/X", "X", true}}; }
    std::string travel(std::string_view kind, std::string_view target) override { calls.push_back(std::format("travel {} {}", kind, target)); return {}; }
    std::string ai(std::string_view op, int count) override { calls.push_back(std::format("ai {} {}", op, count)); return {}; }
    int ai_count() override { return 3; }
    std::vector<PeerSkater> peer_skaters() override { return {{7, {1, 2, 3}, 0.5f, 40}}; }
    void set_nametag(std::string_view plugin, std::uint64_t player, std::string text, std::uint32_t colour) override {
        calls.push_back(std::format("tag {} {} {} {:08x}", plugin, player, text, colour));
    }
    std::string voice_volume(std::uint64_t player, float volume) override { calls.push_back(std::format("voice {} {:.1f}", player, volume)); return {}; }
    std::vector<ChatLine> chat_since(std::uint64_t sequence) override {
        std::vector<ChatLine> result;
        for (const auto& line : lines) if (line.sequence > sequence) result.push_back(line);
        return result;
    }
    std::string send_chat(std::string_view text) override { calls.push_back("chat " + std::string(text)); return {}; }
    std::string local_chat(std::string_view plugin, std::string_view text, std::uint32_t colour) override {
        calls.push_back(std::format("notice {} {} {:08x}", plugin, text, colour));
        return {};
    }
    ThrowdownState throwdowns() override { return throwdown; }
    ClipRef recorded;
    std::string play_clip(std::string_view plugin, ClipRef clip, float speed, float blend, bool loop) override {
        calls.push_back(std::format("play {} {} frames {:.1f} {:.2f} {}", plugin, clip->times.size(), speed, blend, loop));
        return {};
    }
    void stop_clip(std::string_view plugin) override { calls.push_back("stop " + std::string(plugin)); }
    std::string replace_trick(std::string_view plugin, std::string trick, std::string pop, ClipRef clip, float) override {
        calls.push_back(std::format("replace {} {} {} {}", plugin, trick, pop.empty() ? "any" : pop, clip ? "clip" : "none"));
        return {};
    }
    std::string record_clip(std::string_view plugin, float seconds) override {
        calls.push_back(std::format("record {} {:.1f}", plugin, seconds));
        auto clip = std::make_shared<dingosdk::anim_studio::Clip>();
        clip->joints = dingosdk::anim_studio::skeleton_joints;
        clip->times = {0, 0.25f, 0.5f};
        clip->frames.resize(clip->times.size() * clip->joints);
        recorded = std::move(clip);
        return {};
    }
    ClipRef take_recording(std::string_view) override { return std::exchange(recorded, nullptr); }
    std::string current_trick() override { return "kickflip"; }
    std::optional<FlightInput> flight_input(std::string_view) override { return FlightInput{1, 0, 0.5f, 3, 4, true}; }
    std::optional<PadInput> pad = PadInput{0.25f, 0, 0, 0, 0, 1, 0x1000, false};
    std::optional<PadInput> gamepad() override { return pad; }
    std::optional<StickInput> joystick() override {
        StickInput stick;
        stick.axes = {0, -1, 0.5f, 0, 0, 0};
        stick.buttons = 0x4;
        stick.name = "Radio";
        return stick;
    }
    std::string capture_input(std::string_view plugin, bool on) override {
        calls.push_back(std::format("capture {} {}", plugin, on ? "on" : "off"));
        return {};
    }
    std::uint64_t sound_ids{};
    std::uint64_t play_sound(std::string_view plugin, SoundStart sound) override {
        calls.push_back(std::format("sound {} {} {:.2f} {:.2f} {}{}", plugin, sound.wave ? std::to_string(sound.wave->samples.size()) : "motor", sound.volume,
                                    sound.pitch, sound.loop, sound.position ? std::format(" at {} {} {}", (*sound.position)[0], (*sound.position)[1], (*sound.position)[2]) : ""));
        return ++sound_ids;
    }
    bool change_sound(std::string_view plugin, std::uint64_t id, const SoundChange& change) override {
        calls.push_back(std::format("change {} {} {:.1f}{}", plugin, id, change.throttle.value_or(-1),
                                    change.position && !*change.position ? " skater" : ""));
        return true;
    }
    void stop_sound(std::string_view plugin, std::uint64_t id) override { calls.push_back(std::format("stop sound {} {}", plugin, id)); }
    bool sound_playing(std::string_view, std::uint64_t id) override { return id <= sound_ids; }
    std::string host_throwdown(std::string_view plugin, std::string_view series, std::vector<std::uint64_t> players, int most) override {
        calls.push_back(std::format("host {} {} {} players max {}", plugin, series, players.size(), most));
        return {};
    }
    std::string throwdown_status(std::string_view) override { return "open"; }
    std::string kick(std::uint64_t player, std::string_view why) override { calls.push_back(std::format("kick {} {}", player, why)); return {}; }
    std::string announce(std::string_view plugin, std::string_view text) override {
        calls.push_back(std::format("announce {} {}", plugin, text));
        return {};
    }
    std::optional<CameraView> camera_view() override { return CameraView{{1, 2, 3}, {0, 0, 1}, 60}; }
    std::string set_camera(std::string_view plugin, const std::array<float, 16>& pose, float fov) override {
        calls.push_back(std::format("camera {} {:.1f} {:.1f} {:.1f} back {:.2f} {:.2f} {:.2f} fov {:.0f}", plugin, pose[12], pose[13], pose[14],
                                    pose[8], pose[9], pose[10], fov));
        return {};
    }
    void release_camera(std::string_view plugin) override { calls.push_back("camera release " + std::string(plugin)); }
    std::string freeze(std::string_view plugin, bool on) override {
        calls.push_back(std::format("freeze {} {}", plugin, on ? "on" : "off"));
        return {};
    }
    Track track() override { return now_playing; }
    std::string music(std::string_view command) override { calls.push_back("music " + std::string(command)); return {}; }
    Progress progress() override { return {true, {{"historic", "Historic", 4}}, {{"c1", "line", "historic", 2, 1, 3}}, 1500, 9000, 2}; }
    std::optional<std::string> setting(std::string_view name) override { return name == "Render.Fov" ? std::optional<std::string>("90") : std::nullopt; }
    std::vector<std::string> setting_names() override { return {"Render.Fov"}; }
    std::string bind(std::string_view plugin, std::string_view action, std::uint32_t combo) override {
        binds[std::string(plugin) + "/" + std::string(action)] = combo;
        return {};
    }
    std::vector<std::pair<std::string, std::string>> take_presses() override { return std::exchange(presses, {}); }
    void release(std::string_view plugin) override { released.emplace_back(plugin); }
    bool called(std::string_view part) const {
        return std::any_of(calls.begin(), calls.end(), [&](const auto& c) { return c.find(part) != std::string::npos; });
    }
};

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
    std::vector<std::string> toasts, sounds;
    std::vector<ModInfo> mod_list;
    SessionState session_now;
    std::vector<std::vector<std::uint8_t>> sent;
    std::vector<Received> incoming;
    std::vector<plugin_ui::Layer> layers;
    std::vector<plugin_ui::Panel> panels;
    std::vector<plugin_ui::Action> actions;
    bool panels_open{};
    void toast(std::string_view plugin, LogLevel, std::string_view text) override { toasts.push_back(std::string(plugin) + ": " + std::string(text)); }
    bool ui_sound(std::string_view name) override {
        if (name != "select" && name != "cancel" && name != "navigate" && name != "toggle" && name != "tab") return false;
        sounds.emplace_back(name);
        return true;
    }
    std::vector<ModInfo> mods() override { return mod_list; }
    SessionState session() override { return session_now; }
    bool send_message(std::vector<std::uint8_t> bytes) override {
        sent.push_back(std::move(bytes));
        return true;
    }
    std::vector<Received> take_messages() override { return std::exchange(incoming, {}); }
    // A camera at the origin looking down -Z, as the game's matrix has it: rows right, up, back.
    std::optional<Vec3> world_to_screen(const Vec3& p) override {
        if (p[2] >= -0.05f) return std::nullopt;
        const float depth = -p[2];
        return Vec3{960 + p[0] * 1000 / depth, 540 - p[1] * 1000 / depth, depth};
    }
    void publish_layers(std::vector<plugin_ui::Layer> value) override { layers = std::move(value); }
    bool panels_shown() override { return panels_open; }
    void publish_panels(std::vector<plugin_ui::Panel> value) override { panels = std::move(value); }
    std::vector<plugin_ui::Action> take_actions() override { return std::exchange(actions, {}); }
    std::vector<HttpRequest> requests;
    std::vector<HttpResponse> responses;
    bool http_request(HttpRequest request) override {
        requests.push_back(std::move(request));
        return true;
    }
    std::vector<HttpResponse> take_http_responses() override { return std::exchange(responses, {}); }
    FakeGame fake;
    Game& game() override { return fake; }
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

void plugins_draw_pictures() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    const std::string main = R"(
        reskate.on("draw", function(d)
            d:image("icons/flag.png", vector.create(0, 0, 0), vector.create(64, 64, 0), {colour = 0xffffff80})
            d:world_image("icons/flag.png", vector.create(0, 2, -10), 1.5, {uv = {0, 0, 0.5, 0.25}})
            assert(not pcall(d.image, d, "icons/flag.png", vector.create(0, 0, 0), vector.create(1, 1, 0), {uv = {0, 0, 2, 1}}), "uv 0 to 1")
            assert(not pcall(d.image, d, "notes.txt", vector.create(0, 0, 0), vector.create(1, 1, 0)), "only pictures")
            assert(not pcall(d.image, d, "../other/flag.png", vector.create(0, 0, 0), vector.create(1, 1, 0)), "only its own package")
            assert(not pcall(d.image, d, "icons/missing.png", vector.create(0, 0, 0), vector.create(1, 1, 0)), "only files there")
            assert(not pcall(d.world_image, d, "icons/flag.png", vector.create(0, 0, 0), -1), "a height")
            reskate.log("drew")
        end)
    )";
    host.apply({package("pictures", {{manifest_file, manifest(R"("overlay")")}, {"plugin/main.lua", main}, {"icons/flag.png", "not really a png"},
                                     {"notes.txt", "x"}})});
    ticks(host, environment, 1);
    running(host, "pictures", environment);
    check(environment.logged("[pictures] drew") && environment.layers.size() == 1 && environment.layers[0].shapes.size() == 2,
          "A plugin draws pictures from its package\n" + environment.all());
    const auto& picture = environment.layers[0].shapes[0];
    check(picture.kind == plugin_ui::Shape::Kind::image && picture.text.ends_with("flag.png") && picture.style.colour == 0x80ffffff,
          "as the picture's file, tinted");
    check(environment.layers[0].shapes[1].kind == plugin_ui::Shape::Kind::world_image && environment.layers[0].shapes[1].radius == 1.5f &&
              environment.layers[0].shapes[1].style.uv[2] == 0.5f && environment.layers[0].shapes[1].style.uv[3] == 0.25f,
          "and in the world, so many metres tall, a part of it");
}

void plugins_draw_on_the_overlay() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({script("painter", R"(
        local saved = nil
        reskate.on("draw", function(d)
            saved = d
            d:text(vector.create(10, 20, 0), "hello", {colour = "#ff8000", size = 24, align = "centre"})
            d:rect(vector.create(0, 0, 0), vector.create(100, 50, 0), {colour = 0x00ff00ff, fill = true, rounding = 4})
            d:line(vector.create(0, 0, 0), vector.create(5, 5, 0))
            d:circle(vector.create(50, 50, 0), 10)
            d:poly({vector.create(0, 0, 0), vector.create(10, 0, 0), vector.create(0, 10, 0)}, {fill = true})
            d:world_text(vector.create(0, 1, -10), "over there")
            d:world_line(vector.create(0, 0, -5), vector.create(1, 0, -5), {thickness = 3})
            d:world_circle(vector.create(0, 0, -5), 2)
            local on = d:world_to_screen(vector.create(1, 0, -10))
            assert(on and math.abs(on.x - 1060) < 0.01 and math.abs(on.z - 10) < 0.01, "world_to_screen")
            assert(d:world_to_screen(vector.create(0, 0, 5)) == nil, "behind the camera")
            assert(d:size().x == 1920 and d:size().y == 1080)
            assert(not pcall(d.text, d, vector.create(0, 0, 0), "x", {colour = "red"}), "a colour by name")
            assert(not pcall(d.text, d, vector.create(0 / 0, 0, 0), "x"), "NaN")
            assert(not pcall(d.poly, d, {vector.create(0, 0, 0)}), "a polygon of one point")
            assert(not pcall(d.text, d, vector.create(0, 0, 0), string.rep("x", 600)), "a long text")
        end)
        reskate.console.command("outside", "x", function()
            local ok, err = pcall(saved.text, saved, vector.create(0, 0, 0), "x")
            reskate.log("outside " .. tostring(ok) .. " " .. tostring(err):match("only inside a draw handler"))
        end)
        reskate.console.command("toasts", "x", function()
            local shown = 0
            for i = 1, 10 do if reskate.ui.toast("toast " .. i) then shown += 1 end end
            reskate.log("toasts " .. shown)
        end)
    )", R"("console", "overlay")"), script("unseen", R"(
        local ok, err = pcall(reskate.on, "draw", function() end)
        assert(not ok and err:find("overlay"), err)
        ok, err = pcall(reskate.ui.toast, "x")
        assert(not ok and err:find("overlay"), err)
        reskate.log("unseen refused")
    )")});
    running(host, "painter", environment);
    check(environment.logged("unseen refused"), "Drawing needs overlay\n" + environment.all());
    ticks(host, environment, 1);
    running(host, "painter", environment);
    check(environment.layers.size() == 1 && environment.layers[0].plugin == "painter" && environment.layers[0].shapes.size() == 8,
          "Its shapes are handed to the overlay each tick\n" + environment.all());
    const auto& first = environment.layers[0].shapes[0];
    check(first.kind == plugin_ui::Shape::Kind::text && first.text == "hello" && first.style.colour == 0xff0080ff &&
          first.style.size == 24 && first.style.align == 1, "with their style (colours as ImGui keeps them)");
    check(environment.layers[0].shapes[1].style.colour == 0xff00ff00 && environment.layers[0].shapes[1].style.fill, "a colour number too");
    host.run_console("outside");
    check(environment.logged("outside false only inside a draw handler"), "Drawing outside a draw handler is refused\n" + environment.all());
    host.run_console("toasts");
    check(environment.logged("toasts 3") && environment.toasts.size() == 3 && environment.toasts[0] == "painter: toast 1", "Three toasts in 5 s");
    environment.now += 5000;
    host.run_console("toasts");
    check(environment.toasts.size() == 6, "and three more after");
}

void plugins_have_menu_panels() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({script("panels", R"(
        local state = {on = false, speed = 5, mode = 1, presses = 0}
        reskate.menu.panel("Settings", function(ui)
            ui:heading("Settings")
            ui:label("Some words")
            ui:separator()
            if ui:button("go", "Go") then state.presses += 1 end
            state.on = ui:toggle("on", "Switch", state.on)
            state.speed = ui:slider("speed", "Speed", state.speed, 0, 10)
            state.mode = ui:dropdown("mode", "Mode", {"one", "two", "three"}, state.mode)
            ui:bar("Throttle", 0.25, 0, 1)
            assert(not pcall(ui.bar, ui, "Bad", 0, 1, 0), "a bar's range")
            assert(not pcall(ui.button, ui, "go", "Again"), "two widgets with one id")
        end)
        assert(not pcall(reskate.menu.panel, "Settings", function() end), "two panels with one title")
        reskate.console.command("state", "x", function()
            reskate.log(string.format("state %d %s %.1f %d", state.presses, tostring(state.on), state.speed, state.mode))
        end)
    )", R"("console", "menu")")});
    running(host, "panels", environment);
    ticks(host, environment, 2);
    check(environment.panels.empty(), "Panels are built only while the PLUGINS tab shows them");
    environment.panels_open = true;
    ticks(host, environment, 10);
    check(environment.panels.size() == 1 && environment.panels[0].title == "Settings" && environment.panels[0].widgets.size() == 8 &&
              environment.panels[0].widgets[7].kind == plugin_ui::Widget::Kind::bar && environment.panels[0].widgets[7].value == 0.25f,
          "Then its panel is built\n" + environment.all());
    using A = plugin_ui::Action;
    environment.actions = {A{"panels", "Settings", "go"}, A{"panels", "Settings", "on", true}, A{"panels", "Settings", "speed", false, 99},
                           A{"panels", "Settings", "mode", false, 0, 3}, A{"someone", "Settings", "go"}};
    ticks(host, environment, 10);
    host.run_console("state");
    check(environment.logged("state 1 true 10.0 3"), "What the player did reaches it, the slider held in its range\n" + environment.all());
    const auto& widgets = environment.panels[0].widgets;
    check(widgets[4].on && widgets[5].value == 10 && widgets[6].chosen == 3 && widgets[6].choices.size() == 3, "and shows in the next build");
}

// A 16-bit mono WAVE file of `samples` at `rate`.
std::string wave_file(unsigned rate, std::size_t samples) {
    std::string out = "RIFF";
    const auto put = [&](std::uint32_t v, int bytes) {
        for (int i = 0; i < bytes; ++i) out.push_back(static_cast<char>((v >> (8 * i)) & 0xff));
    };
    put(static_cast<std::uint32_t>(36 + samples * 2), 4);
    out += "WAVEfmt ";
    put(16, 4);
    put(1, 2);
    put(1, 2);
    put(rate, 4);
    put(rate * 2, 4);
    put(2, 2);
    put(16, 2);
    out += "data";
    put(static_cast<std::uint32_t>(samples * 2), 4);
    for (std::size_t i = 0; i < samples; ++i) put(static_cast<std::uint32_t>(static_cast<std::int16_t>((i % 20) * 1000)) & 0xffff, 2);
    return out;
}

void plugins_play_their_own_sounds() {
    std::string error;
    const auto bytes = wave_file(8000, 4000);
    const auto wave = decode_wave(std::span<const std::uint8_t>(reinterpret_cast<const std::uint8_t*>(bytes.data()), bytes.size()), 30, error);
    check(wave && wave->samples.size() == 8000, "Half a second at 8 kHz is half a second at 16 kHz: " + error);
    check(!decode_wave(std::span<const std::uint8_t>(reinterpret_cast<const std::uint8_t*>(bytes.data()), bytes.size()), 0.25f, error),
          "and a sound longer than allowed is refused");
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({package("noisy", {{manifest_file, manifest(R"("console", "audio")")}, {"sounds/beep.wav", bytes}, {"notes.txt", "x"},
                                  {"plugin/main.lua", R"(
        reskate.console.command("noise", "x", function()
            local beep = assert(reskate.audio.play("sounds/beep.wav", {volume = 0.5, pitch = 2, loop = true, position = vector.create(1, 2, 3)}))
            local motor = assert(reskate.audio.play("motor", {throttle = 0.4, speed = 10}))
            assert(reskate.audio.set(motor, {throttle = 0.9, position = "skater"}))
            assert(reskate.audio.playing(beep))
            reskate.audio.stop(beep)
            assert(not pcall(reskate.audio.play, "notes.txt"), "a wave")
            assert(not pcall(reskate.audio.play, "../x/beep.wav"), "its own package")
            assert(not pcall(reskate.audio.play, "sounds/beep.wav", {volume = 3}), "a sane volume")
            reskate.log("sounded")
        end)
    )"}})});
    running(host, "noisy", environment);
    host.run_console("noise");
    check(environment.logged("[noisy] sounded"), "A plugin plays its own sounds\n" + environment.all());
    for (const auto* call : {"sound noisy 8000 0.50 2.00 true at 1 2 3", "sound noisy motor 1.00 1.00 false", "change noisy 2 0.9 skater", "stop sound noisy 1"})
        check(environment.fake.called(call), std::string("The game was asked: ") + call + "\n" + environment.all());
}

void plugins_play_sounds_and_read_the_mods() {
    TestEnvironment environment;
    environment.mod_list = {{"Owner-Map", "A Map", true}, {"OldThing", "Old Thing", false}};
    Host host(environment, Side::client);
    host.apply({script("listener", R"(
        reskate.console.command("sounds", "x", function()
            local played = 0
            for i = 1, 20 do if reskate.audio.play_ui("select") then played += 1 end end
            reskate.log("played " .. played)
            assert(not pcall(reskate.audio.play_ui, "explosion"), "a sound there is not")
        end)
        reskate.console.command("mods", "x", function()
            reskate.log(string.format("mods %s %s %s %s %d", tostring(reskate.mods.installed("a map")), tostring(reskate.mods.installed("OWNER-MAP")),
                tostring(reskate.mods.installed("Old Thing")), tostring(reskate.mods.installed("nothing")), #reskate.mods.list()))
        end)
    )", R"("console", "audio.ui", "mods.read")")});
    host.run_console("sounds");
    check(environment.logged("played 10") && environment.sounds.size() == 10, "Ten sounds a second\n" + environment.all());
    host.run_console("mods");
    check(environment.logged("mods on on off nil 2"), "Mods by folder or title, on or off\n" + environment.all());
}

// Two games in one session, each with the same plugin, passing messages through their hosts.
void plugins_follow_the_session_and_message_each_other() {
    TestEnvironment a, b;
    a.session_now = {true, false, {{1, "Ann", true, true}, {2, "Bob", false, false}}};
    b.session_now = {true, false, {{2, "Bob", true, false}, {1, "Ann", false, true}}};
    const auto main = R"(
        reskate.on("player_joined", function(p) reskate.log("joined " .. p.name .. " " .. p.id .. (p.me and " me" or "") .. (p.host and " host" or "")) end)
        reskate.on("player_left", function(p) reskate.log("left " .. p.name) end)
        reskate.on("message", function(from, text) reskate.log("from " .. from.name .. ": " .. text) end)
        reskate.console.command("hello", "x", function(args)
            local s = reskate.session
            reskate.log(string.format("session %s %s %d me %s host %s", tostring(s.active()), tostring(s.is_dedicated()), #s.players(),
                s.me().name, s.host().name))
            assert(reskate.net.send(args[1] or "all", "hi from " .. s.me().name))
        end)
        reskate.console.command("alone", "x", function()
            reskate.log("alone " .. tostring(reskate.net.send("all", "x")) .. " " .. tostring(reskate.session.active()) .. " " .. #reskate.session.players())
        end)
        reskate.console.command("flood", "x", function()
            local ok, err = pcall(function() for i = 1, 10 do reskate.net.send("all", string.rep("x", 4000)) end end)
            reskate.log("flood " .. tostring(err):match("more than %d+ KB") )
            assert(not pcall(reskate.net.send, "all", string.rep("x", 5000)), "too big")
            assert(not pcall(reskate.net.send, "nobody", "x"), "not a player")
        end)
    )";
    // Each game's folder for it is named differently: the messages go by its manifest.json name.
    auto first = package("chat-a", {{manifest_file, manifest(R"("console", "session.read", "net.session")")}, {"manifest.json", R"({"name": "Chatter"})"}, {"plugin/main.lua", main}});
    auto second = package("chat-b", {{manifest_file, manifest(R"("console", "session.read", "net.session")")}, {"manifest.json", R"({"name": "Chatter"})"}, {"plugin/main.lua", main}});
    auto other = package("other", {{manifest_file, manifest(R"("net.session")")}, {"manifest.json", R"({"name": "Other"})"}, {"plugin/main.lua",
        R"(reskate.on("message", function(from, text) reskate.log("other heard " .. text) end))"}});
    Host host_a(a, Side::client), host_b(b, Side::client);
    host_a.apply({first});
    host_b.apply({second, other});
    ticks(host_a, a, 1);
    ticks(host_b, b, 1);
    check(a.logged("[chat-a] joined Ann 1 me host") && a.logged("[chat-a] joined Bob 2"), "Players in the session are heard\n" + a.all());
    host_a.run_console("hello");
    check(a.logged("session true false 2 me Ann host Ann"), "The session reads\n" + a.all());
    check(a.sent.size() == 1 && wire::is_plugin(a.sent[0]), "A message goes out as a plugin message");
    for (auto& bytes : a.sent) b.incoming.push_back({1, bytes});
    a.sent.clear();
    ticks(host_b, b, 1);
    check(b.logged("[chat-b] from Ann: hi from Ann") && !b.logged("other heard"), "and reaches the same plugin only\n" + b.all());
    host_a.run_console("hello 3");
    for (auto& bytes : a.sent) b.incoming.push_back({1, bytes});
    a.sent.clear();
    ticks(host_b, b, 1);
    check(std::count_if(b.logs.begin(), b.logs.end(), [](const auto& l) { return l.find("from Ann") != std::string::npos; }) == 1,
          "A message for another player is not delivered here");
    b.incoming.push_back({1, {0xD1, 1, 1, 2}});
    b.incoming.push_back({1, {7, 7, 7}});
    ticks(host_b, b, 1);
    running(host_b, "chat-b", b);
    host_a.run_console("flood");
    check(a.logged("flood more than 16 KB"), "16 KB a second\n" + a.all());
    a.session_now.players.pop_back();
    ticks(host_a, a, 1);
    check(a.logged("[chat-a] left Bob"), "A player who leaves is heard");
    a.session_now = {};
    ticks(host_a, a, 1);
    a.now += 1000; // past the flood's second
    host_a.run_console("alone");
    check(a.logged("alone false false 0"), "Without a session nothing is sent\n" + a.all());
    running(host_a, "chat-a", a);
}

// A game mode's rules in the plugin's server copy: players' copies talk to it, it answers everyone.
void plugins_server_copies_are_the_authority() {
    TestEnvironment server, player;
    server.session_now = {true, false, {{1, "Ann", true, true}, {2, "Bob", false, false}}};
    player.session_now = {true, false, {{2, "Bob", true, false}, {1, "Ann", false, true}}};
    const std::string main = R"(
        reskate.log("side " .. reskate.side)
        if reskate.side == "server" then
            reskate.on("message", function(from, text)
                reskate.log("server heard " .. text .. " from " .. from.name)
                assert(reskate.net.send("all", "state " .. text))
                assert(reskate.server.kick(from, "testing"))
                assert(reskate.server.say("round over"))
                assert(not pcall(reskate.net.send, "server", "x"), "it is the server")
            end)
        else
            reskate.on("message", function(from, text) reskate.log("player heard " .. text .. " server " .. tostring(from.server)) end)
            reskate.console.command("join", "x", function()
                assert(reskate.net.send("server", "hello"))
                local ok, err = pcall(reskate.server.kick, "1")
                assert(not ok and err:find("server copy"), err)
            end)
        end
    )";
    const auto mode = [&](const char* id) {
        return package(id, {{manifest_file, manifest(R"("console", "net.session", "server")", "both")}, {"manifest.json", R"({"name": "Mode"})"},
                            {"plugin/main.lua", main}});
    };
    Host server_host(server, Side::server), player_host(player, Side::client);
    server_host.apply({mode("mode-s")});
    player_host.apply({mode("mode-p")});
    ticks(server_host, server, 1);
    ticks(player_host, player, 1);
    check(server.logged("[mode-s] side server") && player.logged("[mode-p] side client"), "Each copy knows its side\n" + server.all() + player.all());
    player_host.run_console("join");
    check(player.sent.size() == 1 && wire::to_of(player.sent[0]) == wire::authority && !wire::from_authority(player.sent[0]) &&
          !wire::relayed(player.sent[0]), "A player's copy speaks to the authority, and no relay passes that on\n" + player.all());
    player_host.apply({});
    player_host.apply({mode("mode-p")});
    ticks(player_host, player, 1);
    player.incoming.push_back({2, player.sent[0]});
    ticks(player_host, player, 1);
    check(!player.logged("player heard hello"), "What is for the authority is not heard by players' copies");
    server.incoming.push_back({2, player.sent[0]});
    ticks(server_host, server, 1);
    check(server.logged("[mode-s] server heard hello from Bob"), "The server copy hears it, from the player\n" + server.all());
    check(server.fake.called("kick 2 testing") && server.fake.called("announce Mode round over"), "and rules: a kick, a line to everyone");
    check(server.sent.size() == 1 && wire::from_authority(server.sent[0]) && !wire::relayed(server.sent[0]), "Its answer is the authority's");
    player.incoming.push_back({1, server.sent[0]});
    ticks(player_host, player, 1);
    check(player.logged("[mode-p] player heard state hello server true"), "and players' copies hear it from the server\n" + player.all());
    running(server_host, "mode-s", server);
    running(player_host, "mode-p", player);
}

// Clips on the skater's body: from the package or recorded, played, replacing a trick.
void plugins_animate_the_skater() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    dingosdk::anim_studio::Clip clip;
    clip.joints = dingosdk::anim_studio::skeleton_joints;
    clip.times = {0, 0.5f};
    clip.frames.resize(clip.times.size() * clip.joints);
    clip.takeoff = 0.1f;
    clip.landing = 0.4f;
    const auto bytes = dingosdk::anim_studio::clip_bytes(clip);
    const std::string main = R"(
        reskate.console.command("go", "x", function()
            assert(reskate.animation.play("clips/spin.fas", {speed = 2, blend = 0.3, loop = true}))
            assert(not pcall(reskate.animation.play, "clips/missing.fas"), "a clip in the package")
            assert(not pcall(reskate.animation.play, "notes.txt"), "a clip file")
            assert(not pcall(reskate.animation.play, "../other/spin.fas"), "its own package only")
            assert(not pcall(reskate.animation.play, "clips/spin.fas", {speed = 99}), "a sane speed")
            assert(reskate.animation.replace("Kickflip", "clips/spin.fas", {pop = "high"}))
            assert(not pcall(reskate.animation.replace, "kick;flip", "clips/spin.fas"), "a trick's name")
            assert(not pcall(reskate.animation.replace, "ollie", "clips/spin.fas", {pop = "huge"}), "a pop")
            assert(reskate.animation.record(2))
            assert(not pcall(reskate.animation.record, 60), "at most 10 s")
            local rec = reskate.animation.recorded()
            assert(rec == "rec:1", tostring(rec))
            assert(reskate.animation.recorded() == nil, "one recording, once")
            for i = 1, 3 do reskate.animation.play(rec) end
            local fifth, why = reskate.animation.play(rec)
            assert(not fifth and why:find("4 times"), "4 a second")
            assert(reskate.animation.trick() == "kickflip")
            reskate.animation.forget(rec)
            assert(not pcall(reskate.animation.play, rec), "a forgotten recording is gone")
            reskate.animation.stop()
            reskate.log("danced")
        end)
    )";
    host.apply({package("dancer", {{manifest_file, manifest(R"("console", "skater.animation")")}, {"plugin/main.lua", main},
                                   {"clips/spin.fas", std::string(bytes.begin(), bytes.end())}, {"notes.txt", "x"}})});
    running(host, "dancer", environment);
    host.run_console("go");
    check(environment.logged("[dancer] danced"), "A plugin animates the skater\n" + environment.all());
    for (const auto* call : {"play dancer 2 frames 2.0 0.30 true", "replace dancer kickflip high clip", "record dancer 2.0", "play dancer 3 frames 1.0 0.15 false",
                             "stop dancer"})
        check(environment.fake.called(call), std::string("The game was asked: ") + call + "\n" + environment.all());
    running(host, "dancer", environment);
}

void plugins_call_and_hear_each_other() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({package("library", {{manifest_file, manifest()}, {"manifest.json", R"({"name": "Lib"})"}, {"plugin/main.lua", R"(
        reskate.expose({
            add = function(a, b) return a + b end,
            echo = function(t) return {got = t, n = #t.list} end,
            fail = function() error("library failure") end,
            back = function() return reskate.require_plugin("user").poke() end,
        })
        reskate.on_plugin("user", "hello", function(from, data) reskate.log("heard " .. data.word .. " from " .. from) end)
        reskate.on_plugin("*", "anyone", function(from, data) reskate.log("anyone from " .. from) end)
    )"}}), script("user", R"(
        reskate.expose({poke = function() return 1 end})
        reskate.console.command("use", "x", function()
            local lib = assert(reskate.require_plugin("lib"), "by its manifest name")
            assert(lib.add(2, 3) == 5)
            local echoed = lib.echo({list = {1, 2, 3}, word = "hi"})
            assert(echoed.n == 3 and echoed.got.word == "hi")
            local ok, err = pcall(lib.fail)
            assert(not ok and err:find("raised an error"), err)
            assert(not pcall(lib.add, print, 1), "a function does not cross")
            ok, err = pcall(lib.back)
            assert(not ok, "a call back into a plugin in a call is refused")
            local none, why = reskate.require_plugin("nobody")
            assert(none == nil and why:find("no running plugin"), why)
            assert(reskate.emit("hello", {word = "hey"}))
            assert(reskate.emit("anyone"))
            assert(not pcall(reskate.emit, "bad name!"))
            reskate.log("library fine")
        end)
    )", R"("console")")});
    running(host, "library", environment);
    host.run_console("use");
    check(environment.logged("[user] library fine"), "Libraries work\n" + environment.all());
    running(host, "library", environment);
    ticks(host, environment, 1);
    check(environment.logged("[library] heard hey from user") && environment.logged("[library] anyone from user"), "Events reach the next tick\n" + environment.all());
}

void web_requests_go_only_to_listed_domains() {
    TestEnvironment environment;
    check([] {
        try { parse_manifest(R"({"api": 1, "entry": "a.lua", "domains": ["api.example.com"]})"); } catch (const std::exception&) { return true; }
        return false;
    }(), "domains need net.http");
    check([] {
        try { parse_manifest(R"({"api": 1, "entry": "a.lua", "permissions": ["net.http"], "domains": ["*.example.com"]})"); } catch (const std::exception&) { return true; }
        return false;
    }(), "and are plain host names");
    Host host(environment, Side::client);
    host.apply({package("web", {{manifest_file, R"({"api": 1, "entry": "plugin/main.lua", "permissions": ["console", "net.http"], "domains": ["API.example.com"]})"},
        {"plugin/main.lua", R"(
        reskate.console.command("fetch", "x", function()
            assert(reskate.http.request({url = "https://api.example.com/v1/x?y=1"}, function(status, body, err)
                reskate.log("answer " .. status .. " " .. body .. " " .. tostring(err))
            end))
            assert(reskate.http.request({url = "https://api.example.com:443/post", method = "POST", body = "{}", content_type = "application/json"}, function() end))
            for _, bad in {"http://api.example.com/", "https://other.com/", "https://user@api.example.com/", "https://api.example.com:8080/",
                           "https://api.example.com/a b", "ftp://api.example.com/"} do
                assert(not pcall(reskate.http.request, {url = bad}, function() end), bad)
            end
            assert(not pcall(reskate.http.request, {url = "https://api.example.com/", method = "DELETE"}, function() end))
            reskate.log("web fine")
        end)
        reskate.console.command("many", "x", function()
            local ok, err = pcall(function() for i = 1, 20 do reskate.http.request({url = "https://api.example.com/"}, function() end) end end)
            reskate.log("many " .. tostring(err):match("more than %d+ web requests [%a ]+"))
        end)
    )"}})});
    host.run_console("fetch");
    check(environment.logged("[web] web fine") && environment.requests.size() == 2, "Requests go out\n" + environment.all());
    check(environment.requests[0].host == "api.example.com" && environment.requests[0].path == "/v1/x?y=1" && environment.requests[1].method == "POST",
          "with their host, path and method");
    environment.responses.push_back({environment.requests[0].id, "web", 200, "hello", ""});
    ticks(host, environment, 1);
    check(environment.logged("[web] answer 200 hello nil"), "and the answer comes back\n" + environment.all());
    host.run_console("many");
    check(environment.logged("many more than 4 web requests waiting"), "4 waiting at most\n" + environment.all());
}

void the_wider_set_reaches_the_game() {
    TestEnvironment environment;
    auto& game = environment.fake;
    environment.local_skater = SkaterState{{1, 0, 1}, 0.25f, 3, 0, false, 0};
    Host host(environment, Side::client);
    host.apply({script("wide", R"(
        reskate.on("skater_state", function(now, before) reskate.log("state " .. before .. " to " .. now) end)
        reskate.on("bail", function(e) reskate.log("bail " .. e.count .. " force " .. tostring(e.force) .. " cause " .. tostring(e.cause)) end)
        reskate.on("board_landed", function(e) reskate.log(string.format("landed %.1f onto %s", e.down_speed, e.onto)) end)
        reskate.on("body_hit", function(e) reskate.log(string.format("hit %s %.0f", e.bone, e.impact)) end)
        reskate.on("bail_end", function(e) reskate.log(string.format("fall %.1f s left %.0f thigh %.0f", e.seconds, e.left, e.bones["Left thigh"].impact)) end)
        reskate.console.command("outfit", "x", function()
            assert(reskate.outfit.find("roadrash_leg1") == "RoadRash_Leg1" and reskate.outfit.find("nothing") == nil)
            local slots = reskate.outfit.slots()
            assert(#slots == 2 and slots[1].slot == "tattoo_legl" and slots[1].parameters[1] == 0.5)
            assert(reskate.outfit.set("tattoo_legl", "RoadRash_Leg1", {placement = {0.1, 0.2, 0.3, 0.4, 0.5}, covers = true}))
            assert(not pcall(reskate.outfit.set, "tattoo_legl", "x", {placement = {1, 2, 3, 4, 5, 6, 7, 8, 9}}), "at most 8 numbers")
            reskate.outfit.clear("tattoo_legl")
            reskate.log("outfit fine")
        end)
        reskate.console.command("deck", "x", function()
            local bones = reskate.board.bones()
            assert(math.abs(bones[3].position.y - 0.1) < 1e-6 and bones[0] and bones[15] and #bones[3].rotation == 4, "the board's bones")
            assert(reskate.board.set(12, {position = vector.create(0, 2, 0), rotation = {0, 0, 0, 2}, space = "world"}))
            assert(not pcall(reskate.board.set, 1, {position = vector.create(0, 0, 0)}), "bone 1 places the board")
            assert(reskate.board.hide(3))
            reskate.board.park({12, 13, 14, 15})
            assert(reskate.board.toss({12, 4}, {velocity = vector.create(1, 3, 0), spin = vector.create(0, 5, 0), ground = -2}))
            reskate.log("deck fine")
        end)
        reskate.console.command("physics", "x", function()
            assert(reskate.board.motion().velocity.y == -2)
            assert(reskate.skater.wipeout(2))
            local again, two = reskate.skater.wipeout(2)
            assert(not again and two:find("2 s"), "one wipeout every 2 s")
            assert(not pcall(reskate.skater.wipeout, 10), "a gentle force")
            reskate.log("physics fine")
        end)
        reskate.on("chat", function(line) reskate.log("chat " .. line.name .. ": " .. line.text) end)
        reskate.on("track_changed", function(t) reskate.log("track " .. t.title .. " by " .. t.artist) end)
        reskate.on("throwdown", function(e) reskate.log("throwdown " .. e.phase) end)
        reskate.on("action", function(name) reskate.log("action " .. name) end)
        reskate.on("screen_closed", function(title) reskate.log("closed " .. title) end)
        assert(reskate.input.bind("jump", "Ctrl+K"))
        assert(not pcall(reskate.input.bind, "bad", "Esc"), "Esc is not bindable")
        reskate.ui.screen("Big", function(ui) ui:heading("A screen") if ui:button("ok", "OK") then reskate.log("screen ok") end end)
        reskate.console.command("do", "x", function()
            assert(reskate.skater.state() == "riding")
            assert(reskate.skater.noclip(true) and reskate.skater.boost("up"))
            assert(not pcall(reskate.skater.boost, "sideways"))
            assert(reskate.trainer.set_point() and reskate.trainer.back())
            reskate.hud.hide(true)
            assert(#reskate.objects.catalog() == 1 and reskate.objects.list()[1].id == "42")
            assert(reskate.objects.place("bench_01", vector.create(5, 0, 5), 1.0))
            assert(reskate.objects.move("42", vector.create(6, 0, 6)))
            assert(reskate.objects.remove("42"))
            assert(not pcall(reskate.objects.remove, "not a number"))
            assert(reskate.world.set_time("night") and reskate.world.set_control("fog", 1) and reskate.world.set_park("historic", "skatepark_02"))
            local ok, why = reskate.world.set_time("never")
            assert(not ok and why == "refused")
            assert(#reskate.travel.locations() == 1 and #reskate.travel.levels() == 1)
            assert(reskate.travel.go("bam"))
            local again, wait = reskate.travel.load("Levels/Game/X/X")
            assert(not again and wait:find("5 s"), "one trip every 5 s")
            assert(reskate.ai.spawn(2) and reskate.ai.count() == 3 and reskate.ai.clear())
            assert(not pcall(reskate.ai.spawn, 20))
            local s = reskate.session.skaters()[1]
            assert(s.id == "7" and s.position.y == 2 and s.heading == 0.5)
            reskate.session.tag("7", "IT", 0xff0000ff)
            assert(reskate.voice.set_volume("7", 0))
            assert(reskate.chat.send("hello all"))
            local soon, three = reskate.chat.send("again")
            assert(not soon and three:find("3 s"))
            assert(not pcall(reskate.chat.send, "/kick someone"), "no commands")
            for i = 1, 5 do assert(reskate.chat.notice("note " .. i, 0x00ff00ff)) end
            local sixth, five = reskate.chat.notice("too many")
            assert(not sixth and five:find("5 lines"), "5 lines a second")
            assert(not pcall(reskate.chat.notice, "two\nlines"), "one line")
            assert(reskate.camera.look(vector.create(0, 5, 0), vector.create(0, 5, 10), 70))
            assert(not pcall(reskate.camera.look, vector.create(1, 1, 1), vector.create(1, 1, 1)), "a view needs a direction")
            assert(not pcall(reskate.camera.look, vector.create(0, 0, 0), vector.create(0, 0, 1), 400), "a sane field of view")
            assert(reskate.camera.get().fov == 60 and reskate.camera.get().position.y == 2)
            assert(reskate.camera.set(vector.create(0, 5, 0), vector.create(0, 0, -1), vector.create(1, 1, 0), 80), "a rolled view")
            assert(not pcall(reskate.camera.set, vector.create(0, 0, 0), vector.create(0, 1, 0), vector.create(0, 2, 0)), "up along forward")
            local f = reskate.input.flight()
            assert(f.forward == 0.5 and f.look_x == 3 and f.boost, "the flight keys and mouse")
            local pad = reskate.input.gamepad()
            assert(pad.buttons.a and not pad.buttons.b and pad.left_x == 0.25 and pad.right_trigger == 1, "the gamepad")
            local stick = reskate.input.joystick()
            assert(stick.axes[2] == -1 and stick.buttons[3] and not stick.buttons[1] and stick.name == "Radio", "the radio")
            assert(reskate.input.capture(true))
            reskate.camera.release()
            assert(reskate.skater.freeze(true))
            assert(reskate.throwdowns.state().active == false)
            assert(reskate.throwdowns.host("skate", {"7", {id = "8"}, "7"}, {max = 4}))
            local again, five = reskate.throwdowns.host("jam", {"7"})
            assert(not again and five:find("5 s"), "one drop every 5 s")
            assert(not pcall(reskate.throwdowns.host, "bowling", {"7"}), "a linked mode")
            assert(not pcall(reskate.throwdowns.host, "jam", {}), "someone to play")
            assert(reskate.throwdowns.hosting() == "open")
            assert(reskate.music.next())
            local p = reskate.progression.get()
            assert(p.districts[1].rank == 4 and p.challenges[1].completed_goals == 1 and p.score == 1500)
            assert(reskate.settings.game("Render.Fov") == "90" and reskate.settings.game("Nope") == nil and #reskate.settings.names() == 1)
            reskate.ui.open("Big")
            reskate.log("wide fine")
        end)
    )", R"("console", "events.skater", "skater.read", "skater.control", "trainer", "hud", "world.objects", "world.environment", "travel",
          "world.ai", "session.skaters", "session.nametags", "voice.control", "chat.read", "chat.send", "chat.local", "camera", "throwdowns", "music", "audio", "outfit", "board",
          "progression.read", "settings.read", "input", "ui.screen")")});
    running(host, "wide", environment);
    game.state = 100;
    game.lines = {{1, 5, "Old", "before", false, false}};
    ticks(host, environment, 1);
    check(environment.logged("[wide] state none to riding"), "The skater's state is heard\n" + environment.all());
    host.run_console("do");
    check(environment.logged("[wide] wide fine"), "Every wider-set call works\n" + environment.all());
    for (const auto* call : {"control noclip_on", "control boost_up", "hud wide on", "place 0 bench_01 5.0", "move 42  6.0", "delete 42",
                             "env tod night", "env control fog 1", "env park historic skatepark_02", "travel location bam", "ai spawn 2",
                             "ai clear", "tag wide 7 IT ff0000ff", "voice 7 0.0", "chat hello all", "notice wide note 5 ff00ff00", "camera wide 0.0 5.0 0.0 back 0.00 0.00 -1.00 fov 70",
                             "camera wide 0.0 5.0 0.0 back -0.00 -0.00 1.00 fov 80", "capture wide on",
                             "camera release wide", "freeze wide on", "host wide ThrowdownSkate 2 players max 4", "music next"})
        check(game.called(call), std::string("The game was asked: ") + call);
    check(environment.teleports.size() == 1, "The save point teleports back");
    check(game.binds["wide/jump"] == combo_of("Ctrl+K"), "Its bind is held by the game");
    game.state = 401;
    game.wipe = 1;
    game.lines.push_back({2, 6, "Ann", "hi there", false, false});
    game.now_playing = {true, true, "Spotify", "Song", "Band", "Album", 9};
    game.throwdown.active = true;
    game.presses = {{"wide", "jump"}, {"someone", "jump"}};
    environment.now += 1000; // the music is looked at once a second
    ticks(host, environment, 1);
    check(environment.logged("[wide] state riding to grinding") && environment.logged("[wide] chat Ann: hi there") && !environment.logged("before") &&
          environment.logged("[wide] track Song by Band") && environment.logged("[wide] throwdown started") && environment.logged("[wide] action jump"),
          "And its events, but not chat from before it listened\n" + environment.all());
    ticks(host, environment, 1);
    game.wipe = 2;
    game.news.landings.push_back({4.5f, 6, true});
    game.news.hits.push_back({"Head", 9, false, false});
    game.news.falls.push_back({1.5f, 12, 3, {{"Left thigh", 8, 0.5f}}});
    ticks(host, environment, 1);
    check(environment.logged("[wide] bail 2 force 7.5 cause 12"), "A wipeout is a bail, with the slam's force and cause\n" + environment.all());
    check(environment.logged("[wide] landed 4.5 onto rail") && environment.logged("[wide] hit Head 9") &&
              environment.logged("[wide] fall 1.5 s left 12 thigh 8"),
          "and the board's landings, the body's hits and the fall are heard\n" + environment.all());
    host.run_console("outfit");
    check(environment.logged("[wide] outfit fine") && game.called("outfit wide tattoo_legl RoadRash_Leg1 5 true") &&
              game.called("outfit clear wide tattoo_legl"),
          "A plugin wears items over the outfit\n" + environment.all());
    host.run_console("deck");
    check(environment.logged("[wide] deck fine") && game.called("board wide 12 2.0 world") && game.called("board hide wide 3") &&
              game.called("board park wide 4") && game.called("board toss wide 2 3 -2"),
          "A plugin changes what is drawn of the board\n" + environment.all());
    host.run_console("physics");
    check(environment.logged("[wide] physics fine") && game.called("wipeout 2.0"), "The board's motion reads, and a wipeout is asked for\n" + environment.all());
    ticks(host, environment, 10);
    const auto screen = std::find_if(environment.panels.begin(), environment.panels.end(), [](const auto& p) { return p.screen; });
    check(screen != environment.panels.end() && screen->title == "Big" && screen->widgets.size() == 2, "Its open screen is built, without the PLUGINS tab");
    environment.actions = {plugin_ui::Action{"wide", "Big", "ok"}};
    ticks(host, environment, 10);
    check(environment.logged("[wide] screen ok"), "and answers its widgets");
    environment.actions = {plugin_ui::Action{"wide", "Big", plugin_ui::close_widget}};
    ticks(host, environment, 10);
    check(environment.logged("[wide] closed Big") && std::none_of(environment.panels.begin(), environment.panels.end(), [](const auto& p) { return p.screen; }),
          "Esc closes it, which it hears");
    host.apply({});
    check(std::find(game.released.begin(), game.released.end(), "wide") != game.released.end(), "Unloading gives back what it held of the game");
}

void key_names_become_binds() {
    check(combo_of("F7") == (0x80000000u | 0x76u), "F7");
    check(combo_of("k") == (0x80000000u | 'K'), "a letter, any case");
    check(combo_of("Ctrl+K") != 0 && (combo_of("Ctrl+K") & 0xc0000000u) == 0x40000000u, "a chord");
    check(combo_of("Ctrl+K") == combo_of("k+ctrl"), "in any order");
    check(combo_of("Pad.LB+Pad.RB") == 0x300u && combo_of("pad.a") == 0x1000u && combo_of("Pad.RT") == 0x20000u, "pad combos");
    for (const auto* bad : {"", "Esc", "F13", "Ctrl+", "Pad.Z", "Ctrl+Pad.A", "A+B+C+D+E+F", "Num1"})
        check(combo_of(bad) == 0, std::string("not a bind: ") + bad);
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
    environment.mod_list = {{"reskate-test", "ReSkate_Test", true}};
    Host host(environment, Side::client);
    host.apply({contributed("reskate-test")});
    running(host, "reskate-test", environment);
    ticks(host, environment, 5);
    check(host.run_console("ptest all"), "ptest is its command");
    ticks(host, environment, 120); // its later checks run on timers
    const auto output = printed_lines(environment);
    check(output.find("ptest: 79 passed, 0 failed, 10 skipped") != std::string::npos &&
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
    // Its panel on the PLUGINS tab, and its test HUD.
    environment.panels_open = true;
    ticks(host, environment, 10);
    check(environment.panels.size() == 1 && environment.panels[0].title == "reskate-test" && environment.panels[0].widgets.size() == 8,
          "Its panel is built while the PLUGINS tab shows");
    environment.actions = {plugin_ui::Action{"reskate-test", "reskate-test", "press"}, plugin_ui::Action{"reskate-test", "reskate-test", "hud", true}};
    ticks(host, environment, 10);
    check(environment.panels[0].widgets[1].text.find("pressed 1 times") != std::string::npos && environment.sounds.size() >= 1,
          "and answers its button with a sound");
    check(!environment.layers.empty() && environment.layers[0].shapes.size() >= 6, "Its HUD toggle draws the test HUD");
    check(host.run_console("ptest hud") && environment.printed("ptest hud: off"), "and ptest hud switches it");
    running(host, "reskate-test", environment);
}

// Road Rash as a mod (contrib/plugins/road-rash): its own checks of its rules pass, falls on one
// side mark that side's leg first, and every mark goes on, at the 4 changes a second ReSkate takes.
void road_rash_marks_the_skater_as_it_bails() {
    TestEnvironment environment;
    std::filesystem::remove_all(scratch / "storage" / "road-rash");
    auto& game = environment.fake;
    for (const auto* item : {"roadrash_leg_1", "roadrash_leg_2", "roadrash_leg_3_blood", "roadrash_leg_3_dry", "roadrash_arm_1", "roadrash_arm_2",
                             "roadrash_arm_3_blood", "roadrash_face_1", "roadrash_face_2_blood"})
        game.installed.insert(item);
    Host host(environment, Side::client);
    host.apply({contributed("road-rash")});
    running(host, "road-rash", environment);
    check(host.run_console("roadrash test"), "roadrash test");
    check(environment.printed("roadrash: 11 passed, 0 failed") && !environment.printed("FAIL"),
          "Road Rash's own checks pass:\n" + printed_lines(environment) + environment.all());
    // Ten falls on the left, and one that is the tenth entering the wipeout again.
    for (int i = 0; i < 10; ++i) {
        game.news.falls.push_back({1.0f, 12, 1, {}});
        environment.now += 2000;
        ticks(host, environment, 1);
    }
    game.news.falls.push_back({0.4f, 12, 1, {}});
    ticks(host, environment, 20);
    environment.lines.clear();
    check(host.run_console("roadrash"), "roadrash");
    check(environment.printed("roadrash: on, 10 bail(s) counted (left leg 1, left arm 0, right leg 0, right arm 0, face 0)") &&
              environment.printed("1 mark(s) worn"),
          "Ten falls on the left mark the left leg:\n" + printed_lines(environment));
    check(game.called("outfit road-rash tattoo_legl Pack/roadrash_leg_1 5 false") && !game.called("tattoo_arml"), "and it wears the first leg item");
    // Every part at its worst: five items, more than ReSkate takes in a second.
    game.calls.clear();
    check(host.run_console("roadrash bails 25"), "roadrash bails");
    ticks(host, environment, 120);
    for (const auto* call : {"outfit clear road-rash ", "outfit road-rash tattoo_legl Pack/roadrash_leg_3_blood 5 false",
                             "outfit road-rash tattoo_arml Pack/roadrash_arm_3_blood 5 false", "outfit road-rash tattoo_legr Pack/roadrash_leg_3_blood 5 false",
                             "outfit road-rash tattoo_armr Pack/roadrash_arm_3_blood 5 false", "outfit road-rash moles Pack/roadrash_face_2_blood 0 false"})
        check(game.called(call), std::string("Road Rash asked: ") + call + "\n" + environment.all());
    environment.lines.clear();
    check(host.run_console("roadrash"), "roadrash");
    check(environment.printed("5 mark(s) worn"), "All five are worn:\n" + printed_lines(environment));
    // Dry: the arm has no dry worst, so it wears the level below; the face too. Covering tattoos.
    game.calls.clear();
    check(host.run_console("roadrash blood 0") && host.run_console("roadrash tattoos 1"), "roadrash blood and tattoos");
    ticks(host, environment, 240); // 1.5 s off first, then five items over two seconds
    for (const auto* call : {"outfit road-rash tattoo_legl Pack/roadrash_leg_3_dry 5 true", "outfit road-rash tattoo_arml Pack/roadrash_arm_2 5 true",
                             "outfit road-rash moles Pack/roadrash_face_1 0 true"})
        check(game.called(call), std::string("Road Rash asked: ") + call + "\n" + environment.all());
    // Healed, nothing goes back on; switched off, no bail counts.
    game.calls.clear();
    check(host.run_console("roadrash heal") && environment.printed("roadrash: healed"), "roadrash heal");
    ticks(host, environment, 30);
    check(game.called("outfit clear road-rash ") && !game.called("outfit road-rash"), "Healing takes every mark off");
    check(host.run_console("roadrash 0"), "roadrash 0");
    for (int i = 0; i < 12; ++i) {
        game.news.falls.push_back({1.0f, 12, 1, {}});
        environment.now += 2000;
        ticks(host, environment, 1);
    }
    environment.lines.clear();
    check(host.run_console("roadrash"), "roadrash");
    check(environment.printed("roadrash: off, 0 bail(s) counted"), "Off, it counts nothing:\n" + printed_lines(environment));
    // The switches are kept.
    host.apply({});
    host.apply({contributed("road-rash")});
    running(host, "road-rash", environment);
    environment.lines.clear();
    check(host.run_console("roadrash"), "roadrash");
    check(environment.printed("roadrash: off, 0 bail(s) counted") && environment.printed("blood off, covers tattoos on"),
          "Its switches are kept:\n" + printed_lines(environment));
    // Its panel.
    environment.panels_open = true;
    ticks(host, environment, 10);
    check(environment.panels.size() == 1 && environment.panels[0].title == "Road Rash" && environment.panels[0].widgets.size() == 6,
          "Its panel is built while the PLUGINS tab shows");
    environment.actions = {plugin_ui::Action{"road-rash", "Road Rash", "on", true}};
    ticks(host, environment, 10);
    environment.lines.clear();
    check(host.run_console("roadrash"), "roadrash");
    check(environment.printed("roadrash: on"), "and its toggle switches it on:\n" + printed_lines(environment));
}

// Board Break as a mod (contrib/plugins/board-break), in a copy with one crack among its sounds as
// the deck pack brings them: its own checks pass, the stretches are kept off every board, a slam
// breaks the board (the real deck goes away, the crack sounds) and riding again makes it whole.
void board_break_snaps_the_board_on_a_slam() {
    TestEnvironment environment;
    std::filesystem::remove_all(scratch / "storage" / "board-break");
    auto& game = environment.fake;
    const auto directory = scratch / "mods" / "board-break";
    std::filesystem::remove_all(directory);
    std::filesystem::copy(std::filesystem::path(RESKATE_SOURCE_DIR) / "contrib" / "plugins" / "board-break", directory,
                          std::filesystem::copy_options::recursive);
    write(directory / "sounds" / "heavy" / "snap.wav", wave_file(8000, 4000));
    Host host(environment, Side::client);
    host.apply({{"board-break", directory, "1", check_package_files(directory)}});
    running(host, "board-break", environment);
    check(host.run_console("boardbreak test"), "boardbreak test");
    check(environment.printed("boardbreak: 31 passed, 0 failed") && !environment.printed("FAIL"),
          "Board Break's own checks pass:\n" + printed_lines(environment) + environment.all());
    check(game.called("board park board-break 4"), "The stretches are kept off every board\n" + environment.all());
    // Riding, then a slam; set to always break, it goes.
    check(host.run_console("boardbreak always 1") && environment.printed("boardbreak: always is 1"), "boardbreak always");
    game.state = 100;
    ticks(host, environment, 2);
    game.state = 300;
    game.wipe = 1;
    ticks(host, environment, 1);
    check(game.called("board board-break 3 -949.9 local"), "The real deck goes away\n" + environment.all());
    check(game.called("sound board-break 8000 1.00 1.00 false"), "and the crack sounds\n" + environment.all());
    environment.lines.clear();
    check(host.run_console("boardbreak status") && environment.printed(", broken;"), "It is broken:\n" + printed_lines(environment));
    // Riding again a moment later, it is a new board.
    game.state = 100;
    ticks(host, environment, 100);
    environment.lines.clear();
    check(host.run_console("boardbreak status") && environment.printed("the board is new (0% worn);"),
          "Riding again it is whole and new:\n" + printed_lines(environment) + environment.all());
    // Its panel, whose toggle switches it off; that is kept.
    environment.panels_open = true;
    ticks(host, environment, 10);
    check(environment.panels.size() == 1 && environment.panels[0].title == "Board Break", "Its panel is built while the PLUGINS tab shows");
    environment.actions = {plugin_ui::Action{"board-break", "Board Break", "on", false}};
    ticks(host, environment, 10);
    host.apply({});
    host.apply({{"board-break", directory, "1", check_package_files(directory)}});
    running(host, "board-break", environment);
    environment.lines.clear();
    check(host.run_console("boardbreak") && environment.printed("boardbreak: off;"), "Switched off, it stays off:\n" + printed_lines(environment));
}

// The FPV drone as a mod (contrib/plugins/fpv-drone): its own tests of the flight maths pass, and
// over the fake world (flat ground at y = 0) it flies the camera, keeps the game off the input,
// plays its motor, follows the skater and tells the session where it is, within its 2 ms a tick.
// Hall of Meat as a mod (contrib/plugins/hall-of-meat): its own checks pass, and a bail fed through
// physics_step shows the x-ray, hides the HUD corner, slows the game at a break and keeps the map's best.
void hall_of_meat_scores_a_bail() {
    TestEnvironment environment;
    std::filesystem::remove_all(scratch / "storage" / "hall-of-meat");
    auto& game = environment.fake;
    Host host(environment, Side::client);
    host.apply({contributed("hall-of-meat")});
    running(host, "hall-of-meat", environment);
    check(host.run_console("hallofmeat test"), "hallofmeat test");
    check(environment.printed("hallofmeat: 19 passed, 0 failed") && !environment.printed("FAIL"),
          "Hall of Meat's own checks pass:\n" + printed_lines(environment) + environment.all());
    check(game.called("xray hall-of-meat on") && game.called("corner hall-of-meat off"), "It gets the x-ray and the HUD corner ready\n" + environment.all());
    host.set_level("sanvan");
    // A wipeout that breaks the head, then the body lying for two seconds.
    const auto step = [&](bool wipeout, std::optional<bool> ragdoll, std::vector<PhysicsStep::Body> bodies) {
        game.steps.push_back({environment.now, 1.0f / 60, wipeout, false, ragdoll, Vec3{0, 0, 0}, 0.0f, std::move(bodies)});
        ticks(host, environment, 1);
    };
    step(true, true, {{"Head", 20, 0, false, false, true}});
    for (int i = 0; i < 120; ++i) step(false, true, {});
    check(game.called("corner hall-of-meat on"), "The card takes the HUD corner\n" + environment.all());
    check(game.called("slow hall-of-meat 0.30"), "A break slows the game\n" + environment.all());
    const auto& shapes = environment.layers.empty() ? std::vector<plugin_ui::Shape>{} : environment.layers[0].shapes;
    check(std::any_of(shapes.begin(), shapes.end(), [](const auto& s) { return s.kind == plugin_ui::Shape::Kind::skeleton && s.colours[1] != 0; }) &&
              std::any_of(shapes.begin(), shapes.end(), [](const auto& s) { return s.kind == plugin_ui::Shape::Kind::game_image && s.text == "thrasher"; }),
          "The broken head shows on the x-ray, and the card with skate.'s wordmark");
    // Standing up ends it; it is the map's first, so its best.
    step(false, false, {});
    ticks(host, environment, 200);
    check(game.called("corner hall-of-meat off") && game.called("slow hall-of-meat 1.00"), "The corner and the speed are given back\n" + environment.all());
    environment.lines.clear();
    check(host.run_console("hallofmeat") && environment.printed("hallofmeat: on; this map's best ") && environment.printed("Meat (1 hits, 1 broken"),
          "It says the map's best and the last bail:\n" + printed_lines(environment));
}
// Grind exits as a mod (contrib/plugins/grind-exits): its own checks pass, and RT held in a grind asks
// for the heel side and keeps RT and the stick's lean from the game.
void grind_exits_pick_the_side() {
    TestEnvironment environment;
    std::filesystem::remove_all(scratch / "storage" / "grind-exits");
    auto& game = environment.fake;
    Host host(environment, Side::client);
    host.apply({contributed("grind-exits")});
    running(host, "grind-exits", environment);
    check(host.run_console("grindexit test"), "grindexit test");
    check(environment.printed("grindexit: 7 passed, 0 failed") && !environment.printed("FAIL"),
          "Grind exits' own checks pass:\n" + printed_lines(environment) + environment.all());
    check(game.called("grind grind-exits none"), "It hears its pops from the start");
    check(host.run_console("grindexit stance goofy"), "grindexit stance");
    game.pad = PadInput{};
    game.state = 100;
    ticks(host, environment, 2);
    game.state = 400; // grinding
    ticks(host, environment, 2);
    game.pad->right_trigger = 1;
    ticks(host, environment, 2);
    check(game.called("grind grind-exits heel 2 1.00"), "RT asks for the heel side\n" + environment.all());
    check(game.called("hide grind-exits false true true"), "and keeps RT and the stick's lean from the game\n" + environment.all());
    game.pops.push_back({true, "applied", "toe_heel", -1, 1.5f, -1.5f});
    ticks(host, environment, 1);
    game.pad->right_trigger = 0;
    ticks(host, environment, 10);
    check(game.called("grind grind-exits none") && game.called("hide grind-exits false false false"), "Let go, the game's own again");
    environment.lines.clear();
    check(host.run_console("grindexit") && environment.printed("sides set: 1. Last: right of travel (toe_heel)"),
          "It counts the pop:\n" + printed_lines(environment));
}
void fpv_drone_flies_and_follows() {
    TestEnvironment environment;
    std::filesystem::remove_all(scratch / "storage" / "fpv-drone");
    auto& game = environment.fake;
    environment.session_now = {true, false, {{1, "Me", true, true, false}, {2, "Other", false, false, false}}};
    Host host(environment, Side::client);
    host.apply({contributed("fpv-drone")});
    running(host, "fpv-drone", environment);
    check(host.run_console("fpv test"), "fpv test");
    ticks(host, environment, 150); // one test every 50 ms
    check(environment.printed("fpv test: 27 passed, 0 failed") && !environment.printed("FAIL"),
          "The drone's own tests pass:\n" + printed_lines(environment) + environment.all());
    running(host, "fpv-drone", environment);
    // No level: it does not take off.
    environment.lines.clear();
    check(host.run_console("fpv fly") && environment.printed("fpv: Load into a level to fly."), "No flight without a level");
    host.set_level("sanvan");
    environment.local_skater = SkaterState{{0, 0, 0}, 0, 0, 0, false, 0};
    environment.lines.clear();
    check(host.run_console("fpv fly") && environment.printed("fpv: flying."), "fpv fly:\n" + printed_lines(environment));
    // The fake's keys: full right, half forward, the mouse moving, boost. It must keep off the ground.
    environment.sent.clear();
    ticks(host, environment, 600);
    running(host, "fpv-drone", environment);
    check(game.called("camera fpv-drone") && game.called("capture fpv-drone on"), "It flies the camera and keeps the game off the input\n" + environment.all());
    check(game.called("sound fpv-drone motor"), "Its motor plays\n" + environment.all());
    check(game.called("outfit fpv-drone eyewear Own_EyewearGlasses_Gen_SunglassesVisorLarge_00001"), "The pilot puts on goggles");
    check(game.called("hud fpv-drone on"), "and the game's HUD is hidden");
    check(environment.sent.size() >= 100 && environment.sent.size() <= 140, "It tells the session where it is about 12 times a second: " +
          std::to_string(environment.sent.size()));
    const auto flying_ms = status_of(host, "fpv-drone").tick_ms;
    const auto flying_message = environment.sent[5];
    environment.lines.clear();
    check(host.run_console("fpv status") && environment.printed("fpv: fly, "), "fpv status:\n" + printed_lines(environment));
    // Follow me, behind a skater riding along +Z.
    check(host.run_console("fpv follow"), "fpv follow");
    for (int i = 0; i < 600; ++i) {
        environment.local_skater->position[2] += 5 * 0.0167f;
        environment.local_skater->speed = 5;
        ticks(host, environment, 1);
    }
    running(host, "fpv-drone", environment);
    const auto following_ms = status_of(host, "fpv-drone").tick_ms;
    const auto& last = game.calls;
    const auto camera = std::find_if(last.rbegin(), last.rend(), [](const auto& c) { return c.starts_with("camera fpv-drone "); });
    check(camera != last.rend(), "Following flies the camera");
    float x{}, y{}, z{};
    std::istringstream words(camera->substr(std::string_view("camera fpv-drone ").size()));
    check(static_cast<bool>(words >> x >> y >> z), "the camera's position: " + *camera);
    const float skater_z = environment.local_skater->position[2];
    check(y > 0.5f && z < skater_z - 1 && z > skater_z - 12, std::format("The drone is behind the skater ({:.1f}, {:.1f}, {:.1f}), the skater at z {:.1f}",
          x, y, z, skater_z));
    environment.lines.clear();
    game.calls.clear();
    check(host.run_console("fpv stop") && environment.printed("fpv: landed."), "fpv stop");
    ticks(host, environment, 120);
    check(game.called("camera release fpv-drone") && game.called("outfit clear fpv-drone eyewear") && game.called("hud fpv-drone off"),
          "Landing gives the camera, the eyewear and the HUD back\n" + environment.all());
    std::cout << std::format("fpv-drone: {:.3f} ms a tick flying, {:.3f} ms following (its budget is 2 ms)\n", flying_ms, following_ms);
    check(flying_ms < 2 && following_ms < 2, "It stays within its time");
    // Another player's drone: a marker, a tag on their nametag and their motor; gone once not heard from.
    game.calls.clear();
    environment.incoming.push_back({2, flying_message});
    ticks(host, environment, 2);
    check(game.called("tag fpv-drone 2 FLYING DRONE") && game.called("sound fpv-drone motor"), "Their drone is tagged and heard\n" + environment.all());
    const bool marked = std::any_of(environment.layers.begin(), environment.layers.end(), [](const auto& layer) {
        return std::any_of(layer.shapes.begin(), layer.shapes.end(), [](const auto& shape) { return shape.text.find(" m") != std::string::npos; });
    });
    check(marked, "and marked with how far it is");
    ticks(host, environment, 120);
    check(game.called("tag fpv-drone 2  ") && game.called("stop sound fpv-drone"), "Not heard from, it is gone\n" + environment.all());
    environment.panels_open = true;
    ticks(host, environment, 5);
    check(environment.panels.size() == 5, "Its five panels are built while the PLUGINS tab shows: " + std::to_string(environment.panels.size()));
    environment.panels_open = false;
}

void reskate_hostile_is_stopped_or_refused_every_time() {
    TestEnvironment environment;
    Host host(environment, Side::client);
    host.apply({contributed("reskate-hostile")});
    running(host, "reskate-hostile", environment);
    struct Case { const char* attack; bool stops; int ticks; bool trick = false; bool panels = false; };
    const Case cases[] = {
        {"loop", true, 0}, {"caught-loop", true, 0}, {"tick-loop", true, 1}, {"memory", true, 0},
        {"caught-memory", true, 0}, {"huge-string", true, 0}, {"buffer", true, 0}, {"coroutines", true, 0},
        {"errors", true, 10}, {"slow", true, 60 * 5}, {"log-flood", false, 1}, {"timers", false, 1},
        {"recursion", false, 1}, {"escapes", false, 1}, {"teleport-spam", false, 1}, {"raycast-flood", false, 1}, {"trick-loop", true, 0, true}, {"draw-flood", false, 2}, {"toast-spam", false, 1},
        {"sound-spam", false, 1}, {"message-flood", false, 1}, {"panel-loop", true, 2, false, true},
        {"chat-spam", false, 1}, {"edit-spam", false, 1}, {"travel-spam", false, 1}, {"web-elsewhere", false, 1},
        {"bind-spam", false, 1}, {"screen-loop", true, 10}, {"notice-spam", false, 1}, {"notice-spoof", false, 1},
        {"freeze-spam", false, 1}, {"server-spoof", false, 1}, {"picture-escape", false, 20}, {"clip-garbage", false, 1}, {"outfit-anywhere", false, 1},
        {"throwdown-spam", false, 1},
    };
    for (const auto& item : cases) {
        check(host.reload("reskate-hostile").find("reloaded") != std::string::npos, std::string("reloads before ") + item.attack);
        check(host.run_console(std::string("hostile ") + item.attack), item.attack);
        ticks(host, environment, item.ticks);
        if (item.trick) host.trick({TrickNews::Kind::landed, 100, 1});
        if (item.panels) {
            environment.panels_open = true;
            ticks(host, environment, item.ticks);
            environment.panels_open = false;
        }
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
        plugins_draw_on_the_overlay();
        plugins_draw_pictures();
        plugins_server_copies_are_the_authority();
        plugins_animate_the_skater();
        plugins_play_their_own_sounds();
        plugins_have_menu_panels();
        plugins_play_sounds_and_read_the_mods();
        plugins_follow_the_session_and_message_each_other();
        plugins_call_and_hear_each_other();
        web_requests_go_only_to_listed_domains();
        the_wider_set_reaches_the_game();
        key_names_become_binds();
        level_events_reach_plugins();
        timers_run_when_due();
        a_package_with_a_program_or_on_the_wrong_side_is_not_loaded();
        discovery_finds_the_plugins_among_the_mods_left_on();
        reskate_test_passes_its_own_checks();
        road_rash_marks_the_skater_as_it_bails();
        board_break_snaps_the_board_on_a_slam();
        fpv_drone_flies_and_follows();
        hall_of_meat_scores_a_bail();
        grind_exits_pick_the_side();
        reskate_hostile_is_stopped_or_refused_every_time();
        std::filesystem::remove_all(scratch);
        std::cout << "plugin tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "plugin test failed: " << error.what() << '\n';
        return 1;
    }
}
