// Writes reference TOC, bundle region and manifest files with ReSkate's C++ code, and
// reads files written by the Python port back through it. Built and run by
// tests/test_crosscheck.py when a C++ compiler is available.
#include "Engine/Resource/toc.h"

#include <cstdio>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>

using namespace dingosdk::frostbite;

static std::vector<std::byte> load(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    std::vector<char> raw((std::istreambuf_iterator<char>(in)), {});
    return {reinterpret_cast<std::byte*>(raw.data()), reinterpret_cast<std::byte*>(raw.data() + raw.size())};
}
static void save(const std::string& path, const std::vector<std::byte>& data) {
    std::ofstream out(path, std::ios::binary);
    out.write(reinterpret_cast<const char*>(data.data()), static_cast<std::streamsize>(data.size()));
}
static Guid guid_for(unsigned i) {
    Guid g;
    for (unsigned k = 0; k < 16; ++k) g.bytes[k] = static_cast<std::byte>((i * 37 + k * 11 + 5) & 0xFF);
    g.bytes[0] = static_cast<std::byte>(i & 0xFF); // unique for i below 65536
    g.bytes[1] = static_cast<std::byte>((i >> 8) & 0xFF);
    return g;
}

int main(int argc, char** argv) {
    if (argc < 3) return 2;
    const std::string mode = argv[1], dir = argv[2];
    if (mode == "write") {
        // The same deterministic inputs as tests/test_crosscheck.py builds.
        const unsigned count = static_cast<unsigned>(std::stoul(argv[3]));
        std::vector<TocBundle> bundles;
        std::vector<BundleFileInfo> files;
        for (unsigned i = 0; i < count; ++i) {
            files.push_back({{i % 3 == 0, 3 + (i % 2), static_cast<std::uint16_t>(1 + (i % 4))}, i * 100, i * 7 + 1});
            const auto region = write_bundle_region(files, {});
            bundles.push_back({"win32/levels/game/Test_" + std::to_string(i) + "/Sub_Level", region, 1});
        }
        std::vector<TocChunk> chunks;
        for (unsigned i = 0; i < count; ++i)
            chunks.push_back({guid_for(i), {true, 2, static_cast<std::uint16_t>(i % 5)}, i * 64, i * 3 + 9, i % 7 == 6});
        save(dir + "/cpp.toc", write_patch_toc(bundles, chunks, 3));
        BinaryBundle manifest;
        for (unsigned i = 0; i < count; ++i) {
            BundleAsset e; e.name = "ebx/asset_" + std::to_string(i); e.originalSize = i * 13;
            for (unsigned k = 0; k < 20; ++k) e.sha1.bytes[k] = static_cast<std::byte>(i + k);
            manifest.ebx.push_back(e);
            BundleAsset r; r.name = "res/thing_" + std::to_string(i); r.originalSize = i * 17; r.resourceType = 0x41759364;
            r.resourceMeta.assign(16, std::byte{static_cast<unsigned char>(i)}); r.resourceId = 0x1122334455667788ULL + i;
            manifest.resources.push_back(r);
            BundleAsset c; c.guid = guid_for(i + 100); c.logicalOffset = 0; c.logicalSize = i * 19;
            manifest.chunks.push_back(c);
        }
        save(dir + "/cpp.manifest", write_binary_bundle(manifest));
        return 0;
    }
    if (mode == "read") {
        const auto doc = read_toc(load(dir + "/py.toc"));
        verify_toc(doc);
        std::cout << doc.bundles.size() << " " << doc.chunks.size() << "\n";
        for (const auto& b : doc.bundles) std::cout << b.name << " " << read_bundle_region(b.region).files.size() << "\n";
        const auto manifest = read_binary_bundle(load(dir + "/py.manifest"));
        std::cout << manifest.ebx.size() << " " << manifest.resources.size() << " " << manifest.chunks.size() << "\n";
        for (const auto& r : manifest.resources) std::cout << r.name << " " << r.resourceId << "\n";
        return 0;
    }
    return 2;
}
