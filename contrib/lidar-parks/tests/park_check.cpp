// Reads a park mod with the dedicated server's own loader (Server/server_parks.cpp) and prints what
// players' games would be sent. Usage: park_check <Mods> <folder>
#include "Server/server_parks.h"
#include <cstdio>
#include <exception>
int main(int argc, char **argv) {
    if (argc != 3) { std::fprintf(stderr, "usage: park_check <Mods> <folder>\n"); return 2; }
    dingosdk::server::ParkMod mod;
    mod.name = "check"; mod.folder = argv[2];
    try {
        const auto park = dingosdk::server::load_park_mod(argv[1], mod);
        std::printf("key %s objects %zu skipped %zu\n", park.key.c_str(), park.objects.size(), park.skipped.size());
        for (const auto &s : park.skipped) std::printf("skipped %s\n", s.c_str());
        for (const auto &o : park.objects)
            std::printf("object %s %.4f %.4f %.4f | %.5f %.5f %.5f %.5f | %.4f\n", o.item.c_str(), o.position[0], o.position[1],
                        o.position[2], o.rotation[0], o.rotation[1], o.rotation[2], o.rotation[3], o.scale);
        return park.skipped.empty() ? 0 : 1;
    } catch (const std::exception &e) { std::printf("error %s\n", e.what()); return 3; }
}
