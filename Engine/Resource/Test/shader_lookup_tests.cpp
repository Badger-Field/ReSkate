// The shader program lookup's merge: the rows mods add are combined onto the base, and a row
// that points past the base's shader records is left out (the game reads such an index outside
// its shader database).
#include "Engine/Resource/shader_lookup.h"
#include <cstring>
#include <iostream>
#include <map>
#include <vector>

namespace fb = dingosdk::frostbite;

namespace {
int failures = 0;
void check(bool condition, const char* what) {
    if (!condition) { std::cerr << "FAILED: " << what << "\n"; ++failures; }
}
template <typename Type> void append(std::vector<std::byte>& bytes, Type value) {
    const auto at = bytes.size();
    bytes.resize(at + sizeof(value));
    std::memcpy(bytes.data() + at, &value, sizeof(value));
}
template <typename Type> Type read(const std::vector<std::byte>& bytes, std::size_t at) {
    Type value{};
    std::memcpy(&value, bytes.data() + at, sizeof(value));
    return value;
}
// A program lookup: a blob of `records` records (only its count matters here), no relocations,
// and the rows (key -> record indices).
struct Lookup {
    std::vector<std::byte> resource, meta;
    fb::shader::Table table() const { return {resource, meta}; }
};
Lookup lookup(std::uint32_t records, const std::map<std::uint64_t, std::vector<std::uint32_t>>& rows) {
    Lookup out;
    out.resource.resize(16 + static_cast<std::size_t>(records) * 168);
    std::memcpy(out.resource.data(), &records, sizeof(records));
    const std::uint64_t start = 16;
    std::memcpy(out.resource.data() + 4, &start, sizeof(start));
    const auto program = static_cast<std::uint32_t>(out.resource.size());
    append(out.resource, std::uint32_t{4});
    append(out.resource, static_cast<std::uint32_t>(rows.size()));
    for (const auto& [key, values] : rows) {
        append(out.resource, key);
        append(out.resource, static_cast<std::uint32_t>(values.size()));
        for (const auto value : values) append(out.resource, value);
    }
    append(out.meta, program);
    append(out.meta, std::uint32_t{4});
    append(out.meta, static_cast<std::uint32_t>(out.resource.size() - program - 4));
    append(out.meta, std::uint32_t{0});
    return out;
}
// Real-sized records with a relocated heap, rather than a count-only malformed
// fixture.
Lookup extended_lookup(bool extended, std::uint64_t key, std::byte marker = std::byte{7}) {
    Lookup out;
    const std::uint32_t count = extended ? 2U : 1U;
    const std::uint32_t heap = extended ? 376U : 200U;
    const std::uint32_t size = extended ? 408U : 216U;
    out.resource.resize(size);
    const std::uint64_t start = 32;
    const std::uint64_t pointer = heap;
    std::memcpy(out.resource.data(), &count, sizeof(count));
    std::memcpy(out.resource.data() + 4, &start, sizeof(start));
    std::memcpy(out.resource.data() + 132, &pointer, sizeof(pointer));
    out.resource[heap] = std::byte{3};
    append(out.resource, std::uint32_t{132});
    if (extended) {
        const std::uint64_t extra = 392;
        std::memcpy(out.resource.data() + 300, &extra, sizeof(extra));
        out.resource[392] = marker;
        append(out.resource, std::uint32_t{300});
    }
    append(out.resource, extended ? std::uint32_t{2} : std::uint32_t{1});
    append(out.resource, std::uint64_t{1});
    append(out.resource, std::uint32_t{1});
    append(out.resource, std::uint32_t{0});
    if (extended) {
        append(out.resource, key);
        append(out.resource, std::uint32_t{1});
        append(out.resource, std::uint32_t{1});
    }
    const auto fix = extended ? std::uint32_t{8} : std::uint32_t{4};
    append(out.meta, size);
    append(out.meta, fix);
    append(out.meta, static_cast<std::uint32_t>(out.resource.size() - size - fix));
    append(out.meta, std::uint32_t{0});
    return out;
}

// The merged rows, read back.
std::map<std::uint64_t, std::vector<std::uint32_t>> rows_of(const fb::shader::MergedTable& merged) {
    std::size_t at = static_cast<std::size_t>(read<std::uint32_t>(merged.resourceMeta, 0)) +
                     read<std::uint32_t>(merged.resourceMeta, 4);
    const auto count = read<std::uint32_t>(merged.resource, at);
    at += 4;
    std::map<std::uint64_t, std::vector<std::uint32_t>> rows;
    for (std::uint32_t row = 0; row < count; ++row) {
        const auto key = read<std::uint64_t>(merged.resource, at);
        const auto values = read<std::uint32_t>(merged.resource, at + 8);
        at += 12;
        auto& out = rows[key];
        for (std::uint32_t value = 0; value < values; ++value, at += 4) out.push_back(read<std::uint32_t>(merged.resource, at));
    }
    return rows;
}
} // namespace

int main() {
    const auto base = lookup(3, {{1, {0}}});
    // One mod adds a row onto a program the game has; another brings programs of its own and
    // points rows at them (indices 3 and 4, which the base's three records do not have).
    const auto good = lookup(3, {{1, {0}}, {2, {2}}});
    const auto own = lookup(3, {{1, {0}}, {3, {4}}, {4, {1, 3}}, {5, {1}}});
    const std::vector<fb::shader::Table> edits{good.table(), own.table()};
    const auto merged = fb::shader::merge_program_lookup(base.table(), edits);
    const auto rows = rows_of(merged);
    check(merged.added == 2 && rows.contains(2) && rows.contains(5), "Rows onto the base's programs are merged");
    check(merged.outside == 2 && !rows.contains(3) && !rows.contains(4),
          "Rows pointing past the base's records are left out, also when only one of their indices does");
    check(rows.size() == 3 && rows.at(1) == std::vector<std::uint32_t>{0}, "The base's own rows stay as they were");
    check(read<std::uint32_t>(merged.resourceMeta, 8) == merged.resource.size() - (read<std::uint32_t>(merged.resourceMeta, 0) + read<std::uint32_t>(merged.resourceMeta, 4)),
          "The lookup size in the metadata matches the merged rows");
    for (const auto& [key, values] : rows)
        for (const auto value : values) check(value < read<std::uint32_t>(merged.resource, 0), "No merged row points past the combined records");
    const auto native = extended_lookup(false, 0);
    const auto extension = extended_lookup(true, 10);
    const auto incompatible = extended_lookup(true, 11, std::byte{8});
    const std::vector<fb::shader::Table> graphs{extension.table(),
                                                incompatible.table()};
    const auto combined =
        fb::shader::merge_program_lookup(native.table(), graphs);
    const auto graph_rows = rows_of(combined);
    check(read<std::uint32_t>(combined.resource, 0) == 3 &&
              graph_rows.contains(10),
          "A bounded extension retains its extra program and material row");
    check(
        graph_rows.contains(11) && combined.outside == 0 && graph_rows.at(10) != graph_rows.at(11),
        "Independent graphs retain distinct program indices");
    check(read<std::uint64_t>(combined.resource, 132) == 200,
          "The native program heap remains unchanged");
    const auto program_table = read<std::uint64_t>(combined.resource, 4);
    for (const auto key : {std::uint64_t{10}, std::uint64_t{11}}) {
        const auto record = program_table + graph_rows.at(key).front() * 168;
        const auto pointer = read<std::uint64_t>(combined.resource, static_cast<std::size_t>(record + 100));
        check(combined.resource.at(static_cast<std::size_t>(pointer)) == (key == 10 ? std::byte{7} : std::byte{8}),
              "Each remapped program still addresses its own opaque heap bytes");
    }
    const auto program_size = read<std::uint32_t>(combined.resourceMeta, 0);
    const auto relocation_size = read<std::uint32_t>(combined.resourceMeta, 4);
    for (std::size_t at = 0; at < relocation_size; at += 4) {
        const auto field = read<std::uint32_t>(combined.resource, program_size + at);
        check(field <= program_size && 8 <= program_size - field &&
              read<std::uint64_t>(combined.resource, field) <= program_size,
              "Every merged relocation remains bounded by the combined program payload");
    }
    auto damaged = extension;
    damaged.resource[376] = std::byte{9};
    const std::vector<fb::shader::Table> damaged_graph{damaged.table()};
    const auto rejected =
        fb::shader::merge_program_lookup(native.table(), damaged_graph);
    check(read<std::uint32_t>(rejected.resource, 0) == 2 &&
              rows_of(rejected).contains(10) && rejected.resource[200] == std::byte{3},
          "A private graph cannot overwrite the native program heap");
    auto invalid = extension;
    const std::uint32_t outside = 409;
    std::memcpy(invalid.resource.data() + 412, &outside, sizeof(outside));
    const std::vector<fb::shader::Table> invalid_graph{invalid.table()};
    check(
        fb::shader::merge_program_lookup(native.table(), invalid_graph).outside ==
            1,
        "A relocation past the extension payload is rejected");
    if (failures) return 1;
    std::cout << "shader lookup tests passed\n";
    return 0;
}
