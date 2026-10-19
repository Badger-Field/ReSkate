#pragma once
#include "plugin_manifest.h"
#include "plugin_ui.h"
#include "Engine/Core/Json/json.h"
#include <array>
#include <chrono>
#include <cstddef>
#include <filesystem>
#include <functional>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <string_view>
#include <vector>

struct lua_State;

// One plugin: its own Luau VM, the reskate API registered in it, and the limits that keep it from
// harming the game. Nothing a plugin does reaches the game except through the API, and every
// limit it goes over stops it, not the game.
//
// Not thread safe: a plugin is loaded, ticked and unloaded on one thread (the client tick).
namespace dingosdk::plugins {
enum class LogLevel { info, warn, error };

struct Limits {
    std::size_t memory = 64u << 20;     // the VM's heap
    double call_ms = 50;                // one call into the plugin (load, a handler, a command)
    double unload_ms = 20;              // its unload handlers, together
    double tick_ms = 2;                 // the average time a tick, over a window of 1 s...
    int over_windows = 3;               // ...over it this many windows in a row stops it
    int errors = 5;                     // more errors than this...
    double error_window_ms = 10'000;    // ...in this long stops it
    int log_lines_per_second = 50;
    std::size_t storage_bytes = 1u << 20;
    std::size_t file_bytes = 16u << 20; // a package file read
    std::size_t source_bytes = 1u << 20; // a source file
    std::size_t handlers = 256;         // per event
    std::size_t timers = 1024;
    std::size_t commands = 64;
    int raycasts_per_tick = 64;
    float raycast_metres = 1000;        // the longest ray
    int teleports_per_second = 2;
    std::size_t shapes = 4000;          // drawn each frame
    std::size_t shape_text_bytes = 64u << 10;
    int toasts = 3;                     // in 5 s
    int sounds_per_second = 10;
    std::size_t message_bytes = 4096 - 14; // a plugin message's payload (plugin_wire.h's header is 14)
    std::size_t message_bytes_per_second = 16u << 10;
    std::size_t panels = 8;
    std::size_t widgets = 64;           // a panel's
};

// The game, as plugins see it (Environment, plugin_host.h, gives the host these).
using Vec3 = std::array<float, 3>;
struct SkaterState {
    Vec3 position{};
    // Radians about world up, -pi to pi: 0 faces +Z, pi/2 faces +X, and a higher heading is turned
    // to the skater's left (checked in game). Teleports take it the same way.
    float heading{};
    float speed{};        // metres per second over the ground
    float vertical{};     // metres per second, up
    bool airborne{};
    float air_time{};     // seconds into the jump going on
};
enum class Teleport { sent, unavailable, forbidden };
// A trick of the local skater, read from the score HUD (Extension/Skater/trick_watch.h).
struct TrickNews {
    enum class Kind { started, landed, broken } kind{Kind::started};
    float points{}, multiplier{}; // landed
    Vec3 started_at{}, landed_at{};
    double ms{};                  // landed: from its start
    std::string reason;           // broken: "slam" or "wipeout"
};
struct ScoreState {
    float line{}, multiplier{};
    bool in_trick{};
};
// The multiplayer session as plugins see it: who is in it (the local player too).
struct SessionPlayer {
    std::uint64_t id{}; // SteamID64
    std::string name;
    bool me{}, host{};
    bool operator==(const SessionPlayer&) const = default;
};
struct SessionState {
    bool active{}, dedicated{};
    std::vector<SessionPlayer> players;
};
// A mod in the Mods folder: its folder, the name it gives itself, whether it is on.
struct ModInfo {
    std::string folder, title;
    bool on{};
};

// What a plugin's VM can reach outside itself. The host implements it.
class Services {
public:
    virtual ~Services() = default;
    virtual void log(std::string_view plugin, LogLevel level, std::string_view text) = 0;
    virtual void console(std::string_view text) = 0;
    virtual double now_ms() = 0;           // a steady clock, for reskate.time and the windows
    virtual std::string level() = 0;       // the level being played, or empty
    virtual std::filesystem::path storage_file(std::string_view plugin) = 0;
    // Empty when the plugin may have the console command; otherwise why not.
    virtual std::string claim_command(std::string_view plugin, std::string_view name) = 0;
    // The local skater, or empty without one (the menu, a load).
    virtual std::optional<SkaterState> skater() = 0;
    virtual Teleport teleport(const Vec3& position, float heading) = 0;
    // Where the line first meets the world's collision, or empty when it is clear (or not loaded).
    virtual std::optional<Vec3> raycast(const Vec3& from, const Vec3& to) = 0;
    // The score HUD now, or empty without one.
    virtual std::optional<ScoreState> score() = 0;
    virtual void toast(std::string_view plugin, LogLevel level, std::string_view text) = 0;
    // One of the game's menu sounds by name; false for a name there is none of.
    virtual bool ui_sound(std::string_view name) = 0;
    virtual std::vector<ModInfo> mods() = 0;
    virtual const SessionState& session() = 0;
    // A plugin message to the same plugin on other players' games (to 0: everyone). False without a session.
    virtual bool send(std::uint32_t channel, std::uint64_t to, std::string_view payload) = 0;
    // A world position on screen (x, y in pixels, z its depth in front of the camera), or empty behind it.
    virtual std::optional<Vec3> world_to_screen(const Vec3& position) = 0;
    virtual std::array<float, 2> display() = 0;
};

enum class State { loading, running, stopped };

class Plugin {
public:
    // `directory` is the mod folder; `id` its folder name.
    Plugin(std::string id, std::filesystem::path directory, Manifest manifest, Services& services, Limits limits);
    ~Plugin();
    Plugin(const Plugin&) = delete;
    Plugin& operator=(const Plugin&) = delete;

    // Creates the VM, runs the entry (or `entry` in its place, for the test runner), then the load
    // handlers. False when it stopped; reason() says why.
    bool load(std::string entry = {});
    // The unload handlers (while it runs), its storage saved, the VM closed. Safe to call twice.
    void unload();
    // Stops it at the next safe point: the VM is closed by close_if_stopped().
    void stop(std::string reason);
    void close_if_stopped();

    void tick(double dt_ms);
    void level_changed(const std::string& previous, const std::string& current);
    // trick_started, trick_landed or line_broken, to a plugin with events.tricks.
    void trick(const TrickNews& news);
    // Its draw handlers (overlay): what they drew is taken after.
    void draw();
    std::vector<plugin_ui::Shape> take_shapes() { return std::exchange(shapes_, {}); }
    // Its menu panels, built with what the player did in them since the last build.
    std::vector<plugin_ui::Panel> build_panels(const std::vector<plugin_ui::Action>& actions);
    // session.read: players who came and went.
    void session_changed(const std::vector<SessionPlayer>& joined, const std::vector<SessionPlayer>& left);
    // net.session: a message from the same plugin on another player's game.
    void receive(const SessionPlayer& from, const std::string& payload);
    // The name its messages go under: the same plugin on every game has it (its manifest.json name).
    void set_channel(std::string channel);
    std::uint32_t channel() const noexcept { return channel_hash_; }
    // A console command it registered: the words after the command's name.
    void run_command(const std::string& name, const std::vector<std::string>& arguments);
    void save_storage();

    const std::string& id() const noexcept { return id_; }
    const std::filesystem::path& directory() const noexcept { return directory_; }
    const Manifest& manifest() const noexcept { return manifest_; }
    State state() const noexcept { return state_; }
    const std::string& reason() const noexcept { return reason_; }
    std::size_t memory() const noexcept { return memory_; }
    double tick_ms() const noexcept { return last_window_ms_; }
    // Its console commands and their help.
    std::vector<std::pair<std::string, std::string>> commands() const;
    bool has(std::string_view permission) const { return manifest_.permissions.contains(permission); }
    // Over a limit or stopped: the API refuses every call until the VM is closed.
    bool halted() const noexcept { return halt_ != Halt::none || memory_exceeded_; }

private:
    friend struct Api;
    friend struct ApiFunctions;
    friend struct UiApi;
    struct Command { std::string help; int function{}; };
    struct Timer { double due{}, every{}; int function{}; };
    using Clock = std::chrono::steady_clock;
    enum class Halt { none, time, memory, stopped };

    static void* allocate(void* ud, void* ptr, std::size_t old_size, std::size_t new_size);
    static void interrupt(lua_State* L, int gc);
    // Calls the function under the `count` arguments on top of the main stack; false (and the
    // error reported) when it raised one. Stops the plugin past its limits.
    bool call(int count, std::string_view what, double limit_ms);
    void report(std::string_view what, std::string_view message);
    // reskate.log and console.print: at most limits_.log_lines_per_second lines between them.
    bool count_line();
    void log(LogLevel level, std::string_view text);
    void print(std::string_view text);
    // The compiled chunk of a source file on the main stack, or false and the error on it.
    bool load_chunk(lua_State* L, const std::string& relative, const std::filesystem::path& file);
    void fire(std::string_view event, const std::vector<std::string>& arguments);
    // Each handler of `event` with what `push` puts on the stack (it returns how many values).
    void fire_with(std::string_view event, const std::function<int(lua_State*)>& push);
    void close();

    std::string id_;
    std::filesystem::path directory_;
    std::string code_root_; // the entry's folder in the package: where require looks
    Manifest manifest_;
    Services& services_;
    Limits limits_;
    lua_State* L_{};
    int script_thread_{}, modules_{};
    State state_{State::loading};
    std::string reason_;

    std::size_t memory_{};
    Halt halt_{Halt::none};
    bool memory_exceeded_{};
    Clock::time_point deadline_{};
    bool timed_{};
    unsigned interrupts_{};
    double spent_ms_{};

    bool window_started_{};
    double window_start_{};
    double window_spent_{};
    int window_ticks_{}, over_windows_{};
    double last_window_ms_{};
    std::vector<double> errors_;
    double log_second_{};
    int log_lines_{};
    int raycasts_{};                    // since the last tick
    double teleport_second_{};
    int teleports_{};
    std::vector<plugin_ui::Shape> shapes_;
    std::size_t shape_text_{};
    bool drawing_{}, shapes_warned_{};
    int draw_table_{}, ui_table_{};
    struct PanelEntry { std::string title; int function{}; };
    std::vector<PanelEntry> panels_;
    plugin_ui::Panel* building_{};          // the panel a build is filling
    std::vector<plugin_ui::Action> pending_; // what the player did, for the build going on
    std::vector<double> toasts_;
    double sound_second_{};
    int sounds_{};
    double message_second_{};
    std::size_t message_bytes_{};
    std::string channel_;
    std::uint32_t channel_hash_{};

    std::map<std::string, std::vector<int>, std::less<>> handlers_;
    std::map<int, Timer> timers_;
    int next_timer_{1};
    std::map<std::string, Command, std::less<>> commands_;
    std::set<std::string, std::less<>> loading_modules_;

    Json storage_ = Json::object();
    bool storage_loaded_{}, storage_dirty_{};
};
}
