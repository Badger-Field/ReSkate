#pragma once
#include <optional>
#include <string>
#include <string_view>
#include <vector>

// Reading where Steam keeps a game, from Steam's own text files: steamapps/libraryfolders.vdf
// lists the library folders, and each library's steamapps/appmanifest_<app>.acf names the game's
// folder under steamapps/common. Both are Valve's KeyValues text: "key" "value" pairs and
// { } blocks, with backslashes escaped.
namespace dingosdk::launcher_app::steam_library {
// The quoted strings of a KeyValues text, in order, unescaped.
inline std::vector<std::string> tokens(std::string_view text) {
    std::vector<std::string> out;
    for (std::size_t i = 0; i < text.size(); ++i) {
        if (text[i] != '"') continue;
        std::string value;
        for (++i; i < text.size() && text[i] != '"'; ++i) {
            if (text[i] == '\\' && i + 1 < text.size()) ++i;
            value.push_back(text[i]);
        }
        out.push_back(std::move(value));
    }
    return out;
}
// Every "path" in libraryfolders.vdf: the library folders.
inline std::vector<std::string> library_folders(std::string_view vdf) {
    std::vector<std::string> folders;
    const auto all = tokens(vdf);
    for (std::size_t i = 0; i + 1 < all.size(); ++i)
        if (all[i] == "path" && !all[i + 1].empty()) folders.push_back(all[i + 1]);
    return folders;
}
// A key's value in an appmanifest (installdir, for one); nothing when it is not there.
inline std::optional<std::string> manifest_value(std::string_view acf, std::string_view key) {
    const auto all = tokens(acf);
    for (std::size_t i = 0; i + 1 < all.size(); ++i)
        if (all[i] == key) return all[i + 1];
    return std::nullopt;
}
// A folder name from a manifest is used under steamapps/common: never a path of its own.
inline bool plain_folder_name(std::string_view name) {
    return !name.empty() && name != "." && name != ".." && name.find_first_of("/\\:") == std::string_view::npos;
}
} // namespace dingosdk::launcher_app::steam_library
