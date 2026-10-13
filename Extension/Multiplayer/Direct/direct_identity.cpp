#include "direct_identity.h"
#include "direct_signaling.h"

#ifdef _WIN32
#include <Windows.h>
#include <bcrypt.h>
#else
#include <openssl/evp.h>
#include <sys/random.h>
#endif

#include <monocypher-ed25519.h>
#include <monocypher.h>

#include <algorithm>

namespace dingosdk::multiplayer {
namespace {
std::array<std::uint8_t, 32> sha256(std::span<const std::uint8_t> data) {
    std::array<std::uint8_t, 32> digest{};
#ifdef _WIN32
    BCryptHash(BCRYPT_SHA256_ALG_HANDLE, nullptr, 0, const_cast<PUCHAR>(data.data()), static_cast<ULONG>(data.size()),
               digest.data(), static_cast<ULONG>(digest.size()));
#else
    unsigned length = 0;
    EVP_Digest(data.data(), data.size(), digest.data(), &length, EVP_sha256(), nullptr);
#endif
    return digest;
}

bool random_bytes(std::span<std::uint8_t> out) {
#ifdef _WIN32
    return BCRYPT_SUCCESS(BCryptGenRandom(nullptr, out.data(), static_cast<ULONG>(out.size()), BCRYPT_USE_SYSTEM_PREFERRED_RNG));
#else
    std::size_t done = 0;
    while (done < out.size()) {
        const auto got = getrandom(out.data() + done, out.size() - done, 0);
        if (got <= 0) return false;
        done += static_cast<std::size_t>(got);
    }
    return true;
#endif
}

// Monocypher's secret key: the seed followed by the public key. Wiped after each use.
struct SecretKey {
    std::array<std::uint8_t, 64> bytes{};
    explicit SecretKey(const PlayerKey &key) {
        std::copy(key.seed.begin(), key.seed.end(), bytes.begin());
        std::copy(key.public_key.begin(), key.public_key.end(), bytes.begin() + 32);
    }
    ~SecretKey() { crypto_wipe(bytes.data(), bytes.size()); }
};
} // namespace

std::optional<PlayerKey> generate_player_key() {
    std::array<std::uint8_t, 32> seed{};
    if (!random_bytes(seed)) return std::nullopt;
    auto key = player_key_from_seed(seed);
    crypto_wipe(seed.data(), seed.size());
    return key;
}

PlayerKey player_key_from_seed(const std::array<std::uint8_t, 32> &seed) {
    PlayerKey key;
    key.seed = seed;
    std::array<std::uint8_t, 32> scratch = seed; // crypto_ed25519_key_pair wipes the seed it is given
    std::array<std::uint8_t, 64> secret{};
    crypto_ed25519_key_pair(secret.data(), key.public_key.data(), scratch.data());
    crypto_wipe(secret.data(), secret.size());
    return key;
}

std::array<std::uint8_t, 64> sign(const PlayerKey &key, std::string_view message) {
    std::array<std::uint8_t, 64> signature{};
    const SecretKey secret(key);
    crypto_ed25519_sign(signature.data(), secret.bytes.data(), reinterpret_cast<const std::uint8_t *>(message.data()),
                        message.size());
    return signature;
}

bool verify(const std::array<std::uint8_t, 32> &public_key, std::string_view message,
            const std::array<std::uint8_t, 64> &signature) {
    return crypto_ed25519_check(signature.data(), public_key.data(), reinterpret_cast<const std::uint8_t *>(message.data()),
                                message.size()) == 0;
}

std::uint64_t player_id_of(const std::array<std::uint8_t, 32> &public_key) {
    const auto digest = sha256(public_key);
    const auto account = static_cast<std::uint32_t>(digest[0]) << 24 | static_cast<std::uint32_t>(digest[1]) << 16 |
                         static_cast<std::uint32_t>(digest[2]) << 8 | digest[3];
    return direct_player_id(account);
}

std::uint64_t direct_join_token(std::uint64_t secret, std::span<const std::uint8_t> password_key) {
    static constexpr char label[] = "reskate-direct-join-v2";
    std::array<std::uint8_t, sizeof(label) - 1 + 8 + 64> message{};
    std::copy(label, label + sizeof(label) - 1, message.begin());
    for (int i = 0; i < 8; ++i) message[sizeof(label) - 1 + i] = static_cast<std::uint8_t>(secret >> (56 - 8 * i));
    const auto key_bytes = std::min<std::size_t>(password_key.size(), 64);
    std::copy_n(password_key.begin(), key_bytes, message.begin() + sizeof(label) - 1 + 8);
    std::array<std::uint8_t, 8> digest{};
    crypto_blake2b(digest.data(), digest.size(), message.data(), sizeof(label) - 1 + 8 + key_bytes);
    crypto_wipe(message.data(), message.size());
    std::uint64_t token{};
    for (const auto b : digest) token = token << 8 | b;
    return token ? token : 1;
}

std::string to_hex(std::span<const std::uint8_t> bytes) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string out;
    out.reserve(bytes.size() * 2);
    for (const auto b : bytes) {
        out.push_back(digits[b >> 4]);
        out.push_back(digits[b & 15]);
    }
    return out;
}
} // namespace dingosdk::multiplayer
