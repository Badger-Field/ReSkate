#pragma once
#include "Extension/Multiplayer/Steam/steam_lobbies.h"

#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

// Lobbies without Steam: SteamLobbies' LobbyApi on the lobby service (contrib/lobby-service),
// through the player's own connection to it. A lobby is its host's player ID. A public lobby is
// listed to everyone; any other only to players on the host's home network, who then join it
// from the list without a code. The service keeps a listing two minutes: the host publishes it
// again every minute while it stays open.
namespace dingosdk::multiplayer {
struct LobbyChannel {
    std::function<bool()> ready;                            // connected to the service
    std::function<bool(std::string_view)> request;          // a JSON request
    std::function<std::vector<std::string>()> replies;      // the service's answers, in order
    std::function<std::uint64_t()> local_id;                // this player's ID
    std::function<std::string()> name;                      // this player's name
    std::function<std::uint64_t()> now_ms;                  // a steady clock
};
std::unique_ptr<LobbyApi> make_direct_lobby_api(LobbyChannel channel);
} // namespace dingosdk::multiplayer
