#include "shader_lookup.h"

#include <algorithm>
#include <cstring>
#include <map>
#include <stdexcept>
#include <string>
#include <array>
#include <optional>

namespace dingosdk::frostbite::shader {
namespace {

template <typename Type>
Type read(const std::span<const std::byte> bytes, const std::size_t offset) {
    if (offset > bytes.size() || sizeof(Type) > bytes.size() - offset)
        throw std::out_of_range("Shader lookup read exceeds its payload");
    Type result{};
    std::memcpy(&result, bytes.data() + offset, sizeof(result));
    return result;
}

template <typename Type>
void append(std::vector<std::byte>& bytes, const Type value) {
    const auto offset = bytes.size();
    bytes.resize(offset + sizeof(value));
    std::memcpy(bytes.data() + offset, &value, sizeof(value));
}

using ProgramRows = std::map<std::uint64_t, std::vector<std::uint32_t>>;
using TextureRows = std::map<std::uint64_t, std::uint64_t>;

// Where the lookup section starts, after the compiled programs and relocations.
std::size_t lookup_offset(const Table& table) {
    if (table.resourceMeta.size() != 16)
        throw std::runtime_error("Program lookup metadata is not 16 bytes");
    const auto programSize = read<std::uint32_t>(table.resourceMeta, 0);
    const auto relocationSize = read<std::uint32_t>(table.resourceMeta, 4);
    const auto lookupSize = read<std::uint32_t>(table.resourceMeta, 8);
    const auto offset = static_cast<std::uint64_t>(programSize) + relocationSize;
    if (offset > table.resource.size() || lookupSize < 4 ||
        lookupSize != table.resource.size() - offset)
        throw std::runtime_error("Program lookup metadata does not match its payload");
    return static_cast<std::size_t>(offset);
}

ProgramRows read_program_rows(const Table& table) {
    auto offset = lookup_offset(table);
    const auto count = read<std::uint32_t>(table.resource, offset);
    offset += 4;
    ProgramRows rows;
    for (std::uint32_t index = 0; index < count; ++index) {
        const auto key = read<std::uint64_t>(table.resource, offset);
        const auto values = read<std::uint32_t>(table.resource, offset + 8);
        offset += 12;
        if (!values || values > 4096 ||
            static_cast<std::uint64_t>(values) * 4 > table.resource.size() - offset)
            throw std::runtime_error("Program lookup rows are invalid");
        auto& row = rows[key];
        row.reserve(values);
        for (std::uint32_t value = 0; value < values; ++value) {
            row.push_back(read<std::uint32_t>(table.resource, offset));
            offset += 4;
        }
    }
    if (offset != table.resource.size())
        throw std::runtime_error("Program lookup has trailing data");
    return rows;
}

TextureRows read_texture_rows(const Table& table) {
    if (table.resource.size() % 16)
        throw std::runtime_error("Texture lookup does not have its native layout");
    TextureRows rows;
    for (std::size_t offset = 0; offset < table.resource.size(); offset += 16)
        rows.emplace(read<std::uint64_t>(table.resource, offset),
                     read<std::uint64_t>(table.resource, offset + 8));
    return rows;
}

} // namespace

namespace {
struct ProgramGraph {
    std::uint32_t size{}, count{};
    std::uint64_t start{};
    std::vector<std::uint32_t> fields;
};
std::optional<ProgramGraph> program_graph(const Table& table) {
    (void)lookup_offset(table);
    ProgramGraph graph;
    graph.size = read<std::uint32_t>(table.resourceMeta, 0);
    if (graph.size < 12) return std::nullopt;
    graph.count = read<std::uint32_t>(table.resource, 0);
    graph.start = read<std::uint64_t>(table.resource, 4);
    if (graph.start > graph.size || graph.count > (graph.size - graph.start) / 168) return std::nullopt;
    const auto fix = read<std::uint32_t>(table.resourceMeta, 4);
    if (fix % 4) return std::nullopt;
    for (std::size_t at = 0; at < fix; at += 4) {
        const auto field = read<std::uint32_t>(table.resource, graph.size + at);
        if (field > graph.size || 8 > graph.size - field || read<std::uint64_t>(table.resource, field) > graph.size)
            return std::nullopt;
        graph.fields.push_back(field);
    }
    // Every program array must stay inside this graph. Its pointers are copied
    // through the relocation list; an omitted relocation cannot be rebased.
    const std::array<std::array<std::size_t, 3>, 7> arrays{{
        {0x0c,0x10,16},{0x18,0x1c,8},{0x30,0x34,16},{0x3c,0x40,6},
        {0x48,0x4c,8},{0x54,0x58,2},{0x60,0x64,8}}};
    for (std::uint32_t i = 0; i < graph.count; ++i) {
        const auto record = static_cast<std::size_t>(graph.start) + i * 168;
        for (const auto& array : arrays) {
            const auto count = read<std::uint32_t>(table.resource, record + array[0]);
            const auto pointer = read<std::uint64_t>(table.resource, record + array[1]);
            if (pointer > graph.size || count > (graph.size - pointer) / array[2]) return std::nullopt;
            if (count && std::find(graph.fields.begin(), graph.fields.end(), record + array[1]) == graph.fields.end())
                return std::nullopt;
        }
    }
    return graph;
}
}

MergedTable merge_program_lookup(const Table& base, const std::span<const Table> edits) {
    const auto native = program_graph(base);
    if (!native) throw std::runtime_error("Base shader program graph is invalid");
    auto rows = read_program_rows(base);
    const auto base_rows = rows;
    MergedTable merged;
    merged.resource.assign(base.resource.begin(), base.resource.begin() + native->size);
    auto fields = native->fields;
    struct Copy { std::vector<std::byte> bytes; std::vector<std::uint32_t> fields; };
    std::vector<Copy> copies;
    const auto put = [](auto& bytes, std::size_t at, auto value) { std::memcpy(bytes.data() + at, &value, sizeof(value)); };
    for (const auto& edit : edits) {
        const auto graph = program_graph(edit);
        const auto edit_rows = read_program_rows(edit);
        std::optional<std::size_t> offset;
        std::map<std::uint32_t, std::uint32_t> indices;
        for (const auto& [key, values] : edit_rows) {
            if (base_rows.contains(key)) continue;
            if (!graph || std::any_of(values.begin(), values.end(), [&](auto index) { return index >= graph->count; })) {
                ++merged.outside;
                continue;
            }
            // Existing rows win by mod order. Never replace the game's rows.
            if (rows.contains(key)) { ++merged.conflicts; continue; }
            if (!offset) {
                // Retain each graph's complete heap, including opaque records.
                // Its program indices are local and must never address another mod's graph.
                constexpr std::size_t maximum_bytes = 64 * 1024 * 1024;
                if (graph->size > maximum_bytes || merged.resource.size() > maximum_bytes - graph->size) throw std::runtime_error("Combined shader graphs exceed 64 MiB");
                merged.resource.resize((merged.resource.size() + 15) & ~std::size_t{15});
                offset = merged.resource.size();
                merged.resource.insert(merged.resource.end(), edit.resource.begin(), edit.resource.begin() + graph->size);
                for (const auto field : graph->fields) {
                    const auto pointer = read<std::uint64_t>(edit.resource, field);
                    put(merged.resource, *offset + field, pointer ? pointer + *offset : 0);
                    fields.push_back(static_cast<std::uint32_t>(*offset + field));
                }
            }
            std::vector<std::uint32_t> mapped;
            for (const auto index : values) {
                auto [entry, inserted] = indices.emplace(index, native->count + static_cast<std::uint32_t>(copies.size()));
                if (inserted) {
                    if (copies.size() >= 64 * 1024 * 1024 / 168) throw std::runtime_error("Too many combined shader programs");
                    const auto record = static_cast<std::size_t>(graph->start) + index * 168;
                    Copy copy;
                    copy.bytes.assign(merged.resource.begin() + static_cast<std::ptrdiff_t>(*offset + record),
                                      merged.resource.begin() + static_cast<std::ptrdiff_t>(*offset + record + 168));
                    for (const auto field : graph->fields) if (field >= record && field < record + 168)
                        copy.fields.push_back(static_cast<std::uint32_t>(field - record));
                    copies.push_back(std::move(copy));
                }
                mapped.push_back(entry->second);
            }
            rows.emplace(key, std::move(mapped));
            ++merged.added;
        }
    }
    if (!copies.empty()) {
        merged.resource.resize((merged.resource.size() + 15) & ~std::size_t{15});
        const auto table = static_cast<std::uint64_t>(merged.resource.size());
        const auto source_start = static_cast<std::size_t>(native->start);
        std::vector<std::byte> originals(base.resource.begin() + static_cast<std::ptrdiff_t>(source_start),
                                         base.resource.begin() + static_cast<std::ptrdiff_t>(source_start + native->count * 168));
        merged.resource.insert(merged.resource.end(), originals.begin(), originals.end());
        for (const auto field : native->fields) if (field >= source_start && field < source_start + native->count * 168)
            fields.push_back(static_cast<std::uint32_t>(table + field - source_start));
        for (const auto& copy : copies) {
            const auto at = merged.resource.size();
            merged.resource.insert(merged.resource.end(), copy.bytes.begin(), copy.bytes.end());
            for (const auto field : copy.fields) fields.push_back(static_cast<std::uint32_t>(at + field));
        }
        put(merged.resource, 0, native->count + static_cast<std::uint32_t>(copies.size()));
        put(merged.resource, 4, table);
        fields.push_back(4);
    }
    if (merged.resource.size() > 128 * 1024 * 1024) throw std::runtime_error("Combined shader database exceeds 128 MiB");
    const auto size = static_cast<std::uint32_t>(merged.resource.size());
    std::sort(fields.begin(), fields.end());
    fields.erase(std::unique(fields.begin(), fields.end()), fields.end());
    for (const auto field : fields) append(merged.resource, field);
    const auto fix = static_cast<std::uint32_t>(fields.size() * 4);
    const auto start = merged.resource.size();
    append(merged.resource, static_cast<std::uint32_t>(rows.size()));
    for (const auto& [key, values] : rows) {
        append(merged.resource, key);append(merged.resource, static_cast<std::uint32_t>(values.size()));
        for (const auto index : values) append(merged.resource, index);
    }
    merged.resourceMeta.assign(base.resourceMeta.begin(), base.resourceMeta.end());
    put(merged.resourceMeta, 0, size);put(merged.resourceMeta, 4, fix);
    put(merged.resourceMeta, 8, static_cast<std::uint32_t>(merged.resource.size() - start));
    return merged;
}

MergedTable merge_texture_lookup(const Table& base, const std::span<const Table> edits) {
    auto rows = read_texture_rows(base);
    const auto baseRows = rows;

    MergedTable merged;
    for (const auto& edit : edits) {
        for (const auto& [key, value] : read_texture_rows(edit)) {
            if (baseRows.contains(key)) continue;
            const auto [row, inserted] = rows.try_emplace(key, value);
            if (inserted) ++merged.added;
            else if (row->second != value) ++merged.conflicts;
        }
    }

    merged.resource.reserve(rows.size() * 16);
    for (const auto& [key, value] : rows) {
        append(merged.resource, key);
        append(merged.resource, value);
    }
    merged.resourceMeta.assign(base.resourceMeta.begin(), base.resourceMeta.end());
    return merged;
}

} // namespace dingosdk::frostbite::shader
