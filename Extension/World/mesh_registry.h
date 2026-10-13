#pragma once
#include <cstdint>
#include <optional>

// The game's mesh variation registry, as remove_databases looks an entry up in it
// (Engine/Game/Build/<build>/mesh_variations.h): an outer hash map keyed by the 64-bit
// (mesh name hash << 32 | entry+0x20), each of its nodes holding an inner map keyed by
// entry+0x24. When an entry's mesh has unloaded first, the name hash can no longer be read from
// the mesh; but each outer node keeps its whole key, so it is found again here.
//
// `read(address, value)` reads one value of the game's memory, false when unreadable. Nothing is
// written. Header-only, so the walk is tested off Windows against a stand-in registry.
namespace dingosdk::mesh_registry {
// The registry: buckets at +0x28, bucket count (u32) at +0x30; the bucket array has one more
// slot, the end marker. Outer node (0x38 bytes): key at +0, inner buckets at +8, inner bucket
// count (u32) at +0x10, next at +0x30. Inner node (0x18 bytes): key (u32) at +0, next at +0x10.
inline constexpr std::uintptr_t buckets_at = 0x28, bucket_count_at = 0x30;
inline constexpr std::uintptr_t outer_inner_buckets_at = 0x8, outer_inner_count_at = 0x10, outer_next_at = 0x30;
inline constexpr std::uintptr_t inner_next_at = 0x10;
// More than any map holds: a walk that gets this far is reading something else.
inline constexpr std::uint32_t max_buckets = 1u << 24;
inline constexpr std::uint32_t max_nodes = 1u << 20;

// The mesh name hash of the one outer node whose key's low half is `low` and whose inner map
// holds `inner`. Nothing when there is none, when two nodes with different hashes both fit (the
// entry cannot be told apart), or when the registry cannot be read whole.
template <class Read>
std::optional<std::uint32_t> recover_mesh_hash(std::uintptr_t registry, std::uint32_t low, std::uint32_t inner, Read &&read) {
    std::uintptr_t buckets{};
    std::uint32_t count{};
    if (!read(registry + buckets_at, buckets) || !read(registry + bucket_count_at, count) || !buckets || !count ||
        count > max_buckets)
        return std::nullopt;
    std::uintptr_t end{};
    if (!read(buckets + std::uintptr_t{count} * 8, end)) return std::nullopt;
    std::optional<std::uint32_t> found;
    std::uint32_t budget = max_nodes;
    // Whether the inner map of `node` holds `inner` (its bucket is inner % count: no hash).
    const auto holds = [&](std::uintptr_t node) -> std::optional<bool> {
        std::uintptr_t inner_buckets{};
        std::uint32_t inner_count{};
        if (!read(node + outer_inner_buckets_at, inner_buckets) || !read(node + outer_inner_count_at, inner_count))
            return std::nullopt;
        if (!inner_buckets || !inner_count || inner_count > max_buckets) return false;
        std::uintptr_t inner_end{}, at{};
        if (!read(inner_buckets + std::uintptr_t{inner_count} * 8, inner_end) ||
            !read(inner_buckets + std::uintptr_t{inner % inner_count} * 8, at))
            return std::nullopt;
        for (; at && at != inner_end; ) {
            if (!budget--) return std::nullopt;
            std::uint32_t key{};
            if (!read(at, key)) return std::nullopt;
            if (key == inner) return true;
            if (!read(at + inner_next_at, at)) return std::nullopt;
        }
        return false;
    };
    for (std::uint32_t bucket = 0; bucket < count; ++bucket) {
        std::uintptr_t node{};
        if (!read(buckets + std::uintptr_t{bucket} * 8, node)) return std::nullopt;
        for (; node && node != end; ) {
            if (!budget--) return std::nullopt;
            std::uint64_t key{};
            if (!read(node, key)) return std::nullopt;
            if (static_cast<std::uint32_t>(key) == low) {
                const auto has = holds(node);
                if (!has) return std::nullopt;
                if (*has) {
                    const auto hash = static_cast<std::uint32_t>(key >> 32);
                    if (found && *found != hash) return std::nullopt;
                    found = hash;
                }
            }
            if (!read(node + outer_next_at, node)) return std::nullopt;
        }
    }
    return found;
}
} // namespace dingosdk::mesh_registry
