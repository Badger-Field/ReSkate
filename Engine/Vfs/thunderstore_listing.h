#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <map>
#include <span>
#include <string>
#include <string_view>
#include <vector>

// ReSkate's Thunderstore community as the game's MODS page lists it (Extension/Assets/mod_browser.h):
// the listing, searching it, what can be installed from inside the game and what such a package may
// hold. No network and nothing of Windows here; the browser fetches. The launcher has a browser of
// its own (Launcher/thunderstore.h): the endpoints, the community and the folder a package installs
// as are the same as its.
namespace dingosdk::thunderstore_listing {

inline constexpr char default_community[] = "reskate";
// `requested` when it is a community's slug (lower-case letters, digits and '-'), else reskate:
// RESKATE_THUNDERSTORE_COMMUNITY, read as the launcher reads it.
std::string community(std::string_view requested);
// /c/<community>/api/v1/package-listing-index/: a gzipped JSON array of gzipped listing chunks.
// The plain listing is the fallback, as in the launcher.
std::string listing_index_url(std::string_view community);
std::string listing_url(std::string_view community);

// A package as the listing gives it, with its newest active version.
struct Package {
    std::string owner, name;            // letters, digits and '_'
    std::string full_name;              // Owner-Name
    std::vector<std::string> categories;
    std::string date_updated;           // ISO 8601, so it sorts as text
    bool pinned{}, deprecated{}, nsfw{};
    int rating{};
    std::uint64_t downloads{};          // every version together
    std::string version;                // Major.Minor.Patch
    std::string description, icon;      // the newest version's; icon is an https URL or empty
    std::uint64_t file_size{};          // 0 when not given
    std::vector<std::string> dependencies; // Owner-Name-1.2.3 each
    // The Mods folder it installs as: Owner-Name, as the launcher installs it.
    [[nodiscard]] std::string folder() const;
    // The name as the site shows it: underscores as spaces.
    [[nodiscard]] std::string title() const;
};

// One listing chunk, or the whole plain listing: a JSON array of packages. Rows that are malformed,
// have no active version or a version that is not Major.Minor.Patch are skipped; throws when the
// answer is not an array.
std::vector<Package> parse_listing(std::string_view json);
// The listing index: a JSON array of chunk URLs, each https on thunderstore.io or one of its
// hosts. Throws on anything else, so the plain listing is read instead.
std::vector<std::string> parse_index(std::string_view json);
// Unpacks a gzip stream (the index and its chunks), refusing output over `limit` bytes. Input that
// is not gzip comes back unchanged.
std::string gunzip(std::string_view data, std::size_t limit);

// Below zero, zero or above zero as `a` is older, the same or newer than `b`. Components compare as
// numbers ("1.10.0" is newer than "1.9.0").
int compare_versions(std::string_view a, std::string_view b);

// The mods installed here, by folder name in lower case: the version their manifest gives (empty
// when none) and whether mods.json has them on.
struct Installed {
    std::string version;
    bool enabled{};
};
using InstalledMods = std::map<std::string, Installed, std::less<>>;
std::string folder_key(std::string_view folder);

enum class Action {
    install,    // not here
    update,     // here, at an older version than the newest
    installed,  // here at the newest, or at a version that cannot be compared
};
Action action(const Package& package, const InstalledMods& installed);

// Mod folders (Owner-Name) ReSkate keeps itself: official mods, which the launcher's OFFICIAL tab
// installs (Engine/Vfs/builtin_packs.h), and the mods they retired.
using Kept = std::function<bool(std::string_view folder)>;

// The packages to list for `query`, in the order to list them: pinned ones first, then the most
// recently updated. Never one marked NSFW (by its flag or a category of that name), nor a deprecated
// one that is not installed, nor one ReSkate keeps. Each word of the query has to be in its title,
// owner, description or one of its categories, ignoring case.
std::vector<const Package*> search(std::span<const Package> packages, std::string_view query,
                                   const InstalledMods& installed, const Kept& kept = {});
bool nsfw(const Package& package);

// The packages it names as dependencies that are not installed at that version or newer
// (Owner-Name-1.2.3 each). The game installs one package at a time; the launcher brings the rest.
// One ReSkate keeps is never missing: as in the launcher, it is the OFFICIAL tab's to install.
std::vector<std::string> missing_dependencies(const Package& package, const InstalledMods& installed, const Kept& kept = {});

// Why a package holding `files` (paths inside the mod folder it installs as, '/' between folders)
// is not installed from the game, or empty when it may be. It may hold what the game merges
// (layout.toc and Win32/**.toc, .sb and .cas), what maps and parks register themselves with
// (reskate-levels.json, parks/<map>.park.json), what describes a mod (manifest.json,
// reskate-mod.json, reskate-build.json, .reskate-studio-patch, its icon, README, CHANGELOG and
// LICENSE) and pictures and text files (.png, .jpg, .md, .txt), and has to hold something the game
// loads. Anything else is refused: programs, scripts and settings files, but also Board Break's
// deck packs and music playlists, which only take effect after a restart.
std::string refuse_contents(std::span<const std::string> files);

// `text` as one line of printable text, cut to about `most` bytes (never inside a UTF-8 character).
std::string one_line(std::string_view text, std::size_t most);

// What the MODS page's card and the `mods` console commands share (Extension/Assets/mod_browser.h).
// The package named Owner-Name, ignoring case, in `packages`; null when there is none.
const Package* find(std::span<const Package> packages, std::string_view full_name);
// A download's size as the card and the console say it: "300 KB", "12 MB", "1.50 GB".
std::string size_text(std::uint64_t bytes);
// How far a download is, for its log lines: the tenths of `total` received, or 16 MB steps when
// the size is not known. A line is written each time this goes up.
std::uint64_t progress_step(std::uint64_t received, std::uint64_t total);
// One package as `mods browse` lists it: "Owner-Name  v1.2.3  12 MB  installable", its state one of
// installable, update, installed, or refused: and why (`refused`, when it cannot be installed now).
std::string row_text(const Package& package, Action action, std::string_view refused);

} // namespace dingosdk::thunderstore_listing
