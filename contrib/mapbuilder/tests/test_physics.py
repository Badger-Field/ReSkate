import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reskate_mapbuilder.physics import (MAGIC, Part, Shape, compute_edge_data, read_physics,  # noqa: E402
                                        write_physics)


def grid(n=3, size=1.0, height=lambda x, z: 0.0):
    verts = [(i * size, height(i * size, j * size), j * size) for j in range(n + 1) for i in range(n + 1)]
    tris = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            b, c, d = a + 1, a + n + 1, a + n + 2
            # counter-clockwise seen from above (+y): normals point up
            tris += [(a, c, b), (b, c, d)]
    return Shape(verts, tris)


def test_normals_point_up_for_grid():
    s = grid()
    for a, b, c in s.triangles:
        pa, pb, pc = s.vertices[a], s.vertices[b], s.vertices[c]
        u = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
        v = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
        assert u[2] * v[0] - u[0] * v[2] > 0


def test_flat_ground_edges():
    s = grid()
    compute_edge_data([s], [Part(0)])
    interior = [i for i, (_, a, b) in enumerate(s.half_edges())
                if not (all(s.vertices[x][0] in (0, 3) for x in (a, b)) and s.vertices[a][0] == s.vertices[b][0])
                and not (all(s.vertices[x][2] in (0, 3) for x in (a, b)) and s.vertices[a][2] == s.vertices[b][2])]
    for i in range(len(s.triangles) * 3):
        if i in interior:
            assert s.edge_flat[i] and not s.edge_fold[i] and s.edge_value[i] == 1.0
        else:
            assert not s.edge_flat[i] and not s.edge_fold[i] and s.edge_value[i] == 1.0


def _folded(s):
    return [i for i in range(len(s.edge_value)) if not s.edge_flat[i] and (s.edge_fold[i] or s.edge_value[i] < 1.0)]


def test_ridge_gets_cos_half_angle_on_both_halves():
    # Two slopes meeting in a ridge at x = 1 (convex): both half-edges of each ridge edge are
    # on the positive side, so they carry cos(angle / 2) and no fold bit.
    s = grid(2, 1.0, lambda x, z: 0.5 - abs(x - 1.0) * 0.5)
    compute_edge_data([s], [Part(0)])
    folded = _folded(s)
    angle = 2 * math.degrees(math.atan(0.5))
    assert len(folded) == 4
    assert all(abs(s.edge_value[i] - math.cos(math.radians(angle) / 2)) < 1e-9 for i in folded)
    assert not any(s.edge_fold)


def test_valley_gets_the_fold_bit():
    s = grid(2, 1.0, lambda x, z: abs(x - 1.0) * 0.5)
    compute_edge_data([s], [Part(0)])
    folded = _folded(s)
    assert len(folded) == 4
    assert all(s.edge_fold[i] and s.edge_value[i] == 1.0 for i in folded)


def test_folds_of_90_degrees_or_more_get_no_bit():
    # A floor meeting a wall that rises from its far edge: a 90 degree valley.
    floor = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (1.0, 1.0, 0.0), (1.0, 1.0, 1.0)]
    s = Shape(floor, [(0, 2, 1), (1, 2, 3), (1, 3, 5), (1, 5, 4)])
    compute_edge_data([s], [Part(0)])
    seam = [i for i, (_, a, b) in enumerate(s.half_edges()) if {a, b} == {1, 3}]
    assert len(seam) == 2
    assert all(not s.edge_fold[i] and not s.edge_flat[i] and s.edge_value[i] == 1.0 for i in seam)


def test_twins_found_across_parts():
    left, right = grid(1), grid(1)
    parts = [Part(0, (0.0, 0.0, 0.0)), Part(1, (1.0, 0.0, 0.0))]
    compute_edge_data([left, right], parts)
    # The shared edge x = 1 is flat across the two shapes.
    assert sum(left.edge_flat) == 3 and sum(right.edge_flat) == 3  # its diagonal pair + 1 seam edge each


def test_write_read_round_trip():
    shapes = [grid(), grid(2, 2.0, lambda x, z: 0.2 * x)]
    parts = [Part(0, (10.0, 199.0, 20.0), material=0x1C0), Part(1, (30.0, 199.0, 20.0))]
    compute_edge_data(shapes, parts)
    data = write_physics(shapes, parts)
    assert struct.unpack_from("<II", data, 0) == (2, MAGIC)
    back, back_parts, version = read_physics(data)
    assert version == 2 and len(back) == 2
    f32 = lambda x: struct.unpack("<f", struct.pack("<f", x))[0]
    for a, b in zip(shapes, back):
        assert [tuple(map(f32, v)) for v in a.vertices] == [tuple(v) for v in b.vertices]
        assert a.triangles == b.triangles
        assert a.edge_fold == b.edge_fold and a.edge_flat == b.edge_flat
        assert all(abs(x - y) < 1e-6 for x, y in zip(a.edge_value, b.edge_value))
    assert [(p.shape, p.translation, p.material) for p in back_parts] == [(0, (10.0, 199.0, 20.0), 0x1C0),
                                                                          (1, (30.0, 199.0, 20.0), 0)]
    assert write_physics(back, back_parts) == data


def test_empty_resource_is_132_bytes():
    assert len(write_physics([], [])) == 0x84


def test_vertex_runs_and_face_offsets():
    s = grid(1)
    compute_edge_data([s], [Part(0)])
    data = write_physics([s], [Part(0)])
    rec = 0x84
    v, h, f, n = struct.unpack_from("<4H", data, rec + 24)
    assert (v, h, f, n) == (4, 6, 2, 1)
    slot = lambda k: rec + 36 + 4 * k + struct.unpack_from("<I", data, rec + 36 + 4 * k)[0]
    offsets = struct.unpack_from(f"<{v + 1}H", data, slot(1))
    run = struct.unpack_from(f"<{h}H", data, slot(2))
    starts = struct.unpack_from(f"<{h}H", data, slot(3))
    assert offsets[0] == 0 and offsets[-1] == h
    for vert in range(v):
        assert all(starts[e] == vert for e in run[offsets[vert]:offsets[vert + 1]])
    assert struct.unpack_from("<3H", data, slot(7)) == (0, 3, 6)
    assert data[slot(11):slot(11) + 8] == bytes(8)
