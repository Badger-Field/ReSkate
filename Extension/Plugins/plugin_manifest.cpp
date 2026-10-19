#include "plugin_manifest.h"
#include "Engine/Core/Json/json.h"
#include <algorithm>
#include <array>
#include <stdexcept>

namespace dingosdk::plugins {
namespace {
constexpr std::array<std::string_view, 35> permissions{
    "console", "storage", "overlay", "menu", "ui.screen", "input", "hud", "events.tricks", "events.skater",
    "skater.read", "skater.teleport", "skater.control", "skater.animation", "trainer", "camera",
    "world.raycast", "world.objects", "world.environment", "world.ai", "travel", "session.read",
    "session.skaters", "session.nametags", "net.session", "net.http", "voice.control", "chat.read",
    "chat.send", "throwdowns", "replay", "music", "progression.read", "settings.read", "audio.ui", "mods.read"};
}

bool known_permission(std::string_view name) noexcept {
    for (const auto known : permissions) if (known == name) return true;
    return false;
}

std::string safe_relative_path(std::string_view path) {
    if (path.empty() || path.size() > 260 || path.front() == '/') return {};
    for (const unsigned char c : path)
        if (c < 0x20 || c > 0x7e || c == '\\' || c == ':') return {};
    std::size_t start = 0;
    while (start <= path.size()) {
        const auto end = std::min(path.find('/', start), path.size());
        const auto segment = path.substr(start, end - start);
        if (segment.empty() || segment == "." || segment == "..") return {};
        // Windows drops trailing dots and spaces, so "a." would open "a".
        if (segment.back() == '.' || segment.back() == ' ') return {};
        start = end + 1;
    }
    return std::string(path);
}

const char* side_name(Side side) noexcept {
    return side == Side::server ? "server" : side == Side::both ? "both" : "client";
}

Manifest parse_manifest(std::string_view text) {
    if (text.size() > maximum_manifest_bytes) throw std::runtime_error("reskate-plugin.json is larger than 64 KB");
    Json json;
    try {
        json = Json::parse(text, {maximum_manifest_bytes, 8, 4096});
    } catch (const std::exception& error) {
        throw std::runtime_error(std::string("reskate-plugin.json is not valid JSON: ") + error.what());
    }
    if (!json.is_object()) throw std::runtime_error("reskate-plugin.json must hold a JSON object");
    Manifest result;
    if (!json.contains("api") || !json.at("api").is_number_integer())
        throw std::runtime_error("reskate-plugin.json needs \"api\": the API version it was written for (1)");
    const auto api = json.at("api").get<std::int64_t>();
    if (api < 1) throw std::runtime_error("\"api\" must be 1 or more");
    if (api > api_version)
        throw std::runtime_error("written for plugin API " + std::to_string(api) + ", and this ReSkate has API " +
            std::to_string(api_version) + ": update ReSkate");
    result.api = static_cast<int>(api);
    if (!json.contains("entry") || !json.at("entry").is_string())
        throw std::runtime_error("reskate-plugin.json needs \"entry\": the Lua file to run, such as \"plugin/main.lua\"");
    result.entry = safe_relative_path(json.at("entry").string());
    if (result.entry.empty())
        throw std::runtime_error("\"entry\" must be a path inside the mod folder, with / between folders and no ..");
    if (!result.entry.ends_with(".lua") && !result.entry.ends_with(".luau"))
        throw std::runtime_error("\"entry\" must be a .lua or .luau file");
    if (json.contains("side")) {
        const auto& side = json.at("side");
        const auto name = side.is_string() ? side.string() : std::string{};
        if (name == "client") result.side = Side::client;
        else if (name == "server") result.side = Side::server;
        else if (name == "both") result.side = Side::both;
        else throw std::runtime_error("\"side\" must be \"client\", \"server\" or \"both\"");
    }
    if (json.contains("permissions")) {
        const auto& list = json.at("permissions");
        if (!list.is_array()) throw std::runtime_error("\"permissions\" must be a list of names");
        for (const auto& item : list) {
            if (!item.is_string()) throw std::runtime_error("\"permissions\" must be a list of names");
            const auto& name = item.string();
            if (!known_permission(name))
                throw std::runtime_error("unknown permission \"" + name + "\": check the spelling, or update ReSkate");
            result.permissions.insert(name);
        }
    }
    if (json.contains("domains")) {
        const auto& list = json.at("domains");
        if (!list.is_array() || list.size() > 16) throw std::runtime_error("\"domains\" must be a list of at most 16 host names");
        for (const auto& item : list) {
            if (!item.is_string()) throw std::runtime_error("\"domains\" must be a list of host names");
            std::string name = item.string();
            for (auto& c : name) if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + 32);
            const bool valid = !name.empty() && name.size() <= 253 && name.front() != '.' && name.back() != '.' &&
                               name.find("..") == std::string::npos && name.find('.') != std::string::npos &&
                               std::all_of(name.begin(), name.end(), [](char c) { return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '-'; });
            if (!valid) throw std::runtime_error("\"" + item.string() + "\" is not a host name for \"domains\" (such as api.example.com: no scheme, port, path or *)");
            result.domains.push_back(name);
        }
        if (!result.domains.empty() && !result.permissions.contains("net.http"))
            throw std::runtime_error("\"domains\" is for web requests: add the \"net.http\" permission too");
    }
    return result;
}
}
