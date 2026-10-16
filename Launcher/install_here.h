#pragma once
#include <filesystem>
#include <optional>
#include <string>

// ReSkate lives in a folder of its own, with its own copy of the game that the launcher downloads
// there (never in Steam's skate. folder: main.cpp refuses to start from a Steam library). The
// launcher in that folder makes Desktop and Start menu shortcuts and adds "Install as ReSkate mod"
// to the right-click menu of .zip files. A launcher unzipped somewhere else later finds that
// install by its shortcut and offers to update it with one button (or simply opens it), instead of
// downloading the game a second time.
namespace dingosdk::launcher_app {
// Whether `folder` is inside a Steam library's steamapps\common, where ReSkate is not installed.
bool in_steam_library(const std::filesystem::path &folder);
// The folder of a ReSkate install of its own (ReSkateLauncher.exe beside Skate.exe), found by the
// ReSkate shortcut on the Desktop or in the Start menu; never `self_folder` or a Steam library.
std::optional<std::filesystem::path> find_installed_reskate(const std::filesystem::path &self_folder);
// The Desktop and Start menu shortcuts to `launcher`, when there is no ReSkate shortcut in either
// place yet (best effort; a player who deletes only one keeps it deleted).
void make_shortcuts_once(const std::filesystem::path &launcher);
// Whether `game` already holds exactly ReSkate's files from `from` (each one `from` has, byte for
// byte): then there is nothing to install, and the launcher there can simply be started.
bool installed_copy_matches(const std::filesystem::path &game, const std::filesystem::path &from);
// Copies ReSkate's files from `from` into `game` (a ReSkate folder holding Skate.exe, not Steam's)
// and makes the shortcuts. Returns the installed launcher. Throws a message for the player.
std::filesystem::path install_into(const std::filesystem::path &game, const std::filesystem::path &from);
// Takes back what install_into added to `game`: ReSkate's files there, the shortcuts and the
// right-click entry when they point at this install, and the launcher's settings. The launcher
// itself is deleted a moment after it exits. The game's files, mods, the player's profile and the
// config, scripts and logs folders (which may hold the player's edits) stay. Also takes an older
// ReSkate's one-click install back out of Steam's skate. folder (main.cpp).
// Throws a message for the player (skate. still running, say).
void uninstall_from(const std::filesystem::path &game);
// The right-click "Install as ReSkate mod" on .zip files, for this launcher (per user, HKCU; it
// replaces no program's own handling of .zip files).
void register_mod_install_verb(const std::filesystem::path &launcher);
} // namespace dingosdk::launcher_app
