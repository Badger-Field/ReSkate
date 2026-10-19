#pragma once
#include "plugin_vm.h"
#include <filesystem>
#include <map>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

// Every plugin the game runs: which are loaded, reloaded when their files change, switched off
// with their mod, and stopped when they go over a limit. The host is what plugins share: the
// level being played and the console's command names. One thread drives it (the client tick).
namespace dingosdk::plugins {
// A mod folder that is a plugin, as discovery found it.
struct Candidate {
    std::string id;                  // the mod's folder name
    std::filesystem::path directory; // the mod folder
    std::string fingerprint;         // changes when any of its files does
    std::string refusal;             // why it may not load, from its files alone, or empty
    bool operator==(const Candidate&) const = default;
};

// What the host needs from where it runs: the game, or the test runner.
class Environment {
public:
    virtual ~Environment() = default;
    virtual void log(LogLevel level, std::string_view text) = 0;
    virtual void console(std::string_view text) = 0;
    virtual double now_ms() = 0;
    // <root>/<plugin id>/storage.json holds each plugin's storage.
    virtual std::filesystem::path storage_root() = 0;
    // Whether ReSkate has a console command starting with this word.
    virtual bool builtin_command(std::string_view word) = 0;
};

struct Status {
    std::string id;
    std::string state;  // "running", "stopped", "switched off", "not loaded"
    std::string reason; // why it is not running
    std::size_t memory{};
    double tick_ms{};
    std::vector<std::pair<std::string, std::string>> commands;
};

class Host final : private Services {
public:
    // A client host runs client plugins and those for both sides; a server host, server ones and both.
    Host(Environment& environment, Side side, Limits limits = {});
    ~Host() override;
    Host(const Host&) = delete;
    Host& operator=(const Host&) = delete;

    // The plugins discovery found now: new ones load, changed ones reload, missing ones unload.
    void apply(const std::vector<Candidate>& found);
    void tick(double dt_ms);
    void set_level(std::string level);
    // Runs a plugin's console command when the text starts with one; false when none matched.
    bool run_console(std::string_view text);
    std::vector<Status> status() const;
    // The `plugin` console commands. Each returns what to print.
    std::string reload(std::string_view id);
    std::string switch_off(std::string_view id);
    std::string switch_on(std::string_view id);
    // Unloads every plugin (their unload handlers run).
    void shutdown();

private:
    struct Entry {
        Candidate candidate;
        std::unique_ptr<Plugin> plugin;
        std::string failure; // why it is not running
        bool held{};         // switched off with `plugin off` until `plugin on` or the game restarts
    };
    void load(Entry& entry);
    void unload(Entry& entry);
    // A plugin that stopped itself (a limit, its errors) is closed and its commands released.
    void collect(Entry& entry);
    void release_commands(std::string_view id);
    Entry* find(std::string_view id);

    void log(std::string_view plugin, LogLevel level, std::string_view text) override;
    void console(std::string_view text) override;
    double now_ms() override;
    std::string level() override;
    std::filesystem::path storage_file(std::string_view plugin) override;
    std::string claim_command(std::string_view plugin, std::string_view name) override;

    Environment& environment_;
    Side side_;
    Limits limits_;
    std::map<std::string, Entry, std::less<>> entries_;
    std::map<std::string, std::string, std::less<>> commands_; // command name: plugin id
    std::string level_;
    double next_save_{};
};
}
