#include "plugin_runtime.h"
#include "plugin_discovery.h"
#include "plugin_host.h"
#include "plugin_camera.h"
#include "Engine/Core/Log/logging.h"
#include "Engine/Game/Multiplayer/session_tools.h"
#include "Extension/Multiplayer/Session/session.h"
#include "Extension/UI/NativeMenu/ui_sound.h"
#include "Extension/UI/Overlay/overlay.h"
#include "Extension/Console/commands.h"
#include "Extension/Profile/local_profile_runtime.h"
#include "Extension/Skater/score_hud.h"
#include "Extension/Skater/trick_watch.h"
#include "Extension/Trainer/trainer.h"
#include <Windows.h>
#include <ShlObj.h>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <format>
#include <mutex>
#include <numbers>
#include <thread>

namespace dingosdk::plugins {
namespace {
std::uintptr_t base{};
// What the discovery thread last found of the Mods folder (reskate.mods).
std::mutex mods_mutex;
std::vector<ModInfo> found_mods; // under mods_mutex
// Plugin messages in and out of the session's throwdown packets: a few at a time.
std::mutex messages_mutex;
std::vector<Environment::Received> inbox;       // under messages_mutex
std::vector<std::vector<std::uint8_t>> outbox;  // under messages_mutex

class GameEnvironment final : public Environment {
public:
    explicit GameEnvironment(std::filesystem::path storage) : storage_(std::move(storage)) {}
    void log(LogLevel level, std::string_view text) override {
        logging::write(level == LogLevel::error ? logging::Level::error : level == LogLevel::warn ? logging::Level::warning : logging::Level::info,
            logging::Channel::plugins, text);
    }
    // As the console's own lines are written (console_line): an "error: " line is shown as one.
    void console(std::string_view text) override {
        const bool error = text.starts_with("error: ");
        logging::write(error ? logging::Level::error : logging::Level::info, logging::Channel::command, error ? text.substr(7) : text);
    }
    double now_ms() override {
        return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now().time_since_epoch()).count();
    }
    std::filesystem::path storage_root() override { return storage_; }
    bool builtin_command(std::string_view word) override {
        const auto& commands = console::game_commands();
        return commands.find(word) || !commands.under(word).empty();
    }
    // The trainer's telemetry of the local skater, published each tick. Its heading is 0 to 360
    // degrees the same way round; plugins get -pi to pi.
    std::optional<SkaterState> skater() override {
        const auto t = trainer::telemetry();
        if (!t.skater) return std::nullopt;
        const float heading = std::remainder(t.heading * std::numbers::pi_v<float> / 180, 2 * std::numbers::pi_v<float>);
        return SkaterState{t.position, heading, t.speed, t.vertical, t.airborne, t.air_time};
    }
    // As the console's tp: refused in a session whose host has turned teleporting (and noclip) off.
    Teleport teleport(const Vec3& position, float heading) override {
        if (!session_noclip_allowed()) return Teleport::forbidden;
        return teleport_local_skater_heading(position, heading) ? Teleport::sent : Teleport::unavailable;
    }
    std::optional<Vec3> raycast(const Vec3& from, const Vec3& to) override { return local_surface_hit(from, to); }
    std::optional<ScoreState> score() override { return score_now; }
    std::optional<ScoreState> score_now; // read each tick while a plugin wants tricks
    // ReSkate's notices in the top left, titled with the plugin's folder name.
    void toast(std::string_view plugin, LogLevel level, std::string_view text) override {
        overlay::notify(level == LogLevel::error ? overlay::NoticeLevel::error
                        : level == LogLevel::warn ? overlay::NoticeLevel::warning : overlay::NoticeLevel::info,
                        std::string(plugin), std::string(text));
    }
    bool ui_sound(std::string_view name) override {
        using multiplayer::UiSound;
        UiSound sound{};
        if (name == "select") sound = UiSound::select;
        else if (name == "cancel") sound = UiSound::cancel;
        else if (name == "navigate") sound = UiSound::navigate;
        else if (name == "toggle") sound = UiSound::toggle;
        else if (name == "tab") sound = UiSound::tab;
        else return false;
        multiplayer::queue_ui_sound(static_cast<std::uint32_t>(sound));
        return true;
    }
    std::vector<ModInfo> mods() override {
        std::lock_guard lock(mods_mutex);
        return found_mods;
    }
    // The session's model is large to copy: read four times a second.
    SessionState session() override {
        const auto now = std::chrono::steady_clock::now();
        if (now < next_session_) return session_;
        next_session_ = now + std::chrono::milliseconds(250);
        const auto m = multiplayer::model();
        session_ = {};
        if (!m.active || !m.connected || !m.local_id || m.echo) return session_;
        session_.active = true;
        session_.dedicated = m.dedicated;
        session_.players.push_back({m.local_id, m.local_name, true, !m.dedicated && m.host_id == m.local_id});
        for (const auto& p : m.roster)
            if (p.connected && p.id && p.id != m.local_id)
                session_.players.push_back({p.id, p.name, false, !m.dedicated && p.id == m.host_id});
        return session_;
    }
    bool send_message(std::vector<std::uint8_t> bytes) override {
        std::lock_guard lock(messages_mutex);
        if (outbox.size() >= 64) return false;
        outbox.push_back(std::move(bytes));
        return true;
    }
    std::vector<Received> take_messages() override {
        std::lock_guard lock(messages_mutex);
        return std::exchange(inbox, {});
    }
    std::optional<Vec3> world_to_screen(const Vec3& p) override {
        const auto display = plugin_ui::display();
        const auto projector = plugin_camera::projector(display[0], display[1]);
        return projector ? projector->project(p) : std::nullopt;
    }
    std::array<float, 2> display() override { return plugin_ui::display(); }
    void publish_layers(std::vector<plugin_ui::Layer> layers) override { plugin_ui::publish_layers(std::move(layers)); }
    bool panels_shown() override { return plugin_ui::panels_shown(); }
    void publish_panels(std::vector<plugin_ui::Panel> panels) override { plugin_ui::publish_panels(std::move(panels)); }
    std::vector<plugin_ui::Action> take_actions() override { return plugin_ui::take_actions(); }

private:
    std::chrono::steady_clock::time_point next_session_{};
    SessionState session_;

private:
    std::filesystem::path storage_;
};

std::filesystem::path data_root;
// Left to the process's end on purpose: unloading plugins from a static destructor would run
// their scripts while the game shuts down.
GameEnvironment* environment{};
Host* host{};
std::mutex found_mutex;
std::vector<Candidate> found;      // under found_mutex
std::uint64_t found_generation{};  // under found_mutex
std::uint64_t applied_generation{};
std::chrono::steady_clock::time_point last_tick{};
// The local skater's tricks (Extension/Skater/trick_watch.h), and where and when the one going on began.
tricks::TrickWatch trick_watch;
Vec3 started_at{};
std::chrono::steady_clock::time_point started_time{};
std::string watched_level;

// The score HUD into the trick watch, and what it saw to the plugins. Client thread.
void read_tricks() {
    const auto hud = tricks::read_score_hud(base);
    if (!hud) {
        environment->score_now.reset();
        return;
    }
    const auto skater = trainer::telemetry();
    tricks::TrickSignals signals;
    signals.action = hud->action;
    signals.line_score = hud->line_score;
    signals.multiplier = hud->multiplier;
    signals.slam = hud->slam;
    // A wipeout is physics states 300-399 (as Domination reads it).
    signals.wipeout = skater.skater && skater.physics_state >= 300 && skater.physics_state < 400;
    const auto event = trick_watch.update(signals);
    environment->score_now = ScoreState{hud->line_score, hud->multiplier, trick_watch.in_trick()};
    const auto now = std::chrono::steady_clock::now();
    TrickNews news;
    switch (event.kind) {
    case tricks::TrickEvent::Kind::none: return;
    case tricks::TrickEvent::Kind::started:
        started_at = skater.position;
        started_time = now;
        news.kind = TrickNews::Kind::started;
        news.started_at = started_at;
        break;
    case tricks::TrickEvent::Kind::landed:
        news.kind = TrickNews::Kind::landed;
        news.points = event.points;
        news.multiplier = event.multiplier;
        news.started_at = started_at;
        news.landed_at = skater.position;
        news.ms = std::chrono::duration<double, std::milli>(now - started_time).count();
        break;
    case tricks::TrickEvent::Kind::broken:
        news.kind = TrickNews::Kind::broken;
        news.reason = signals.slam ? "slam" : "wipeout";
        break;
    }
    host->trick(news);
}

void watch() {
    FileChecks checks;
    for (;;) {
        try {
            std::vector<ModInfo> mods;
            auto plugins = discover(data_root, checks, &mods);
            {
                std::lock_guard lock(mods_mutex);
                found_mods = std::move(mods);
            }
            std::lock_guard lock(found_mutex);
            if (plugins != found || !found_generation) {
                found = std::move(plugins);
                ++found_generation;
            }
        } catch (const std::exception& error) {
            logging::log(logging::Level::warning, logging::Channel::plugins, "Looking for plugins failed: {}", error.what());
        }
        std::this_thread::sleep_for(std::chrono::seconds(2));
    }
}

std::filesystem::path local_storage() {
    PWSTR raw{};
    std::filesystem::path result;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_LocalAppData, 0, nullptr, &raw)) && raw)
        result = std::filesystem::path(raw) / L"ReSkate" / L"Plugins";
    CoTaskMemFree(raw);
    return result;
}
}

bool start(std::uintptr_t image_base) noexcept {
    try {
        if (host) return true;
        base = image_base;
        plugin_camera::image_base().store(image_base);
        std::array<wchar_t, 32768> path{};
        const auto length = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
        if (!length || length >= path.size()) return false;
        data_root = std::filesystem::path(std::wstring(path.data(), length)).parent_path();
        auto storage = local_storage();
        if (storage.empty()) storage = data_root / L"ReSkate.Plugins";
        environment = new GameEnvironment(std::move(storage));
        host = new Host(*environment, Side::client);
        std::thread(watch).detach();
        logging::write(logging::Level::info, logging::Channel::plugins, "Plugins: looking in Mods for reskate-plugin.json every 2 s.");
        return true;
    } catch (const std::exception& error) {
        logging::log(logging::Level::error, logging::Channel::plugins, "Plugins could not start: {}", error.what());
        return false;
    }
}

void on_client_tick() noexcept {
    if (!host) return;
    try {
        {
            std::vector<Candidate> latest;
            bool changed = false;
            {
                std::lock_guard lock(found_mutex);
                if (found_generation != applied_generation) {
                    latest = found;
                    applied_generation = found_generation;
                    changed = true;
                }
            }
            if (changed) host->apply(latest);
        }
        const auto now = std::chrono::steady_clock::now();
        const double dt = last_tick == std::chrono::steady_clock::time_point{} ? 0
            : std::chrono::duration<double, std::milli>(now - last_tick).count();
        last_tick = now;
        if (host->wants_tricks()) read_tricks();
        else environment->score_now.reset();
        host->tick(dt);
    } catch (const std::exception& error) {
        logging::log(logging::Level::error, logging::Channel::plugins, "Plugin tick failed: {}", error.what());
    }
}

void set_level(std::string_view level) noexcept {
    if (!host) return;
    try {
        // A new level starts a new line: a trick going on before it scores nothing.
        if (level != watched_level) {
            watched_level = std::string(level);
            trick_watch = tricks::TrickWatch{};
        }
        host->set_level(std::string(level));
    }
    catch (const std::exception& error) {
        logging::log(logging::Level::error, logging::Channel::plugins, "Plugin level change failed: {}", error.what());
    }
}

bool run_console(const std::string& text) noexcept {
    if (!host) return false;
    try { return host->run_console(text); }
    catch (const std::exception& error) {
        logging::log(logging::Level::error, logging::Channel::plugins, "Plugin command failed: {}", error.what());
        return true;
    }
}

void receive_message(std::uint64_t sender, const std::vector<std::uint8_t>& bytes) noexcept {
    try {
        std::lock_guard lock(messages_mutex);
        if (inbox.size() < 256) inbox.push_back({sender, bytes});
    } catch (...) {}
}

std::vector<std::vector<std::uint8_t>> take_outgoing() noexcept {
    std::lock_guard lock(messages_mutex);
    return std::exchange(outbox, {});
}

std::string describe() {
    if (!host) return "error: Plugins did not start.";
    const auto plugins = host->status();
    if (plugins.empty()) return "No plugins. A plugin is a mod in Mods with a reskate-plugin.json.";
    std::string text;
    for (const auto& plugin : plugins) {
        if (!text.empty()) text += '\n';
        text += std::format("{}: {}", plugin.id, plugin.state);
        if (plugin.state == "running")
            text += std::format(", {:.1f} MB, {:.3f} ms a tick, {} command{}", plugin.memory / 1048576.0, plugin.tick_ms,
                plugin.commands.size(), plugin.commands.size() == 1 ? "" : "s");
        else if (!plugin.reason.empty()) text += " (" + plugin.reason + ")";
    }
    return text;
}

std::string list_commands() {
    if (!host) return "error: Plugins did not start.";
    std::string text;
    for (const auto& plugin : host->status())
        for (const auto& [name, help] : plugin.commands) {
            if (!text.empty()) text += '\n';
            text += std::format("{} - {} ({})", name, help, plugin.id);
        }
    return text.empty() ? "No plugin has a console command." : text;
}

std::string reload(std::string_view id) { return host ? host->reload(id) : "error: Plugins did not start."; }
std::string switch_off(std::string_view id) { return host ? host->switch_off(id) : "error: Plugins did not start."; }
std::string switch_on(std::string_view id) { return host ? host->switch_on(id) : "error: Plugins did not start."; }
}
