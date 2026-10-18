#pragma once
#include "Engine/Vfs/thunderstore_listing.h"
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

// The MODS page's GET MODS card: ReSkate's Thunderstore community, searched and installed from
// inside the game. The listing is read on a thread of its own and kept for the run; a package is
// fetched, checked, installed and switched on in mods.json on another (package_install.h), and the
// player applies it with the page's Apply button, as any other change to the mods (live_mods.h).
// Nothing here waits on the network or the disk: the page is drawn on the game's render thread.
//
// What the game installs this way is held to what it can apply: the package has to be listed in the
// community, not deprecated, not marked NSFW and not rejected by its moderators (asked of
// Thunderstore again before the download); every file in it has to be one a mod is made of
// (thunderstore_listing.h: refuse_contents), which is checked before anything is written and again
// on the installed folder; and whatever it depends on has to be installed already. Downloads come
// from thunderstore.io only. Anything else is the launcher's to install.
namespace dingosdk::mod_browser {
struct Listing {
    std::vector<thunderstore_listing::Package> packages;
    std::string error;          // why the last read failed; the packages are the last good ones
    bool loaded{};              // read once at least
};
// The listing as last read (null before the first read ends), reading it again in the background
// when it is ten minutes old, or with `again`.
std::shared_ptr<const Listing> listing(bool again = false);
bool reading() noexcept;

enum class Stage {
    idle,
    checking,     // asking Thunderstore about the package
    downloading,
    installing,
    enabling,     // switching it on in mods.json
    done,         // installed: the player applies it
    failed,
};
struct View {
    Stage stage{};
    std::string title, version, folder;  // "Vancouver Plaza", "2.0.0", "Sandos-Vancouver_Plaza"
    bool update{};                        // in the place of an installed version
    std::uint64_t received{}, total{};    // downloading; total 0 when the size is not known
    float installed{};                    // installing: how much is written, 0 to 1
    std::string message;                  // done and failed: what happened, for the player
};
View view();
// True from an install's start until it is done or has failed.
bool busy() noexcept;
// Why an install cannot start now (another install, a map download or an apply under way), or empty.
std::string blocked();
// Starts installing the package (or its update); ignored while busy.
void install(const thunderstore_listing::Package& package);
// Stops an install that has not been written into Mods yet.
void cancel() noexcept;
// Forgets a done or failed install's message.
void dismiss() noexcept;
}
