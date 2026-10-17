"""Reading a rigid MeshSet resource's header, LODs and sections (res type 0x49B156D4), as laid out
in FORMAT-NOTES 1. Read-only: it prints what a MeshSet writer will have to fill (`inspect-meshset`).
Pointers are u64 counted from byte 16 of the resource.
"""
from __future__ import annotations

import struct

from .bundle import Guid

RESOURCE_TYPE = 0x49B156D4
HEADER_SIZE, LOD_SIZE, SECTION_SIZE = 192, 176, 384


def _name(data: bytes, pointer: int) -> str:
    if not pointer or pointer + 16 >= len(data):
        return ""
    at = pointer + 16
    end = data.find(b"\0", at)
    return data[at:end if end >= 0 else len(data)].decode("latin-1")


def _floats(data: bytes, at: int, n: int) -> list[float]:
    return [round(v, 3) for v in struct.unpack_from(f"<{n}f", data, at)]


def describe_meshset(data: bytes) -> list[str]:
    header, lod_size, section_size = struct.unpack_from("<III", data, 0)
    out = [f"{len(data)} bytes; header {header}, LOD struct {lod_size}, section struct {section_size}"
           + ("" if (header, lod_size, section_size) == (HEADER_SIZE, LOD_SIZE, SECTION_SIZE) else " (NOT the rigid layout)")]
    lods, sections = struct.unpack_from("<HH", data, 180)
    out.append(f"box {_floats(data, 0x10, 4)}..{_floats(data, 0x20, 4)}; names {_name(data, struct.unpack_from('<Q', data, 0x68)[0])!r}, "
               f"{_name(data, struct.unpack_from('<Q', data, 0x70)[0])!r}; mesh type {data[124]}; {lods} LODs, {sections} sections")
    out.append(f"header bytes: {data[:HEADER_SIZE].hex()}")
    for k in range(lods):
        (pointer,) = struct.unpack_from("<Q", data, 0x30 + 8 * k)
        lod = pointer + 16
        if lod + LOD_SIZE > len(data):
            out.append(f"LOD {k}: pointer {pointer:#x} out of range")
            continue
        count = struct.unpack_from("<I", data, lod + 8)[0]
        (table,) = struct.unpack_from("<Q", data, lod + 12)
        flags, index_format, index_bytes, vertex_bytes = struct.unpack_from("<4I", data, lod + 80)
        out.append(f"LOD {k} @{lod}: word0 {struct.unpack_from('<I', data, lod)[0]}, {count} sections @ {table + 16}, "
                   f"flags {flags:#x}, index format {index_format}, index bytes {index_bytes}, vertex bytes "
                   f"{vertex_bytes}, chunk {Guid(data[lod + 116:lod + 132]).string()}")
        out.append(f"  LOD bytes: {data[lod:lod + LOD_SIZE].hex()}")
        for s in range(count):
            at = table + 16 + s * SECTION_SIZE
            if at + SECTION_SIZE > len(data):
                out.append(f"  section {s}: out of range")
                continue
            per_vertex, primitive = data[at + 30], data[at + 31]
            triangles, first_index, vertex_start, vertices = struct.unpack_from("<4I", data, at + 32)
            decl = at + 112
            elements, streams = data[decl + 96], data[decl + 97]
            layout = [tuple(data[decl + 4 * e:decl + 4 * e + 4]) for e in range(min(elements, 16))]
            strides = [tuple(data[decl + 64 + 2 * e:decl + 64 + 2 * e + 2]) for e in range(min(streams, 16))]
            out.append(f"  section {s} @{at}: material {_name(data, struct.unpack_from('<Q', data, at + 8)[0])!r}, "
                       f"{per_vertex} bytes per vertex, primitive {primitive}, {triangles} triangles, first index "
                       f"{first_index}, vertex start {vertex_start}, {vertices} vertices")
            out.append(f"    elements (usage, format, offset, stream) {layout}; streams (stride, class) {strides}")
            out.append(f"    section bytes: {data[at:at + SECTION_SIZE].hex()}")
    return out
