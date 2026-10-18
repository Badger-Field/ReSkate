"""Reading a rigid MeshSet resource's header, LODs and sections (res type 0x49B156D4), as laid out
in FORMAT-NOTES 1. Read-only: it prints what a MeshSet writer will have to fill (`inspect-meshset`).
Pointers are u64 counted from byte 16 of the resource.
"""
from __future__ import annotations

import struct
from collections import Counter
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
# Each section's shader-block key (the PC's finding, FORMAT-NOTES 1): an entry key in the
# ShaderBlockDepot of the bundle holding the MeshSet. LODs of one mesh share keys.
SECTION_KEY = 0x138
# What a section takes from a shader donor (--shader-from): bytes per vertex (+30) and everything
# from the vertex declaration (+112) through the key (+0x138..+0x140).
DONOR_SPANS = ((30, 31), (112, SECTION_KEY + 8))

DEPOT_TYPE = 0x73312045          # ShaderBlockDepot: "<bundle path>_win32_shaderstate/shaderblockdepot_<n>"
SHADER_STATE_ID_TYPE = 0x2FEC6526  # 32 bytes: "<bundle path>_win32_shaderstateshaderstateassetid"


def section_key(data: bytes, section: "Section") -> int:
    return struct.unpack_from("<Q", data, section.at + SECTION_KEY)[0]


def depot_records(data: bytes) -> list[tuple[int, int]]:
    """(key, offset) records of a ShaderBlockDepot. Header as read on the PC: u64 0x10, u64 0, then
    (u64 key, u64 offset) pairs; the pairs end where the first block begins (the smallest offset)
    or at a zero key. Only the keys and offsets are decoded."""
    if len(data) < 16:
        raise ValueError("ShaderBlockDepot shorter than its header")
    first, zero = struct.unpack_from("<QQ", data, 0)
    if first != 0x10 or zero != 0:
        raise ValueError(f"ShaderBlockDepot header {first:#x}, {zero:#x}; expected 0x10, 0")
    records, at, end = [], 16, len(data)
    while at + 16 <= end:
        key, offset = struct.unpack_from("<QQ", data, at)
        if key == 0 or offset >= len(data) or offset < at:
            break
        records.append((key, offset))
        end = min(end, offset)
        at += 16
    return records


def depot_keys(data: bytes) -> set[int]:
    """The depot's entry keys: its records', or (if the records do not parse) every u64 at an
    8-byte boundary of the first 64 KiB, which still answers "is this key in there"."""
    try:
        records = depot_records(data)
    except (ValueError, struct.error):
        records = []
    if records:
        return {k for k, _ in records}
    return {struct.unpack_from("<Q", data, at)[0] for at in range(0, min(len(data), 0x10000) - 7, 8)}


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
    if len(found) != 1 or found[0][1] not in FORMAT_CODES or FORMAT_CODES[found[0][1]][1] < 3:
        raise ValueError(f"section at {section.at}: position element {found}; not a readable 3-component format")
    _, fmt, offset, stream = found[0]
    return fmt, offset, stream


def section_positions(section: Section, chunk: bytes) -> list[tuple[float, float, float]]:
    fmt, offset, stream = _position(section)
    base, stride = _stream_base(section, stream), section.strides[stream]
    return [tuple(decode_element(fmt, chunk, base + i * stride + offset)[:3]) for i in range(section.vertex_count)]


def section_element(section: Section, chunk: bytes, usage: int) -> list[list[float]] | None:
    """Every vertex's values of the element with this usage, or None."""
    found = next(((f, o, t) for u, f, o, t in section.elements if u == usage and f in FORMAT_CODES), None)
    if found is None:
        return None
    f, o, t = found
    base, stride = _stream_base(section, t), section.strides[t]
    return [decode_element(f, chunk, base + i * stride + o) for i in range(section.vertex_count)]


def section_triangles(lod: Lod, section: Section, chunk: bytes) -> list[tuple[int, int, int]]:
    start = lod.vertex_bytes + 2 * section.first_index
    flat = struct.unpack_from(f"<{3 * section.triangle_count}H", chunk, start)
    return [tuple(flat[i:i + 3]) for i in range(0, len(flat), 3)]


def _align(value: int, to: int) -> int:
    return (value + to - 1) // to * to


# Vertex element formats as read on files (FORMAT-NOTES 1): 3 = three float32; 8 = four float16
# (x, y, z and a fourth half, meaning unknown, kept as the model vertex's); 12 = two float16 (UV,
# usage 51); 6 and 13 = 4 bytes, not decoded (usage 33/34: probably a packed normal and tangent
# frame; 52: UV or colour): taken from the model vertex, which faces up when the section has an
# up-facing triangle.
# Vertex element formats: Skate's VertexElementFormat enum, read from its reflection names by the PC
# (2026-10-18), in declaration order. (struct code, components, normalisation divisor or None.)
FORMAT_NAMES = {0: "None", 1: "Float", 2: "Float2", 3: "Float3", 4: "Float4", 5: "Half", 6: "Half2", 7: "Half3",
                8: "Half4", 9: "UByteN", 10: "Byte4", 11: "Byte4N", 12: "UByte4", 13: "UByte4N", 14: "Short",
                15: "Short2", 16: "Short3", 17: "Short4", 18: "ShortN", 19: "Short2N", 20: "Short3N", 21: "Short4N",
                22: "UShort2", 23: "UShort4", 24: "UShort2N", 25: "UShort4N", 26: "Int",
                40: "Comp3_10_10_10", 41: "Comp3N_10_10_10", 42: "UComp3_10_10_10", 43: "UComp3N_10_10_10"}
FORMAT_CODES = {1: ("f", 1, None), 2: ("f", 2, None), 3: ("f", 3, None), 4: ("f", 4, None),
                5: ("e", 1, None), 6: ("e", 2, None), 7: ("e", 3, None), 8: ("e", 4, None),
                9: ("B", 1, 255.0), 10: ("b", 4, None), 11: ("b", 4, 127.0), 12: ("B", 4, None), 13: ("B", 4, 255.0),
                14: ("h", 1, None), 15: ("h", 2, None), 16: ("h", 3, None), 17: ("h", 4, None),
                18: ("h", 1, 32767.0), 19: ("h", 2, 32767.0), 20: ("h", 3, 32767.0), 21: ("h", 4, 32767.0),
                22: ("H", 2, None), 23: ("H", 4, None), 24: ("H", 2, 65535.0), 25: ("H", 4, 65535.0), 26: ("i", 1, None)}
FORMATS = FORMAT_NAMES

# Vertex usages, as read on the skatepark box (PC): 1 position; 33 and 34 texture coordinates
# (TexCoord0, atlas-like, and TexCoord1, world scale) as Half2; 51 UByte4, zeros or (1, 0, 0, 0);
# 52 UByte4N, (0, 0, 0 or 255, a). No normal or tangent in this layout.
UV0_USAGE, UV1_USAGE, ZERO_USAGE, COLOUR_USAGE = 33, 34, 51, 52
AREA_OFFSET = 332                     # section: surface area (float), by the PC's diff
UV_METRES = 6.0
UV1_DEFAULT = 0.5           # per metre, when the template has no TexCoord1 to measure


def decode_element(fmt: int, data: bytes, at: int) -> list[float]:
    code, n, norm = FORMAT_CODES[fmt]
    values = struct.unpack_from("<" + code * n, data, at)
    return [v / norm for v in values] if norm else [float(v) for v in values]


def encode_element(fmt: int, buffer: bytearray, at: int, values) -> None:
    """Writes the first len(values) components; the rest keep what the buffer holds."""
    if fmt not in FORMAT_CODES:
        raise ValueError(f"vertex format {fmt} ({FORMAT_NAMES.get(fmt, '?')}) is not written by this builder")
    code, n, norm = FORMAT_CODES[fmt]
    current = list(struct.unpack_from("<" + code * n, buffer, at))
    for i, v in enumerate(list(values)[:n]):
        v = v * norm if norm else v
        current[i] = int(round(v)) if code in "bBhHi" else v
    struct.pack_into("<" + code * n, buffer, at, *current)


def element_size(fmt: int) -> int:
    code, n, _ = FORMAT_CODES[fmt]
    return struct.calcsize("<" + code * n)


def model_vertex(lod: Lod, section: Section, chunk: bytes) -> tuple[list[bytes], int]:
    """(bytes per stream, index) of the section's vertex best facing up: a corner of the triangle
    whose normal is the most upward (either winding), so copied normal and tangent bytes suit a
    flat ground. The first vertex when the section has no triangles."""
    points, best, index = section_positions(section, chunk), -2.0, 0
    for a, b, c in section_triangles(lod, section, chunk):
        pa, pb, pc = points[a], points[b], points[c]
        u = [pb[i] - pa[i] for i in range(3)]
        v = [pc[i] - pa[i] for i in range(3)]
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        length = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5
        if length and abs(n[1]) / length > best:
            best, index = abs(n[1]) / length, a
    out = []
    for t, stride in enumerate(section.strides):
        at = _stream_base(section, t) + index * stride
        out.append(chunk[at:at + stride])
    return out, index


def _outward(points, triangles) -> bool | None:
    """Whether (b - a) x (c - a) points out of the surface: from the signed volume for a closed
    shape, from the summed upward normal for an open one (None if neither says)."""
    volume = up = 0.0
    for a, b, c in triangles:
        pa, pb, pc = points[a], points[b], points[c]
        volume += (pa[0] * (pb[1] * pc[2] - pb[2] * pc[1]) - pa[1] * (pb[0] * pc[2] - pb[2] * pc[0])
                   + pa[2] * (pb[0] * pc[1] - pb[1] * pc[0]))
        up += (pb[2] - pa[2]) * (pc[0] - pa[0]) - (pb[0] - pa[0]) * (pc[2] - pa[2])
    if abs(volume) > 1e-6:
        return volume > 0
    if abs(up) > 1e-9:
        return up > 0
    return None


def _find_box(data: bytes, start: int, end: int, lo, hi) -> list[int]:
    """Offsets in [start, end) where three floats equal `lo` and, 12 or 16 bytes on, three equal `hi`."""
    found = []
    for at in range(start, end - 27, 4):
        a = struct.unpack_from("<3f", data, at)
        if all(abs(x - y) <= 1e-3 * max(1.0, abs(y)) for x, y in zip(a, lo)):
            for gap in (12, 16):
                b = struct.unpack_from("<3f", data, at + gap)
                if all(abs(x - y) <= 1e-3 * max(1.0, abs(y)) for x, y in zip(b, hi)):
                    found.append((at, gap))
    return found


def keep_one_section(data: bytearray, index: int) -> list[str]:
    """Makes each LOD list only its section `index` (or its last, if it has fewer): the LOD's
    section count becomes 1 and its section-table pointer points at that struct; the header's
    total becomes the LOD count. The other section structs stay in the file, unreferenced."""
    report = []
    lods = read_layout(bytes(data))
    for k, lod in enumerate(lods):
        n = min(index, len(lod.sections) - 1)
        struct.pack_into("<I", data, lod.at + 8, 1)
        struct.pack_into("<Q", data, lod.at + 12, lod.sections[n].at - 16)
        report.append(f"LOD {k}: keeps section {n} of {len(lod.sections)}")
    struct.pack_into("<H", data, 182, len(lods))
    return report


def box_geometry(lo, hi):
    """A closed box, 4 corners per face (24 vertices, 12 triangles), wound outward."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    faces = [((x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)),     # top, seen from above: anticlockwise
             ((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)),     # bottom
             ((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)),     # z1 side
             ((x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0)),     # z0 side
             ((x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1)),     # x1 side
             ((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0))]     # x0 side
    vertices, triangles = [], []
    for quad in faces:
        n = len(vertices)
        vertices += list(quad)
        triangles += [(n, n + 1, n + 2), (n, n + 2, n + 3)]
    # Make every face outward by the right-hand rule.
    out = []
    for a, b, c in triangles:
        pa, pb, pc = vertices[a], vertices[b], vertices[c]
        u = [pb[i] - pa[i] for i in range(3)]
        v = [pc[i] - pa[i] for i in range(3)]
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        centre = [(lo[i] + hi[i]) / 2 for i in range(3)]
        outward = sum(n[i] * (pa[i] - centre[i]) for i in range(3)) > 0
        out.append((a, b, c) if outward else (a, c, b))
    return vertices, out


def write_mesh(template: bytes, template_chunks: list[bytes], vertices, triangles,
               chunk_guids: list[Guid], donor: tuple[bytes, list[bytes]] | None = None,
               keep_section: int | None = None, copy_template: bool = False,
               geometry_section: int | None = 0, uv_per_metre: float | None = None, uv_label: str | None = None,
               measures: tuple | None = None) -> tuple[bytes, list[bytes], list[str]]:
    """A MeshSet and its geometry chunks (one per LOD, under `chunk_guids`), patched into a copy of
    `template` (the drawn mesh whose fields we keep). Returns (meshset, chunks, report).

    Every kept section of every LOD gets `vertices` and `triangles` ((b - a) x (c - a) outward,
    turned to the template's winding). Each vertex is built in the section's own layout: the
    position from ours (format 3 or 8), UVs (usage 51, format 12) from x and z every UV_METRES,
    everything else (normal and tangent frames, colour) the section's most up-facing vertex's.
    Changed fields: the header box; each LOD's index and vertex byte counts, chunk GUID and any
    box found in it; each section's triangle count, first index, vertex start, vertex count and
    any box found in it (a box: three floats equal to the template's minimum and, 12 or 16 bytes
    on, its maximum), and the section's surface area at +332 when the template's own matches its
    geometry. `geometry_section` N: only section N of each LOD (its last if it has fewer) gets our
    geometry; every other section keeps its struct, material and layout and draws one zero-area
    triangle on one vertex of its own (so every per-section count and table stays the template's).
    None: every section gets our geometry. `keep_section` N lists only section N in each LOD (this
    crashed the game: sections are counted elsewhere too). `copy_template` writes the
    template's own vertex and index bytes into the new chunks instead (the plumbing test).

    `donor` = (a section struct of another mesh, its model vertex per stream): every section
    first takes the donor's bytes per vertex, vertex declaration and shader-block key
    (DONOR_SPANS), and its vertices are built from the donor's model vertex."""
    lods = read_layout(template)
    if len(template_chunks) != len(lods) or len(chunk_guids) != len(lods):
        raise ValueError(f"the template has {len(lods)} LODs; give one chunk and one GUID for each")
    for k, (lod, chunk) in enumerate(zip(lods, template_chunks)):
        if rebuild_chunk(lod, chunk) != chunk:
            raise ValueError(f"the template's LOD {k} chunk does not rebuild from its layout; not written")
    report = [f"template: {len(lods)} LODs; " + "; ".join(
        f"LOD {k}: {len(lod.sections)} sections, {[s.vertex_count for s in lod.sections]} vertices, "
        f"{[s.triangle_count for s in lod.sections]} triangles, chunk {len(c)} bytes rebuilds identically"
        for k, (lod, c) in enumerate(zip(lods, template_chunks)))]
    meshset = bytearray(template)

    # Boxes in the LOD and section structs, found by the template's own bounds.
    boxes = []                       # (struct offset + found offset, gap)
    for lod, chunk in zip(lods, template_chunks):
        all_points = []
        for s in lod.sections:
            points = section_positions(s, chunk)
            all_points += points
            if points:
                lo = [min(p[i] for p in points) for i in range(3)]
                hi = [max(p[i] for p in points) for i in range(3)]
                for at, gap in _find_box(template, s.at, s.at + SECTION_SIZE, lo, hi):
                    boxes.append((at, gap, f"section +{at - s.at}"))
        if all_points:
            lo = [min(p[i] for p in all_points) for i in range(3)]
            hi = [max(p[i] for p in all_points) for i in range(3)]
            for at, gap in _find_box(template, lod.at, lod.at + LOD_SIZE, lo, hi):
                boxes.append((at, gap, f"LOD +{at - lod.at}"))
    report.append("boxes found in the structs: " + (", ".join(sorted({label for _, _, label in boxes})) or "none"))
    # Section +332: the section's surface area (PC: 2500 for the 50 m plane, 36.96 for the box's
    # section 0). Written only if the template's own sections say so.
    area_ok = all(abs(struct.unpack_from("<f", template, s.at + AREA_OFFSET)[0] - _area(section_positions(s, c),
                                                                                         section_triangles(lod, s, c)))
                  <= 0.01 * max(1.0, _area(section_positions(s, c), section_triangles(lod, s, c)))
                  for lod, c in zip(lods, template_chunks) for s in lod.sections)
    report.append(f"section +{AREA_OFFSET} " + ("is each template section's surface area: ours written" if area_ok else
                                                 "is not the template sections' surface area: left as it was"))
    report += ["template sections:"] + section_list(template)

    if keep_section is not None:
        report += keep_one_section(meshset, keep_section)
    if donor is not None:
        donor_section, _ = donor
        for lod in read_layout(bytes(meshset)):
            for s in lod.sections:
                for a, b in DONOR_SPANS:
                    meshset[s.at + a:s.at + b] = donor_section[a:b]
        report.append(f"donor section: key {struct.unpack_from('<Q', donor_section, SECTION_KEY)[0]:#018x}, "
                      f"{donor_section[30]} bytes per vertex")
    lods_now = read_layout(bytes(meshset))

    if copy_template:
        chunks = []
        for lod, chunk, guid in zip(lods_now, template_chunks, chunk_guids):
            meshset[lod.at + 116:lod.at + 132] = guid.bytes
            chunks.append(chunk)
        report.append("copy of the template's own geometry and vertex bytes, under new chunk GUIDs")
        return bytes(meshset), chunks, report

    if len(vertices) > 0xFFFF:
        raise ValueError("at most 65535 vertices")
    normals = vertex_normals(vertices, triangles)          # ours, outward: picks each face's UV plane
    first, first_chunk = lods[0].sections[0], template_chunks[0]
    theirs = _outward(section_positions(first, first_chunk), section_triangles(lods[0], first, first_chunk))
    flip = theirs is False
    if flip:
        triangles = [(a, c, b) for a, b, c in triangles]
    report.append(f"winding {'flipped to match' if flip else 'as the template'}"
                  + ("" if theirs is not None else " (the template's could not be told)"))

    # Per-vertex data, by usage: position ours; TexCoord0 (33) planar per face at the template
    # section's own density (or --uv-per-metre) from its own minimum; TexCoord1 (34) planar at
    # the template's density (world scale); 51 zeros; 52 the template section's most common value;
    # anything else the section's most up-facing template vertex's.
    geometry_index = min(geometry_section or 0, len(lods[0].sections) - 1)
    measure_section = lods[0].sections[geometry_index]
    if measures is not None:                   # a donor section's own (another mesh's), as measured by the caller
        uv0, uv1, colour = measures
    else:
        uv0 = uv_measure(lods[0], measure_section, template_chunks[0], UV0_USAGE)
        uv1 = uv_measure(lods[0], measure_section, template_chunks[0], UV1_USAGE)
        colour = common_value(measure_section, template_chunks[0], COLOUR_USAGE)
    density0 = uv_per_metre or (uv0[0] if uv0 and uv0[0] else 1.0 / UV_METRES)
    origin0 = uv0[1] if uv0 else [0.0, 0.0]
    density1 = uv1[0] if uv1 and uv1[0] else UV1_DEFAULT
    report.append(f"TexCoord0 (usage 33): planar per face at {density0:.4g} per metre ("
                  + (uv_label or ("given" if uv_per_metre else "the template section's")) + ") from "
                  f"{[round(c, 3) for c in origin0]}"
                  + (f"; the template's: {uv0[0]:.4g} per metre over {[round(c, 3) for c in uv0[1]]}.."
                     f"{[round(c, 3) for c in uv0[2]]}" if uv0 else "; the template has none"))
    report.append(f"TexCoord1 (usage 34): planar at {density1:.4g} per metre"
                  + (f" (the template's, over {[round(c, 3) for c in uv1[1]]}..{[round(c, 3) for c in uv1[2]]})"
                     if uv1 else " (default; the template has none)")
                  + f"; usage 51 zeros; usage 52 {colour.hex() if colour else 'the model vertex'}s")
    chunks = []
    described = set()
    for lod, old_lod, template_chunk, guid in zip(lods_now, lods, template_chunks, chunk_guids):
        vertex_data = bytearray()
        index_parts = []
        first_index = 0
        for n_section, s in enumerate(lod.sections):
            ours = geometry_section is None or n_section == min(geometry_section, len(lod.sections) - 1)
            verts, tris = (vertices, triangles) if ours else (vertices[:1], [(0, 0, 0)])
            if donor is not None:
                model = donor[1]
            else:
                old = next(o for o in old_lod.sections if o.at == s.at)
                model, _ = model_vertex(old_lod, old, template_chunk)
            if len(model) != len(s.strides) or any(len(m) != w for m, w in zip(model, s.strides)):
                raise ValueError(f"section at {s.at}: the model vertex does not match its streams")
            key = (tuple(s.elements), tuple(s.strides))
            if key not in described:
                described.add(key)
                report.append("section layout: " + ", ".join(
                    f"usage {u} format {f} ({FORMAT_NAMES.get(f, '?')}) at +{o} of stream {t}" for u, f, o, t in s.elements)
                    + f"; strides {s.strides}")
            fmt, offset, stream = _position(s)
            start = _align(len(vertex_data), 16)
            vertex_data += bytes(start - len(vertex_data))
            for t, stride in enumerate(s.strides):
                for v_i, (x, y, z) in enumerate(verts):
                    vertex = bytearray(model[t])
                    n = normals[v_i] if ours else (0.0, 1.0, 0.0)
                    for usage, f, o, st in s.elements:
                        if st != t or f not in FORMAT_CODES:
                            continue
                        if usage == POSITION_USAGE:
                            encode_element(f, vertex, o, (x, y, z))
                        elif usage == UV0_USAGE:
                            u, v = planar_uv((x, y, z), n, density0)
                            encode_element(f, vertex, o, (u + origin0[0], v + origin0[1]))
                        elif usage == UV1_USAGE:
                            encode_element(f, vertex, o, planar_uv((x, y, z), n, density1))
                        elif usage == ZERO_USAGE:
                            vertex[o:o + element_size(f)] = bytes(element_size(f))
                        elif usage == COLOUR_USAGE and colour is not None and len(colour) == element_size(f):
                            vertex[o:o + element_size(f)] = colour
                    vertex_data += vertex
            struct.pack_into("<4I", meshset, s.at + 32, len(tris), first_index, start, len(verts))
            if area_ok:
                struct.pack_into("<f", meshset, s.at + AREA_OFFSET, _area(verts, tris))
            first_index += 3 * len(tris)
            index_parts.append(struct.pack(f"<{3 * len(tris)}H", *(i for t in tris for i in t)))
        vertex_bytes = _align(len(vertex_data), 16)
        indices = b"".join(index_parts)
        chunk = bytes(vertex_data) + bytes(vertex_bytes - len(vertex_data)) + indices
        chunk += bytes(-len(chunk) % 16)
        struct.pack_into("<II", meshset, lod.at + 88, len(indices), vertex_bytes)
        meshset[lod.at + 116:lod.at + 132] = guid.bytes
        chunks.append(chunk)
    lo, hi = padded_bounds(vertices)
    struct.pack_into("<3f", meshset, 0x10, *lo)
    struct.pack_into("<3f", meshset, 0x20, *hi)
    for at, gap, _ in boxes:
        struct.pack_into("<3f", meshset, at, *lo)
        struct.pack_into("<3f", meshset, at + gap, *hi)
    report.append(f"ours: {len(vertices)} vertices, {len(triangles)} triangles per section, chunks "
                  f"{[len(c) for c in chunks]} bytes; box {[round(v, 2) for v in lo]}..{[round(v, 2) for v in hi]} "
                  f"written to the header and {len(boxes)} boxes found in the structs")
    for back, chunk in zip(read_layout(bytes(meshset)), chunks):
        if rebuild_chunk(back, chunk) != chunk:
            raise ValueError("the written chunk does not rebuild from the written layout")
        for n_section, s in enumerate(back.sections):
            ours = geometry_section is None or n_section == min(geometry_section, len(back.sections) - 1)
            verts, tris = (vertices, triangles) if ours else (vertices[:1], [(0, 0, 0)])
            tolerance = 1e-4 if _position(s)[0] == 3 else 0.05
            read = section_positions(s, chunk)
            if section_triangles(back, s, chunk) != list(tris) or len(read) != len(verts) or any(
                    abs(a - b) > tolerance * max(1.0, abs(b)) for p, v in zip(read, verts) for a, b in zip(p, v)):
                raise ValueError("the written mesh does not read back")
    report += ["our sections:"] + section_list(bytes(meshset))
    return bytes(meshset), chunks, report


def _area(points, triangles) -> float:
    total = 0.0
    for a, b, c in triangles:
        pa, pb, pc = points[a], points[b], points[c]
        u = [pb[i] - pa[i] for i in range(3)]
        v = [pc[i] - pa[i] for i in range(3)]
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        total += 0.5 * (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5
    return total


def section_list(data: bytes) -> list[str]:
    """Per LOD: its flags and the six floats at +140; per section its triangles, vertices,
    material name, the words at +324 and +328 (a hash and a small number, perhaps the material
    index), the area at +332, the key and the layout."""
    out = []
    for k, lod in enumerate(read_layout(data)):
        flags = struct.unpack_from("<I", data, lod.at + 80)[0]
        out.append(f"  LOD {k}: flags {flags:#x}, {len(lod.sections)} sections, +140..+164 "
                   f"{[round(v, 3) for v in struct.unpack_from('<6f', data, lod.at + 140)]}")
        for n, s in enumerate(lod.sections):
            (pointer,) = struct.unpack_from("<Q", data, s.at + 8)
            end = data.find(b"\0", pointer + 16) if pointer else -1
            name = data[pointer + 16:end].decode("latin-1") if pointer and 0 < pointer + 16 < len(data) and end > 0 else ""
            word324, word328 = struct.unpack_from("<II", data, s.at + 324)
            out.append(f"    section {n}: {s.triangle_count} triangles, {s.vertex_count} vertices, material {name!r}, "
                       f"+324 {word324:#010x}, +328 {word328}, +332 {struct.unpack_from('<f', data, s.at + AREA_OFFSET)[0]:.4g}, "
                       f"key {section_key(data, s):#018x}, strides {s.strides}")
    return out


def first_vertex(section: Section, chunk: bytes) -> list[bytes]:
    """A section's first vertex: its bytes in each stream."""
    return [chunk[_stream_base(section, t):_stream_base(section, t) + stride] for t, stride in enumerate(section.strides)]


HEADER_FIELDS = {0: "header size", 4: "LOD struct size", 8: "section struct size", 0x10: "box min", 0x20: "box max",
                 0x30: "LOD pointers", 0x68: "name pointer", 0x70: "name pointer 2", 124: "mesh type (byte)",
                 180: "LOD count (u16) / section total (u16)"}
LOD_FIELDS = {0: "word 0", 8: "section count", 12: "section table pointer", 80: "flags", 84: "index format",
              88: "index bytes", 92: "vertex bytes", 116: "chunk GUID", 120: "chunk GUID", 124: "chunk GUID",
              128: "chunk GUID"}
SECTION_FIELDS = {8: "material name pointer", 16: "bone palette pointer", 24: "bone count (u16)",
                  28: "bytes per vertex (byte 30) / primitive (31)", 32: "triangle count", 36: "first index",
                  40: "vertex start", 44: "vertex count", SECTION_KEY: "shader-block key (low)",
                  SECTION_KEY + 4: "shader-block key (high)"}


def _word_diff(a: bytes, b: bytes, at_a: int, at_b: int, size: int, fields: dict, label: str) -> list[str]:
    out = []
    for o in range(0, size, 4):
        wa, wb = a[at_a + o:at_a + o + 4], b[at_b + o:at_b + o + 4]
        if wa != wb and len(wa) == len(wb) == 4:
            name = fields.get(o) or (f"vertex declaration +{o - 112}" if 112 <= o < 112 + 98 else "")
            ua, ub = struct.unpack("<I", wa)[0], struct.unpack("<I", wb)[0]
            fa, fb = struct.unpack("<f", wa)[0], struct.unpack("<f", wb)[0]
            out.append(f"  {label} +{o}{' ' + name if name else ''}: {ua:#010x} ({fa:.6g}) vs {ub:#010x} ({fb:.6g})")
    return out


def diff_meshsets(a: bytes, b: bytes) -> list[str]:
    """Field by field, every 4-byte word of the header, of each LOD struct and of each section
    struct that differs between two MeshSets (pointers included: they differ when sizes do)."""
    out = [f"sizes {len(a)} vs {len(b)}"]
    out += _word_diff(a, b, 0, 0, HEADER_SIZE, HEADER_FIELDS, "header")
    try:
        la, lb = read_layout(a), read_layout(b)
    except (ValueError, struct.error) as e:
        return out + [f"  layouts not comparable: {e}"]
    out.append(f"LODs {len(la)} vs {len(lb)}")
    for k, (x, y) in enumerate(zip(la, lb)):
        out += _word_diff(a, b, x.at, y.at, LOD_SIZE, LOD_FIELDS, f"LOD {k}")
        out.append(f"  LOD {k}: sections {len(x.sections)} vs {len(y.sections)}")
        for n, (sx, sy) in enumerate(zip(x.sections, y.sections)):
            out += _word_diff(a, b, sx.at, sy.at, SECTION_SIZE, SECTION_FIELDS, f"LOD {k} section {n}")
            if (sx.elements, sx.strides) != (sy.elements, sy.strides):
                out.append(f"  LOD {k} section {n} layout: {sx.elements} {sx.strides} vs {sy.elements} {sy.strides}")
    return out


BOUNDS_PAD = 0.05     # metres: a box is never written flat (a 0-high box made the 50 m plane not spawn)


def padded_bounds(vertices):
    lo = [min(v[i] for v in vertices) for i in range(3)]
    hi = [max(v[i] for v in vertices) for i in range(3)]
    for i in range(3):
        if hi[i] - lo[i] < 2 * BOUNDS_PAD:
            middle = (hi[i] + lo[i]) / 2
            lo[i], hi[i] = middle - BOUNDS_PAD, middle + BOUNDS_PAD
    return lo, hi


def _face_normal(pa, pb, pc):
    u = [pb[i] - pa[i] for i in range(3)]
    v = [pc[i] - pa[i] for i in range(3)]
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


def _unit(n):
    length = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5
    return (n[0] / length, n[1] / length, n[2] / length) if length else (0.0, 1.0, 0.0)


def vertex_normals(vertices, triangles):
    """Per vertex, the sum of its triangles' (b - a) x (c - a), normalised."""
    sums = [[0.0, 0.0, 0.0] for _ in vertices]
    for a, b, c in triangles:
        n = _face_normal(vertices[a], vertices[b], vertices[c])
        for i in (a, b, c):
            for k in range(3):
                sums[i][k] += n[k]
    return [_unit(n) for n in sums]


def planar_uv(p, n, density):
    """UV by the face's main axis: x, z for top and bottom; z, y facing x; x, y facing z."""
    axis = max(range(3), key=lambda i: abs(n[i]))
    u, v = {1: (p[0], p[2]), 0: (p[2], p[1]), 2: (p[0], p[1])}[axis]
    return u * density, v * density


def uv_measure(lod, section, chunk, usage) -> tuple[float, list[float], list[float]] | None:
    """(UV units per metre over the section's triangle edges, UV minimum, UV maximum) of the
    element with this usage, or None."""
    uvs = section_element(section, chunk, usage)
    if not uvs:
        return None
    points = section_positions(section, chunk)
    world = uv = 0.0
    for a, b, c in section_triangles(lod, section, chunk):
        for p, q in ((a, b), (b, c), (c, a)):
            world += sum((points[p][k] - points[q][k]) ** 2 for k in range(3)) ** 0.5
            uv += sum((uvs[p][k] - uvs[q][k]) ** 2 for k in range(2)) ** 0.5
    lo = [min(v[k] for v in uvs) for k in range(2)]
    hi = [max(v[k] for v in uvs) for k in range(2)]
    return (uv / world if world and uv else 0.0), lo, hi


def common_value(section, chunk, usage) -> bytes | None:
    """The most common bytes of the element with this usage in the section."""
    found = next(((f, o, t) for u, f, o, t in section.elements if u == usage and f in FORMAT_CODES), None)
    if found is None or not section.vertex_count:
        return None
    f, o, t = found
    base, stride, size = _stream_base(section, t), section.strides[t], element_size(f)
    counts = Counter(chunk[base + i * stride + o:base + i * stride + o + size] for i in range(section.vertex_count))
    return counts.most_common(1)[0][0]


