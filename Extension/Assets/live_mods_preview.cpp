#include "live_mods.h"
#include "Engine/Vfs/builtin_packs.h"
#include "Engine/Vfs/mod_catalog.h"
#include <Windows.h>
#include <algorithm>
#include <cctype>
#include <map>
#include <mutex>

// What applying the mods will do, worked out from the Mods folder alone (live_mods.h: preview_of),
// so the MODS page's live test can ask it without the game (Extension/Assets/Test/mod_browser_live_test.cpp).
namespace dingosdk::live_mods {
namespace {
// True when everything a mod changes only matters once one of its maps is
// loaded: its TOCs are level superbundles (the root included, which every load
// re-reads) and globals, where maps register themselves. Such a mod needs no
// reload of the level being played; a player sees it by loading the map.
bool scan_maps_only(const std::filesystem::path &directory) {
    std::error_code error;
    for (std::filesystem::recursive_directory_iterator at(directory, error), end; !error && at != end; at.increment(error)) {
        if (!at->is_regular_file(error) || _wcsicmp(at->path().extension().c_str(), L".toc") != 0) continue;
        auto relative = mods::ascii_path(std::filesystem::relative(at->path(), directory, error));
        std::ranges::transform(relative, relative.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        if (relative == "layout.toc" || relative == "win32/globals.toc" || relative.starts_with("win32/levels/")) continue;
        return false;
    }
    return !error;
}
} // namespace

// Cached per folder: the overlay asks every time it redraws its apply button.
bool maps_only_cached(const std::filesystem::path &directory) {
    static std::mutex cache_mutex;
    static std::map<std::wstring, bool> cache;
    std::lock_guard lock(cache_mutex);
    const auto key = directory.wstring();
    if (const auto found = cache.find(key); found != cache.end()) return found->second;
    return cache[key] = scan_maps_only(directory);
}
bool only_maps(const std::filesystem::path &directory) { return scan_maps_only(directory); }

ApplyEffect preview_of(const mods::ModList &list, std::span<const std::string> before, std::string_view level) {
    const auto named = [](std::span<const std::string> names, const std::string &name) {
        return std::ranges::any_of(names, [&](const std::string &other) { return _stricmp(other.c_str(), name.c_str()) == 0; });
    };
    std::vector<std::string> now;
    for (const auto &entry : list.entries)
        if (entry.enabled) now.push_back(entry.mod.name);
    const auto entry_of = [&](const std::string &name) -> const mods::ModEntry * {
        for (const auto &entry : list.entries)
            if (_stricmp(entry.mod.name.c_str(), name.c_str()) == 0) return &entry;
        return nullptr;
    };
    bool reload{};
    for (const auto &name : before) {
        // One of a Board Break deck pack's decks stands for the pack (mod_catalog.h).
        if (const auto pack = mods::board_break_pack_of(name); named(now, pack.empty() ? name : pack)) continue;
        const auto *entry = entry_of(name);
        // Content ReSkate keeps itself (builtin_packs.h) is in no mod list, and stays.
        if (!entry && (name.starts_with("reskate-builtin-") || mods::kept_by_reskate(list.root, name))) continue;
        // A folder deleted since: what it held is unknown, so assume the worst.
        if (!entry) {
            reload = true;
            continue;
        }
        if (!level.empty() && std::ranges::any_of(entry->mod.levels, [&](const std::string &asset) {
                return asset.size() == level.size() && _strnicmp(asset.c_str(), level.data(), level.size()) == 0;
            }))
            return ApplyEffect::leave_level;
        reload |= !maps_only_cached(entry->mod.directory);
    }
    for (const auto &name : now)
        if (!named(before, name))
            if (const auto *entry = entry_of(name)) reload |= !maps_only_cached(entry->mod.directory);
    return reload ? ApplyEffect::reload_level : ApplyEffect::maps_only;
}
} // namespace dingosdk::live_mods
