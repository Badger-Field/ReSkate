// Reads EBX documents with ReSkate's C++ (Engine/Resource/ebx_document.cpp) and writes them
// back with ebx_writer.cpp, optionally after editing fields. Built and run by
// tests/test_ebx_crosscheck.py when a C++ compiler is available.
//
//   ebx_crosscheck dump IN                  prints the document as tests/test_ebx_crosscheck.py dumps it
//   ebx_crosscheck rewrite IN OUT           read_document then write_document
//   ebx_crosscheck edit IN OUT EDIT...      the same with edits to the root object between them:
//                                           Path=K:V sets, Path+=K:V appends to an array, where Path is
//                                           a dotted field path and K is s (string, hex), i, u, d (double),
//                                           b (bool), p (pointer kind,index)
#include "Engine/Resource/ebx_writer.h"

#include <bit>
#include <cinttypes>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <iterator>
#include <sstream>
#include <string>

using namespace dingosdk::frostbite;
using namespace dingosdk::frostbite::ebx;

static std::vector<std::byte> load(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    std::vector<char> raw((std::istreambuf_iterator<char>(in)), {});
    return {reinterpret_cast<std::byte*>(raw.data()), reinterpret_cast<std::byte*>(raw.data() + raw.size())};
}
static void save(const std::string& path, const std::vector<std::byte>& data) {
    std::ofstream out(path, std::ios::binary);
    out.write(reinterpret_cast<const char*>(data.data()), static_cast<std::streamsize>(data.size()));
}

template <typename Bytes> static std::string hex(const Bytes& bytes) {
    static const char* digits = "0123456789abcdef";
    std::string out;
    for (const auto b : bytes) {
        const auto v = static_cast<unsigned char>(b);
        out += digits[v >> 4];
        out += digits[v & 15];
    }
    return out;
}
static std::string unhex(const std::string& text) {
    std::string out;
    for (std::size_t i = 0; i + 1 < text.size(); i += 2) out += static_cast<char>(std::stoi(text.substr(i, 2), nullptr, 16));
    return out;
}

static void dump_value(std::ostream& out, const Value& value);

static void dump_object(std::ostream& out, const std::shared_ptr<Object>& object) {
    if (!object) { out << "o-"; return; }
    out << "o" << object->descriptor << "{";
    for (const auto& field : object->fields) {
        out << field.name << "#" << field.descriptor << "=";
        dump_value(out, field.value);
        out << ";";
    }
    out << "}";
}

static void dump_value(std::ostream& out, const Value& value) {
    std::visit([&](const auto& v) {
        using T = std::decay_t<decltype(v)>;
        if constexpr (std::is_same_v<T, std::monostate>) out << "-";
        else if constexpr (std::is_same_v<T, bool>) out << "b" << (v ? 1 : 0);
        else if constexpr (std::is_same_v<T, std::int64_t>) out << "i" << v;
        else if constexpr (std::is_same_v<T, std::uint64_t>) out << "u" << v;
        else if constexpr (std::is_same_v<T, double>) {
            char text[32];
            std::snprintf(text, sizeof text, "%016" PRIx64, std::bit_cast<std::uint64_t>(v));
            out << "d" << text;
        }
        else if constexpr (std::is_same_v<T, std::string>) out << "s" << hex(v);
        else if constexpr (std::is_same_v<T, Guid>) out << "g" << hex(v.bytes);
        else if constexpr (std::is_same_v<T, Sha1>) out << "h" << hex(v.bytes);
        else if constexpr (std::is_same_v<T, ResourceReference>) out << "r" << v.id;
        else if constexpr (std::is_same_v<T, PointerReference>) out << "p" << static_cast<int>(v.kind) << "," << v.index;
        else if constexpr (std::is_same_v<T, TypeReference>)
            out << "t" << (v.primitive ? 1 : 0) << "," << static_cast<int>(v.primitiveType) << "," << v.descriptor << "," << v.encoded;
        else if constexpr (std::is_same_v<T, BoxedReference>) out << "x" << v.encodedType << "," << v.dataOffset << "," << v.relativeOffset;
        else if constexpr (std::is_same_v<T, std::shared_ptr<Object>>) dump_object(out, v);
        else if constexpr (std::is_same_v<T, Value::Array>) {
            out << "[";
            for (std::size_t i = 0; i < v.size(); ++i) {
                if (i) out << ",";
                dump_value(out, v[i]);
            }
            out << "]";
        }
    }, value.data);
}

template <typename List> static void dump_list(std::ostream& out, const char* label, const List& values) {
    out << label;
    for (const auto v : values) out << " " << v;
    out << "\n";
}

static void dump(std::ostream& out, const Document& d) {
    out << "file " << hex(d.fileGuid.bytes) << "\n";
    out << "root " << d.rootType << "\n";
    out << "layout " << d.dataStart << " " << d.dataEnd << " " << d.arraysOffset << " " << d.boxedValuesOffset << " "
        << d.stringsOffset << " " << d.serializedLayout << " " << d.hashedReflectionNames << "\n";
    out << "refl " << hex(d.reflectionData) << "\n";
    out << "fixup";
    for (const auto& g : d.fixupTypeGuids) out << " " << hex(g.bytes);
    out << "\n";
    dump_list(out, "signatures", d.fixupTypeSignatures);
    for (const auto& t : d.types)
        out << "type " << t.name << " " << t.nameHash << " " << t.fieldIndex << " " << t.fieldCount << " " << t.encodedType
            << " " << t.size << " " << t.alignment << " " << hex(t.guid.bytes) << " " << t.signature << "\n";
    for (const auto& f : d.fields)
        out << "field " << f.name << " " << f.nameHash << " " << f.encodedType << " " << f.classRef << " " << f.dataOffset << "\n";
    for (const auto& i : d.imports) out << "import " << hex(i.fileGuid.bytes) << " " << hex(i.classGuid.bytes) << "\n";
    for (const auto& a : d.arrays)
        out << "array " << a.offset << " " << a.count << " " << a.hash << " " << a.encodedType << " " << a.classRef << "\n";
    for (const auto& b : d.boxedValues)
        out << "boxed " << b.offset << " " << b.count << " " << b.hash << " " << b.encodedType << " " << b.classRef << " "
            << hex(b.rawBytes) << "\n";
    dump_list(out, "pointers", d.pointerOffsets);
    dump_list(out, "resources", d.resourceRefOffsets);
    dump_list(out, "imports", d.importOffsets);
    dump_list(out, "typeinfo", d.typeInfoOffsets);
    dump_list(out, "boxslots", d.boxedValueSlots);
    dump_list(out, "boxrefs", d.boxedReferenceOffsets);
    for (const auto& i : d.instances) {
        out << "instance " << i.fixupType << " " << i.descriptor << " " << i.dataOffset << " " << i.exported << " "
            << hex(i.instanceGuid.bytes) << " " << hex(i.rawImage) << " ";
        dump_object(out, i.object);
        out << "\n";
    }
}

static Value parse_value(const std::string& text) {
    const auto kind = text.at(0);
    const auto body = text.substr(2);
    switch (kind) {
    case 's': return Value{unhex(body)};
    case 'i': return Value{static_cast<std::int64_t>(std::stoll(body))};
    case 'u': return Value{static_cast<std::uint64_t>(std::stoull(body))};
    case 'd': return Value{std::stod(body)};
    case 'b': return Value{body == "1"};
    case 'p': {
        const auto comma = body.find(',');
        return Value{PointerReference{static_cast<PointerKind>(std::stoi(body.substr(0, comma))), std::stoi(body.substr(comma + 1))}};
    }
    default: throw std::runtime_error("unknown edit kind");
    }
}

static void edit(Document& document, const std::string& spec) {
    const auto append = spec.find("+=") != std::string::npos;
    const auto split = append ? spec.find("+=") : spec.find('=');
    const auto path = spec.substr(0, split);
    const auto value = parse_value(spec.substr(split + (append ? 2 : 1)));
    auto* object = document.instances.at(0).object.get();
    std::size_t begin = 0;
    while (true) {
        const auto dot = path.find('.', begin);
        const auto name = path.substr(begin, dot == std::string::npos ? std::string::npos : dot - begin);
        auto* field = const_cast<FieldValue*>(object->find(name));
        if (!field) throw std::runtime_error("no field " + name);
        if (dot == std::string::npos) {
            if (append) std::get<Value::Array>(field->value.data).push_back(value);
            else field->value = value;
            return;
        }
        object = std::get<std::shared_ptr<Object>>(field->value.data).get();
        begin = dot + 1;
    }
}

int main(int argc, char** argv) {
    if (argc < 3) return 2;
    const std::string mode = argv[1];
    try {
        auto document = read_document(load(argv[2]));
        if (mode == "dump") {
            dump(std::cout, document);
            return 0;
        }
        if (argc < 4) return 2;
        if (mode == "edit")
            for (int i = 4; i < argc; ++i) edit(document, argv[i]);
        else if (mode != "rewrite") return 2;
        save(argv[3], write_document(document));
        return 0;
    } catch (const std::exception& error) {
        std::cout << "error " << error.what() << "\n";
        return 1;
    }
}
