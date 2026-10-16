"""Why an EBX document does or does not rewrite to its own bytes (`ebx-roundtrip`, and the checks
`new-level --dry-run` prints): sizes, the first differing byte and the region it is in, each
region compared, the fixup and array tables compared, and whether the rewrite reads back to the
same values (a semantic dump of both).
"""
from __future__ import annotations

from . import ebx
import struct
from collections import Counter

from .ebx import ArrayRecord, Document, FieldType, array_element, pointer_target, read_document, write_document
from .ebx import Object
from .ebxedit import describe

_CHUNK_ROLE = {ebx._EFIX: "fixup tables", ebx._EBXX: "array and boxed records", ebx._REFL: "reflection",
               ebx._RFL2: "reflection"}


def regions(raw: bytes, doc: Document) -> list[tuple[str, int, int]]:
    """(name, start, end) of each part of the file, in file offsets."""
    reader = ebx._Reader(raw)
    out = [("RIFF header", 0, 12)]
    for c in ebx._read_chunks(reader):
        name = c.id.to_bytes(4, "big").decode("latin-1").strip("\0")
        out.append((f"{name} chunk header", c.begin - 8, c.begin))
        if c.id == ebx._EBXD:
            start = doc.data_start
            marks = [start, start + doc.arrays_offset, start + doc.boxed_values_offset, start + doc.strings_offset, c.end]
            out.append(("EBXD padding", c.begin, start))
            for label, a, b in zip(("instance data", "arrays", "boxed values", "strings"), marks, marks[1:]):
                out.append((label, a, max(a, b)))
        else:
            out.append((f"{name} ({_CHUNK_ROLE.get(c.id, 'other')})", c.begin, c.end))
        if (c.end - c.begin) & 1:
            out.append((f"{name} pad", c.end, c.end + 1))
    return out


def _region_at(parts, offset: int) -> str:
    return next((name for name, a, b in parts if a <= offset < b), "past the end")


def _first_difference(a: bytes, b: bytes) -> int | None:
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return i
    return None if len(a) == len(b) else min(len(a), len(b))


def _fields(doc: Document, descriptor: int) -> list[tuple[int, str]]:
    out = []
    t = doc.types[descriptor]
    for i in range(t.field_index, t.field_index + t.field_count):
        f = doc.fields[i]
        if f.type == FieldType.inherited:
            out += _fields(doc, f.class_ref)
        else:
            out.append((f.data_offset, f.name))
    return sorted(out)


def _where_in_instances(doc: Document, relative: int) -> str:
    for index, instance in enumerate(doc.instances):
        size = doc.types[instance.descriptor].size if 0 <= instance.descriptor < len(doc.types) else 0
        start = instance.data_offset - (16 if instance.exported else 0)
        if start <= relative < instance.data_offset + size:
            kind = doc.types[instance.descriptor].name if instance.descriptor >= 0 else "?"
            if relative < instance.data_offset:
                return f"instance #{index} {kind}, its GUID"
            inside = relative - instance.data_offset
            field = next((name for offset, name in reversed(_fields(doc, instance.descriptor)) if offset <= inside),
                         "header")
            return f"instance #{index} {kind} at +{inside} (field {field})"
    return "between instances (padding)"


def _compare(label: str, a, b) -> str:
    if a == b:
        return f"{label}: same ({len(a)})"
    first = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    shown = lambda v: v[first] if first < len(v) else "-"  # noqa: E731
    same_set = sorted(map(repr, a)) == sorted(map(repr, b))
    return (f"{label}: {len(a)} vs {len(b)}, first differs at [{first}] ({shown(a)!r} vs {shown(b)!r})"
            + (", same entries in another order" if same_set else ""))


class _Maps:
    def __init__(self, doc: Document):
        self.arrays = {r.offset: r for r in doc.arrays}
        self.instances = {i.data_offset: n for n, i in enumerate(doc.instances)}
        self.pointers = sorted(set(doc.pointer_offsets))


def _slots(maps: _Maps, start: int, length: int) -> list[int]:
    return [o for o in maps.pointers if start <= o < start + length]


def _array_type(doc: Document, record: ArrayRecord) -> str:
    kind = (record.encoded_type >> 5) & 0x1F
    if kind == FieldType.structure:
        slot = record.class_ref
        key = (doc.fixup_type_guids[slot], doc.fixup_type_signatures[slot]) if slot < len(doc.fixup_type_signatures) else None
        return next((t.name for t in doc.types if (t.guid, t.signature) == key), "struct ?")
    return FieldType(kind).name if kind <= 0x1C else str(kind)


def _array_length(doc: Document, record: ArrayRecord) -> int:
    try:
        return array_element(doc, record)[0] * record.count
    except ValueError:   # a struct type the fixup table does not name: up to the next array
        later = [r.offset - 4 for r in doc.arrays if r.offset > record.offset]
        return max(0, min(later + [doc.boxed_values_offset]) - record.offset)


def _masked(doc: Document, maps: _Maps, start: int, length: int) -> bytes:
    """The bytes with each pointer's displacement blanked (it changes whenever anything moves)."""
    out = bytearray(doc.payload[start:start + length])
    for o in _slots(maps, start, length):
        out[o - start:o - start + 4] = b"\xff" * 4
    return bytes(out)


def _block(doc: Document, maps: _Maps, start: int, length: int, depth: int, seen: set) -> list[str]:
    """What a span of carried bytes holds and reaches, by value."""
    pad = "  " * depth
    lines = [f"{pad}bytes {_masked(doc, maps, start, length).hex()}"]
    for o in _slots(maps, start, length):
        kind, key = pointer_target(doc, o, maps.arrays, maps.instances)
        label = f"{pad}+{o - start}:"
        if kind == "string":
            end = doc.payload.find(b"\0", key)
            lines.append(f"{label} string {doc.payload[key:end]!r}")
        elif kind == "array":
            record = maps.arrays[key]
            lines.append(f"{label} array {record.count} x {_array_type(doc, record)}")
            if key not in seen and depth < 16:
                lines += _block(doc, maps, key, _array_length(doc, record), depth + 1, seen | {key})
        elif kind == "instance":
            lines.append(f"{label} -> #{key}")
        elif kind == "boxed":
            index = next((i for i, b in enumerate(sorted(doc.boxed_values, key=lambda b: b.offset))
                          if b.offset <= key < b.offset + len(b.raw_bytes)), None)
            lines.append(f"{label} boxed #{index}")
        else:
            lines.append(f"{label} {kind}")
    return lines


def semantic(doc: Document) -> list[str]:
    lines = list(describe(doc, limit=1 << 30))
    lines += [f"import {i.file_guid.string()} {i.class_guid.string()}" for i in doc.imports]
    maps = _Maps(doc)
    for index, b in enumerate(sorted(doc.boxed_values, key=lambda b: b.offset)):
        lines.append(f"boxed #{index} {b.count} {b.hash:#x} {b.encoded_type:#x} {b.class_ref}")
        if doc.payload:
            lines += _block(doc, maps, b.offset, len(b.raw_bytes), 1, set())
        else:
            lines.append(f"  bytes {b.raw_bytes.hex()}")
    return lines


def semantically_equal(raw: bytes) -> bool:
    doc = read_document(raw)
    return semantic(doc) == semantic(read_document(write_document(doc)))


def _where(doc: Document, maps: _Maps, slot: int) -> str:
    if slot < doc.arrays_offset:
        return _where_in_instances(doc, slot)
    if slot < doc.boxed_values_offset:
        for record in doc.arrays:
            if record.offset <= slot < record.offset + _array_length(doc, record):
                return f"array @{record.offset} ({record.count} x {_array_type(doc, record)}) at +{slot - record.offset}"
        return f"arrays section at {slot}"
    if slot < doc.strings_offset:
        for index, b in enumerate(sorted(doc.boxed_values, key=lambda b: b.offset)):
            if b.offset <= slot < b.offset + len(b.raw_bytes):
                return f"boxed #{index} (type {b.encoded_type:#x}) at +{slot - b.offset}"
    return f"offset {slot}"


def _referrers(doc: Document, maps: _Maps) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for slot in maps.pointers:
        target = slot + struct.unpack_from("<i", doc.payload, slot)[0]
        out.setdefault(target, []).append(_where(doc, maps, slot))
    return out


def _detail(doc: Document, back: Document, limit: int) -> list[str]:
    lines = ["detail:"]
    sides = []
    for d in (doc, back):
        maps = _Maps(d)
        refs = _referrers(d, maps)
        arrays = {}
        for r in d.arrays:
            key = (_array_type(d, r), r.count, _masked(d, maps, r.offset, _array_length(d, r)))
            arrays.setdefault(key, []).append(r)
        strings = {}
        at = d.strings_offset
        while at < len(d.payload):
            end = d.payload.find(b"\0", at)
            end = len(d.payload) if end < 0 else end
            strings.setdefault(d.payload[at:end], []).append(at)
            at = end + 1
        sides.append((d, refs, arrays, strings))
    for (d, refs, arrays, strings), (_, _, other_arrays, other_strings), label in (
            (sides[0], sides[1], "only in the original"), (sides[1], sides[0], "only in the rewrite")):
        missing = Counter({k: len(v) for k, v in arrays.items()}) - Counter({k: len(v) for k, v in other_arrays.items()})
        lines.append(f"  arrays {label}: {sum(missing.values())}")
        for key in list(missing)[:limit]:
            for r in arrays[key][:missing[key]]:
                lines.append(f"    @{r.offset}: {r.count} x {key[0]}, {len(key[2])} bytes, hash {r.hash}, "
                             f"reached from {refs.get(r.offset, ['nothing'])}")
        gone = [t for t in strings if t not in other_strings]
        lines.append(f"  strings {label}: {len(gone)}")
        for text in gone:
            for offset in strings[text]:
                lines.append(f"    {text!r} @{offset}, reached from {refs.get(offset, ['nothing'])}")
    changed = [(a, b) for a, b in zip(doc.arrays, back.arrays)
               if (a.count, a.hash, a.encoded_type, a.class_ref) != (b.count, b.hash, b.encoded_type, b.class_ref)]
    lines.append(f"  array records that differ at the same index: {len(changed)}")
    refs = (sides[0][1], sides[1][1])
    for a, b in changed[:limit]:
        lines.append(f"    original @{a.offset} {a.count} x {_array_type(doc, a)} hash {a.hash} "
                     f"({_array_length(doc, a)} bytes) from {refs[0].get(a.offset, ['nothing'])}")
        lines.append(f"    rewrite  @{b.offset} {b.count} x {_array_type(back, b)} hash {b.hash} "
                     f"({_array_length(back, b)} bytes) from {refs[1].get(b.offset, ['nothing'])}")
    return lines


def roundtrip(raw: bytes, brief: bool = False, detail: int = 0) -> list[str]:
    """The diagnosis; `detail` > 0 adds, for that many differing arrays and every lost or added
    string, where each is reached from."""
    doc = read_document(raw)
    out = write_document(doc)
    if out == raw:
        return [f"rewrite identical ({len(raw)} bytes)"]
    back = read_document(out)
    old_parts, new_parts = regions(raw, doc), regions(out, back)
    first = _first_difference(raw, out)
    a, b = semantic(doc), semantic(back)
    by_name = {name: (s, e) for name, s, e in new_parts}
    differ = [name for name, s, e in old_parts if raw[s:e] != out[slice(*by_name.get(name, (0, 0)))]]
    order = ([n for n, _, _ in old_parts] != [n for n, _, _ in new_parts])
    lines = [f"rewrite DIFFERS: {len(raw)} -> {len(out)} bytes, first at {first} ({_region_at(old_parts, first)}); "
             f"regions differing: {', '.join(differ) or 'none'}; read back "
             + ("SEMANTICALLY EQUAL" if a == b else "NOT semantically equal")]
    if brief:
        return lines
    lines.append("regions (original -> rewrite)" + (", in a DIFFERENT ORDER:" if order else ":"))
    for name, s, e in old_parts:
        s2, e2 = by_name.get(name, (0, 0))
        if raw[s:e] == out[s2:e2]:
            state = "same"
        else:
            d = _first_difference(raw[s:e], out[s2:e2])
            state = f"differs from +{d}"
            if name == "instance data" and d is not None:
                state += f": {_where_in_instances(doc, d)}"
        lines.append(f"  {name}: {s}..{e} ({e - s}) -> {s2}..{e2} ({e2 - s2}) {state}")
    lines.append("  " + _compare("instance offsets", [i.data_offset for i in doc.instances],
                                 [i.data_offset for i in back.instances]))
    for label in ("pointer_offsets", "resource_ref_offsets", "import_offsets", "type_info_offsets",
                  "boxed_reference_offsets"):
        lines.append("  " + _compare(label, getattr(doc, label), getattr(back, label)))
    lines.append("  " + _compare("section offsets", [doc.arrays_offset, doc.boxed_values_offset, doc.strings_offset],
                                 [back.arrays_offset, back.boxed_values_offset, back.strings_offset]))
    lines.append("  " + _compare("array records (offset, count, hash, type, class)",
                                 [(r.offset, r.count, r.hash, r.encoded_type, r.class_ref) for r in doc.arrays],
                                 [(r.offset, r.count, r.hash, r.encoded_type, r.class_ref) for r in back.arrays]))
    lines.append("  " + _compare("array records without offsets",
                                 [(r.count, r.hash, r.encoded_type, r.class_ref) for r in doc.arrays],
                                 [(r.count, r.hash, r.encoded_type, r.class_ref) for r in back.arrays]))

    def table(d: Document, data: bytes) -> list[bytes]:
        start = d.data_start + d.strings_offset
        return data[start:d.data_end].split(b"\0")
    lines.append("  " + _compare("string table", table(doc, raw), table(back, out)))
    if first is not None:
        lines.append(f"  bytes at {first}: original {raw[max(0, first - 16):first + 16].hex(' ')}")
        lines.append(f"  {' ' * len(str(first))}           rewrite  {out[max(0, first - 16):first + 16].hex(' ')}")
    if a != b:
        line = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        lines.append(f"  dump differs at line {line}: {a[line] if line < len(a) else '-'!r} vs "
                     f"{b[line] if line < len(b) else '-'!r}")
    if detail:
        lines += _detail(doc, back, detail)
    return lines


# ---- What an array record's hash is made from ------------------------------------------------------

def _djb2_xor(text: str) -> int:
    value = 5381
    for byte in text.encode("utf-8", "surrogateescape"):
        value = ((value * 33) & 0xFFFFFFFF) ^ byte
    return value


def _djb2_add(text: str) -> int:
    value = 5381
    for byte in text.encode("utf-8", "surrogateescape"):
        value = (value * 33 + byte) & 0xFFFFFFFF
    return value


def _fnv1(text: str) -> int:
    value = 0x811C9DC5
    for byte in text.encode("utf-8", "surrogateescape"):
        value = ((value * 0x01000193) & 0xFFFFFFFF) ^ byte
    return value


def _fnv1a(text: str) -> int:
    value = 0x811C9DC5
    for byte in text.encode("utf-8", "surrogateescape"):
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return value


def _array_fields(doc: Document):
    """(type declaring the field, field descriptor, element type name or '', record hash, type of
    the object holding it) for every array read with a record."""
    owner_of = {}
    for t in doc.types:
        for i in range(t.field_index, t.field_index + t.field_count):
            owner_of.setdefault(i, t)

    def walk(value):
        if isinstance(value, Object):
            for f in value.fields:
                if f.array_hash is not None:
                    d = doc.fields[f.descriptor]
                    element = doc.types[d.class_ref].name if d.type in (FieldType.structure, FieldType.pointer) \
                        and d.class_ref < len(doc.types) else ""
                    yield owner_of.get(f.descriptor), d, element, f.array_hash, doc.types[value.descriptor]
                yield from walk(f.value)
        elif isinstance(value, list):
            for v in value:
                yield from walk(v)
    for instance in doc.instances:
        yield from walk(instance.object)


def array_hash_report(doc: Document, samples: int = 20) -> list[str]:
    """Scores candidate rules for the array records' hashes against every array the document read
    with a record, and prints samples with what each is reached from."""
    from .toc import toc_hash
    rows = list(_array_fields(doc))
    type_hashes = {t.name_hash: t.name for t in doc.types}
    functions = {"djb2-xor": _djb2_xor, "djb2-add": _djb2_add, "fnv1": _fnv1, "fnv1a": _fnv1a,
                 "toc_hash": lambda s: toc_hash(s.encode("latin-1", "replace"))}

    def texts(owner, d, element, holder):
        o = owner.name if owner else ""
        kind = d.type.name if isinstance(d.type, FieldType) else str(d.type)
        base = {"field": d.name, "owner": o, "owner.field": f"{o}.{d.name}", "element": element,
                "element-Array": f"{element or kind}-Array", "elementArray": f"{element or kind}Array",
                "Array<element>": f"Array<{element or kind}>", "field type": kind, "element[]": f"{element or kind}[]",
                "holder": holder.name, "holder.field": f"{holder.name}.{d.name}"}
        out = {}
        for label, text in base.items():
            out[label] = text
            out[label + " (lower)"] = text.lower()
        return out

    scores: Counter = Counter()
    for owner, d, element, value, holder in rows:
        found = set()
        if value == d.name_hash:
            found.add("field name_hash (reflection)")
        if owner is not None and value == owner.name_hash:
            found.add("owner type name_hash")
        if d.class_ref < len(doc.types) and value == doc.types[d.class_ref].name_hash:
            found.add("class_ref type name_hash")
        if value in type_hashes:
            found.add("some reflection type's name_hash")
        if owner is not None and value == (owner.name_hash ^ d.name_hash):
            found.add("owner ^ field name_hash")
        if value == holder.name_hash or value == (holder.name_hash ^ d.name_hash):
            found.add("holder type name_hash (^ field)")
        for label, text in texts(owner, d, element, holder).items():
            for name, function in functions.items():
                if function(text) == value:
                    found.add(f"{name}({label})")
        scores.update(found)
    lines = [f"{len(rows)} arrays read with a record; {len(set(r[3] for r in rows))} distinct hashes"]
    if scores:
        lines += [f"  {count} of {len(rows)}: {rule}" for rule, count in scores.most_common(12)]
    else:
        lines.append("  no candidate rule matched any array")
    by_hash: dict[int, set] = {}
    for owner, d, element, value, holder in rows:
        by_hash.setdefault(value, set()).add(f"{holder.name}.{d.name}")
    shared = {h: n for h, n in by_hash.items() if len(n) > 1}
    lines.append(f"  hashes shared by more than one field: {len(shared)}")
    by_field: dict[int, set] = {}
    for _, d, _, value, _ in rows:
        by_field.setdefault(id(d), set()).add(value)
    lines.append(f"  fields whose arrays carry more than one hash: {sum(len(v) > 1 for v in by_field.values())}")
    for owner, d, element, value, holder in rows[:samples]:
        hit = type_hashes.get(value)
        lines.append(f"  {holder.name} / {owner.name if owner else '?'}.{d.name} ({d.type.name if isinstance(d.type, FieldType) else d.type}"
                     f"{' of ' + element if element else ''}): hash {value} ({value:#010x}), field name_hash "
                     f"{d.name_hash:#010x}" + (f", = name_hash of type {hit}" if hit else ""))
    return lines
