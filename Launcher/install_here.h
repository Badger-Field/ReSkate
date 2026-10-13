#pragma once
#include <filesystem>
#include <optional>
#include <string>

// Installing ReSkate with one button: the launcher, started from wherever it was unzipped, finds
// skate. in the player's Steam library and puts itself there, with ReSkate.dll and
// GameNetworkingSockets.dll, makes Desktop and Start menu shortcuts, and adds "Install as
// ReSkate mod" to the right-click menu of .zip files. Nothing of the game's own is touched.
namespace dingosdk::launcher_app {
// skate.'s folder in the Steam library (holding Skate.exe), when Steam has it installed.
std::optional<std::filesystem::path> find_steam_skate();
// Copies ReSkate's files from `from` into `game` and makes the shortcuts. Returns the installed
// launcher. Throws a message for the player.
std::filesystem::path install_into(const std::filesystem::path &game, const std::filesystem::path &from);
// The right-click "Install as ReSkate mod" on .zip files, for this launcher (per user, HKCU; it
// replaces no program's own handling of .zip files).
void register_mod_install_verb(const std::filesystem::path &launcher);
} // namespace dingosdk::launcher_app
