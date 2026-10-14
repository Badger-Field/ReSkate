"""The EBX reader and writer, checked against ReSkate's C++ (ebx_document.cpp, ebx_writer.cpp).

There are no game EBX files in the repository, so the documents are synthetic: a reflection
table describing a class with a field of every kind, objects filled from a seed, written by
the Python writer. The C++ harness (tests/crosscheck/ebx_crosscheck.cpp) then reads each one
and dumps it, rewrites it and rewrites it after edits; the Python port must parse to the
same dump and write the same bytes. The C++ part is skipped when no C++20 compiler is available.
"""
import os
import random
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))

from reskate_mapbuilder.bundle import Guid  # noqa: E402
from reskate_mapbuilder.ebx import (BoxedReference, BoxedValueRecord, Document, FieldCategory, FieldType,  # noqa: E402
                                    FieldValue, ImportReference, InstanceRecord, Object, PointerKind,
                                    PointerReference, ResourceReference, Sha1, TypeReference, ensure_fixup_type,
                                    read_document, read_file_guid, write_document)

T = FieldType
C = FieldCategory


def enc(kind, category):
    return (kind << 4) | category


# (name, size, alignment, fields); a field is (name, encoded type, class ref, offset).
TYPES = [
    ("DataContainer", 16, 8, [("Flags", enc(T.uint32, C.primitive), 0, 8)]),
    ("TestStruct", 24, 8, [
        ("A", enc(T.int32, C.primitive), 0, 0), ("B", enc(T.float32, C.primitive), 0, 4),
        ("C", enc(T.boolean, C.primitive), 0, 8), ("D", enc(T.uint16, C.primitive), 0, 10),
        ("E", enc(T.int8, C.primitive), 0, 12), ("Values", enc(T.float32, C.array), 0, 16)]),
    ("TestAsset", 304, 8, [
        ("$", enc(T.inherited, C.none), 0, 0),
        ("Name", enc(T.cString, C.primitive), 0, 16), ("Label", enc(T.string, C.primitive), 0, 24),
        ("Kind", enc(T.enumeration, C.enumeration), 0, 56), ("Enabled", enc(T.boolean, C.primitive), 0, 60),
        ("Small", enc(T.int8, C.primitive), 0, 61), ("Byte", enc(T.uint8, C.primitive), 0, 62),
        ("Short", enc(T.int16, C.primitive), 0, 64), ("UShort", enc(T.uint16, C.primitive), 0, 66),
        ("Int", enc(T.int32, C.primitive), 0, 68), ("UInt", enc(T.uint32, C.primitive), 0, 72),
        ("Long", enc(T.int64, C.primitive), 0, 80), ("ULong", enc(T.uint64, C.primitive), 0, 88),
        ("Float", enc(T.float32, C.primitive), 0, 96), ("Double", enc(T.float64, C.primitive), 0, 104),
        ("Id", enc(T.guid, C.primitive), 0, 112), ("Hash", enc(T.sha1, C.primitive), 0, 128),
        ("Mesh", enc(T.resourceRef, C.primitive), 0, 152), ("Type", enc(T.typeRef, C.primitive), 0, 160),
        ("Boxed", enc(T.boxedValueRef, C.primitive), 0, 168), ("Inner", enc(T.structure, C.structure), 1, 184),
        ("Next", enc(T.pointer, C.pointer), 0, 208), ("External", enc(T.pointer, C.pointer), 0, 216),
        ("Missing", enc(T.pointer, C.pointer), 0, 224), ("Numbers", enc(T.int32, C.array), 0, 232),
        ("Structs", enc(T.structure, C.array), 1, 240), ("Names", enc(T.cString, C.array), 0, 248),
        ("Refs", enc(T.pointer, C.array), 0, 256), ("File", enc(T.fileRef, C.primitive), 0, 264),
        ("Callback", enc(T.function, C.function), 0, 272), ("Prim", enc(T.typeRef, C.primitive), 0, 280),
        ("Handler", enc(T.delegate, C.delegate), 0, 288), ("Empty", enc(T.int32, C.array), 0, 296)]),
    ("TestChild", 24, 8, [("$", enc(T.inherited, C.none), 0, 0), ("Value", enc(T.int32, C.primitive), 0, 16)]),
]


def guid(seed):
    return Guid(bytes((seed * 13 + k * 7 + 1) & 0xFF for k in range(16)))


def name_hash(name):
    value = 5381
    for b in name.encode():
        value = ((value * 33) & 0xFFFFFFFF) ^ b
    return value


def reflection(hashed=False, odd=False):
    """A REFL (or RFL2, with hashed names) chunk body for TYPES."""
    names, strings = {}, bytearray()

    def name(text):
        if text not in names:
            names[text] = len(strings)
            strings.extend(text.encode() + b"\0")
        return name_hash(text) if hashed else names[text]

    body = bytearray(struct.pack("<I", len(TYPES)))
    for index in range(len(TYPES)):
        body += guid(100 + index).bytes + struct.pack("<I", 0xC0DE0000 + index)
    body += struct.pack("<I", len(TYPES))
    first = 0
    for type_name, size, alignment, fields in TYPES:
        body += struct.pack("<IiHHHH", name(type_name), first, len(fields), enc(T.dbObject, 0) << 1, size, alignment)
        first += len(fields)
    body += struct.pack("<I", first)
    for _, _, _, fields in TYPES:
        for field_name, encoded, class_ref, offset in fields:
            body += struct.pack("<IIHH", name(field_name), offset, encoded << 1, class_ref)
    body += struct.pack("<I", 1) + bytes(range(12)) + struct.pack("<I", 1) + bytes(range(8))  # a group, a mapping
    if hashed:
        body += struct.pack("<I", len(names))
        for text, offset in names.items():
            body += struct.pack("<II", name_hash(text), offset)
    body += strings
    if odd and len(body) % 2 == 0:
        body += b"\x5a"
    return bytes(body)


def empty_document(hashed=False, serialized=False, odd=False):
    """The reflection and nothing else, read back so the types and fields are filled in."""
    doc = Document(file_guid=guid(1), serialized_layout=serialized, hashed_reflection_names=hashed,
                   reflection_data=reflection(hashed, odd))
    parsed = read_document(write_document(doc))
    assert [t.name for t in parsed.types] == [t[0] for t in TYPES]
    return parsed


def new_object(doc, descriptor, values):
    obj = Object(descriptor)

    def walk(index):
        t = doc.types[index]
        for i in range(t.field_index, t.field_index + t.field_count):
            f = doc.fields[i]
            if f.type == T.inherited:
                walk(f.class_ref)
            else:
                obj.fields.append(FieldValue(i, f.name, values.get(f.name)))
    walk(descriptor)
    return obj


def text(rng, longest=12):
    return "".join(rng.choice("abcdefghijklmnopqrstuvwxyz_/0123456789") for _ in range(rng.randint(0, longest)))


def synthetic(seed, hashed=False, serialized=False, odd=False):
    rng = random.Random(seed)
    doc = empty_document(hashed, serialized, odd)
    doc.imports = [ImportReference(guid(200 + i), guid(300 + i)) for i in range(3)]

    def struct_value():
        return new_object(doc, 1, {
            "A": rng.randint(-2**31, 2**31 - 1), "B": float(struct.unpack("<f", struct.pack("<f", rng.uniform(-1e6, 1e6)))[0]),
            "C": rng.random() < 0.5, "D": rng.randint(0, 0xFFFF), "E": rng.randint(-128, 127),
            "Values": [float(rng.randint(-100, 100)) / 4 for _ in range(rng.randint(0, 3))]})

    children = 2 + seed % 3
    pointer = lambda: rng.choice([PointerReference(PointerKind.null, -1),  # noqa: E731
                                  PointerReference(PointerKind.internal, rng.randrange(children + 1)),
                                  PointerReference(PointerKind.external, rng.randrange(3))])
    shared = text(rng)
    root = new_object(doc, 2, {
        "Flags": rng.randint(0, 2**32 - 1), "Name": text(rng, 40), "Label": text(rng, 31), "Kind": rng.randint(-5, 5),
        "Enabled": rng.random() < 0.5, "Small": rng.randint(-128, 127), "Byte": rng.randint(0, 255),
        "Short": rng.randint(-2**15, 2**15 - 1), "UShort": rng.randint(0, 2**16 - 1),
        "Int": rng.randint(-2**31, 2**31 - 1), "UInt": rng.randint(0, 2**32 - 1),
        "Long": rng.randint(-2**63, 2**63 - 1), "ULong": rng.randint(0, 2**64 - 1),
        "Float": 0.15625 * rng.randint(-1000, 1000), "Double": rng.uniform(-1e300, 1e300),
        "Id": guid(rng.randint(0, 255)), "Hash": Sha1(bytes(rng.randrange(256) for _ in range(20))),
        "Mesh": ResourceReference(rng.randint(0, 2**64 - 1)), "Type": TypeReference(False, 0, 3, 3 << 2),
        "Boxed": BoxedReference(0x80000260, 0, 0), "Inner": struct_value(),
        "Next": PointerReference(PointerKind.internal, 1), "External": PointerReference(PointerKind.external, 2),
        "Missing": PointerReference(), "Numbers": [rng.randint(-2**31, 2**31 - 1) for _ in range(rng.randint(0, 6))],
        "Structs": [struct_value() for _ in range(rng.randint(0, 3))],
        "Names": [rng.choice([shared, text(rng)]) for _ in range(rng.randint(0, 4))] + [shared],
        "Refs": [pointer() for _ in range(rng.randint(0, 5))], "File": rng.choice([shared, text(rng, 30)]),
        "Prim": TypeReference(True, T.float32, -1, 0x80000000 | (T.float32 << 5)),
        "Handler": TypeReference(False, 0, 1, 1 << 2), "Empty": []})
    raw = bytes((i * 7 + seed) & 0xFF for i in range(304))   # carried through padding and the function slot
    doc.instances.append(InstanceRecord(ensure_fixup_type(doc, 2), 2, exported=True, instance_guid=guid(50),
                                        object=root, raw_image=raw))
    for i in range(children):
        child = new_object(doc, 3, {"Flags": i, "Value": rng.randint(-1000, 1000)})
        doc.instances.append(InstanceRecord(ensure_fixup_type(doc, 3), 3, exported=i == 0,
                                            instance_guid=guid(60 + i), object=child))
    doc.boxed_values_offset = 0
    doc.boxed_values = [BoxedValueRecord(0, 1, 0x1234, 0x0260, 0xFFFF, bytes(rng.randrange(256) for _ in range(24)))]
    doc.pointer_offsets = [8]          # relocated with the boxed bytes
    doc.resource_ref_offsets = [16]
    doc.boxed_value_slots = [0]
    doc.boxed_reference_offsets = [0]
    return doc


def imports_only(trailing):
    """A document with no instances, the way the merge tests build one by hand."""
    fixup = guid(9).bytes + struct.pack("<6I", 0, 0, 0, 0, 0, 0) + struct.pack("<I", 2)
    fixup += guid(10).bytes + guid(0xEE).bytes + guid(11).bytes + guid(0xEE).bytes
    fixup += struct.pack("<5I", 0, 0, 0, 0, 0) + (struct.pack("<I", 0) if trailing else b"")
    body = b"EBX\0"
    for name, data in ((b"EFIX", fixup), (b"EBXD", bytes(16)), (b"EBXX", bytes(8)), (b"REFL", bytes(20))):
        body += name + struct.pack("<I", len(data)) + data + (b"\0" if len(data) & 1 else b"")
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ---- The same dump the C++ harness prints ------------------------------------------------------

_SIGNED = {T.int8, T.int16, T.int32, T.int64, T.enumeration}
_UNSIGNED = {T.uint8, T.uint16, T.uint32, T.uint64}


def dump_value(doc, kind, value):
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "b1" if value else "b0"
    if isinstance(value, int):
        assert kind in _SIGNED | _UNSIGNED
        return ("i" if kind in _SIGNED else "u") + str(value)
    if isinstance(value, float):
        return "d%016x" % struct.unpack("<Q", struct.pack("<d", value))[0]
    if isinstance(value, str):
        return "s" + value.encode("utf-8", "surrogateescape").hex()
    if isinstance(value, Guid):
        return "g" + value.bytes.hex()
    if isinstance(value, Sha1):
        return "h" + value.bytes.hex()
    if isinstance(value, ResourceReference):
        return f"r{value.id}"
    if isinstance(value, PointerReference):
        return f"p{int(value.kind)},{value.index}"
    if isinstance(value, TypeReference):
        return f"t{int(value.primitive)},{int(value.primitive_type)},{value.descriptor},{value.encoded}"
    if isinstance(value, BoxedReference):
        return f"x{value.encoded_type},{value.data_offset},{value.relative_offset}"
    if isinstance(value, Object):
        return dump_object(doc, value)
    if isinstance(value, list):
        return "[" + ",".join(dump_value(doc, kind, v) for v in value) + "]"
    raise TypeError(value)


def dump_object(doc, obj):
    if obj is None:
        return "o-"
    return f"o{obj.descriptor}{{" + "".join(
        f"{f.name}#{f.descriptor}={dump_value(doc, doc.fields[f.descriptor].type, f.value)};" for f in obj.fields) + "}"


def dump(d):
    def nums(label, values):
        return label + "".join(f" {v}" for v in values)
    lines = [f"file {d.file_guid.bytes.hex()}", f"root {d.root_type}",
             f"layout {d.data_start} {d.data_end} {d.arrays_offset} {d.boxed_values_offset} {d.strings_offset} "
             f"{int(d.serialized_layout)} {int(d.hashed_reflection_names)}",
             f"refl {d.reflection_data.hex()}", "fixup" + "".join(" " + g.bytes.hex() for g in d.fixup_type_guids),
             nums("signatures", d.fixup_type_signatures)]
    lines += [f"type {t.name} {t.name_hash} {t.field_index} {t.field_count} {t.encoded_type} {t.size} {t.alignment} "
              f"{t.guid.bytes.hex()} {t.signature}" for t in d.types]
    lines += [f"field {f.name} {f.name_hash} {f.encoded_type} {f.class_ref} {f.data_offset}" for f in d.fields]
    lines += [f"import {i.file_guid.bytes.hex()} {i.class_guid.bytes.hex()}" for i in d.imports]
    lines += [f"array {a.offset} {a.count} {a.hash} {a.encoded_type} {a.class_ref}" for a in d.arrays]
    lines += [f"boxed {b.offset} {b.count} {b.hash} {b.encoded_type} {b.class_ref} {b.raw_bytes.hex()}"
              for b in d.boxed_values]
    lines += [nums("pointers", d.pointer_offsets), nums("resources", d.resource_ref_offsets),
              nums("imports", d.import_offsets), nums("typeinfo", d.type_info_offsets),
              nums("boxslots", d.boxed_value_slots), nums("boxrefs", d.boxed_reference_offsets)]
    lines += [f"instance {i.fixup_type} {i.descriptor} {i.data_offset} {int(i.exported)} {i.instance_guid.bytes.hex()} "
              f"{i.raw_image.hex()} {dump_object(d, i.object)}" for i in d.instances]
    return "\n".join(lines) + "\n"


# ---- Edits, applied the same way on both sides ---------------------------------------------------

def edits(seed):
    rng = random.Random(1000 + seed)
    return [f"Name=s:{text(rng, 50).encode().hex()}", f"Label=s:{'renamed'.encode().hex()}",
            f"Int=i:{rng.randint(-2**31, 2**31 - 1)}", f"ULong=u:{rng.randint(0, 2**64 - 1)}", "Float=d:2.5",
            "Double=d:-0.1", "Enabled=b:1", f"Inner.A=i:{rng.randint(-99, 99)}", "Inner.Values+=d:1.75",
            "Numbers+=i:9", f"Names+=s:{'appended'.encode().hex()}", "Next=p:1,2", "Missing=p:2,0",
            "Refs+=p:1,0"]


def apply_edits(doc, specs):
    for spec in specs:
        append = "+=" in spec
        path, _, value = spec.partition("+=" if append else "=")
        kind, body = value[0], value[2:]
        parsed = {"s": lambda: bytes.fromhex(body).decode("utf-8", "surrogateescape"), "i": lambda: int(body),
                  "u": lambda: int(body), "d": lambda: float(body), "b": lambda: body == "1",
                  "p": lambda: PointerReference(PointerKind(int(body.split(",")[0])), int(body.split(",")[1]))}[kind]()
        obj = doc.root().object
        *parents, name = path.split(".")
        for parent in parents:
            obj = obj.get(parent)
        if append:
            obj.get(name).append(parsed)
        else:
            obj.set(name, parsed)


# ---- Python-only checks --------------------------------------------------------------------------

VARIANTS = [dict(), dict(hashed=True), dict(serialized=True), dict(odd=True), dict(hashed=True, odd=True)]
SEEDS = range(8)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("variant", range(len(VARIANTS)))
def test_python_round_trip(seed, variant):
    doc = synthetic(seed, **VARIANTS[variant])
    data = write_document(doc)
    back = read_document(data)
    assert write_document(back) == data
    assert read_file_guid(data) == doc.file_guid
    assert back.root_type == "TestAsset"
    root, expected = back.root().object, doc.root().object
    for f in expected.fields:
        if isinstance(f.value, (int, str, Guid, Sha1, ResourceReference)) and f.name != "Kind":
            assert root.get(f.name) == f.value, f.name
    assert root.get("double") == expected.get("Double")
    assert root.find("NAMES").value == expected.get("Names")
    assert [s.get("A") for s in root.get("Structs")] == [s.get("A") for s in expected.get("Structs")]
    assert back.instances[0].raw_image[2:8] == doc.instances[0].raw_image[2:8]   # carried padding
    assert back.instances[0].raw_image[272:280] == doc.instances[0].raw_image[272:280]   # function slot


def test_object_set_and_find():
    doc = synthetic(3)
    root = doc.root().object
    root.set("inner.a", 5)
    assert root.get("Inner").get("A") == 5
    with pytest.raises(KeyError):
        root.set("Nope", 1)
    with pytest.raises(KeyError):
        root.set("Int.x", 1)
    assert root.find("missing-field") is None


def test_edit_changes_only_what_was_set():
    doc = synthetic(5)
    data = write_document(doc)
    edited = read_document(data)
    edited.root().object.set("Int", 1234)
    edited.root().object.set("Label", "hello")
    back = read_document(write_document(edited))
    assert back.root().object.get("Int") == 1234
    assert back.root().object.get("Label") == "hello"
    original = read_document(data).root().object
    for f in back.root().object.fields:
        if f.name not in ("Int", "Label"):
            assert dump_value(back, back.fields[f.descriptor].type, f.value) == \
                dump_value(back, back.fields[f.descriptor].type, original.get(f.name)), f.name


def test_ensure_fixup_type():
    doc = empty_document()
    assert doc.fixup_type_guids == []
    assert ensure_fixup_type(doc, 2) == 0
    assert ensure_fixup_type(doc, 3) == 1
    assert ensure_fixup_type(doc, 2) == 0
    assert doc.fixup_type_guids == [doc.types[2].guid, doc.types[3].guid]
    doc.fixup_type_guids.append(guid(77))           # a type with no signature: the new slot goes before it
    assert ensure_fixup_type(doc, 1) == 2
    assert doc.fixup_type_guids[2:] == [doc.types[1].guid, guid(77)]
    with pytest.raises(IndexError):
        ensure_fixup_type(doc, 4)


def test_writer_refuses_what_the_cpp_refuses():
    doc = synthetic(0)
    doc.root().object.set("Small", 128)
    with pytest.raises(OverflowError):
        write_document(doc)
    doc = synthetic(0)
    doc.root().object.set("Label", "x" * 32)
    with pytest.raises(OverflowError):
        write_document(doc)
    doc = synthetic(0)
    doc.root().object.set("Float", 1e39)
    with pytest.raises(OverflowError):
        write_document(doc)
    doc = synthetic(0)
    doc.root().object.set("Next", PointerReference(PointerKind.internal, 99))
    with pytest.raises(ValueError):
        write_document(doc)
    doc = synthetic(0)
    doc.instances.append(doc.instances.pop(1))      # an exported instance after an internal one
    with pytest.raises(ValueError):
        write_document(doc)
    with pytest.raises(ValueError):
        write_document(Document())


def test_reader_refuses_bad_input():
    data = write_document(synthetic(1))
    with pytest.raises(ValueError):
        read_document(b"RIFX" + data[4:])
    with pytest.raises(ValueError):
        read_document(data[:-2])
    with pytest.raises(ValueError):
        read_document(data[:8] + b"WAVE" + data[12:])


# ---- Against the C++ ----------------------------------------------------------------------------

def compiler():
    return shutil.which(os.environ.get("CXX", "")) or shutil.which("g++") or shutil.which("clang++")


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    cxx = compiler()
    if not cxx:
        pytest.skip("no C++ compiler")
    out = tmp_path_factory.mktemp("ebxxc") / "ebx_crosscheck"
    src = [HERE / "crosscheck" / "ebx_crosscheck.cpp"] + [REPO / "Engine" / "Resource" / f for f in
                                                           ("ebx_document.cpp", "ebx_writer.cpp", "binary_io.cpp")]
    result = subprocess.run([cxx, "-std=c++20", "-O1", f"-I{REPO}", *map(str, src), "-o", str(out)],
                            capture_output=True, text=True)
    if result.returncode:
        pytest.skip("C++ harness did not build: " + result.stderr[-2000:])
    return out


def cpp(harness, *args):
    result = subprocess.run([str(harness), *map(str, args)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def check_against_cpp(harness, tmp_path, data):
    """Python and C++ read the same document and rewrite it to the same bytes."""
    source = tmp_path / "in.ebx"
    source.write_bytes(data)
    assert dump(read_document(data)) == cpp(harness, "dump", source)
    cpp(harness, "rewrite", source, tmp_path / "cpp.ebx")
    cpp_bytes = (tmp_path / "cpp.ebx").read_bytes()
    assert write_document(read_document(data)) == cpp_bytes
    return cpp_bytes


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("variant", range(len(VARIANTS)))
def test_cpp_reads_and_rewrites_python_documents(harness, tmp_path, seed, variant):
    data = write_document(synthetic(seed, **VARIANTS[variant]))
    assert check_against_cpp(harness, tmp_path, data) == data   # the C++ round-trips the Python writer's output


@pytest.mark.parametrize("seed", SEEDS)
def test_edits_agree_with_cpp(harness, tmp_path, seed):
    data = write_document(synthetic(seed, hashed=seed % 2 == 1))
    source = tmp_path / "in.ebx"
    source.write_bytes(data)
    specs = edits(seed)
    cpp(harness, "edit", source, tmp_path / "cpp.ebx", *specs)
    doc = read_document(data)
    apply_edits(doc, specs)
    edited = write_document(doc)
    assert edited == (tmp_path / "cpp.ebx").read_bytes()
    assert edited != data
    root = read_document(edited).root().object
    assert root.get("Label") == "renamed" and root.get("Numbers")[-1] == 9 and root.get("Names")[-1] == "appended"
    assert root.get("Inner").get("Values")[-1] == 1.75
    check_against_cpp(harness, tmp_path, edited)


@pytest.mark.parametrize("trailing", [False, True])
def test_hand_built_document(harness, tmp_path, trailing):
    data = imports_only(trailing)
    rewritten = check_against_cpp(harness, tmp_path, data)
    # Not the input's bytes (the writer puts EBXD first and always reserves the empty-array
    # block), but a fixed point from then on, on both sides.
    assert rewritten != data
    assert check_against_cpp(harness, tmp_path, rewritten) == rewritten
    assert read_document(rewritten).imports == read_document(data).imports
    assert read_file_guid(data) == guid(9)


def test_array_hash_is_borrowed_on_rewrite(harness, tmp_path):
    # Two int32 arrays with their own name hashes: the first write gives each its own, but a
    # rewrite takes the hash of the first record of the same element type the document read.
    doc = synthetic(2)
    doc.root().object.set("Numbers", [1, 2, 3])
    doc.root().object.set("Empty", [4, 5])
    data = write_document(doc)
    assert len({a.hash for a in read_document(data).arrays if a.encoded_type == enc(T.int32, C.array) << 1}) == 2
    rewritten = check_against_cpp(harness, tmp_path, data)
    assert rewritten != data
    assert len({a.hash for a in read_document(rewritten).arrays if a.encoded_type == enc(T.int32, C.array) << 1}) == 1
    assert check_against_cpp(harness, tmp_path, rewritten) == rewritten
