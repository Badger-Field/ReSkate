#pragma once
#include <array>
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>

// Who a player is in direct multiplayer: an Ed25519 key of their own. The lobby service (and,
// later, a host) checks a signature made with it, and the player ID other players see is
// derived from it the same way the service derives it (contrib/lobby-service/src/index.js):
// direct_player_id(the first 4 bytes of SHA-256(public key), big-endian).
namespace dingosdk::multiplayer {
struct PlayerKey {
    std::array<std::uint8_t, 32> seed{};       // secret: what is stored
    std::array<std::uint8_t, 32> public_key{};
};

// A new key from the system's secure random generator. Nothing when that fails.
std::optional<PlayerKey> generate_player_key();
// The key a stored seed stands for.
PlayerKey player_key_from_seed(const std::array<std::uint8_t, 32> &seed);
// An Ed25519 (RFC 8032) signature of `message`.
std::array<std::uint8_t, 64> sign(const PlayerKey &key, std::string_view message);
bool verify(const std::array<std::uint8_t, 32> &public_key, std::string_view message,
            const std::array<std::uint8_t, 64> &signature);
// The player ID this key connects as.
std::uint64_t player_id_of(const std::array<std::uint8_t, 32> &public_key);

std::string to_hex(std::span<const std::uint8_t> bytes);
// Bytes from lowercase or uppercase hex; nothing when it is not hex of that length.
template <std::size_t N> std::optional<std::array<std::uint8_t, N>> from_hex(std::string_view text);
} // namespace dingosdk::multiplayer
