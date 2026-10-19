#include "plugin_host.h"
#include "plugin_api.h"
#include "Engine/Core/Console/console_core.h"
#include <format>
#include <utility>

namespace dingosdk::plugins {
namespace {
std::string lowercase(std::string text) {
    for (auto& c : text) if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + 32);
    return text;
}
}

Host::Host(Environment& environment, Side side, Limits limits) : environment_(environment), side_(side), limits_(limits) {}

Host::~Host() { shutdown(); }

Host::Entry* Host::find(std::string_view id) {
    const auto found = entries_.find(id);
    return found == entries_.end() ? nullptr : &found->second;
}

void Host::load(Entry& entry) {
    entry.failure.clear();
    entry.plugin.reset();
    const auto& id = entry.candidate.id;
    if (!entry.candidate.refusal.empty()) {
        entry.failure = entry.candidate.refusal;
        environment_.log(LogLevel::warn, std::format("Plugin {} was not loaded: {}", id, entry.failure));
        return;
    }
    Manifest manifest;
    try {
        std::string error;
        const auto text = read_package_file(entry.candidate.directory / manifest_file, maximum_manifest_bytes, error);
        if (!error.empty()) throw std::runtime_error(std::string(manifest_file) + " " + error);
        manifest = parse_manifest(text);
    } catch (const std::exception& error) {
        entry.failure = error.what();
        environment_.log(LogLevel::warn, std::format("Plugin {} was not loaded: {}", id, entry.failure));
        return;
    }
    const bool here = manifest.side == Side::both || manifest.side == side_;
    if (!here) {
        entry.failure = std::format("it is a {} plugin", side_name(manifest.side));
        environment_.log(LogLevel::info, std::format("Plugin {} was not loaded here: {}", id, entry.failure));
        return;
    }
    std::string permissions;
    for (const auto& permission : manifest.permissions) permissions += (permissions.empty() ? "" : ", ") + permission;
    Services& services = *this;
    entry.plugin = std::make_unique<Plugin>(id, entry.candidate.directory, std::move(manifest), services, limits_);
    if (!entry.plugin->load()) {
        entry.failure = entry.plugin->reason();
        entry.plugin.reset();
        release_commands(id); // its own log says why it stopped
        return;
    }
    environment_.log(LogLevel::info, std::format("Plugin {} loaded ({}).", id, permissions.empty() ? "no permissions" : permissions));
}

void Host::unload(Entry& entry) {
    if (entry.plugin) {
        entry.plugin->unload();
        entry.plugin.reset();
    }
    release_commands(entry.candidate.id);
}

void Host::collect(Entry& entry) {
    if (!entry.plugin || entry.plugin->state() != State::stopped) return;
    entry.failure = entry.plugin->reason();
    entry.plugin->close_if_stopped();
    entry.plugin.reset();
    release_commands(entry.candidate.id);
}

void Host::release_commands(std::string_view id) {
    std::erase_if(commands_, [&](const auto& item) { return item.second == id; });
}

void Host::apply(const std::vector<Candidate>& found) {
    std::map<std::string, const Candidate*, std::less<>> wanted;
    for (const auto& candidate : found) wanted.emplace(candidate.id, &candidate);
    for (auto it = entries_.begin(); it != entries_.end();) {
        if (wanted.contains(it->first)) {
            ++it;
            continue;
        }
        if (it->second.plugin) environment_.log(LogLevel::info, std::format("Plugin {} unloaded: its mod was switched off or removed.", it->first));
        unload(it->second);
        it = entries_.erase(it);
    }
    for (const auto& [id, candidate] : wanted) {
        auto* entry = find(id);
        if (!entry) {
            entry = &entries_.emplace(id, Entry{*candidate, {}, {}, false}).first->second;
            load(*entry);
            continue;
        }
        if (entry->candidate == *candidate) continue;
        entry->candidate = *candidate;
        if (entry->held) continue;
        environment_.log(LogLevel::info, std::format("Plugin {} reloading: its files changed.", id));
        unload(*entry);
        load(*entry);
    }
}

void Host::tick(double dt_ms) {
    const double now = environment_.now_ms();
    const bool save = now >= next_save_;
    if (save) next_save_ = now + 2000;
    for (auto& [id, entry] : entries_) {
        if (!entry.plugin) continue;
        entry.plugin->tick(dt_ms);
        collect(entry);
        if (save && entry.plugin) entry.plugin->save_storage();
    }
}

void Host::set_level(std::string level) {
    if (level == level_) return;
    const auto previous = std::exchange(level_, std::move(level));
    for (auto& [id, entry] : entries_) {
        if (!entry.plugin) continue;
        entry.plugin->level_changed(previous, level_);
        collect(entry);
    }
}

bool Host::run_console(std::string_view text) {
    if (commands_.empty()) return false;
    const auto parsed = parse_console_command(text);
    if (!parsed || parsed.arguments.empty()) return false;
    const auto& words = parsed.arguments;
    // The longest command name the text starts with.
    for (std::size_t count = std::min<std::size_t>(3, words.size()); count > 0; --count) {
        std::string name;
        for (std::size_t i = 0; i < count; ++i) name += (i ? " " : "") + lowercase(words[i]);
        const auto found = commands_.find(name);
        if (found == commands_.end()) continue;
        auto* entry = find(found->second);
        if (!entry || !entry->plugin) return false;
        entry->plugin->run_command(name, {words.begin() + static_cast<std::ptrdiff_t>(count), words.end()});
        collect(*entry);
        return true;
    }
    return false;
}

std::vector<Status> Host::status() const {
    std::vector<Status> result;
    for (const auto& [id, entry] : entries_) {
        Status status{id};
        if (entry.plugin) {
            status.state = "running";
            status.memory = entry.plugin->memory();
            status.tick_ms = entry.plugin->tick_ms();
            status.commands = entry.plugin->commands();
        } else if (entry.held) {
            status.state = "switched off";
            status.reason = "plugin on " + id + " starts it again";
        } else {
            status.state = entry.failure.starts_with("it is a ") ? "not loaded" : "stopped";
            status.reason = entry.failure;
        }
        result.push_back(std::move(status));
    }
    return result;
}

std::string Host::reload(std::string_view id) {
    auto* entry = find(id);
    if (!entry) return std::format("error: There is no plugin {}.", id);
    entry->held = false;
    unload(*entry);
    load(*entry);
    return entry->plugin ? std::format("Plugin {} reloaded.", id) : std::format("error: Plugin {} did not start: {}", id, entry->failure);
}

std::string Host::switch_off(std::string_view id) {
    auto* entry = find(id);
    if (!entry) return std::format("error: There is no plugin {}.", id);
    entry->held = true;
    unload(*entry);
    return std::format("Plugin {} switched off until plugin on {}, or the next launch.", id, id);
}

std::string Host::switch_on(std::string_view id) {
    auto* entry = find(id);
    if (!entry) return std::format("error: There is no plugin {}.", id);
    if (entry->plugin) return std::format("Plugin {} is running.", id);
    entry->held = false;
    load(*entry);
    return entry->plugin ? std::format("Plugin {} started.", id) : std::format("error: Plugin {} did not start: {}", id, entry->failure);
}

void Host::shutdown() {
    for (auto& [id, entry] : entries_) unload(entry);
    entries_.clear();
}

void Host::log(std::string_view plugin, LogLevel level, std::string_view text) {
    environment_.log(level, std::format("[{}] {}", plugin, text));
}
void Host::console(std::string_view text) { environment_.console(text); }
double Host::now_ms() { return environment_.now_ms(); }
std::string Host::level() { return level_; }
std::filesystem::path Host::storage_file(std::string_view plugin) {
    return environment_.storage_root() / std::filesystem::path(std::string(plugin)) / "storage.json";
}

std::string Host::claim_command(std::string_view plugin, std::string_view name) {
    const auto first = name.substr(0, name.find(' '));
    if (first == "plugin" || first == "help" || environment_.builtin_command(first))
        return std::format("ReSkate has a console command \"{}\": pick another name", first);
    for (const auto& [taken, owner] : commands_) {
        if (owner == plugin) continue;
        const bool overlaps = taken == name || name.starts_with(taken + " ") || taken.starts_with(std::string(name) + " ");
        if (overlaps) return std::format("the plugin {} has the console command \"{}\"", owner, taken);
    }
    commands_.emplace(std::string(name), std::string(plugin));
    return {};
}
}
