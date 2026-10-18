#pragma once
#include "Engine/Vfs/builtin_packs.h"
#include "Engine/Vfs/mod_list.h"
#include "Engine/Vfs/thunderstore_listing.h"
#include <cstdint>
#include <memory>
#include <span>
#include <string>
#include <string_view>
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
// on the installed folder; and whatever it depends on has to be installed already. Official mods
// (Engine/Vfs/builtin_packs.h) are not listed or installed here: they are the launcher's OFFICIAL
// tab's, as in its own browser. Downloads come from thunderstore.io only. Anything else is the
// launcher's to install.
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
    // The last install's outcome, kept until the next one ends the same way: "done: ...", and
    // "refused: ...", "cancelled: ..." or "failed: ...".
    std::string last_result, last_error;
};
View view();
// Whether ReSkate keeps the mod folder itself (thunderstore_listing.h: Kept): one of the official
// mods its features are made for, one in place among `officials` (the folders official_packs found,
// read elsewhere: this reads nothing), or a mod an official one retired.
bool kept(std::string_view folder, std::span<const mods::BuiltinPack> officials) noexcept;
// True from an install's start until it is done or has failed.
bool busy() noexcept;
// Why an install cannot start now (another install, a map download or an apply under way), or empty.
std::string blocked();
// What the card lists and `mods browse` prints, so the two never differ: the packages matching
// `query` (thunderstore_listing.h: search), with what installing each does and, when it needs a
// mod that is not installed, why it cannot be. `installed` is the Mods folder as last read, and
// `officials` the official mods in place, both read elsewhere (this reads nothing). The rows
// point into `listing`.
struct Row {
    const thunderstore_listing::Package* package{};
    thunderstore_listing::Action action{};
    std::string needs;
};
std::vector<Row> rows(const Listing& listing, std::string_view query, const mods::ModList& installed,
                      std::span<const mods::BuiltinPack> officials);
// Why the row's package cannot be installed now (its button is disabled with this), or empty.
std::string refusal(const Row& row);
// Starts installing the row's package (or its update): its button and `mods install`. Empty when
// it has started, else why not.
std::string install(const Row& row);

// The Apply button's: applies every change to the mods waiting (live_mods.h), reloading the level
// only when `installed` (the Mods folder as last read) changes what it shows. Not while a mod is
// going into the Mods folder. What happened, for the player.
std::string apply(const mods::ModList& installed);

// The search, shared by the card's box and `mods browse`; the generation changes with it.
std::string query();
std::uint64_t query_generation() noexcept;
void set_query(std::string text);
// `mods browse`: the ReSkate menu opens on MODS (take_open, the menu's) with GET MODS in view
// (take_reveal, the card's).
void show() noexcept;
bool take_open() noexcept;
bool take_reveal() noexcept;
// Stops an install that has not been written into Mods yet.
void cancel() noexcept;
// Forgets a done or failed install's message.
void dismiss() noexcept;
}
