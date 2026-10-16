"""Why an EBX document does or does not rewrite to its own bytes (`ebx-roundtrip`, and the checks
`new-level --dry-run` prints): sizes, the first differing byte and the region it is in, each
region compared, the fixup and array tables compared, and whether the rewrite reads back to the
same values (a semantic dump of both).
"""
from __future__ import annotations

from . import ebx
from .ebx import Document, FieldType, read_document, write_document
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


def semantic(doc: Document) -> list[str]:
    lines = list(describe(doc, limit=1 << 30))
    lines += [f"import {i.file_guid.string()} {i.class_guid.string()}" for i in doc.imports]
    lines += [f"boxed {b.count} {b.hash:#x} {b.encoded_type:#x} {b.class_ref} {b.raw_bytes.hex()}" for b in doc.boxed_values]
    return lines


def roundtrip(raw: bytes, brief: bool = False) -> list[str]:
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
    return lines
