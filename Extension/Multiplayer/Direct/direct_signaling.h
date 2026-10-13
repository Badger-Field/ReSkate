#pragma once
#include <cstdint>
#include <memory>
#include <span>
#include <string>
#include <string_view>
#include <vector>

// Direct multiplayer's side channel: small messages to and from other players, addressed by
// player ID, through the lobby service. The networking library uses it only while two players
// find a route to each other (their addresses and ICE checks); the game's own traffic then goes
// straight between them.
//
// Best effort: a message may be lost, repeated or reordered, which slows finding a route
// but does not break it.
namespace dingosdk::multiplayer {
struct SignalMessage {
    std::uint64_t from{};
    std::vector<std::uint8_t> bytes;
};
class DirectSignaling {
  public:
    virtual ~DirectSignaling() = default;
    // Queues a message for `to`. False when it cannot be sent (no service connection, too large).
    virtual bool send(std::uint64_t to, std::span<const std::uint8_t> bytes) = 0;
    // What has arrived since the last call. Never blocks.
    virtual std::vector<SignalMessage> receive() = 0;
    virtual bool connected() const = 0;
    // What is wrong, for the menu and the log; empty when connected.
    virtual std::string problem() const = 0;
    // The lobby service refused this player's key because another key already owns the same
    // player ID: the game should make a new key and connect with that.
    virtual bool needs_new_key() const { return false; }
};

// Over a plain TCP connection, one line a message: the protocol of GameNetworkingSockets'
// trivial signaling server. First this player's identity ("steamid:<id>"), then
// "<to identity> <hex>" lines out and "<from identity> <hex>" lines in. Reconnects on its own.
std::unique_ptr<DirectSignaling> make_tcp_signaling(std::string host, std::uint16_t port, std::uint64_t local_id);

// A direct player's ID: shaped as a Steam ID (the networking library takes no other), an
// individual account in Steam's development universe, which real Steam accounts are never in,
// so it cannot be mistaken for one. `account` is never 0.
constexpr std::uint64_t direct_player_id(std::uint32_t account) {
    return std::uint64_t{4} << 56 | std::uint64_t{1} << 52 | std::uint64_t{1} << 32 | (account ? account : 1u);
}
constexpr bool is_direct_player_id(std::uint64_t id) {
    return (id >> 32) == ((std::uint64_t{4} << 24) | (std::uint64_t{1} << 20) | 1u) && static_cast<std::uint32_t>(id) != 0;
}

// "steamid:<id>", how the networking library and the signaling server name a player.
std::string signal_identity(std::uint64_t id);
// The player ID in an identity string; 0 when it is not one.
std::uint64_t signal_identity_id(std::string_view text);
} // namespace dingosdk::multiplayer
