#pragma once
#include "Engine/Game/Build/fingerprint.h"
#include <array>
#include <cstdint>

// The game's mesh variation databases (Extension/World/mesh_unload_guard.cpp).
namespace dingosdk::game::build::v20260929::mesh_variations {
// remove_databases(registry, ?, list): run on a job worker as a level's bundles unload. For
// each MeshVariationDatabase in the list it takes every entry out of the registry
// (0x3f5a200), keyed by the entry's mesh (its name hash, mesh + 0xc8) and variation hashes
// (entry + 0x20, + 0x24). It holds the registry's lock (0x158f2c0 / 0x158f430) throughout.
inline constexpr Fingerprint remove_databases{0x3f5fbc0, {
    0x48,0x89,0x54,0x24,0x10,0x53,0x57,0x41,0x54,0x41,0x55,0x41,0x56,0x41,0x57,0x48,
    0x83,0xec,0x28,0x48,0x8b,0x79,0x10,0x4c,0x8b,0xe2,0x4c,0x8b,0xe9,0x49,0x8b,0xd8}};
// The read of the mesh's name hash (mov edx, [rax + 0xc8]), with no check that the entry still
// has its mesh: an access violation reading 0xc8 when the mesh was unloaded before its
// database (crash reports of 2026-10-08 to 2026-10-11, always on leaving one custom map).
inline constexpr std::uintptr_t mesh_hash_read = 0x3f5fc52;
inline constexpr std::array<unsigned char, 6> mesh_hash_read_bytes{0x8b, 0x90, 0xc8, 0x00, 0x00, 0x00};
// Right after that read: the key's low half from the entry (mov eax, [rbx + 0x20]) and the
// shift of the name hash into the high half (shl rdx, 0x20), then the call that removes the
// entry from the registry (0x3f5a200). It needs only the key, never the mesh: when the guard
// finds the name hash again (Extension/World/mesh_registry.h) it puts it in edx and goes on here.
inline constexpr std::uintptr_t key_low_read = 0x3f5fc58;
inline constexpr std::array<unsigned char, 7> key_low_read_bytes{0x8b, 0x43, 0x20, 0x48, 0xc1, 0xe2, 0x20};
// release(object): vtable slot 0x40 of the class whose vtable is at 0x64f2ba8, called from
// 0x3ea5a80 when a 0xf0-byte resource slot's use count reaches zero, also as a level unloads.
// When byte [object + 0x3b0] is set it frees the buffer at [object + 0x360] (0x1584c50: unless
// it is the empty value, the address 0x72fe704), puts the empty value back and clears the flag.
// Leaving Skate 3 Improved for San Vansterdam crashed in that free, twice: the "buffer" was two
// floats (0x44c00000_xxxxxxxx), not an address (dumps of 2026-10-13 01:26 and 01:47).
inline constexpr Fingerprint release_buffer{0x3f5fa20, {
    0x40,0x53,0x48,0x83,0xec,0x20,0x80,0xb9,0xb0,0x03,0x00,0x00,0x00,0x48,0x8b,0xd9,
    0x74,0x19,0x48,0x81,0xc1,0x60,0x03,0x00,0x00,0xe8,0x12,0x52,0x62,0xfd,0xc6,0x83}};
inline constexpr std::uintptr_t release_buffer_at = 0x360, release_flag_at = 0x3b0;
inline constexpr std::uintptr_t empty_buffer = 0x72fe704;
// The step to the next entry (add rbx, 0x30), after the call that takes one out: where an entry
// without a mesh goes on from.
inline constexpr std::uintptr_t next_entry = 0x3f5fc67;
inline constexpr std::array<unsigned char, 4> next_entry_bytes{0x48, 0x83, 0xc3, 0x30};
}
