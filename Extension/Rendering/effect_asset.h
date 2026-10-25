#pragma once
#include "Engine/Core/Platform/memory.h"
#include "Engine/Game/Build/addresses.h"
#include "Engine/Game/Build/20260929/effects.h"
#include <optional>

namespace dingosdk::effects {
// Bundles can stream out without replacing the world. Remember only where a
// name was found, and resolve it again before handing an asset to native code.
class AssetLookup {
public:
    template<class Find> std::uintptr_t resolve(std::uint64_t now, Find&& find) {
        if (domain_) {
            if (const auto asset = find(*domain_)) return asset;
            domain_.reset();
        }
        if (now < retry_) return 0;
        retry_ = now + 2000;
        for (std::uint16_t domain = 0; domain < addr::effects::domain_count; ++domain)
            if (const auto asset = find(domain)) {
                domain_ = domain;
                return asset;
            }
        return 0;
    }
private:
    std::optional<std::uint16_t> domain_;
    std::uint64_t retry_{};
};

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
