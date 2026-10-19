#pragma once
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

// A plugin's message to the same plugin on other players' games, sent in the session's throwdown
// packets (as Domination's are). Throwdown kinds are 1-15 and Domination's tag 0xD0; plugins' is
// 0xD1. Little-endian: u8 tag, u8 version, u32 channel (fnv1a of the plugin's lowercase name), u64
// the player it is for (0: everyone), then the payload. The host fills in all but the payload, so
// a plugin cannot pretend to be another or say who sent it (the session says that).
namespace dingosdk::plugins::wire {
inline constexpr std::uint8_t tag = 0xD1, version = 1;
inline constexpr std::size_t header = 14;

inline std::uint32_t channel_of(std::string_view name) {
    std::uint32_t hash = 2166136261u;
    for (const char c : name) {
        hash ^= static_cast<unsigned char>(c >= 'A' && c <= 'Z' ? c + 32 : c);
        hash *= 16777619u;
    }
    return hash;
}
inline bool is_plugin(std::span<const std::uint8_t> bytes) noexcept { return !bytes.empty() && bytes[0] == tag; }

inline std::vector<std::uint8_t> encode(std::uint32_t channel, std::uint64_t to, std::string_view payload) {
    std::vector<std::uint8_t> bytes;
    bytes.reserve(header + payload.size());
    bytes.push_back(tag);
    bytes.push_back(version);
    for (int i = 0; i < 4; ++i) bytes.push_back(static_cast<std::uint8_t>(channel >> (8 * i)));
    for (int i = 0; i < 8; ++i) bytes.push_back(static_cast<std::uint8_t>(to >> (8 * i)));
    bytes.insert(bytes.end(), payload.begin(), payload.end());
    return bytes;
}
struct Message {
    std::uint32_t channel{};
    std::uint64_t to{};
    std::string payload;
};
// Empty for anything that is not a plugin message of this version.
inline std::optional<Message> decode(std::span<const std::uint8_t> bytes) {
    if (bytes.size() < header || bytes[0] != tag || bytes[1] != version) return std::nullopt;
    Message message;
    for (int i = 0; i < 4; ++i) message.channel |= static_cast<std::uint32_t>(bytes[2 + i]) << (8 * i);
    for (int i = 0; i < 8; ++i) message.to |= static_cast<std::uint64_t>(bytes[6 + i]) << (8 * i);
    message.payload.assign(reinterpret_cast<const char*>(bytes.data() + header), bytes.size() - header);
    return message;
}
}
