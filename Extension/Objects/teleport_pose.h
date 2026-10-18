#pragma once
// The transform the game's teleport (the native teleport manager, profile.h
// placement_teleport_contract) is handed: 16 floats, rows right, up, forward, position, the
// same layout the trainer reads the skater's heading from (`trainer where`). Without a heading
// the rotation is identity, as every teleport wrote before: the skater then faces +z (heading 0),
// which is what `trainer where` read after every teleport (the PC, e3ed5ee). With one, the
// rotation is a yaw about +y: heading 0 = +z (north), 90 = +x (east), clockwise seen from above.
// No game in it, so the trainer tests run it on its own (Extension/Trainer/Test/heading_tests.cpp).
#include <array>
#include <cmath>
#include <numbers>
#include <optional>

namespace dingosdk::teleport {

// A heading in degrees wrapped to [0, 360); empty when it is not a finite number.
inline std::optional<float> heading_degrees(double value) noexcept {
    if (!std::isfinite(value)) return std::nullopt;
    double wrapped = std::fmod(value, 360.0);
    if (wrapped < 0) wrapped += 360.0;
    if (wrapped >= 360.0) wrapped = 0.0;
    return static_cast<float>(wrapped);
}

// The skater's forward vector for a heading: (sin h, 0, cos h).
inline std::array<float, 3> forward(float heading) noexcept {
    const float h = heading * std::numbers::pi_v<float> / 180.0f;
    return {std::sin(h), 0.0f, std::cos(h)};
}

// The heading of a transform's forward row (matrix[8], matrix[10]), in [0, 360).
inline float heading_of(const float *matrix) noexcept {
    float h = std::atan2(matrix[8], matrix[10]) * 180.0f / std::numbers::pi_v<float>;
    if (h < 0) h += 360.0f;
    return h >= 360.0f ? 0.0f : h;
}

// Upright at `at`, facing `heading` (identity rotation when there is none). The right row is
// up x forward, so the rows stay a right-handed rotation: (cos h, 0, -sin h).
inline std::array<float, 16> transform(const std::array<float, 3> &at, std::optional<float> heading = std::nullopt) noexcept {
    if (!heading) return {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, at[0], at[1], at[2], 1};
    const auto f = forward(*heading);
    return {f[2], 0, -f[0], 0, 0, 1, 0, 0, f[0], 0, f[2], 0, at[0], at[1], at[2], 1};
}

} // namespace dingosdk::teleport
