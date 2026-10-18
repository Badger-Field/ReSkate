#pragma once
#include "steam_library.h"

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

// "Use the game on this PC": a skate. already installed (Steam's library, or a folder the player
// picks) is copied into ReSkate's own folder, in place of the Steam download. The installed game
// is only read: its files are never written, moved or renamed, and Steam is neither started nor
// asked anything (its libraryfolders.vdf and appmanifest are read as text). A copy, not hard links
// or junctions, so a later Steam update of that install cannot change ReSkate's game.
namespace dingosdk::launcher_app::local_game {
namespace fs = std::filesystem;

inline constexpr std::string_view steam_app_id = "3354750";
// A file being copied is written under this name beside its target, and renamed when complete:
// a copy that stops part-way never leaves a file that looks done.
inline constexpr std::string_view partial_suffix = ".reskate-copy";

// What a skate. folder has at the least: Skate.exe and the data's table of contents. (Whether it is
// the supported build is game_files_supported's to say, in launch.cpp.)
inline bool looks_like_game(const fs::path& folder) {
    std::error_code error;
    return fs::is_regular_file(folder / "Skate.exe", error) && fs::is_regular_file(folder / "Data" / "layout.toc", error);
}

// A path as UTF-8 text, for messages.
inline std::string text(const fs::path& path) {
    const auto utf8 = path.u8string();
    return std::string(utf8.begin(), utf8.end());
}

inline std::string lower_name(const fs::path& name) {
    auto out = text(name);
    std::transform(out.begin(), out.end(), out.begin(), [](char c) { return c >= 'A' && c <= 'Z' ? static_cast<char>(c + 32) : c; });
    return out;
}

// Whether the file at `relative` (to the game folder) is the game's own and is copied: not ReSkate's
// files (launcher, runtime, settings, logs, mods) that an older ReSkate put in Steam's folder, nor
// the player's things there. `reskate_export`: the scripts and config folders are ReSkate's export
// of the game's scripts (scripts\.initfs-export.json says so), the player's edits, not game files.
inline bool game_file(const fs::path& relative, bool reskate_export = false) {
    const auto first = lower_name(*relative.begin());
    const auto name = lower_name(relative.filename());
    const bool top = std::distance(relative.begin(), relative.end()) == 1;
    if (name.ends_with(partial_suffix) || name.starts_with(".reskate")) return false;
    if (top) {
        // ReSkateLauncher.exe (and .old), ReSkate.dll, ReSkate.log, ReSkate*.json and the like.
        if (name.starts_with("reskate")) return false;
        for (const auto* own : {"gamenetworkingsockets.dll", "api-ms-win-core-debug-minidump-l1-1-0.dll",
                                "texture_limit_stock.txt"})
            if (name == own) return false;
        return true;
    }
    for (const auto* own : {"mods", "moddata", "logs", ".depotdownloader"})
        if (first == own) return false;
    if (reskate_export && (first == "scripts" || first == "config")) return false;
    return true;
}

// The same file is in place already: same size and write time (the copy is given the source's).
inline bool already_copied(const fs::path& from, const fs::path& to) {
    std::error_code error;
    if (!fs::is_regular_file(to, error)) return false;
    const auto size = fs::file_size(to, error);
    if (error || size != fs::file_size(from, error) || error) return false;
    const auto written = fs::last_write_time(to, error);
    return !error && written == fs::last_write_time(from, error) && !error;
}

// Whether one folder is the other or holds it: the game is never copied into itself.
inline bool overlapping(const fs::path& a, const fs::path& b) {
    std::error_code error;
    const auto x = fs::weakly_canonical(a, error), y = fs::weakly_canonical(b, error);
    const auto inside = [](const fs::path& outer, const fs::path& inner) {
        auto at = inner.begin();
        for (const auto& part : outer) {
            if (part.empty()) continue;
            if (at == inner.end() || lower_name(*at) != lower_name(part)) return false;
            ++at;
        }
        return true;
    };
    return inside(x, y) || inside(y, x);
}

struct CopyPlan {
    std::vector<fs::path> files; // relative, still to copy
    std::uintmax_t bytes{};      // their size
    std::size_t in_place{};      // game files already copied, left as they are
};

// What copying `source` into `target` has left to do.
inline CopyPlan plan_copy(const fs::path& source, const fs::path& target) {
    CopyPlan plan;
    std::error_code error;
    const bool reskate_export = fs::is_regular_file(source / "scripts" / ".initfs-export.json", error);
    fs::recursive_directory_iterator at(source, fs::directory_options::skip_permission_denied, error), end;
    if (error) throw std::runtime_error("Cannot read the game folder " + text(source) + ": " + error.message());
    for (; at != end; at.increment(error)) {
        if (error) throw std::runtime_error("Cannot read the game folder: " + error.message());
        const auto relative = at->path().lexically_relative(source);
        if (at->is_directory(error)) {
            // A folder left out is not walked (a big Mods folder, say).
            if (!game_file(relative / "x", reskate_export)) at.disable_recursion_pending();
            continue;
        }
        if (!at->is_regular_file(error) || !game_file(relative, reskate_export)) continue;
        if (already_copied(at->path(), target / relative)) { ++plan.in_place; continue; }
        plan.files.push_back(relative);
        plan.bytes += at->file_size(error);
    }
    return plan;
}

// Copies one file: under the partial name first, then given the source's write time and renamed
// over the target. `copied` hears each block. False, with nothing left behind, when `cancel` was set.
inline bool copy_one(const fs::path& from, const fs::path& to, const std::function<void(std::uintmax_t)>& copied,
                     const std::atomic<bool>& cancel) {
    auto partial = to;
    partial += std::string(partial_suffix);
    std::error_code error;
    fs::create_directories(to.parent_path(), error);
    {
        std::ifstream in(from, std::ios::binary);
        if (!in) throw std::runtime_error("Cannot read " + text(from));
        std::ofstream out(partial, std::ios::binary | std::ios::trunc);
        if (!out) throw std::runtime_error("Cannot write " + text(partial));
        std::vector<char> buffer(4u << 20);
        while (in) {
            if (cancel) {
                out.close();
                fs::remove(partial, error);
                return false;
            }
            in.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
            const auto got = in.gcount();
            if (got <= 0) break;
            if (!out.write(buffer.data(), got)) {
                out.close();
                fs::remove(partial, error);
                throw std::runtime_error("Cannot write " + text(to) + " (is the disk full?)");
            }
            if (copied) copied(static_cast<std::uintmax_t>(got));
        }
        if (in.bad()) {
            out.close();
            fs::remove(partial, error);
            throw std::runtime_error("Cannot read " + text(from));
        }
    }
    fs::last_write_time(partial, fs::last_write_time(from));
    fs::rename(partial, to);
    return true;
}

// Steam's skate. folders, from its own files under `steam` (the Steam client's folder): every
// library in steamapps/libraryfolders.vdf (and Steam's own folder), and in each the folder its
// appmanifest_3354750.acf names under steamapps/common. Only folders that look like the game.
inline std::vector<fs::path> steam_game_folders(const fs::path& steam) {
    const auto read = [](const fs::path& file) {
        std::ifstream in(file, std::ios::binary);
        return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
    };
    const auto path_of = [](const std::string& utf8) { return fs::path(std::u8string(utf8.begin(), utf8.end())); };
    std::vector<fs::path> libraries{steam};
    for (const auto* vdf : {"steamapps/libraryfolders.vdf", "config/libraryfolders.vdf"})
        for (const auto& folder : steam_library::library_folders(read(steam / vdf))) libraries.push_back(path_of(folder));
    std::vector<fs::path> found;
    for (const auto& library : libraries) {
        const auto manifest = read(library / "steamapps" / ("appmanifest_" + std::string(steam_app_id) + ".acf"));
        const auto folder = steam_library::manifest_value(manifest, "installdir");
        if (!folder || !steam_library::plain_folder_name(*folder)) continue;
        const auto game = library / "steamapps" / "common" / path_of(*folder);
        if (!looks_like_game(game)) continue;
        if (std::none_of(found.begin(), found.end(), [&](const fs::path& seen) {
                std::error_code error;
                return fs::equivalent(seen, game, error);
            }))
            found.push_back(game);
    }
    return found;
}
} // namespace dingosdk::launcher_app::local_game
