#include "plugin_runtime.h"
#include "plugin_discovery.h"
#include "plugin_host.h"
#include "Engine/Core/Log/logging.h"
#include "Extension/Console/commands.h"
#include <Windows.h>
#include <ShlObj.h>
#include <array>
#include <atomic>
#include <chrono>
#include <format>
#include <mutex>
#include <thread>

namespace dingosdk::plugins {
namespace {
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

void watch() {
    FileChecks checks;
    for (;;) {
        try {
            auto plugins = discover(data_root, checks);
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

bool start() noexcept {
    try {
        if (host) return true;
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
        host->tick(dt);
    } catch (const std::exception& error) {
        logging::log(logging::Level::error, logging::Channel::plugins, "Plugin tick failed: {}", error.what());
    }
}

void set_level(std::string_view level) noexcept {
    if (!host) return;
    try { host->set_level(std::string(level)); }
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
