// `trainer tp X Y Z [H]` and `trainer ground X Z [H]`: the heading's wrap, and the transform
// handed to the game's teleport (Extension/Objects/teleport_pose.h), read back the way
// `trainer where` reads the skater's heading.
#include "Extension/Objects/teleport_pose.h"

#include <cmath>
#include <iostream>
#include <string>

namespace {
using namespace dingosdk::teleport;
int failures = 0;
void check(bool condition, const std::string &message) {
    if (!condition) { std::cerr << "FAIL: " << message << '\n'; ++failures; }
}
bool near(float a, float b, float eps = 1e-4f) { return std::abs(a - b) <= eps; }
} // namespace

int main() {
    // ---- the argument -----------------------------------------------------------------------
    check(heading_degrees(90.0) == 90.0f, "90 stays 90");
    check(heading_degrees(-90.0) == 270.0f, "-90 is 270");
    check(heading_degrees(360.0) == 0.0f && heading_degrees(720.0) == 0.0f, "whole turns are 0");
    check(near(*heading_degrees(450.5), 90.5f), "450.5 is 90.5");
    check(!heading_degrees(std::nan("")) && !heading_degrees(INFINITY), "not a number: no heading");

    // ---- no heading: identity, as every teleport wrote before -------------------------------
    const auto plain = transform({1.0f, 2.0f, 3.0f});
    check(plain[0] == 1 && plain[5] == 1 && plain[10] == 1 && plain[8] == 0 && plain[2] == 0, "identity rotation");
    check(plain[12] == 1.0f && plain[13] == 2.0f && plain[14] == 3.0f && plain[15] == 1.0f, "position in the last row");
    check(heading_of(plain.data()) == 0.0f, "identity faces heading 0 (+z), as `where` read after every teleport");

    // ---- with a heading: forward (sin h, 0, cos h), read back by `where`'s formula ---------
    for (const float h : {0.0f, 45.0f, 90.0f, 180.0f, 270.0f, 359.0f}) {
        const auto m = transform({10.0f, 300.0f, -5.0f}, h);
        check(near(heading_of(m.data()), h, 1e-3f) || (h == 0.0f && near(heading_of(m.data()), 360.0f, 1e-3f)),
              "heading " + std::to_string(h) + " reads back");
        // Orthonormal and right-handed: right = up x forward, all unit length, up stays +y.
        const float rx = m[0], ry = m[1], rz = m[2], fx = m[8], fy = m[9], fz = m[10];
        check(near(rx * rx + ry * ry + rz * rz, 1.0f) && near(fx * fx + fy * fy + fz * fz, 1.0f), "unit rows");
        check(near(rx * fx + ry * fy + rz * fz, 0.0f), "right is perpendicular to forward");
        check(m[4] == 0 && m[5] == 1 && m[6] == 0, "upright");
        check(near(rx, fz) && near(rz, -fx), "right = up x forward");
        check(m[12] == 10.0f && m[13] == 300.0f && m[14] == -5.0f, "position kept");
    }
    const auto east = transform({0, 0, 0}, 90.0f);
    check(near(east[8], 1.0f) && near(east[10], 0.0f), "90 faces +x (east)");
    check(near(east[0], 0.0f) && near(east[2], -1.0f), "facing east, the right hand points -z (south)");
    const auto west = transform({0, 0, 0}, 270.0f);
    check(near(west[8], -1.0f) && near(heading_of(west.data()), 270.0f, 1e-3f), "270 faces -x (west)");

    if (failures) { std::cerr << failures << " failure(s)\n"; return 1; }
    std::cout << "heading tests passed\n";
    return 0;
}
