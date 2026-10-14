"""Collision: the game's PhysicsResource (res type 0x41759364), triangle-mesh shapes.

Layout and edge rules from contrib/mapbuilder/FORMAT-NOTES.md, section 2.
What has been tested in game: a resource with a shape replaced by one
built this way (vertices, half-edge arrays, edge data by the rule below, zeroed tree nodes)
loads, and the skater stands on it. A version-2 file written whole from scratch has not
been tested yet.

Pointers in this format are u32 distances from the pointer's own position (0 = none).
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

RESOURCE_TYPE = 0x41759364
MAGIC = 0x69AF7015
SHAPE_SIZE = 84
PART_SIZE = 72
_V2_BLOCK = struct.pack("<4f6IfII3fIII", 0.0, 0.0, 0.0, 1.0, 0, 0, 0, 0, 0, 0, 1.0, 1, 0,
                        -1.0, -1.0, -1.0, 0, 0, 256)
assert len(_V2_BLOCK) == 76

KIND_TRIANGLE_MESH = 5
# Copied from the custom map's collision parts (meaning unknown; FORMAT-NOTES 2, part record).
DEFAULT_PART_HASHES = (0x859F4B83, 0xE6686C9C)
WATER_PART_HASHES = (0xA7324F6E, 0xE6686C9C)
DEFAULT_PART_FLOAT = -0.073

FLAT_DEGREES = 0.1
FOLD_LIMIT_DEGREES = 90.0
WELD = 1e-3  # metres: twins are matched to 1 mm across shapes and files


@dataclass
class Shape:
    """A triangle-mesh shape in its part's local frame."""
    vertices: list[tuple[float, float, float]]
    triangles: list[tuple[int, int, int]]
    # Edge data per half-edge (filled by compute_edge_data); half-edge 3f+k runs from
    # triangles[f][k] to triangles[f][(k+1)%3].
    edge_value: list[float] = field(default_factory=list)
    edge_fold: list[bool] = field(default_factory=list)   # slot 5
    edge_flat: list[bool] = field(default_factory=list)   # slot 6

    def __post_init__(self):
        if len(self.vertices) > 0xFFFF or 3 * len(self.triangles) > 0xFFFF:
            raise ValueError("a shape holds at most 65535 vertices and half-edges; split it")
        for tri in self.triangles:
            if len(set(tri)) != 3 or any(not 0 <= i < len(self.vertices) for i in tri):
                raise ValueError(f"bad triangle {tri}")

    def half_edges(self):
        for f, tri in enumerate(self.triangles):
            for k in range(3):
                yield 3 * f + k, tri[k], tri[(k + 1) % 3]


@dataclass
class Part:
    shape: int                          # index into the resource's shapes
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)  # x, y, z, w
    scale: float = 1.0
    material: int = 0                   # packed: bits 0-5 flags, 6-18 material slot, 19-31 property slot
    kind: int = KIND_TRIANGLE_MESH
    hashes: tuple[int, int] = DEFAULT_PART_HASHES
    # Words not yet understood, kept so a rewritten file carries the source's values.
    # +0x34 differs per part in multi-part files (2, 0, 1 in one); writing 0 for all crashed the game.
    word_20: int = 0
    float_30: float = DEFAULT_PART_FLOAT
    word_34: int = 0
    word_3c: int = 0x7FFFFFFF
    word_44: int = 2048

    def to_world(self, p):
        x, y, z = (c * self.scale for c in p)
        qx, qy, qz, qw = self.rotation
        # v' = v + 2w(q x v) + 2 q x (q x v)
        tx = 2 * (qy * z - qz * y); ty = 2 * (qz * x - qx * z); tz = 2 * (qx * y - qy * x)
        rx = x + qw * tx + (qy * tz - qz * ty)
        ry = y + qw * ty + (qz * tx - qx * tz)
        rz = z + qw * tz + (qx * ty - qy * tx)
        return (rx + self.translation[0], ry + self.translation[1], rz + self.translation[2])


def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _len(a): return math.sqrt(_dot(a, a))


def _key(p):
    return (round(p[0] / WELD), round(p[1] / WELD), round(p[2] / WELD))


def compute_edge_data(shapes: list[Shape], parts: list[Part]) -> None:
    """Fills slots 4-6 for every shape, finding twins by position over all parts given.

    Pass every part of the area together (and neighbouring areas' parts if they share
    edges): the game's edge data is computed across shape and file boundaries.
    """
    world_normals = {}
    index = {}
    for p_i, part in enumerate(parts):
        if part.shape < 0:
            continue
        shape = shapes[part.shape]
        world = [part.to_world(v) for v in shape.vertices]
        for f, (a, b, c) in enumerate(shape.triangles):
            world_normals[(p_i, f)] = _cross(_sub(world[b], world[a]), _sub(world[c], world[a]))
        for h, a, b in shape.half_edges():
            index.setdefault((_key(world[a]), _key(world[b])), []).append((p_i, h // 3))
    done = set()
    for p_i, part in enumerate(parts):
        if part.shape < 0 or part.shape in done:
            continue  # primitives have no edges; a shape used by two parts gets the first part's data
        done.add(part.shape)
        shape = shapes[part.shape]
        world = [part.to_world(v) for v in shape.vertices]
        values, folds, flats = [], [], []
        for h, a, b in shape.half_edges():
            twins = [t for t in index.get((_key(world[b]), _key(world[a])), []) if t != (p_i, h // 3)]
            if not twins:
                values.append(1.0); folds.append(False); flats.append(False)
                continue
            n1 = world_normals[(p_i, h // 3)]
            n2 = world_normals[twins[0]]
            angle = math.degrees(math.atan2(_len(_cross(n1, n2)), _dot(n1, n2)))
            side = _dot(_cross(n1, n2), _sub(world[b], world[a]))
            if angle <= FLAT_DEGREES:
                values.append(1.0); folds.append(False); flats.append(True)
            elif side > 0:
                values.append(math.cos(math.radians(angle) / 2)); folds.append(False); flats.append(False)
            else:
                values.append(1.0); folds.append(angle < FOLD_LIMIT_DEGREES); flats.append(False)
        shape.edge_value, shape.edge_fold, shape.edge_flat = values, folds, flats


def _bits(flags: list[bool]) -> bytes:
    out = bytearray(((len(flags) + 31) // 32) * 4)
    for i, on in enumerate(flags):
        if on:
            out[i >> 3] |= 1 << (i & 7)
    return bytes(out)


def _shape_arrays(shape: Shape) -> list[bytes | None]:
    v, f = len(shape.vertices), len(shape.triangles)
    h = 3 * f
    if len(shape.edge_value) != h:
        raise ValueError("edge data missing: call compute_edge_data first")
    starts = [a for _, a, _ in shape.half_edges()]
    by_vertex: list[list[int]] = [[] for _ in range(v)]
    for he, a in enumerate(starts):
        by_vertex[a].append(he)
    run, offsets = [], [0]
    for hes in by_vertex:
        run += hes
        offsets.append(len(run))
    slots: list[bytes | None] = [None] * 12
    slots[0] = struct.pack(f"<{3 * v}f", *(c for p in shape.vertices for c in p))
    slots[1] = struct.pack(f"<{v + 1}H", *offsets)
    slots[2] = struct.pack(f"<{h}H", *run)
    slots[3] = struct.pack(f"<{h}H", *starts)
    slots[4] = struct.pack(f"<{h}f", *shape.edge_value)
    slots[5] = _bits(shape.edge_fold)
    slots[6] = _bits(shape.edge_flat)
    slots[7] = struct.pack(f"<{f + 1}H", *range(0, h + 1, 3))
    if f >= 2:
        slots[11] = bytes(8 * (f - 1))  # zeroed tree nodes: tested in game, see FORMAT-NOTES 2.4
    return slots


def _box(points):
    if not points:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    return tuple(min(p[i] for p in points) for i in range(3)), tuple(max(p[i] for p in points) for i in range(3))


def write_physics(shapes: list[Shape], parts: list[Part], align: int = 16) -> bytes:
    """A version-2 PhysicsResource holding triangle-mesh shapes and their parts."""
    shape_table = 0x84
    part_table = shape_table + SHAPE_SIZE * len(shapes)
    data = part_table + PART_SIZE * len(parts)
    out = bytearray(data)
    struct.pack_into("<IIII", out, 0, 2, MAGIC, 1, len(parts))
    struct.pack_into("<I", out, 0x18, len(shapes))
    for at, target in ((0x24, 0x38), (0x28, shape_table), (0x2C, part_table), (0x30, part_table)):
        struct.pack_into("<I", out, at, target - at)
    first_array = None
    out[0x38:0x84] = _V2_BLOCK
    for s_i, shape in enumerate(shapes):
        rec = shape_table + s_i * SHAPE_SIZE
        lo, hi = _box(shape.vertices)
        f = len(shape.triangles)
        struct.pack_into("<6f4HI", out, rec, *lo, *hi, len(shape.vertices), 3 * f, f, f - 1 if f >= 2 else 0, 0)
        for slot, blob in enumerate(_shape_arrays(shape)):
            if blob is None:
                continue
            start = (len(out) + align - 1) // align * align
            out += bytes(start - len(out)) + blob
            first_array = start if first_array is None else first_array
            pointer = rec + 36 + 4 * slot
            struct.pack_into("<I", out, pointer, start - pointer)
    struct.pack_into("<I", out, 0x34, (first_array if first_array is not None else data) - 0x34)
    for p_i, part in enumerate(parts):
        rec = part_table + p_i * PART_SIZE
        struct.pack_into("<4f3ffIIIIfIIIII", out, rec, *part.rotation, *part.translation, part.scale, part.word_20,
                         part.kind, part.hashes[0], part.hashes[1], part.float_30, part.word_34, part.shape,
                         part.word_3c, part.material, part.word_44)
    return bytes(out)


def _ptr(data, at):
    (value,) = struct.unpack_from("<I", data, at)
    return at + value if value else None


def read_physics(data: bytes) -> tuple[list[Shape], list[Part], int]:
    """Reads shapes and every part (in file order; shape -1 for a primitive). Returns (shapes, parts, version).

    Shapes with non-triangle faces or without slots 4-6 come back with empty edge data and
    triangles fanned from each face's first vertex; enough for tests and inspection.
    """
    version, magic, _, part_count = struct.unpack_from("<IIII", data, 0)
    if magic != MAGIC:
        raise ValueError("not a PhysicsResource")
    (shape_count,) = struct.unpack_from("<I", data, 0x18)
    shape_table, part_table = _ptr(data, 0x28), _ptr(data, 0x30)
    shapes = []
    for s_i in range(shape_count):
        rec = shape_table + s_i * SHAPE_SIZE
        v, h, f, _ = struct.unpack_from("<4H", data, rec + 24)
        slots = [_ptr(data, rec + 36 + 4 * k) for k in range(12)]
        verts = [struct.unpack_from("<3f", data, slots[0] + 12 * i) for i in range(v)]
        starts = struct.unpack_from(f"<{h}H", data, slots[3])
        face_at = struct.unpack_from(f"<{f + 1}H", data, slots[7])
        tris = []
        for k in range(f):
            loop = starts[face_at[k]:face_at[k + 1]]
            tris += [(loop[0], loop[j], loop[j + 1]) for j in range(1, len(loop) - 1)]
        shape = Shape.__new__(Shape)
        shape.vertices, shape.triangles = verts, tris
        shape.edge_value, shape.edge_fold, shape.edge_flat = [], [], []
        if slots[4] and slots[5] and slots[6] and h == 3 * f:
            shape.edge_value = list(struct.unpack_from(f"<{h}f", data, slots[4]))
            shape.edge_fold = [bool(data[slots[5] + (i >> 3)] >> (i & 7) & 1) for i in range(h)]
            shape.edge_flat = [bool(data[slots[6] + (i >> 3)] >> (i & 7) & 1) for i in range(h)]
        shapes.append(shape)
    parts = []
    for p_i in range(part_count):
        rec = part_table + p_i * PART_SIZE
        fields = struct.unpack_from("<4f3ffIIIIfIIIII", data, rec)
        # rotation 0-3, translation 4-6, scale 7, word 8, kind 9, hashes 10-11, float 12, word 13,
        # shape 14, word 15, material 16, word 17
        # An analytic primitive has no shape (index -1); it is kept so part indices match the file.
        parts.append(Part(-1 if fields[14] == 0xFFFFFFFF else fields[14], fields[4:7], fields[0:4], fields[7], fields[16], fields[9],
                          (fields[10], fields[11]), fields[8], fields[12], fields[13], fields[15], fields[17]))
    return shapes, parts, version


def replace_shape(data: bytes, shape_index: int, shape: Shape, align: int = 16) -> bytes:
    """Replaces one shape of an existing resource (version 0 or 2) by appending its arrays
    at the end of the file and repointing the shape record: the method tested in game with
    the ramp (FORMAT-NOTES 2.5). Fill the shape's edge data first."""
    version, magic = struct.unpack_from("<II", data, 0)
    if magic != MAGIC:
        raise ValueError("not a PhysicsResource")
    (shape_count,) = struct.unpack_from("<I", data, 0x18)
    if not 0 <= shape_index < shape_count:
        raise IndexError("shape index out of range")
    out = bytearray(data)
    rec = _ptr(data, 0x28) + shape_index * SHAPE_SIZE
    lo, hi = _box(shape.vertices)
    f = len(shape.triangles)
    struct.pack_into("<6f4HI", out, rec, *lo, *hi, len(shape.vertices), 3 * f, f, f - 1 if f >= 2 else 0, 0)
    for slot, blob in enumerate(_shape_arrays(shape)):
        pointer = rec + 36 + 4 * slot
        if blob is None:
            struct.pack_into("<I", out, pointer, 0)
            continue
        start = (len(out) + align - 1) // align * align
        out += bytes(start - len(out)) + blob
        struct.pack_into("<I", out, pointer, start - pointer)
    return bytes(out)
