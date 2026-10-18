"""Reading a rigid MeshSet resource's header, LODs and sections (res type 0x49B156D4), as laid out
in FORMAT-NOTES 1. Read-only: it prints what a MeshSet writer will have to fill (`inspect-meshset`).
Pointers are u64 counted from byte 16 of the resource.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

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


# ---- Writing a MeshSet of our own geometry by patching a copy of a template ---------------------
#
# Only the fields FORMAT-NOTES 1 has verified are changed; every other byte (names, vertex
# declarations, material references, the per-section box, unknown words) is the template's, and
# the resource keeps the template's size, so the template's resource meta stays valid.

POSITION_USAGE = 1
INDEX_FORMAT_U16 = 33
TRIANGLE_LIST = 3


@dataclass
class Section:
    at: int                 # offset of the section struct in the MeshSet
    vertex_start: int       # byte offset into the chunk
    vertex_count: int
    triangle_count: int
    first_index: int
    elements: list          # (usage, format, offset, stream)
    strides: list           # per stream


@dataclass
class Lod:
    at: int
    index_bytes: int
    vertex_bytes: int
    chunk: Guid
    sections: list


def read_layout(data: bytes) -> list[Lod]:
    """The LODs and sections of a rigid MeshSet; ValueError when it is not one this writer handles."""
    header, lod_size, section_size = struct.unpack_from("<III", data, 0)
    if (header, lod_size, section_size) != (HEADER_SIZE, LOD_SIZE, SECTION_SIZE):
        raise ValueError("not the rigid MeshSet layout (192/176/384)")
    if data[124] != 0:
        raise ValueError(f"mesh type {data[124]}; only type 0 is handled")
    (lod_count,) = struct.unpack_from("<H", data, 180)
    lods = []
    for k in range(lod_count):
        lod = struct.unpack_from("<Q", data, 0x30 + 8 * k)[0] + 16
        count = struct.unpack_from("<I", data, lod + 8)[0]
        table = struct.unpack_from("<Q", data, lod + 12)[0] + 16
        _, index_format, index_bytes, vertex_bytes = struct.unpack_from("<4I", data, lod + 80)
        if index_format != INDEX_FORMAT_U16:
            raise ValueError(f"LOD {k}: index format {index_format}; only 16-bit indices are handled")
        sections = []
        for s in range(count):
            at = table + s * SECTION_SIZE
            if data[at + 31] != TRIANGLE_LIST:
                raise ValueError(f"LOD {k} section {s}: primitive type {data[at + 31]}, not a triangle list")
            triangles, first_index, vertex_start, vertices = struct.unpack_from("<4I", data, at + 32)
            decl = at + 112
            elements = [tuple(data[decl + 4 * e:decl + 4 * e + 4]) for e in range(data[decl + 96])]
            strides = [data[decl + 64 + 2 * e] for e in range(data[decl + 97])]
            sections.append(Section(at, vertex_start, vertices, triangles, first_index, elements, strides))
        lods.append(Lod(lod, index_bytes, vertex_bytes, Guid(data[lod + 116:lod + 132]), sections))
    return lods


def _stream_base(section: Section, stream: int) -> int:
    return section.vertex_start + sum(stride * section.vertex_count for stride in section.strides[:stream])


def _vertex_end(section: Section) -> int:
    return section.vertex_start + sum(section.strides) * section.vertex_count


def rebuild_chunk(lod: Lod, chunk: bytes) -> bytes:
    """The chunk rebuilt from what the layout says it holds (each section's streams, the index
    buffer at `vertex_bytes`), gaps zero: equal to the original when the layout is understood."""
    out = bytearray(len(chunk))
    for s in lod.sections:
        out[s.vertex_start:_vertex_end(s)] = chunk[s.vertex_start:_vertex_end(s)]
        start = lod.vertex_bytes + 2 * s.first_index
        out[start:start + 6 * s.triangle_count] = chunk[start:start + 6 * s.triangle_count]
    return bytes(out)


def _position(section: Section) -> tuple[int, int, int]:
    found = [e for e in section.elements if e[0] == POSITION_USAGE]
    if len(found) != 1 or found[0][1] not in (3, 8):
        raise ValueError(f"section at {section.at}: position element {found}; only formats 3 and 8 are handled")
    _, fmt, offset, stream = found[0]
    return fmt, offset, stream


def section_positions(section: Section, chunk: bytes) -> list[tuple[float, float, float]]:
    fmt, offset, stream = _position(section)
    base, stride = _stream_base(section, stream), section.strides[stream]
    code = "<3f" if fmt == 3 else "<3e"
    return [struct.unpack_from(code, chunk, base + i * stride + offset) for i in range(section.vertex_count)]


def section_triangles(lod: Lod, section: Section, chunk: bytes) -> list[tuple[int, int, int]]:
    start = lod.vertex_bytes + 2 * section.first_index
    flat = struct.unpack_from(f"<{3 * section.triangle_count}H", chunk, start)
    return [tuple(flat[i:i + 3]) for i in range(0, len(flat), 3)]


def _align(value: int, to: int) -> int:
    return (value + to - 1) // to * to


def write_mesh(template: bytes, template_chunks: list[bytes], vertices, triangles,
               chunk_guids: list[Guid]) -> tuple[bytes, list[bytes], list[str]]:
    """A MeshSet and its geometry chunks (one per LOD) holding `vertices` and `triangles` in every
    section of every LOD of the template; every vertex's other elements are the template section's
    first vertex's. Triangles take the template's winding (seen from +y), read from LOD 0's first
    section. Returns (meshset, chunks, report)."""
    lods = read_layout(template)
    if len(template_chunks) != len(lods) or len(chunk_guids) != len(lods):
        raise ValueError(f"the template has {len(lods)} LODs; give one chunk and one GUID for each")
    for k, (lod, chunk) in enumerate(zip(lods, template_chunks)):
        if rebuild_chunk(lod, chunk) != chunk:
            raise ValueError(f"the template's LOD {k} chunk does not rebuild from its layout; not written")
    if len(vertices) > 0xFFFF:
        raise ValueError("at most 65535 vertices")
    first = lods[0].sections[0]
    points, tris = section_positions(first, template_chunks[0]), section_triangles(lods[0], first, template_chunks[0])
    ups = [((b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]))
           for a, b, c in ((points[i], points[j], points[k]) for i, j, k in tris)]
    ours = [((vertices[b][2] - vertices[a][2]) * (vertices[c][0] - vertices[a][0])
             - (vertices[b][0] - vertices[a][0]) * (vertices[c][2] - vertices[a][2])) for a, b, c in triangles[:1]]
    flip = bool(ups and ours and (sum(ups) > 0) != (ours[0] > 0))
    if flip:
        triangles = [(a, c, b) for a, b, c in triangles]
    report = [f"template: {len(lods)} LODs; " + "; ".join(
        f"LOD {k}: {len(lod.sections)} sections, {[s.vertex_count for s in lod.sections]} vertices, "
        f"{[s.triangle_count for s in lod.sections]} triangles, chunk {len(c)} bytes rebuilds identically"
        for k, (lod, c) in enumerate(zip(lods, template_chunks)))
        + f"; winding {'flipped to match' if flip else 'as the template'}"]

    meshset = bytearray(template)
    chunks = []
    for lod, template_chunk, guid in zip(lods, template_chunks, chunk_guids):
        vertex_data = bytearray()
        first_index = 0
        for s in lod.sections:
            fmt, offset, stream = _position(s)
            start = _align(len(vertex_data), 16)
            vertex_data += bytes(start - len(vertex_data))
            for t, stride in enumerate(s.strides):
                model = template_chunk[_stream_base(s, t):_stream_base(s, t) + stride]
                for x, y, z in vertices:
                    vertex = bytearray(model)
                    if t == stream:
                        struct.pack_into("<3f" if fmt == 3 else "<3e", vertex, offset, x, y, z)
                    vertex_data += vertex
            struct.pack_into("<4I", meshset, s.at + 32, len(triangles), first_index, start, len(vertices))
            first_index += 3 * len(triangles)
        vertex_bytes = _align(len(vertex_data), 16)
        indices = struct.pack(f"<{3 * len(triangles)}H", *(i for t in triangles for i in t)) * len(lod.sections)
        chunk = bytes(vertex_data) + bytes(vertex_bytes - len(vertex_data)) + indices
        chunk += bytes(-len(chunk) % 16)
        struct.pack_into("<II", meshset, lod.at + 88, len(indices), vertex_bytes)
        meshset[lod.at + 116:lod.at + 132] = guid.bytes
        chunks.append(chunk)
    lo = [min(v[i] for v in vertices) for i in range(3)]
    hi = [max(v[i] for v in vertices) for i in range(3)]
    struct.pack_into("<3f", meshset, 0x10, *lo)
    struct.pack_into("<3f", meshset, 0x20, *hi)
    old_lo, old_hi = struct.unpack_from("<3f", template, 0x10), struct.unpack_from("<3f", template, 0x20)
    report.append(f"ours: {len(vertices)} vertices, {len(triangles)} triangles per section in every LOD, chunks "
                  f"{[len(c) for c in chunks]} bytes; box {[round(v, 2) for v in lo]}..{[round(v, 2) for v in hi]}"
                  + ("" if all(abs(a - b) < 1e-3 for a, b in zip(lo + hi, old_lo + old_hi)) else
                     " (differs from the template's; each section's own box, not decoded, still says the template's)"))
    for back, chunk in zip(read_layout(bytes(meshset)), chunks):
        if rebuild_chunk(back, chunk) != chunk:
            raise ValueError("the written chunk does not rebuild from the written layout")
        for s in back.sections:
            tolerance = 1e-4 if _position(s)[0] == 3 else 0.05
            read = section_positions(s, chunk)
            if section_triangles(back, s, chunk) != list(triangles) or any(
                    abs(a - b) > tolerance * max(1.0, abs(b)) for p, v in zip(read, vertices) for a, b in zip(p, v)):
                raise ValueError("the written mesh does not read back")
    return bytes(meshset), chunks, report
