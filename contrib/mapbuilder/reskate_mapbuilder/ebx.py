"""EBX document reader and writer (port of Engine/Resource/ebx_document.cpp and ebx_writer.cpp).

The writer lays out a document's objects again from their values, carrying each
instance's raw image (padding and fields it has no value for), the boxed values and
the reflection table from the document it was read from, so an unmodified document
writes back to the same bytes.

Values are None, bool, int, float, str, Guid, Sha1, ResourceReference,
PointerReference, TypeReference, BoxedReference, Object (a struct) or a list (an array).
Strings hold the file's bytes as UTF-8 with surrogate escapes, so any byte survives.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional

from .bundle import Guid

_RIFF = 0x46464952
_EBX = 0x45425800
_EBXS = 0x45425853
_EBXD = 0x45425844
_EFIX = 0x45464958
_EBXX = 0x45425858
_REFL = 0x5245464C
_RFL2 = 0x52464C32
_U32 = 0xFFFFFFFF


class FieldType(IntEnum):
    inherited = 0x00
    dbObject = 0x01
    structure = 0x02
    pointer = 0x03
    array = 0x04
    fixedArray = 0x05
    string = 0x06
    cString = 0x07
    enumeration = 0x08
    fileRef = 0x09
    boolean = 0x0A
    int8 = 0x0B
    uint8 = 0x0C
    int16 = 0x0D
    uint16 = 0x0E
    int32 = 0x0F
    uint32 = 0x10
    uint64 = 0x11
    int64 = 0x12
    float32 = 0x13
    float64 = 0x14
    guid = 0x15
    sha1 = 0x16
    resourceRef = 0x17
    function = 0x18
    typeRef = 0x19
    boxedValueRef = 0x1A
    interfaceType = 0x1B
    delegate = 0x1C


class FieldCategory(IntEnum):
    none = 0
    pointer = 1
    structure = 2
    primitive = 3
    array = 4
    enumeration = 5
    function = 6
    interfaceType = 7
    delegate = 8


def _type_of(encoded: int) -> int:
    value = (encoded >> 4) & 0x1F
    return FieldType(value) if value <= 0x1C else value


def _category_of(encoded: int) -> int:
    value = encoded & 0x0F
    return FieldCategory(value) if value <= 8 else value


class PointerKind(IntEnum):
    null = 0
    internal = 1
    external = 2


@dataclass(frozen=True)
class Sha1:
    bytes: bytes = b"\0" * 20

    def __post_init__(self):
        if len(self.bytes) != 20:
            raise ValueError("SHA1 must be 20 bytes")


@dataclass
class FieldDescriptor:
    name: str = ""
    name_hash: int = 0
    encoded_type: int = 0
    class_ref: int = 0
    data_offset: int = 0

    @property
    def type(self) -> int:
        return _type_of(self.encoded_type)

    @property
    def category(self) -> int:
        return _category_of(self.encoded_type)


@dataclass
class TypeDescriptor:
    name: str = ""
    name_hash: int = 0
    field_index: int = 0
    field_count: int = 0
    encoded_type: int = 0
    size: int = 0
    alignment: int = 0
    guid: Guid = field(default_factory=Guid)
    signature: int = 0

    @property
    def type(self) -> int:
        return _type_of(self.encoded_type)

    @property
    def category(self) -> int:
        return _category_of(self.encoded_type)


@dataclass
class ImportReference:
    file_guid: Guid = field(default_factory=Guid)
    class_guid: Guid = field(default_factory=Guid)


@dataclass
class ArrayRecord:
    offset: int = 0
    count: int = 0
    hash: int = 0
    encoded_type: int = 0
    class_ref: int = 0


@dataclass
class BoxedValueRecord:
    offset: int = 0
    count: int = 0
    hash: int = 0
    encoded_type: int = 0
    class_ref: int = 0
    raw_bytes: bytes = b""


@dataclass
class PointerReference:
    kind: PointerKind = PointerKind.null
    index: int = -1


@dataclass
class TypeReference:
    primitive: bool = False
    primitive_type: int = 0
    descriptor: int = -1
    encoded: int = 0


@dataclass
class ResourceReference:
    id: int = 0


@dataclass
class BoxedReference:
    encoded_type: int = 0
    data_offset: int = -1
    relative_offset: int = 0


@dataclass
class FieldValue:
    descriptor: int
    name: str
    value: Any = None
    array_hash: Optional[int] = None   # an array's own record hash, kept on a rewrite


def _lower(name: str) -> str:
    # std::tolower in the C locale: ASCII only.
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in name)


@dataclass
class Object:
    descriptor: int = -1
    fields: list[FieldValue] = field(default_factory=list)

    def find(self, name: str) -> Optional[FieldValue]:
        """The field with this name, ignoring ASCII case, or None."""
        key = _lower(name)
        return next((f for f in self.fields if _lower(f.name) == key), None)

    def get(self, name: str, default: Any = None) -> Any:
        found = self.find(name)
        return default if found is None else found.value

    def set(self, name: str, value: Any) -> None:
        """Sets an existing field's value; a dotted name reaches into nested structs."""
        head, _, rest = name.partition(".")
        found = self.find(head)
        if found is None:
            raise KeyError(name)
        if rest:
            if not isinstance(found.value, Object):
                raise KeyError(name)
            found.value.set(rest, value)
        else:
            found.value = value


@dataclass
class InstanceRecord:
    fixup_type: int = 0
    descriptor: int = -1
    data_offset: int = 0
    exported: bool = False
    instance_guid: Guid = field(default_factory=Guid)
    object: Optional[Object] = None
    raw_image: bytes = b""


@dataclass
class Document:
    file_guid: Guid = field(default_factory=Guid)
    root_type: str = ""
    data_start: int = 0
    data_end: int = 0
    arrays_offset: int = 0
    boxed_values_offset: int = 0
    strings_offset: int = 0
    serialized_layout: bool = False
    hashed_reflection_names: bool = False
    reflection_data: bytes = b""
    fixup_type_guids: list[Guid] = field(default_factory=list)
    fixup_type_signatures: list[int] = field(default_factory=list)
    boxed_reference_offsets: list[int] = field(default_factory=list)
    types: list[TypeDescriptor] = field(default_factory=list)
    fields: list[FieldDescriptor] = field(default_factory=list)
    instances: list[InstanceRecord] = field(default_factory=list)
    imports: list[ImportReference] = field(default_factory=list)
    arrays: list[ArrayRecord] = field(default_factory=list)
    boxed_values: list[BoxedValueRecord] = field(default_factory=list)
    pointer_offsets: list[int] = field(default_factory=list)
    resource_ref_offsets: list[int] = field(default_factory=list)
    import_offsets: list[int] = field(default_factory=list)
    type_info_offsets: list[int] = field(default_factory=list)
    boxed_value_slots: list[int] = field(default_factory=list)
    # The EBXD bytes the document was read from (offsets as in the fixup tables): what boxed
    # values point at (arrays, strings) is carried from here on a rewrite.
    payload: bytes = b""
    # The reflection's 12-byte group records (index, hash, count), sorted by hash. An array
    # record's hash is the hash of the group whose index is the array field's class_ref
    # (array_hash_rule: every array this document was read with obeys that).
    reflection_groups: list[tuple[int, int, int]] = field(default_factory=list)
    array_hash_rule: bool = False

    def root(self) -> Optional[InstanceRecord]:
        return self.instances[0] if self.instances else None


# ---- Reading ----------------------------------------------------------------------------------

def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def seek(self, position: int) -> None:
        if position > len(self.data) or position < 0:
            raise ValueError("Native reader seek is out of range")
        self.pos = position

    def view(self, length: int) -> bytes:
        if length > len(self.data) - self.pos:
            raise ValueError("Native reader ran past the end")
        raw = self.data[self.pos:self.pos + length]
        self.pos += length
        return raw

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.view(size))[0]

    def u8(self) -> int: return self.unpack("<B")
    def u16(self) -> int: return self.unpack("<H")
    def u32(self) -> int: return self.unpack("<I")
    def i32(self) -> int: return self.unpack("<i")
    def u32be(self) -> int: return self.unpack(">I")
    def guid(self) -> Guid: return Guid(self.view(16))

    def c_string(self) -> bytes:
        end = self.data.find(b"\0", self.pos)
        if end < 0:
            raise ValueError("Native reader ran past the end")
        raw = self.data[self.pos:end]
        self.pos = end + 1
        return raw


@dataclass
class _Chunk:
    id: int
    begin: int
    end: int


def _nonnegative(reader: _Reader, label: str) -> int:
    value = reader.i32()
    if value < 0:
        raise ValueError(f"{label} count cannot be negative")
    return value


def _ensure_records(reader: _Reader, end: int, count: int, stride: int, label: str) -> None:
    if reader.pos > end or count > (end - reader.pos) // stride:
        raise ValueError(f"{label} records exceed their chunk")


def _read_chunks(reader: _Reader) -> list[_Chunk]:
    if reader.u32() != _RIFF:
        raise ValueError("EBX is not a RIFF document")
    riff_end = reader.u32() + 8
    if riff_end != len(reader.data):
        raise ValueError("RIFF EBX size does not match its stream")
    if reader.u32be() not in (_EBX, _EBXS):
        raise ValueError("RIFF document is not EBX or EBXS")
    chunks = []
    while reader.pos < riff_end:
        if len(reader.data) - reader.pos < 8:
            raise ValueError("truncated RIFF chunk header")
        chunk_id = reader.u32be()
        size = reader.u32()
        begin = reader.pos
        if size > riff_end - begin:
            raise ValueError("RIFF chunk exceeds its document")
        chunks.append(_Chunk(chunk_id, begin, begin + size))
        following = begin + size + (size & 1)
        if following > riff_end:
            raise ValueError("RIFF chunk padding is truncated")
        reader.seek(following)
    return chunks


def _chunk(chunks: list[_Chunk], chunk_id: int, name: str, required: bool) -> Optional[_Chunk]:
    found = [c for c in chunks if c.id == chunk_id]
    if len(found) > 1:
        raise ValueError(f"duplicate RIFF {name} chunk")
    if not found and required:
        raise ValueError(f"missing RIFF {name} chunk")
    return found[0] if found else None


def _validate_offset(doc: Document, offset: int, required: int, label: str) -> None:
    size = doc.data_end - doc.data_start
    if offset > size or required > size - offset:
        raise ValueError(f"{label} offset exceeds EBXD")


def _read_fixup(reader: _Reader, chunk: _Chunk, doc: Document) -> None:
    reader.seek(chunk.begin)
    doc.file_guid = reader.guid()
    type_count = _nonnegative(reader, "EFIX type")
    _ensure_records(reader, chunk.end, type_count, 16, "EFIX type")
    doc.fixup_type_guids = [reader.guid() for _ in range(type_count)]
    signature_count = _nonnegative(reader, "EFIX signature")
    if signature_count > type_count:
        raise ValueError("EFIX has more signatures than types")
    _ensure_records(reader, chunk.end, signature_count, 4, "EFIX signature")
    doc.fixup_type_signatures = [reader.u32() for _ in range(signature_count)]

    exported_count = _nonnegative(reader, "EFIX exported instance")
    instance_count = _nonnegative(reader, "EFIX instance")
    if exported_count > instance_count:
        raise ValueError("EFIX exported instance count is invalid")
    _ensure_records(reader, chunk.end, instance_count, 4, "EFIX instance")
    for index in range(instance_count):
        offset = reader.u32()
        _validate_offset(doc, offset, 2, "EFIX instance")
        class_ref = struct.unpack_from("<H", reader.data, doc.data_start + offset)[0]
        if class_ref >= type_count:
            raise ValueError("EFIX instance type is invalid")
        doc.instances.append(InstanceRecord(class_ref, -1, offset, index < exported_count))

    def offsets(label: str, width: int) -> list[int]:
        count = _nonnegative(reader, label)
        _ensure_records(reader, chunk.end, count, 4, label)
        result = []
        for _ in range(count):
            offset = reader.u32()
            _validate_offset(doc, offset, width, label)
            result.append(offset)
        return result

    doc.pointer_offsets = offsets("EFIX pointer", 8)
    doc.resource_ref_offsets = offsets("EFIX resource reference", 8)
    import_count = _nonnegative(reader, "EFIX import")
    _ensure_records(reader, chunk.end, import_count, 32, "EFIX import")
    doc.imports = [ImportReference(reader.guid(), reader.guid()) for _ in range(import_count)]
    doc.import_offsets = offsets("EFIX import relocation", 8)
    doc.type_info_offsets = offsets("EFIX type-info reference", 8)
    if chunk.end - reader.pos < 12:
        raise ValueError("truncated EFIX section offsets")
    doc.arrays_offset, doc.boxed_values_offset, doc.strings_offset = (reader.u32() for _ in range(3))
    _validate_offset(doc, doc.arrays_offset, 0, "EFIX array section")
    _validate_offset(doc, doc.boxed_values_offset, 0, "EFIX boxed-value section")
    _validate_offset(doc, doc.strings_offset, 0, "EFIX string section")
    if reader.pos < chunk.end:
        doc.boxed_reference_offsets = offsets("EFIX boxed reference", 16)
    if reader.pos != chunk.end:
        raise ValueError("unexpected bytes at the end of EFIX")


def _read_extra(reader: _Reader, chunk: _Chunk, doc: Document) -> None:
    reader.seek(chunk.begin)
    array_count = _nonnegative(reader, "EBXX array")
    boxed_count = _nonnegative(reader, "EBXX boxed value")
    _ensure_records(reader, chunk.end, array_count + boxed_count, 16, "EBXX")
    for index in range(array_count + boxed_count):
        offset, count, hash_, encoded, class_ref = struct.unpack("<IiIHH", reader.view(16))
        label = "EBXX array" if index < array_count else "EBXX boxed value"
        if count < 0:
            raise ValueError(f"{label} has a negative count")
        _validate_offset(doc, offset, 0, label)
        if index < array_count:
            doc.arrays.append(ArrayRecord(offset, count, hash_, encoded, class_ref))
        else:
            doc.boxed_values.append(BoxedValueRecord(offset, count, hash_, encoded, class_ref))
    if reader.pos != chunk.end:
        raise ValueError("unexpected bytes at the end of EBXX")


def _string_at(data: bytes, offset: int, end: int) -> str:
    if offset >= end:
        raise ValueError("reflection string offset is invalid")
    stop = data.find(b"\0", offset, end)
    if stop < 0:
        raise ValueError("reflection string is unterminated")
    return _text(data[offset:stop])


def _read_reflection(reader: _Reader, chunk: _Chunk, hashed: bool, doc: Document) -> list[tuple[Guid, int]]:
    reader.seek(chunk.begin)
    signature_count = _nonnegative(reader, "reflection signature")
    _ensure_records(reader, chunk.end, signature_count, 20, "reflection signature")
    ids = [(reader.guid(), reader.u32()) for _ in range(signature_count)]

    type_count = _nonnegative(reader, "reflection type")
    _ensure_records(reader, chunk.end, type_count, 16, "reflection type")
    for index in range(type_count):
        name_hash, field_index, field_count, encoded, size, alignment = struct.unpack("<IiHHHH", reader.view(16))
        if alignment > 0xFF:
            raise ValueError("reflection type alignment is unsupported")
        guid, signature = ids[index] if index < len(ids) else (Guid(), 0)
        doc.types.append(TypeDescriptor("", name_hash, field_index, field_count, encoded >> 1, size, alignment,
                                        guid, signature))

    field_count = _nonnegative(reader, "reflection field")
    _ensure_records(reader, chunk.end, field_count, 12, "reflection field")
    for _ in range(field_count):
        name_hash, data_offset, encoded, class_ref = struct.unpack("<IIHH", reader.view(12))
        doc.fields.append(FieldDescriptor("", name_hash, encoded >> 1, class_ref, data_offset))
    for t in doc.types:
        if t.field_index < 0 or t.field_index > len(doc.fields) or t.field_count > len(doc.fields) - t.field_index:
            raise ValueError("reflection type field range is invalid")

    group_count = _nonnegative(reader, "reflection group")
    _ensure_records(reader, chunk.end, group_count, 12, "reflection group")
    doc.reflection_groups = [struct.unpack("<III", reader.view(12)) for _ in range(group_count)]
    mapping_count = _nonnegative(reader, "reflection mapping")
    _ensure_records(reader, chunk.end, mapping_count, 8, "reflection mapping")
    reader.seek(reader.pos + mapping_count * 8)

    if hashed:
        name_count = _nonnegative(reader, "reflection name")
        _ensure_records(reader, chunk.end, name_count, 8, "reflection name")
        names = {}
        for _ in range(name_count):
            name_hash, offset = struct.unpack("<II", reader.view(8))
            names[name_hash] = offset
        strings = reader.pos
        for t in doc.types:
            if t.name_hash not in names:
                raise ValueError(f"RFL2 type name 0x{t.name_hash:x} is missing from {len(names)} names")
            t.name = _string_at(reader.data, strings + names[t.name_hash], chunk.end)
        for f in doc.fields:
            if f.name_hash not in names:
                raise ValueError("RFL2 field name is missing")
            f.name = _string_at(reader.data, strings + names[f.name_hash], chunk.end)
    else:
        strings = reader.pos
        for t in doc.types:
            t.name = _string_at(reader.data, strings + t.name_hash, chunk.end)
        for f in doc.fields:
            f.name = _string_at(reader.data, strings + f.name_hash, chunk.end)
    return ids


def _map_instances(doc: Document, ids: list[tuple[Guid, int]]) -> None:
    for instance in doc.instances:
        slot = instance.fixup_type
        if slot >= len(doc.fixup_type_guids) or slot >= len(doc.fixup_type_signatures):
            continue
        key = (doc.fixup_type_guids[slot], doc.fixup_type_signatures[slot])
        instance.descriptor = next((i for i, value in enumerate(ids) if value == key), -1)
    if doc.instances:
        descriptor = doc.instances[0].descriptor
        if 0 <= descriptor < len(doc.types):
            doc.root_type = doc.types[descriptor].name


_SCALARS = {
    FieldType.boolean: "<?", FieldType.int8: "<b", FieldType.uint8: "<B", FieldType.int16: "<h",
    FieldType.uint16: "<H", FieldType.int32: "<i", FieldType.enumeration: "<i", FieldType.uint32: "<I",
    FieldType.int64: "<q", FieldType.uint64: "<Q", FieldType.float32: "<f", FieldType.float64: "<d",
}


class _ValueReader:
    def __init__(self, data: bytes, doc: Document):
        self.reader = _Reader(data)
        self.doc = doc
        self.depth = 0
        self.array_hashes: dict[int, int] = {}
        for record in doc.arrays:
            self.array_hashes.setdefault(record.offset, record.hash)
        self.groups = group_hashes(doc)
        self.rule = [0, 0]   # arrays that obey the group rule, arrays that do not

    def read_instances(self) -> None:
        doc, reader = self.doc, self.reader
        for instance in doc.instances:
            if not 0 <= instance.descriptor < len(doc.types):
                continue
            start = doc.data_start + instance.data_offset
            if instance.exported:
                if start < doc.data_start + 16:
                    raise ValueError("exported EBX instance has no GUID")
                reader.seek(start - 16)
                instance.instance_guid = reader.guid()
            size = doc.types[instance.descriptor].size
            if start < doc.data_start or size > doc.data_end - start:
                raise ValueError("EBX object exceeds EBXD")
            instance.raw_image = reader.data[start:start + size]
            instance.object = self.read_object(instance.descriptor, start)

    def nest(self) -> None:
        if self.depth >= 128:
            raise ValueError("EBX types nest too deeply")
        self.depth += 1

    def read_object(self, descriptor: int, start: int) -> Object:
        self.nest()
        try:
            doc = self.doc
            if not 0 <= descriptor < len(doc.types):
                raise ValueError("EBX object type reference is invalid")
            t = doc.types[descriptor]
            if start < doc.data_start or t.size > doc.data_end - start:
                raise ValueError("EBX object exceeds EBXD")
            result = Object(descriptor)
            self.read_fields(result, t, start)
            return result
        finally:
            self.depth -= 1

    def read_fields(self, output: Object, t: TypeDescriptor, start: int) -> None:
        doc = self.doc
        for ordinal in range(t.field_count):
            index = t.field_index + ordinal
            f = doc.fields[index]
            if f.type == FieldType.inherited:
                if f.class_ref >= len(doc.types):
                    raise ValueError("EBX base type is invalid")
                self.nest()
                try:
                    self.read_fields(output, doc.types[f.class_ref], start)
                finally:
                    self.depth -= 1
                continue
            position = start + f.data_offset
            if position > doc.data_end:
                raise ValueError("EBX field offset exceeds EBXD")
            value = self.read_field(f, position)
            array_hash = None
            if f.category == FieldCategory.array and value:
                target = position + struct.unpack_from("<i", self.reader.data, position)[0] - doc.data_start
                array_hash = self.array_hashes.get(target)
                if array_hash is not None:
                    self.rule[self.groups.get(f.class_ref) != array_hash] += 1
            output.fields.append(FieldValue(index, f.name, value, array_hash))

    def read_field(self, f: FieldDescriptor, position: int) -> Any:
        doc, reader = self.doc, self.reader
        if f.category == FieldCategory.array:
            reader.seek(position)
            target = position + reader.i32()
            if target < doc.data_start + 4 or target > doc.data_end:
                raise ValueError("EBX array displacement exceeds EBXD")
            reader.seek(target - 4)
            count = reader.i32()
            if count < 0:
                raise ValueError("EBX array has a negative count")
            if count > doc.data_end - target:
                raise ValueError("EBX array exceeds EBXD")
            reader.seek(target)
            values = []
            for _ in range(count):
                element = reader.pos
                values.append(self.read_value(f.type, f.class_ref, element))
                if f.type == FieldType.structure:
                    if f.class_ref >= len(doc.types):
                        raise ValueError("EBX array struct type is invalid")
                    reader.seek(element + doc.types[f.class_ref].size)
                elif f.type in (FieldType.pointer, FieldType.cString):
                    reader.seek(_align(reader.pos, 8))
            return values
        reader.seek(position)
        return self.read_value(f.type, f.class_ref, position)

    def read_value(self, kind: int, class_ref: int, position: int) -> Any:
        doc, reader = self.doc, self.reader
        fmt = _SCALARS.get(kind)
        if fmt:
            value = reader.unpack("<B" if kind == FieldType.boolean else fmt)
            return value != 0 if kind == FieldType.boolean else value
        if kind == FieldType.guid:
            return reader.guid()
        if kind == FieldType.sha1:
            return Sha1(reader.view(20))
        if kind == FieldType.resourceRef:
            return ResourceReference(reader.unpack("<Q"))
        if kind == FieldType.string:
            raw = reader.view(32)
            return _text(raw.split(b"\0", 1)[0])
        if kind == FieldType.cString:
            return self.relative_string(position, reader.u32())
        if kind == FieldType.fileRef:
            displacement = reader.u32()
            reader.view(4)
            return self.relative_string(position, displacement)
        if kind == FieldType.pointer:
            encoded = reader.i32()
            if encoded == 0:
                return PointerReference(PointerKind.null, -1)
            if encoded & 1:
                index = encoded >> 1
                if index < 0 or index >= len(doc.imports):
                    raise ValueError("EBX pointer import is invalid")
                return PointerReference(PointerKind.external, index)
            target = position + encoded - doc.data_start
            for index, instance in enumerate(doc.instances):
                if instance.data_offset == target:
                    return PointerReference(PointerKind.internal, index)
            raise ValueError("EBX internal pointer target is invalid")
        if kind in (FieldType.typeRef, FieldType.delegate):
            encoded = reader.u32()
            reader.view(4)
            if encoded == 0:
                return TypeReference()
            if encoded & 0x80000000:
                return TypeReference(True, _type_of(((encoded & 0x7FFFFFFF) >> 5 & 0x1F) << 4), -1, encoded)
            return TypeReference(False, 0, encoded >> 2, encoded)
        if kind == FieldType.boxedValueRef:
            encoded = reader.u32()
            reader.view(4)
            displacement = reader.unpack("<q")
            doc.boxed_value_slots.append((position - doc.data_start) & _U32)
            return BoxedReference(encoded, position + 8 + displacement - doc.data_start, displacement)
        if kind == FieldType.structure:
            return self.read_object(class_ref, position)
        return None

    def relative_string(self, base: int, displacement: int) -> str:
        if displacement == _U32:
            return ""
        doc = self.doc
        target = base + displacement
        if target < doc.data_start or target >= doc.data_end:
            raise ValueError("EBX string displacement exceeds EBXD")
        end = self.reader.data.find(b"\0", target)
        if end < 0:
            raise ValueError("Native reader ran past the end")
        if end + 1 > doc.data_end:
            raise ValueError("EBX string exceeds EBXD")
        return _text(self.reader.data[target:end])


def read_document(data: bytes) -> Document:
    data = bytes(data)
    reader = _Reader(data)
    chunks = _read_chunks(reader)
    payload = _chunk(chunks, _EBXD, "EBXD", True)
    fixup = _chunk(chunks, _EFIX, "EFIX", True)
    extra = _chunk(chunks, _EBXX, "EBXX", True)
    refl = _chunk(chunks, _REFL, "REFL", False)
    rfl2 = _chunk(chunks, _RFL2, "RFL2", False)
    if (refl is None) == (rfl2 is None):
        raise ValueError("RIFF EBX must contain exactly one REFL or RFL2 chunk")

    doc = Document()
    reader.seek(8)
    doc.serialized_layout = reader.u32be() == _EBXS
    doc.data_start = _align(payload.begin, 16)
    doc.data_end = payload.end
    if doc.data_start > doc.data_end:
        raise ValueError("EBXD alignment exceeds the chunk")
    _read_fixup(reader, fixup, doc)
    _read_extra(reader, extra, doc)
    reflection = rfl2 or refl
    ids = _read_reflection(reader, reflection, rfl2 is not None, doc)
    doc.hashed_reflection_names = rfl2 is not None
    doc.reflection_data = data[reflection.begin:reflection.end]
    doc.payload = data[doc.data_start:doc.data_end]
    _map_instances(doc, ids)
    values = _ValueReader(data, doc)
    values.read_instances()
    doc.array_hash_rule = values.rule[0] > 0 and values.rule[1] == 0
    doc.boxed_values.sort(key=lambda b: b.offset)
    for index, value in enumerate(doc.boxed_values):
        end = doc.boxed_values[index + 1].offset if index + 1 < len(doc.boxed_values) else doc.strings_offset
        if end < value.offset:
            raise ValueError("EBXX boxed values are not ordered")
        _validate_offset(doc, value.offset, end - value.offset, "EBXX boxed value bytes")
        value.raw_bytes = data[doc.data_start + value.offset:doc.data_start + end]
    return doc


def read_file_guid(data: bytes) -> Guid:
    """The document's own guid (what another document's import names it by), without reading the rest."""
    reader = _Reader(bytes(data))
    reader.seek(_chunk(_read_chunks(reader), _EFIX, "EFIX", True).begin)
    return reader.guid()


# ---- Writing ----------------------------------------------------------------------------------

_INT_RANGES = {
    FieldType.int8: (-0x80, 0x7F), FieldType.uint8: (0, 0xFF), FieldType.int16: (-0x8000, 0x7FFF),
    FieldType.uint16: (0, 0xFFFF), FieldType.int32: (-0x80000000, 0x7FFFFFFF),
    FieldType.enumeration: (-0x80000000, 0x7FFFFFFF), FieldType.uint32: (0, _U32),
    FieldType.int64: (-(1 << 63), (1 << 63) - 1), FieldType.uint64: (0, (1 << 64) - 1),
}
_FLOAT_MAX = {FieldType.float32: 3.4028234663852886e38, FieldType.float64: 1.7976931348623157e308}


def _number(value: Any, kind: int) -> Any:
    """The C++ number<T>: ints are range checked, floats must be finite, bools convert, the rest is 0."""
    if isinstance(value, bool):
        return float(value) if kind in _FLOAT_MAX else int(value)
    if isinstance(value, int):
        if not -(1 << 63) <= value < 1 << 64:
            raise OverflowError("EBX integer field exceeds its native width")
        if kind == FieldType.boolean:
            return int(value != 0)
        if kind in _FLOAT_MAX:
            return float(value)
        low, high = _INT_RANGES[kind]
        if not low <= value <= high:
            raise OverflowError("EBX integer field exceeds its native width")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise OverflowError("EBX floating-point field must be finite")
        if kind in _FLOAT_MAX:
            if abs(value) > _FLOAT_MAX[kind]:
                raise OverflowError("EBX floating-point field exceeds its native width")
            return value
        if kind == FieldType.boolean:
            return int(value != 0)
        # The C++ cast is undefined out of range; this wraps.
        low, high = _INT_RANGES[kind]
        span = high - low + 1
        return (int(value) - low) % span + low
    return 0.0 if kind in _FLOAT_MAX else 0


def _scalar_size(kind: int) -> int:
    if kind in (FieldType.boolean, FieldType.int8, FieldType.uint8):
        return 1
    if kind in (FieldType.int16, FieldType.uint16):
        return 2
    if kind in (FieldType.int32, FieldType.uint32, FieldType.float32, FieldType.enumeration):
        return 4
    if kind in (FieldType.guid, FieldType.boxedValueRef):
        return 16
    if kind == FieldType.sha1:
        return 20
    if kind == FieldType.string:
        return 32
    return 8


def _wrap_align(value: int, alignment: int) -> int:
    if alignment <= 1:
        return value
    return (value + alignment - 1) // alignment * alignment


def _checked(value: int, label: str) -> int:
    if value > _U32:
        raise OverflowError(label)
    return value


def _raw(text: Any) -> bytes:
    if isinstance(text, (bytes, bytearray)):
        return bytes(text)
    return text.encode("utf-8", "surrogateescape")


@dataclass
class _PendingArray:
    slot: int
    values: list
    descriptor: int
    hash: Optional[int] = None
    emitted: bool = False


class _Builder:
    def __init__(self, doc: Document):
        self.doc = doc
        self.payload = bytearray()
        self.instance_offsets: list[int] = []
        self.pointer_offsets: list[int] = []
        self.resource_offsets: list[int] = []
        self.import_offsets: list[int] = []
        self.type_info_offsets: list[int] = []
        self.arrays_pending: list[_PendingArray] = []
        self.strings_pending: list[tuple[int, bytes]] = []
        self.pointers_pending: list[tuple[int, PointerReference]] = []
        self.boxes_pending: list[tuple[int, BoxedReference]] = []
        self.arrays: list[ArrayRecord] = []
        self.boxed: list[BoxedValueRecord] = []
        self.arrays_offset = 0
        self.boxed_offset = 0
        self.empty_array_target = 0
        self.carried: dict[int, int] = {}                 # old array offset -> new
        self.groups: dict[int, int] | None = None
        self.boxed_targets: list[tuple[int, int]] = []    # (new slot, old target in the boxed section)

    def run(self) -> bytes:
        self.validate()
        self.write_instances()
        self.write_arrays()
        self.write_boxed_values()
        string_offset = _checked(len(self.payload), "EBX string offset")
        self.write_strings()
        self.resolve_pointers()
        return self.write_riff(bytes(self.payload), self.write_fixup(string_offset), self.write_extra())

    def validate(self) -> None:
        doc = self.doc
        if not doc.reflection_data:
            raise ValueError("EBX has no donor reflection table")
        if len(doc.fixup_type_signatures) > len(doc.fixup_type_guids):
            raise ValueError("EBX fixup type table is invalid")
        internal_seen = False
        for instance in doc.instances:
            internal_seen |= not instance.exported
            if internal_seen and instance.exported:
                raise ValueError("exported EBX instances must precede internal instances")
            if not 0 <= instance.descriptor < len(doc.types):
                raise ValueError("EBX instance has no valid type descriptor")
            if instance.fixup_type >= len(doc.fixup_type_guids):
                raise ValueError("EBX instance has no valid fixup type")

    def grow(self, end: int) -> None:
        if end > len(self.payload):
            self.payload.extend(bytes(end - len(self.payload)))

    def put(self, offset: int, raw: bytes) -> None:
        self.grow(offset + len(raw))
        self.payload[offset:offset + len(raw)] = raw

    def put_number(self, offset: int, fmt: str, value) -> None:
        self.put(offset, struct.pack(fmt, value))

    def write_instances(self) -> None:
        cursor = 0
        for instance in self.doc.instances:
            t = self.doc.types[instance.descriptor]
            start = _wrap_align(cursor + (16 if instance.exported else 0), max(t.alignment, 1))
            if instance.exported:
                self.put(start - 16, instance.instance_guid.bytes)
            self.grow(start + t.size)
            if instance.raw_image:
                self.put(start, instance.raw_image[:t.size])
            self.put_number(start, "<H", instance.fixup_type)
            self.write_type(instance.descriptor, start, instance.object)
            self.instance_offsets.append(_checked(start, "EBX instance offset"))
            cursor = start + t.size

    def write_type(self, type_index: int, start: int, obj: Optional[Object]) -> None:
        doc = self.doc
        t = doc.types[type_index]
        self.grow(start + t.size)
        values = {f.descriptor: f.value for f in reversed(obj.fields)} if obj is not None else {}
        hashes = {f.descriptor: f.array_hash for f in reversed(obj.fields)} if obj is not None else {}
        for ordinal in range(t.field_count):
            index = t.field_index + ordinal
            if index >= len(doc.fields):
                raise ValueError("EBX field range is invalid")
            f = doc.fields[index]
            if f.type == FieldType.inherited:
                if f.class_ref >= len(doc.types):
                    raise ValueError("EBX base type is invalid")
                self.write_type(f.class_ref, start, obj)
                continue
            position = start + f.data_offset
            has = index in values
            value = values.get(index)
            if f.category == FieldCategory.array:
                self.grow(position + 8)
                slot = _checked(position, "EBX array slot")
                self.pointer_offsets.append(slot)
                self.arrays_pending.append(_PendingArray(slot, value if has and isinstance(value, list) else [],
                                                         index, hashes.get(index)))
            elif f.type == FieldType.structure:
                if f.class_ref >= len(doc.types):
                    raise ValueError("EBX struct type is invalid")
                nested = _wrap_align(position, max(doc.types[f.class_ref].alignment, 1))
                self.write_type(f.class_ref, nested, value if isinstance(value, Object) else None)
            else:
                self.write_scalar(f, position, value)

    def write_scalar(self, f: FieldDescriptor, position: int, value: Any) -> None:
        kind = f.type
        fmt = _SCALARS.get(kind)
        if fmt:
            self.put_number(position, "<B" if kind == FieldType.boolean else fmt, _number(value, kind))
        elif kind == FieldType.guid:
            self.put(position, value.bytes if isinstance(value, Guid) else bytes(16))
        elif kind == FieldType.sha1:
            self.put(position, value.bytes if isinstance(value, Sha1) else bytes(20))
        elif kind == FieldType.string:
            raw = _raw(value) if isinstance(value, (str, bytes, bytearray)) else b""
            if len(raw) > 31:
                raise OverflowError("EBX fixed string exceeds 31 bytes")
            self.put(position, raw.ljust(32, b"\0"))
        elif kind in (FieldType.cString, FieldType.fileRef):
            self.put(position, bytes(8))
            slot = _checked(position, "EBX string slot")
            self.pointer_offsets.append(slot)
            raw = _raw(value) if isinstance(value, (str, bytes, bytearray)) else b""
            if len(raw) > 1024 * 1024:
                raise OverflowError("EBX string exceeds the authoring limit")
            self.strings_pending.append((slot, raw))
        elif kind == FieldType.resourceRef:
            self.put_number(position, "<Q", value.id if isinstance(value, ResourceReference) else 0)
            self.resource_offsets.append(_checked(position, "EBX resource slot"))
        elif kind == FieldType.pointer:
            self.put(position, bytes(8))
            pointer = value if isinstance(value, PointerReference) else PointerReference()
            self.pointers_pending.append((_checked(position, "EBX pointer slot"), pointer))
        elif kind in (FieldType.typeRef, FieldType.delegate):
            encoded = value.encoded if isinstance(value, TypeReference) else 0
            self.put(position, struct.pack("<II", encoded, 0))
            if encoded != 0 and kind == FieldType.typeRef:
                self.type_info_offsets.append(_checked(position, "EBX type-info slot"))
        elif kind == FieldType.boxedValueRef:
            self.grow(position + 16)
            if not isinstance(value, BoxedReference) or value.encoded_type == 0:
                self.put(position, bytes(16))
                return
            self.put(position, struct.pack("<II", value.encoded_type, 0))
            self.type_info_offsets.append(_checked(position, "EBX boxed type slot"))
            self.pointer_offsets.append(_checked(position + 8, "EBX boxed pointer slot"))
            self.boxes_pending.append((_checked(position, "EBX boxed slot"), value))
        else:
            self.grow(position + _scalar_size(kind))

    def element_stride(self, f: FieldDescriptor) -> int:
        if f.type == FieldType.structure:
            if f.class_ref >= len(self.doc.types):
                raise ValueError("EBX array struct type is invalid")
            return max(self.doc.types[f.class_ref].size, 1)
        return _scalar_size(f.type)

    def element_alignment(self, f: FieldDescriptor) -> int:
        if f.type == FieldType.structure:
            return max(self.doc.types[f.class_ref].alignment, 1)
        return max(min(self.element_stride(f), 8), 1)

    def fixup_slot(self, descriptor: int) -> int:
        doc = self.doc
        if descriptor >= len(doc.types):
            return 0xFFFF
        t = doc.types[descriptor]
        for index in range(min(len(doc.fixup_type_guids), len(doc.fixup_type_signatures))):
            if doc.fixup_type_guids[index] == t.guid and doc.fixup_type_signatures[index] == t.signature:
                return index & 0xFFFF
        return 0xFFFF

    def array_hash(self, f: FieldDescriptor) -> int:
        raw_type = (f.encoded_type << 1) & 0xFFFF
        class_ref = self.fixup_slot(f.class_ref) if f.type == FieldType.structure else 0xFFFF
        for record in self.doc.arrays:
            if record.encoded_type == raw_type and record.class_ref == class_ref:
                return record.hash
        if self.doc.arrays:
            return self.doc.arrays[0].hash
        return f.name_hash

    def write_arrays(self) -> None:
        self.arrays_offset = _checked(_align(len(self.payload), 16), "EBX array section")
        self.grow(self.arrays_offset + 32)
        self.empty_array_target = self.arrays_offset + 16
        index = 0
        while index < len(self.arrays_pending):
            if not self.arrays_pending[index].emitted:
                self.emit_array(index)
            index += 1
        self.carry_boxed_arrays()

    # What a boxed value points at is not in the document's values: its bytes are carried as they
    # were, so the arrays and strings its pointers reach are carried with them and the pointers
    # moved to the new places.

    def old_target(self, slot: int) -> tuple[str, int]:
        return pointer_target(self.doc, slot, self.old_arrays, self.old_instances)

    def old_slots(self, start: int, length: int, offsets: list[int]) -> list[int]:
        return [o for o in offsets if start <= o < start + length]

    def carry_boxed_arrays(self) -> None:
        doc = self.doc
        if not doc.payload:
            return
        self.old_arrays = {record.offset: record for record in doc.arrays}
        self.old_instances = {instance.data_offset: index for index, instance in enumerate(doc.instances)}
        for source in sorted(doc.boxed_values, key=lambda b: b.offset):
            for slot in self.old_slots(source.offset, len(source.raw_bytes), sorted(doc.pointer_offsets)):
                kind, key = self.old_target(slot)
                if kind == "array":
                    self.carry_array(self.old_arrays[key])

    def carry_array(self, record: ArrayRecord) -> None:
        if record.offset in self.carried:
            return
        stride, alignment = array_element(self.doc, record)
        length = stride * record.count
        if record.offset + length > len(self.doc.payload):
            raise ValueError("EBX carried array exceeds EBXD")
        data = _wrap_align(len(self.payload) + 4, alignment)
        self.put_number(data - 4, "<I", record.count)
        self.put(data, self.doc.payload[record.offset:record.offset + length])
        self.carried[record.offset] = _checked(data, "EBX array data")
        self.arrays.append(ArrayRecord(self.carried[record.offset], record.count, record.hash, record.encoded_type,
                                       record.class_ref))
        self.relocate(record.offset, length, data)

    def relocate(self, old_start: int, length: int, new_start: int) -> None:
        """Re-lists the fixups of carried bytes (old_start..+length, now at new_start) and points
        their pointers at where the targets now are."""
        doc = self.doc
        for old_offsets, new_offsets in ((doc.resource_ref_offsets, self.resource_offsets),
                                         (doc.import_offsets, self.import_offsets),
                                         (doc.type_info_offsets, self.type_info_offsets)):
            for old in self.old_slots(old_start, length, old_offsets):
                new_offsets.append(_checked(new_start + old - old_start, "EBX carried relocation"))
        for old in self.old_slots(old_start, length, sorted(set(doc.pointer_offsets))):
            slot = _checked(new_start + old - old_start, "EBX carried relocation")
            self.pointer_offsets.append(slot)
            if not doc.payload:
                continue
            kind, key = self.old_target(old)
            if kind == "array":
                self.carry_array(self.old_arrays[key])
                self.put_number(slot, "<I", (self.carried[key] - slot) & _U32)
            elif kind == "string":
                end = doc.payload.find(b"\0", key)
                self.strings_pending.append((slot, doc.payload[key:end if end >= 0 else len(doc.payload)]))
            elif kind == "instance":
                self.put_number(slot, "<I", (self.instance_offsets[key] - slot) & _U32)
            elif kind == "empty":
                self.put_number(slot, "<I", (self.empty_array_target - slot) & _U32)
            elif kind == "boxed":
                self.boxed_targets.append((slot, key))

    def emit_array(self, index: int) -> None:
        pending = self.arrays_pending[index]
        pending.emitted = True
        f = self.doc.fields[pending.descriptor]
        values = pending.values
        if not values:
            self.put_number(pending.slot, "<I", (self.empty_array_target - pending.slot) & _U32)
            return
        data = _wrap_align(len(self.payload) + 4, self.element_alignment(f))
        stride = self.element_stride(f)
        count = _checked(len(values), "EBX array count")
        self.put_number(data - 4, "<I", count)
        self.grow(data + stride * len(values))
        self.put_number(pending.slot, "<I", (data - pending.slot) & _U32)
        first_child = len(self.arrays_pending)
        for element, value in enumerate(values):
            start = data + element * stride
            if f.type == FieldType.structure:
                self.write_type(f.class_ref, start, value if isinstance(value, Object) else None)
            else:
                self.write_scalar(f, start, value)
        class_ref = self.fixup_slot(f.class_ref) if f.type == FieldType.structure else 0xFFFF
        # An array read from a record keeps that record's hash; a new one borrows (array_hash).
        own = pending.hash
        if own is None and self.doc.array_hash_rule:
            if self.groups is None:
                self.groups = group_hashes(self.doc)
            own = self.groups.get(f.class_ref)
        if own is None:
            own = self.array_hash(f)
        self.arrays.append(ArrayRecord(_checked(data, "EBX array data"), count, own,
                                       (f.encoded_type << 1) & 0xFFFF, class_ref))
        child = first_child
        while child < len(self.arrays_pending):
            if not self.arrays_pending[child].emitted:
                self.emit_array(child)
            child += 1

    def write_boxed_values(self) -> None:
        doc = self.doc
        self.boxed_offset = _checked(_align(len(self.payload), 16), "EBX boxed section")
        self.grow(self.boxed_offset)
        moved = {}
        for source in sorted(doc.boxed_values, key=lambda b: b.offset):
            if source.offset < doc.boxed_values_offset:
                raise ValueError("EBX boxed record precedes its section")
            offset = self.boxed_offset + source.offset - doc.boxed_values_offset
            self.put(offset, source.raw_bytes)
            self.relocate(source.offset, len(source.raw_bytes), offset)
            moved[source.offset] = _checked(offset, "EBX boxed value")
            self.boxed.append(BoxedValueRecord(moved[source.offset], source.count, source.hash,
                                               source.encoded_type, source.class_ref, source.raw_bytes))
        for slot, target in self.boxed_targets:
            self.put_number(slot, "<I", (self.boxed_offset + target - doc.boxed_values_offset - slot) & _U32)
        for slot, value in self.boxes_pending:
            self.put(slot, struct.pack("<II", value.encoded_type, 0))
            key = value.data_offset & _U32
            relative = moved[key] - ((slot + 8) & _U32) if key in moved else value.relative_offset
            self.put_number(slot + 8, "<q", relative)

    def write_strings(self) -> None:
        offsets: dict[bytes, int] = {}
        for slot, raw in self.strings_pending:
            if raw not in offsets:
                offsets[raw] = _checked(len(self.payload), "EBX string table")
                self.put(len(self.payload), raw)
                self.grow(len(self.payload) + 1)
            self.put(slot, struct.pack("<II", (offsets[raw] - slot) & _U32, 0))

    def resolve_pointers(self) -> None:
        for slot, pointer in self.pointers_pending:
            if pointer.kind == PointerKind.internal:
                if not 0 <= pointer.index < len(self.instance_offsets):
                    raise ValueError("EBX internal pointer index is invalid")
                self.put_number(slot, "<I", (self.instance_offsets[pointer.index] - slot) & _U32)
                self.pointer_offsets.append(slot)
            elif pointer.kind == PointerKind.external:
                if not 0 <= pointer.index < len(self.doc.imports):
                    raise ValueError("EBX import pointer index is invalid")
                self.put_number(slot, "<I", ((pointer.index << 1) | 1) & _U32)
                self.import_offsets.append(slot)
            self.put_number(slot + 4, "<I", 0)

    @staticmethod
    def table(values: list[int]) -> bytes:
        values = sorted(set(values))
        return struct.pack(f"<I{len(values)}I", _checked(len(values), "EBX fixup table count"), *values)

    def write_fixup(self, string_offset: int) -> bytes:
        doc = self.doc
        out = bytearray(doc.file_guid.bytes)
        out += struct.pack("<I", len(doc.fixup_type_guids))
        for guid in doc.fixup_type_guids:
            out += guid.bytes
        out += struct.pack(f"<I{len(doc.fixup_type_signatures)}I", len(doc.fixup_type_signatures),
                           *doc.fixup_type_signatures)
        out += struct.pack("<I", sum(1 for i in doc.instances if i.exported))
        out += self.table(self.instance_offsets)
        out += self.table(self.pointer_offsets)
        out += self.table(self.resource_offsets)
        out += struct.pack("<I", len(doc.imports))
        for value in doc.imports:
            out += value.file_guid.bytes + value.class_guid.bytes
        out += self.table(self.import_offsets)
        out += self.table(self.type_info_offsets)
        out += struct.pack("<III", self.arrays_offset, self.boxed_offset, string_offset)
        old_slots = sorted(doc.boxed_value_slots)
        new_slots = sorted(slot for slot, _ in self.boxes_pending)
        trailing = []
        positions = {}
        for index, slot in enumerate(old_slots):
            positions.setdefault(slot, index)  # lower_bound: the first equal slot
        for old in doc.boxed_reference_offsets:
            index = positions.get(old)
            if index is not None and index < len(new_slots):
                trailing.append(new_slots[index])
        out += self.table(trailing)
        return bytes(out)

    def write_extra(self) -> bytes:
        out = bytearray(struct.pack("<II", len(self.arrays), len(self.boxed)))
        for value in sorted(self.arrays, key=lambda a: a.offset) + sorted(self.boxed, key=lambda b: b.offset):
            out += struct.pack("<IIIHH", value.offset, value.count, value.hash, value.encoded_type, value.class_ref)
        return bytes(out)

    def write_riff(self, payload: bytes, fixup: bytes, extra: bytes) -> bytes:
        doc = self.doc
        out = bytearray(b"RIFF\0\0\0\0" + (b"EBXS" if doc.serialized_layout else b"EBX\0"))

        def chunk(name: bytes, body: bytes) -> None:
            out.extend(name + struct.pack("<I", _checked(len(body), "EBX RIFF chunk size")) + body)
            if len(body) & 1:
                out.append(0)

        chunk(b"EBXD", bytes(12) + payload)
        chunk(b"EFIX", fixup)
        chunk(b"EBXX", extra)
        chunk(b"RFL2" if doc.hashed_reflection_names else b"REFL", doc.reflection_data)
        struct.pack_into("<I", out, 4, _checked(len(out) - 8, "EBX RIFF size"))
        return bytes(out)


def ensure_fixup_type(doc: Document, descriptor: int) -> int:
    """The fixup slot naming a type, added when the document has none yet."""
    if not 0 <= descriptor < len(doc.types):
        raise IndexError("EBX type descriptor is invalid")
    t = doc.types[descriptor]
    count = min(len(doc.fixup_type_guids), len(doc.fixup_type_signatures))
    for index in range(count):
        if doc.fixup_type_guids[index] == t.guid and doc.fixup_type_signatures[index] == t.signature:
            if index > 0xFFFF:
                raise OverflowError("EBX type slot overflow")
            return index
    if count >= 0xFFFF:
        raise OverflowError("EBX type slot overflow")
    doc.fixup_type_guids.insert(count, t.guid)
    doc.fixup_type_signatures.append(t.signature)
    return count


def group_hashes(doc: Document) -> dict[int, int]:
    """Reflection group hash by group index (the first of equal indices)."""
    out: dict[int, int] = {}
    for index, value, _ in doc.reflection_groups:
        out.setdefault(index, value)
    return out


def pointer_target(doc: Document, slot: int, arrays: dict[int, ArrayRecord] | None = None,
                   instances: dict[int, int] | None = None) -> tuple[str, int]:
    """What the pointer at a slot of the read EBXD (`doc.payload`) reaches: ("array", offset),
    ("string", offset), ("instance", index), ("boxed", offset), ("empty", offset) for an empty
    array's target, or ("unknown", offset)."""
    if arrays is None:
        arrays = {record.offset: record for record in doc.arrays}
    if instances is None:
        instances = {instance.data_offset: index for index, instance in enumerate(doc.instances)}
    target = slot + struct.unpack_from("<i", doc.payload, slot)[0]
    if target in arrays:
        return "array", target
    if doc.strings_offset <= target < len(doc.payload):
        return "string", target
    if target in instances:
        return "instance", instances[target]
    if doc.boxed_values_offset <= target < doc.strings_offset:
        return "boxed", target
    if 4 <= target <= len(doc.payload) and doc.payload[target - 4:target] == bytes(4):
        return "empty", target
    return "unknown", target


def array_element(doc: Document, record: ArrayRecord) -> tuple[int, int]:
    """(stride, alignment) of an array the document read, from its record."""
    kind = _type_of(record.encoded_type >> 1)
    if kind == FieldType.structure:
        slot = record.class_ref
        if slot < len(doc.fixup_type_guids) and slot < len(doc.fixup_type_signatures):
            key = (doc.fixup_type_guids[slot], doc.fixup_type_signatures[slot])
            for t in doc.types:
                if (t.guid, t.signature) == key:
                    return max(t.size, 1), max(t.alignment, 1)
        raise ValueError("EBX array record has an unknown struct type")
    stride = _scalar_size(kind)
    return stride, max(min(stride, 8), 1)


def write_document(doc: Document) -> bytes:
    return _Builder(doc).run()
