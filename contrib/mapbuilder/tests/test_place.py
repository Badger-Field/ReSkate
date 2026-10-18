"""new-place-level: a real place's collision from the lidar-parks tile export (reskate-level-tiles
v2), on a small synthetic tile directory written here (FORMAT-NOTES 6.4)."""
import json
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_level import LEVEL, NEW, fake_game  # noqa: E402
from reskate_mapbuilder.__main__ import main  # noqa: E402
from reskate_mapbuilder.game import Game  # noqa: E402
from reskate_mapbuilder.physics import Part, read_physics  # noqa: E402
from reskate_mapbuilder.place import PlaceError, check_spots, collision, read_place  # noqa: E402
from reskate_mapbuilder.toc import read_toc  # noqa: E402

SUBLEVEL = f"levels/game/{LEVEL}/ground_sublevel"


def grid(x0, z0, size, n, y=0.0):
    """A welded square of n x n cells, its triangles wound up (the exporter's convention)."""
    step = size / n
    vertices = [(x0 + i * step, y, z0 + j * step) for j in range(n + 1) for i in range(n + 1)]
    triangles = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            triangles += [(a, a + n + 1, a + 1), (a + 1, a + n + 1, a + n + 2)]
    return vertices, triangles


def write_tiles(root: Path, version=2, checks=None, spawn=(5.0, 0.0, 5.0), corrupt=None):
    """Tile (0, 0): ground 50 x 50 m in two chunks sharing an edge (bit-identical vertices), a
    ledge top 0.5 m high on 10..12 x 10..12, and a visual object. Tile (-1, 0): ground in one chunk."""
    root.mkdir(parents=True, exist_ok=True)
    tiles = {
        (0, 0): [("ground", "collision", "ground", "ground", _rows(0.0), 0, 2),
                 ("ground", "collision", "ground", "ground", _rows(25.0), 1, 2),
                 ("spot_1_ledge", "collision", "concrete", "ledge", grid(10, 10, 2, 1, 0.5), 0, 1),
                 ("ground", "visual", "ground", "ground", grid(0, 0, 50, 2), 0, 1)],
        (-1, 0): [("ground", "collision", "ground", "ground", grid(-50, 0, 50, 2), 0, 1)],
    }
    listed = []
    for (i, j), objects in tiles.items():
        stem = f"tile_{i}_{j}"
        blob, described = bytearray(), []
        for name, role, material, part, (vertices, triangles), k, n in objects:
            entry = {"name": name, "role": role, "material": material, "part": part, "source": "test",
                     "vertices": {"offset": len(blob), "count": len(vertices)}}
            blob += struct.pack(f"<{3 * len(vertices)}f", *(c for v in vertices for c in v))
            entry["indices"] = {"offset": len(blob), "count": 3 * len(triangles)}
            blob += struct.pack(f"<{3 * len(triangles)}I", *(c for t in triangles for c in t))
            if role == "collision":
                entry.update(chunk=k, chunks=n)
            described.append(entry)
        if corrupt == "short" and (i, j) == (0, 0):
            blob = blob[:-8]
        if corrupt == "index" and (i, j) == (-1, 0):
            blob[-4:] = struct.pack("<I", 999)
        (root / f"{stem}.bin").write_bytes(bytes(blob))
        (root / f"{stem}.json").write_text(json.dumps({"tile": [i, j], "bin": f"{stem}.bin", "objects": described}))
        listed.append({"tile": [i, j], "json": f"{stem}.json", "bin": f"{stem}.bin"})
    manifest = {"format": "reskate-level-tiles", "version": version, "units": "metres", "tile_size": 50.0,
                "origin": {"lat": 37.795, "lon": -122.395, "elevation": 3.0}, "collision_chunk_triangles": 256,
                "collision_checks": checks or {"edges_same_direction_twice": 0, "edges_shared_by_3_or_more": 0},
                "spawn": list(spawn), "grinds": [], "tiles": listed, "attribution": ["test"]}
    (root / "level.json").write_text(json.dumps(manifest))
    return root


def _rows(z0):
    """Half of tile (0, 0)'s ground, 50 x 25 m from z0: the two halves share the row z = 25."""
    vertices, triangles = grid(0, z0, 50, 2)
    vertices = [(x, y, z0 + (z - z0) / 2) for x, y, z in vertices]       # 50 x 25 m
    return vertices, triangles


def test_reads_the_tiles_and_finds_the_ground(tmp_path):
    place = read_place(write_tiles(tmp_path / "t"))
    assert len(place.collision) == 4 and len(place.visual) == 1
    assert sum(len(p.triangles) for p in place.collision) == 8 + 8 + 2 + 8
    lo, hi = place.bounds()
    assert lo == (-50.0, 0.0, 0.0) and hi == (50.0, 0.5, 50.0)
    assert place.ground(11.0, 11.0) == 0.5 and place.ground(5.0, 5.0) == 0.0 and place.ground(-25.0, 30.0) == 0.0
    assert place.ground(80.0, 10.0) is None
    for piece in place.collision:                                    # wound up, as the exporter says
        for a, b, c in piece.triangles:
            pa, pb, pc = (piece.vertices[i] for i in (a, b, c))
            u = [pb[i] - pa[i] for i in range(3)]
            v = [pc[i] - pa[i] for i in range(3)]
            assert u[2] * v[0] - u[0] * v[2] > 0
    assert place.spawn == (5.0, 0.0, 5.0)


def test_mirror_flips_north_and_keeps_the_winding(tmp_path):
    place = read_place(write_tiles(tmp_path / "t"), mirror=True)
    lo, hi = place.bounds()
    assert lo[2] == -50.0 and hi[2] == 0.0 and place.spawn == (5.0, 0.0, -5.0)
    assert place.ground(11.0, -11.0) == 0.5
    for piece in place.collision:
        for a, b, c in piece.triangles:
            pa, pb, pc = (piece.vertices[i] for i in (a, b, c))
            assert (pb[2] - pa[2]) * (pc[0] - pa[0]) - (pb[0] - pa[0]) * (pc[2] - pa[2]) > 0


@pytest.mark.parametrize("kind, message", [
    ("version", "version 1; this builder reads version 2 only"),
    ("format", "not 'reskate-level-tiles'"),
    ("checks", "edges a half-edge shape cannot hold"),
    ("short", "runs past the end of the .bin"),
    ("index", "an index is past its"),
    ("missing", "level.json not found"),
])
def test_refuses_bad_tiles(tmp_path, kind, message):
    root = tmp_path / "t"
    if kind == "missing":
        root.mkdir()
    else:
        write_tiles(root, version=1 if kind == "version" else 2,
                    checks={"edges_shared_by_3_or_more": 2} if kind == "checks" else None,
                    corrupt=kind if kind in ("short", "index") else None)
        if kind == "format":
            manifest = json.loads((root / "level.json").read_text())
            manifest["format"] = "something-else"
            (root / "level.json").write_text(json.dumps(manifest))
    with pytest.raises(PlaceError, match=message):
        read_place(root)


def test_collision_is_one_part_per_chunk(tmp_path):
    place = read_place(write_tiles(tmp_path / "t"))
    shapes, parts = collision(place, Part(0, material=0x42, word_3c=7))
    assert len(shapes) == len(parts) == 4
    assert [p.shape for p in parts] == [0, 1, 2, 3] and [p.word_34 for p in parts] == [0, 1, 2, 3]
    assert all(p.material == 0x42 and p.word_3c == 7 and p.translation == (0.0, 0.0, 0.0) for p in parts)
    spots = check_spots(place, (0.0, 300.0, 0.0))
    assert spots[0] == ("spawn", 5.0, 5.0, 300.0)
    assert ("origin", 0.0, 0.0, 300.0) in spots and spots[-1][3] is None


def test_new_place_level_builds_the_collision(tmp_path, capsys):
    game_dir = fake_game(tmp_path / "game")
    tiles = write_tiles(tmp_path / "tiles")
    main(["new-place-level", "--game", str(game_dir), "--mods", str(tmp_path / "Mods"), "--name", "zz-place",
          "--level", LEVEL, "--tiles", str(tiles), "--at", "0", "300", "0"])
    out = capsys.readouterr().out
    assert "2 tiles (tile size 50.0 m), 4 collision chunks, 26 collision triangles" in out
    assert "bounds in the game: [-50.0, 300.0, 0.0]..[50.0, 300.5, 50.0]" in out
    assert "ours: version 2, 4 parts, one per collision chunk" in out
    assert "trainer ground 5.0 5.0    -> 300.0    (spawn)" in out
    assert "(outside, 20 m east of the bounds)" in out
    root = tmp_path / "Mods" / "zz-place"
    view = Game(game_dir, mod=root)
    toc = read_toc((root / "Win32" / (NEW + ".toc")).read_bytes())
    _, region, manifest = view.bundle(toc, "win32/" + SUBLEVEL)
    shapes, parts, version = read_physics(view.payload(region, 1 + len(manifest.ebx)))
    assert version == 2 and len(shapes) == 4 and [p.word_34 for p in parts] == [0, 1, 2, 3]
    # The two ground chunks meet at z = 25: their shared edges are flat (twins found across shapes).
    first = shapes[0]
    seam = [h for h, (a, b, c) in enumerate(first.triangles) for k, (p, q) in enumerate(((a, b), (b, c), (c, a)))
            if first.vertices[p][2] == first.vertices[q][2] == 25.0 for h in [3 * h + k]]
    assert seam and all(first.edge_flat[h] for h in seam)


def test_new_place_level_refuses_with_a_clear_message(tmp_path):
    game_dir = fake_game(tmp_path / "game")
    tiles = write_tiles(tmp_path / "tiles", version=3)
    with pytest.raises(SystemExit, match="new-place-level: .*version 3"):
        main(["new-place-level", "--game", str(game_dir), "--name", "zz-place", "--level", LEVEL, "--tiles",
              str(tiles), "--dry-run"])
