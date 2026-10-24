#pragma once
#include "Engine/Resource/binary_bundle.h"
#include <algorithm>

namespace dingosdk::mods {
// Sharing a delivery bundle is safe only when every original asset is intact.
// New names/GUIDs may coexist with a variant; replacing or dropping originals may not.
inline bool preserves_variant_bundle(const frostbite::BinaryBundle& base,
                                     const frostbite::BinaryBundle& incoming) {
    const auto intact = [](const auto& originals, const auto& candidates, bool chunks) {
        for (const auto& original : originals) {
            if (std::ranges::count_if(candidates, [&](const auto& candidate) {
                return chunks ? candidate.guid == original.guid : candidate.name == original.name;
            }) != 1) return false;
            const auto found = std::ranges::find_if(candidates, [&](const auto& candidate) {
                return chunks ? candidate.guid == original.guid : candidate.name == original.name;
            });
            if (found == candidates.end() || found->sha1 != original.sha1 ||
                found->kind != original.kind || found->originalSize != original.originalSize ||
                found->resourceId != original.resourceId || found->resourceType != original.resourceType ||
                found->resourceMeta != original.resourceMeta || found->logicalOffset != original.logicalOffset ||
                found->logicalSize != original.logicalSize) return false;
        }
        return true;
    };
    return intact(base.ebx, incoming.ebx, false) && intact(base.resources, incoming.resources, false) &&
           intact(base.chunks, incoming.chunks, true);
}
}
