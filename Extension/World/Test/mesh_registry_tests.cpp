// The mesh variation registry walk (mesh_registry.h) against a stand-in registry laid out as the
// game's is: an entry whose mesh is gone is found again by its key's low half and inner key; an
// entry that cannot be told apart, or a registry that cannot be read, gives nothing.
#include "Extension/World/mesh_registry.h"

#include <cstddef>
#include <cstdio>
#include <cstring>
#include <deque>
#include <vector>

using namespace dingosdk;

namespace {
int failures = 0;
void check(bool ok, const char *what) {
    if (!ok) {
        ++failures;
        std::fprintf(stderr, "FAIL: %s\n", what);
    }
}

// Raw blocks at the offsets the game uses; addresses are real pointers into these.
struct alignas(8) Inner {
    std::uint32_t key{}, pad{};
    std::uint64_t value{};
    Inner *next{};
};
struct alignas(8) Outer {
    std::uint64_t key{};
    Inner **inner_buckets{};
    std::uint32_t inner_count{}, inner_size{};
    std::uint64_t unused[3]{};
    Outer *next{};
};
static_assert(offsetof(Outer, inner_buckets) == 0x8 && offsetof(Outer, inner_count) == 0x10 && offsetof(Outer, next) == 0x30);
static_assert(offsetof(Inner, next) == 0x10);
struct Registry {
    std::uint8_t head[0x28]{};
    Outer **buckets{};
    std::uint32_t count{}, size{};
};
static_assert(offsetof(Registry, buckets) == 0x28 && offsetof(Registry, count) == 0x30);

struct World {
    Registry registry;
    std::vector<Outer *> buckets;
    std::deque<Outer> outers;
    std::deque<Inner> inners;
    std::deque<std::vector<Inner *>> inner_buckets;
    Outer *const end = reinterpret_cast<Outer *>(~std::uintptr_t{0});

    explicit World(std::uint32_t count) : buckets(count + 1, nullptr) {
        buckets[count] = end; // the end marker
        registry.buckets = buckets.data();
        registry.count = count;
    }
    void add(std::uint32_t hash, std::uint32_t low, std::vector<std::uint32_t> keys) {
        auto &outer = outers.emplace_back();
        outer.key = (std::uint64_t{hash} << 32) | low;
        auto &slots = inner_buckets.emplace_back(4, nullptr);
        slots[3] = reinterpret_cast<Inner *>(~std::uintptr_t{0});
        outer.inner_buckets = slots.data();
        outer.inner_count = 3;
        for (const auto key : keys) {
            auto &inner = inners.emplace_back();
            inner.key = key;
            inner.next = slots[key % 3];
            slots[key % 3] = &inner;
        }
        const auto bucket = static_cast<std::size_t>(outer.key % registry.count);
        outer.next = buckets[bucket];
        buckets[bucket] = &outer;
    }
    std::uintptr_t address() const { return reinterpret_cast<std::uintptr_t>(&registry); }
};
const auto memory_read = [](std::uintptr_t address, auto &value) {
    std::memcpy(&value, reinterpret_cast<const void *>(address), sizeof(value));
    return true;
};
} // namespace

int main() {
    World world(7);
    world.add(0xaaaa0001, 0x10, {1, 2, 3});
    world.add(0xbbbb0002, 0x20, {4, 5});
    world.add(0xcccc0003, 0x20, {6}); // same low half as the one above, other inner keys
    world.add(0xdddd0004, 0x30, {7});

    const auto found = mesh_registry::recover_mesh_hash(world.address(), 0x10, 2, memory_read);
    check(found && *found == 0xaaaa0001, "an entry is found by its low half and inner key");
    const auto shared = mesh_registry::recover_mesh_hash(world.address(), 0x20, 6, memory_read);
    check(shared && *shared == 0xcccc0003, "two nodes share a low half: the inner key tells them apart");
    check(!mesh_registry::recover_mesh_hash(world.address(), 0x20, 9, memory_read), "no node holds that inner key: nothing");
    check(!mesh_registry::recover_mesh_hash(world.address(), 0x40, 1, memory_read), "no node has that low half: nothing");

    world.add(0xeeee0005, 0x10, {2}); // a second fit, with another hash
    check(!mesh_registry::recover_mesh_hash(world.address(), 0x10, 2, memory_read), "two nodes fit: it cannot be told apart");

    // A registry that cannot be read whole gives nothing, rather than a guess.
    int reads = 0;
    const auto failing = [&](std::uintptr_t address, auto &value) {
        if (++reads > 3) return false;
        return memory_read(address, value);
    };
    check(!mesh_registry::recover_mesh_hash(world.address(), 0x30, 7, failing), "an unreadable registry gives nothing");

    // A loop in a chain (memory that is not a registry any more) ends at the node budget.
    World looped(1);
    looped.add(0x11110000, 0x50, {1});
    looped.outers.front().next = &looped.outers.front();
    check(!mesh_registry::recover_mesh_hash(looped.address(), 0x99, 1, memory_read), "a looping chain ends without an answer");

    std::printf("%s\n", failures ? "FAILED" : "Mesh registry: entries without a mesh are found again by their key.");
    return failures ? 1 : 0;
}
