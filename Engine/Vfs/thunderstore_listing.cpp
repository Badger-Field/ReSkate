#include "thunderstore_listing.h"

#include "Engine/Core/Json/json.h"

#include <miniz.h>

#include <algorithm>
#include <array>
#include <cstdio>
#include <optional>
#include <stdexcept>

namespace dingosdk::thunderstore_listing {
namespace {

constexpr char site[] = "https://thunderstore.io";
// As mod_list.h's maximum_mod_name, which the launcher cuts a package's folder to.
constexpr std::size_t longest_folder = 64;
constexpr JsonLimits listing_limits{128ull * 1024 * 1024, 16, 32ull * 1024 * 1024};

[[noreturn]] void fail(const std::string& message) { throw std::runtime_error(message); }

std::string lower(std::string_view text) {
    std::string result(text);
    for (auto& c : result)
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + ('a' - 'A'));
    return result;
}
// Thunderstore's own rule for names and namespaces.
bool plain_name(std::string_view value) {
    return !value.empty() && value.size() <= 128 && std::all_of(value.begin(), value.end(), [](char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_';
    });
}
// Major.Minor.Patch, as Thunderstore numbers versions (and as download_url takes one).
bool plain_version(std::string_view text) {
    unsigned numbers{};
    for (std::size_t at = 0; at <= text.size();) {
        const auto end = std::min(text.find('.', at), text.size());
        const auto number = text.substr(at, end - at);
        if (number.empty() || number.size() > 9 || number.find_first_not_of("0123456789") != std::string_view::npos) return false;
        ++numbers;
        at = end + 1;
    }
    return numbers == 3;
}
std::string text(const Json& row, std::string_view key) {
    return row.contains(key) && row.at(key).is_string() ? row.at(key).string() : std::string();
}
std::uint64_t count(const Json& row, std::string_view key) {
    if (!row.contains(key) || !row.at(key).is_number()) return 0;
    const auto value = row.at(key).get<double>();
    return value > 0 && value < 1e18 ? static_cast<std::uint64_t>(value) : 0;
}
bool flag(const Json& row, std::string_view key) {
    return row.contains(key) && row.at(key).is_boolean() && row.at(key).get<bool>();
}
std::vector<std::string> strings(const Json& row, std::string_view key) {
    std::vector<std::string> result;
    if (!row.contains(key) || !row.at(key).is_array()) return result;
    for (const auto& item : row.at(key))
        if (item.is_string()) result.push_back(item.string());
    return result;
}
// https://thunderstore.io/... or https://<host>.thunderstore.io/..., and nothing that is not a plain URL.
bool thunderstore_url(std::string_view url) {
    constexpr std::string_view scheme = "https://";
    if (!url.starts_with(scheme) || url.size() > 1000) return false;
    const auto path = url.find('/', scheme.size());
    if (path == std::string_view::npos) return false;
    const auto host = url.substr(scheme.size(), path - scheme.size());
    const bool plain = std::all_of(url.begin(), url.end(), [](unsigned char c) {
        return c > 0x20 && c < 0x7f && c != '"' && c != '<' && c != '>' && c != '\\' && c != '@';
    });
    return plain && (host == "thunderstore.io" || (host.ends_with(".thunderstore.io") && host.size() > 16));
}

std::optional<Package> parse_package(const Json& row) {
    if (!row.is_object()) return std::nullopt;
    Package package;
    package.name = text(row, "name");
    package.owner = text(row, "owner");
    package.full_name = text(row, "full_name");
    if (!plain_name(package.name) || !plain_name(package.owner) || package.full_name != package.owner + "-" + package.name)
        return std::nullopt;
    package.categories = strings(row, "categories");
    package.date_updated = text(row, "date_updated");
    package.rating = static_cast<int>(std::min<std::uint64_t>(count(row, "rating_score"), 1u << 30));
    package.pinned = flag(row, "is_pinned");
    package.deprecated = flag(row, "is_deprecated");
    package.nsfw = flag(row, "has_nsfw_content");
    const Json* newest{};
    if (row.contains("versions") && row.at("versions").is_array())
        for (const auto& version : row.at("versions")) {
            if (!version.is_object() || (version.contains("is_active") && !flag(version, "is_active"))) continue;
            const auto number = text(version, "version_number");
            if (!plain_version(number)) continue;
            package.downloads += count(version, "downloads");
            if (!newest || compare_versions(number, text(*newest, "version_number")) > 0) newest = &version;
        }
    if (!newest) return std::nullopt;
    package.version = text(*newest, "version_number");
    package.description = one_line(text(*newest, "description"), 300);
    package.icon = text(*newest, "icon");
    if (!thunderstore_url(package.icon)) package.icon.clear();
    package.file_size = count(*newest, "file_size");
    package.dependencies = strings(*newest, "dependencies");
    return package;
}

std::uint32_t little32(const unsigned char* bytes) {
    return bytes[0] | (bytes[1] << 8) | (bytes[2] << 16) | (static_cast<std::uint32_t>(bytes[3]) << 24);
}

// "Owner-Name-1.2.3" as {"Owner-Name", "1.2.3"}; the version is empty when it does not end in one.
std::pair<std::string, std::string> split_dependency(std::string_view text) {
    const auto dash = text.rfind('-');
    if (dash == std::string_view::npos || dash == 0 || !plain_version(text.substr(dash + 1))) return {std::string(text), {}};
    return {std::string(text.substr(0, dash)), std::string(text.substr(dash + 1))};
}

bool contains_word(const Package& package, std::string_view word) {
    const auto in = [&](std::string_view field) { return lower(field).find(word) != std::string::npos; };
    return in(package.title()) || in(package.name) || in(package.owner) || in(package.description) ||
           std::any_of(package.categories.begin(), package.categories.end(), in);
}

} // namespace

std::string community(std::string_view requested) {
    const bool slug = !requested.empty() && requested.size() <= 64 && std::all_of(requested.begin(), requested.end(), [](char c) {
        return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-';
    });
    return slug ? std::string(requested) : std::string(default_community);
}

std::string listing_index_url(std::string_view community) {
    return std::string(site) + "/c/" + std::string(community) + "/api/v1/package-listing-index/";
}

std::string listing_url(std::string_view community) {
    return std::string(site) + "/c/" + std::string(community) + "/api/v1/package/";
}

std::string Package::folder() const { return full_name.substr(0, std::min(full_name.size(), longest_folder)); }

std::string Package::title() const {
    auto result = name;
    std::replace(result.begin(), result.end(), '_', ' ');
    return result;
}

std::vector<Package> parse_listing(std::string_view json) {
    const auto root = Json::parse(json, listing_limits);
    if (!root.is_array()) fail("The Thunderstore listing is not a JSON array.");
    std::vector<Package> packages;
    for (const auto& row : root)
        if (auto package = parse_package(row)) packages.push_back(std::move(*package));
    return packages;
}

std::vector<std::string> parse_index(std::string_view json) {
    const auto root = Json::parse(json, JsonLimits{1024 * 1024, 4, 65536});
    if (!root.is_array()) fail("The Thunderstore listing index is not a JSON array.");
    std::vector<std::string> urls;
    for (const auto& item : root) {
        if (!item.is_string() || !thunderstore_url(item.string())) fail("The Thunderstore listing index names a chunk elsewhere.");
        urls.push_back(item.string());
    }
    return urls;
}

std::string gunzip(std::string_view data, std::size_t limit) {
    const auto* bytes = reinterpret_cast<const unsigned char*>(data.data());
    if (data.size() < 2 || bytes[0] != 0x1f || bytes[1] != 0x8b) {
        if (data.size() > limit) fail("The Thunderstore listing is too large.");
        return std::string(data);
    }
    if (data.size() < 18 || bytes[2] != 8) fail("The Thunderstore listing is not a gzip stream that can be read.");
    const unsigned flags = bytes[3];
    std::size_t offset = 10;
    const auto need = [&](std::size_t more) { if (offset + more > data.size() - 8) fail("The Thunderstore listing is truncated."); };
    if (flags & 0x04) { // FEXTRA
        need(2);
        const std::size_t extra = bytes[offset] | (bytes[offset + 1] << 8);
        offset += 2;
        need(extra);
        offset += extra;
    }
    for (const unsigned field : {0x08u, 0x10u}) { // FNAME, FCOMMENT: zero-terminated
        if (!(flags & field)) continue;
        while (true) { need(1); if (bytes[offset++] == 0) break; }
    }
    if (flags & 0x02) { need(2); offset += 2; } // FHCRC
    const auto crc = little32(bytes + data.size() - 8);
    const std::size_t size = little32(bytes + data.size() - 4);
    if (size > limit) fail("The Thunderstore listing is too large.");
    std::string output(size, '\0');
    const auto written = tinfl_decompress_mem_to_mem(output.data(), output.size(), bytes + offset, data.size() - 8 - offset, 0);
    if (written == TINFL_DECOMPRESS_MEM_TO_MEM_FAILED || written != size ||
        mz_crc32(MZ_CRC32_INIT, reinterpret_cast<const unsigned char*>(output.data()), output.size()) != crc)
        fail("The Thunderstore listing is damaged (gzip check failed).");
    return output;
}

int compare_versions(std::string_view a, std::string_view b) {
    while (!a.empty() || !b.empty()) {
        const auto take = [](std::string_view& value) {
            const auto dot = value.find('.');
            const auto part = value.substr(0, dot);
            value = dot == std::string_view::npos ? std::string_view() : value.substr(dot + 1);
            return part;
        };
        const auto left = take(a), right = take(b);
        const bool numeric = left.find_first_not_of("0123456789") == std::string_view::npos &&
                             right.find_first_not_of("0123456789") == std::string_view::npos;
        if (numeric) {
            // Leading zeros aside, the longer number is the larger one.
            const auto l = left.substr(std::min(left.find_first_not_of('0'), left.size()));
            const auto r = right.substr(std::min(right.find_first_not_of('0'), right.size()));
            if (l.size() != r.size()) return l.size() < r.size() ? -1 : 1;
            if (const auto order = l.compare(r)) return order < 0 ? -1 : 1;
        } else if (const auto order = left.compare(right)) {
            return order < 0 ? -1 : 1;
        }
    }
    return 0;
}

std::string folder_key(std::string_view folder) { return lower(folder); }

Action action(const Package& package, const InstalledMods& installed) {
    const auto found = installed.find(folder_key(package.folder()));
    if (found == installed.end()) return Action::install;
    return plain_version(found->second.version) && compare_versions(package.version, found->second.version) > 0 ? Action::update
                                                                                                              : Action::installed;
}

bool nsfw(const Package& package) {
    return package.nsfw || std::any_of(package.categories.begin(), package.categories.end(),
                                       [](const std::string& category) { return lower(category) == "nsfw"; });
}

std::vector<const Package*> search(std::span<const Package> packages, std::string_view query, const InstalledMods& installed,
                                   const Kept& kept) {
    std::vector<std::string> words;
    const auto wanted = lower(query);
    for (std::size_t at = 0; at < wanted.size();) {
        const auto end = std::min(wanted.find(' ', at), wanted.size());
        if (end > at) words.push_back(wanted.substr(at, end - at));
        at = end + 1;
    }
    std::vector<const Package*> result;
    for (const auto& package : packages) {
        if (nsfw(package)) continue;
        if (package.deprecated && !installed.contains(folder_key(package.folder()))) continue;
        if (kept && kept(package.folder())) continue;
        if (std::all_of(words.begin(), words.end(), [&](const std::string& word) { return contains_word(package, word); }))
            result.push_back(&package);
    }
    std::stable_sort(result.begin(), result.end(), [](const Package* a, const Package* b) {
        if (a->pinned != b->pinned) return a->pinned;
        if (a->date_updated != b->date_updated) return a->date_updated > b->date_updated;
        return lower(a->title()) < lower(b->title());
    });
    return result;
}

std::vector<std::string> missing_dependencies(const Package& package, const InstalledMods& installed, const Kept& kept) {
    std::vector<std::string> missing;
    for (const auto& dependency : package.dependencies) {
        const auto [name, version] = split_dependency(dependency);
        const auto folder = name.substr(0, std::min(name.size(), longest_folder));
        if (kept && kept(folder)) continue;
        const auto found = installed.find(folder_key(folder));
        if (found != installed.end() && (version.empty() || compare_versions(found->second.version, version) >= 0)) continue;
        missing.push_back(dependency);
    }
    return missing;
}

std::string refuse_contents(std::span<const std::string> files) {
    constexpr std::size_t most_files = 100000;
    if (files.empty()) return "Its package holds no files.";
    if (files.size() > most_files) return "Its package holds more files than any mod does.";
    static constexpr std::string_view top_files[]{
        "manifest.json", "reskate-mod.json", "reskate-build.json", ".reskate-studio-patch", "reskate-levels.json",
        "layout.toc", "icon.png", "readme.md", "changelog.md", "license", "license.md", "license.txt",
        // A copy of the game's InitFS archive beside the layout, as mods exported from a whole patch
        // folder carry it. ReSkate never reads it from a mod: the game's InitFS is read from its own
        // Data and Patch folders only (Engine/Vfs/initfs.cpp), the merge takes a mod's layout.toc
        // and Win32 TOCs and archives and nothing else (mod_merge.cpp, mod_merge_files.cpp: scan),
        // and the only layer mounted from Mods is the merged patch (Extension/Assets/mod_layers.cpp).
        // So it is installed as it is, as the launcher installs it, and nothing in it runs. Anywhere
        // else in the folder it is refused.
        "initfs_win32"};
    static constexpr std::string_view game_data[]{".toc", ".sb", ".cas"};
    static constexpr std::string_view inert[]{".png", ".jpg", ".jpeg", ".md", ".txt"};
    const auto ends_in = [](std::string_view name, std::span<const std::string_view> endings) {
        return std::any_of(endings.begin(), endings.end(), [&](std::string_view ending) { return name.size() > ending.size() && name.ends_with(ending); });
    };
    bool loads{};
    std::vector<std::string> refused; // the first few files that may not be installed, for the reason
    std::size_t more{};
    bool frosty{};
    for (const auto& file : files) {
        const auto path = lower(file);
        // Plain ASCII names only, as every name the game uses is (mod_list.h's ascii_path), with no
        // way out of the folder: no absolute path, drive, stream, "." or "..", empty part or
        // backslash.
        bool safe = !path.empty() && path.size() <= 240 && std::all_of(path.begin(), path.end(), [](unsigned char c) {
            return c >= 0x20 && c < 0x7f && c != '\\' && c != ':' && c != '*' && c != '?' && c != '"' && c != '<' && c != '>' && c != '|';
        });
        std::vector<std::string_view> parts;
        for (std::size_t at = 0; safe && at <= path.size();) {
            const auto end = std::min(path.find('/', at), path.size());
            const auto part = std::string_view(path).substr(at, end - at);
            if (part.empty() || part == "." || part == ".." || part.back() == '.' || part.back() == ' ' || part.front() == ' ') safe = false;
            parts.push_back(part);
            at = end + 1;
        }
        if (!safe || parts.size() > 16) return "Its package holds a file with a name no mod uses (" + one_line(file, 80) + ").";
        const auto leaf = parts.back();
        bool allowed = ends_in(leaf, inert);
        if (parts.size() == 1 && std::find(std::begin(top_files), std::end(top_files), leaf) != std::end(top_files)) {
            allowed = true;
            loads = loads || leaf == "layout.toc" || leaf == "reskate-levels.json";
        }
        if (parts.size() == 2 && parts[0] == "parks" && ends_in(leaf, std::array<std::string_view, 1>{".park.json"})) allowed = loads = true;
        if (parts.size() >= 2 && parts[0] == "win32" && ends_in(leaf, game_data)) allowed = true;
        if (!allowed) {
            frosty = frosty || ends_in(leaf, std::array<std::string_view, 1>{".fbmod"});
            if (refused.size() < 3) refused.push_back(one_line(file, 80));
            else ++more;
        }
    }
    if (!refused.empty()) {
        std::string names = refused.front();
        for (std::size_t i = 1; i < refused.size(); ++i) names += (i + 1 == refused.size() && !more ? " and " : ", ") + refused[i];
        if (more) names += " and " + std::to_string(more) + " other file(s)";
        // (A Frosty mod is not something ReSkate loads at all: nothing reads or converts one.)
        return "Its package holds " + names + ", which a mod installed from the game may not hold "
               "(only game data, maps, parks and what describes them)." +
               (frosty ? std::string(" A .fbmod is a Frosty mod, which ReSkate does not load.") : std::string());
    }
    if (!loads) return "Its package has nothing for the game to load (no layout.toc, reskate-levels.json or parks).";
    return {};
}

std::string one_line(std::string_view text, std::size_t most) {
    std::string out;
    bool space{};
    for (const unsigned char c : text) {
        if (c < 0x20 || c == 0x7f || c == ' ') {
            space = !out.empty();
            continue;
        }
        if (space) out += ' ';
        space = false;
        out += static_cast<char>(c);
    }
    if (out.size() <= most) return out;
    out.resize(most);
    // (Never half a UTF-8 character.)
    while (!out.empty() && (static_cast<unsigned char>(out.back()) & 0xc0) == 0x80) out.pop_back();
    if (!out.empty() && (static_cast<unsigned char>(out.back()) & 0x80)) out.pop_back();
    return out + "...";
}

const Package* find(std::span<const Package> packages, std::string_view full_name) {
    const auto wanted = lower(full_name);
    for (const auto& package : packages)
        if (lower(package.full_name) == wanted) return &package;
    return nullptr;
}

std::string size_text(std::uint64_t bytes) {
    const auto text = [](double value, int decimals) {
        char out[32]{};
        std::snprintf(out, sizeof(out), "%.*f", decimals, value);
        return std::string(out);
    };
    const auto mb = static_cast<double>(bytes) / (1024.0 * 1024.0);
    if (mb >= 1024.0) return text(mb / 1024.0, 2) + " GB";
    if (mb >= 1.0) return text(mb, 0) + " MB";
    return std::to_string(std::max<std::uint64_t>(1, bytes / 1024)) + " KB";
}

std::uint64_t progress_step(std::uint64_t received, std::uint64_t total) {
    constexpr std::uint64_t unknown_step = 16ull << 20;
    if (!total) return received / unknown_step;
    // (Tenths, without overflowing on a large download; at most 10.)
    return std::min<std::uint64_t>(10, received >= total ? 10 : received / (total / 10 ? total / 10 : 1));
}

std::string row_text(const Package& package, Action action, std::string_view refused) {
    std::string out = package.full_name + "  v" + package.version + "  " + (package.file_size ? size_text(package.file_size) : "size unknown") + "  ";
    if (action == Action::installed) return out + "installed";
    if (!refused.empty()) return out + "refused: " + std::string(refused);
    return out + (action == Action::update ? "update" : "installable");
}

} // namespace dingosdk::thunderstore_listing
