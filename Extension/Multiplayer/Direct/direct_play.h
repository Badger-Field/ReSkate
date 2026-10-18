#pragma once
#include "direct_identity.h"
#include "Extension/Multiplayer/Steam/steam_transport.h"

#include <optional>
#include <string>
#include <string_view>

// Direct multiplayer in the game: what SteamTransport::open() needs to play without Steam, in
// offline mode. The networking library is GameNetworkingSockets.dll beside ReSkate.dll; the
// player's key and the name other players see are kept with the offline profile, in
// %LOCALAPPDATA%\ReSkate\profiles\offline\direct-player.txt (the key's seed in hex, then the
// name, one a line).
namespace dingosdk::multiplayer {
struct DirectPlay {
    void *library{};
    PlayerKey key;
    std::uint64_t id{};
    std::string name;
    std::string lobby_url;
    DirectRoutes routes;
};

// Whether this install can play without Steam: offline mode, with the library in place.
bool direct_play_installed();
// Everything open_direct needs. Nothing, with `error` saying why, when something is missing.
std::optional<DirectPlay> prepare_direct_play(std::string &error);
// The lobby service gave this key's player ID to another key: make a new key, kept for the
// next start (the networking library keeps the identity it started with until then).
void replace_direct_player_key();

// Opens `transport` in direct mode, if it is not open yet: the networking library, this
// player's key and name, and the lobby service. False with the reason in its status otherwise.
bool open_direct_play(SteamTransport &transport);

// The name other players see, at most this many bytes. A new player is "Skater" and the last
// digits of their ID until they choose one.
inline constexpr std::size_t max_direct_name = 32;
std::string direct_player_name();
// Keeps a new name: an empty string when it is kept, otherwise what is wrong with it.
std::string set_direct_player_name(std::string_view name);
} // namespace dingosdk::multiplayer
