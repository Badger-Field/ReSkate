#pragma once
#include "Engine/Core/Platform/memory.h"
#include "Engine/Game/Build/addresses.h"
#include "Engine/Game/Build/20260929/effects.h"

namespace dingosdk::effects {
inline bool asset_type_matches(std::uintptr_t asset, bool decal) noexcept {
    // Named lookup can return any asset class. Check its reflected type before
    // passing it to an API that expects a blueprint or a decal template.
    std::uintptr_t type{}, record{};
    std::uint32_t hash{};
    return asset && asset < memory::highest_user_address - addr::effects::asset_type &&
        memory::peek(asset + addr::effects::asset_type, type) && memory::peek(type, record) &&
        memory::peek(record, hash) && hash ==
            (decal ? addr::effects::decal_type_hash : addr::effects::particle_type_hash);
}
}
