#pragma once

#include <atomic>
#include <filesystem>
#include <functional>
#include <stdexcept>
#include <string>
#include <vector>

// The launcher's side of the Mods folder: installing mods from a .zip or a
// folder, and removing them. Order and enabled state live in mods.json
// (Engine/Vfs/mod_list.h), which the runtime reads when the game starts.
namespace dingosdk::launcher_mods {

// The Mods folder the game will read: <game>/ModData/Default/Mods when the
// game is started with that data path (a ModData folder exists), else <game>/Mods.
std::filesystem::path mods_root(const std::filesystem::path& game_directory);

// Thrown when a mod with the same folder name is already installed and the
// caller did not ask to replace it.
struct AlreadyInstalled : std::runtime_error {
    explicit AlreadyInstalled(std::string folder)
        : std::runtime_error("A mod named \"" + folder + "\" is already installed."), name(std::move(folder)) {}
    std::string name;
};

using Progress = std::function<void(float fraction)>;

struct InstallOptions {
    // Install under this folder name instead of one taken from the source
    // (Thunderstore packages: Namespace-Name).
    std::string folder;
    // Written into manifest.json as "author" when it names none (Thunderstore
    // manifests have no author; the team is the author).
    std::string author;
    // Refuse a source with nothing the game loads, unless its manifest.json
    // lists dependencies (a mod pack).
    bool require_content{};
};

// Installs `source` (a .zip, or a folder) as Mods/<name> and returns <name>.
// The mod is the shallowest folder holding layout.toc, reskate-levels.json or
// parks/<map>.park.json, else the shallowest holding manifest.json or
// reskate-mod.json. Thunderstore's manifest.json, icon.png, README.md and
// CHANGELOG.md at the top of a package are copied in when the mod sits one
// folder deeper. A top-level mod takes the archive's name without a trailing
// version ("Team-Map-1.0.0.zip" installs as Team-Map). With `replace`, an
// installed mod of the same name is deleted for good once the new copy is in. Throws a
// user-facing message; a failed or cancelled install leaves the Mods folder as it was.
std::string install(const std::filesystem::path& mods_root, const std::filesystem::path& source, bool replace,
                    const Progress& progress, const std::atomic<bool>& cancel, const InstallOptions& options = {});

// Moves Mods/<name> to the Recycle Bin. Throws a user-facing message.
void remove(const std::filesystem::path& mods_root, const std::string& name);

// Which mods a mod pack brought in: the dependencies a Thunderstore install of it downloaded that
// were not installed before (theirs too), kept by the launcher in Mods/brought-in.json, pack folder
// to dependency folders. A mod the player had already is never in it.
inline constexpr char brought_in_file[] = "brought-in.json";
std::vector<std::string> brought_in(const std::filesystem::path& mods_root, const std::string& pack);
// Adds to `pack`'s list. Throws a user-facing message when the file cannot be written.
void record_brought_in(const std::filesystem::path& mods_root, const std::string& pack,
    const std::vector<std::string>& dependencies);
// Drops the lists of mods that are gone; nothing when there were none.
void forget_brought_in(const std::filesystem::path& mods_root, const std::vector<std::string>& removed);

// The installed mods a pack brought in that nothing else needs, to offer with its uninstall: what
// the mods in `removing` brought in (brought_in), without the ones `removing` already has and without
// any that a mod staying installed (the rest of `installed`) lists as a dependency in its
// manifest.json ("Namespace-Name-1.2.3" is the folder Namespace-Name), or that one's dependencies in
// turn. In `installed`'s order. A pack installed before this was kept brought nothing in.
std::vector<std::string> orphaned_dependencies(const std::filesystem::path& mods_root,
    const std::vector<std::string>& removing, const std::vector<std::string>& installed);


} // namespace dingosdk::launcher_mods
