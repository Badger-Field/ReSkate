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
from reskate_mapbuilder.place import (PlaceError, check_spots, collision, filter_collision, read_place,  # noqa: E402
                                      spawn_at)
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
                 ("buildings", "collision", "concrete", "buildings", grid(40, 40, 2, 1, 159.0), 0, 1),
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
                "spawn": list(spawn), "tiles": listed, "attribution": ["test"],
                "grinds": [{"surface": "concrete", "what": "ledge", "closed": False, "radius": 0.04,
                            "points": [[10.0, 0.5, 11.0], [12.0, 0.5, 11.0]]}]}
    (root / "level.json").write_text(json.dumps(manifest))
    return root


def _rows(z0):
    """Half of tile (0, 0)'s ground, 50 x 25 m from z0: the two halves share the row z = 25."""
    vertices, triangles = grid(0, z0, 50, 2)
    vertices = [(x, y, z0 + (z - z0) / 2) for x, y, z in vertices]       # 50 x 25 m
    return vertices, triangles


def test_reads_the_tiles_and_finds_the_ground(tmp_path):
    place = read_place(write_tiles(tmp_path / "t"))
    assert len(place.collision) == 5 and len(place.visual) == 1
    assert sum(len(p.triangles) for p in place.collision) == 8 + 8 + 2 + 2 + 8
    lo, hi = place.bounds()
    assert lo == (-50.0, 0.0, 0.0) and hi == (50.0, 159.0, 50.0)
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
    assert spawn_at(place) == (-5.0, 300.0, 5.0)
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
    shapes, parts, _ = collision(place, Part(0, material=0x42, word_3c=7))
    assert len(shapes) == len(parts) == 5
    assert [p.shape for p in parts] == [0, 1, 2, 3, 4] and [p.word_34 for p in parts] == [0, 1, 2, 3, 4]
    assert all(p.material == 0x42 and p.word_3c == 7 and p.translation == (0.0, 0.0, 0.0) for p in parts)
    shapes, parts, what = collision(place, Part(0, material=0x42), 3, [Part(0, material=m) for m in (1, 2, 3)])
    assert len(shapes) == 3 and [p.material for p in parts] == [1, 2, 3]                # by part: three kinds
    assert "3 part(s)" in what and sum(len(s.triangles) for s in shapes) == 28
    shapes, parts, _ = collision(place, Part(0), 1)
    assert len(shapes) == 1 and len(shapes[0].triangles) == 28 and parts[0].word_34 == 0
    spots = check_spots(place, (0.0, 300.0, 0.0))
    assert spots[0] == ("spawn", 5.0, 5.0, 300.0)
    assert ("origin", 0.0, 0.0, 300.0) in spots and spots[-1][3] is None
    assert ("grind 0 (ledge, concrete) midpoint, its top 300.50", 11.0, 11.0, 300.5) in spots


def test_new_place_level_builds_the_collision(tmp_path, capsys):
    game_dir = fake_game(tmp_path / "game")
    tiles = write_tiles(tmp_path / "tiles")
    main(["new-place-level", "--game", str(game_dir), "--mods", str(tmp_path / "Mods"), "--name", "zz-place",
          "--level", LEVEL, "--tiles", str(tiles), "--at", "0", "300", "0", "--skip-part", "buildings"])
    out = capsys.readouterr().out
    assert "2 tiles (tile size 50.0 m), 4 collision chunks, 26 collision triangles" in out
    assert "dropped 1 collision chunk(s) of buildings (skipped part buildings)" in out
    assert "bounds in the game: [-50.0, 300.0, 0.0]..[50.0, 300.5, 50.0]" in out
    assert "ours: version 2, 1 part(s) as the template's .physics has, each one shape merging its chunks (4 chunks " \
           "of ['ground', 'ledge'])" in out
    assert "physics parts: ours 1, the template's 1: consistent" in out
    assert "trainer ground 5.0 5.0    -> 300.0    (spawn)" in out
    assert "(outside, 20 m east of the bounds)" in out
    root = tmp_path / "Mods" / "zz-place"
    view = Game(game_dir, mod=root)
    toc = read_toc((root / "Win32" / (NEW + ".toc")).read_bytes())
    _, region, manifest = view.bundle(toc, "win32/" + SUBLEVEL)
    shapes, parts, version = read_physics(view.payload(region, 1 + len(manifest.ebx)))
    assert version == 2 and len(shapes) == 1 and [p.word_34 for p in parts] == [0]
    merged = shapes[0]
    assert len(merged.vertices) == 25 and len(merged.triangles) == 26          # shared vertices welded
    # The two ground chunks met at z = 25: inside the merged shape those edges are flat.
    seam = [3 * h + k for h, (a, b, c) in enumerate(merged.triangles) for k, (p, q) in enumerate(((a, b), (b, c), (c, a)))
            if merged.vertices[p][2] == merged.vertices[q][2] == 25.0 and merged.vertices[p][0] >= 0
            and merged.vertices[q][0] >= 0]
    assert seam and all(merged.edge_flat[h] for h in seam)


def test_grow_makes_the_blueprint_declare_every_chunk(tmp_path, capsys):
    from reskate_mapbuilder.ebx import read_document
    game_dir = fake_game(tmp_path / "game")
    tiles = write_tiles(tmp_path / "tiles")
    main(["new-place-level", "--game", str(game_dir), "--mods", str(tmp_path / "Mods"), "--name", "zz-place",
          "--level", LEVEL, "--tiles", str(tiles), "--at", "0", "300", "0", "--skip-part", "buildings", "--parts", "grow"])
    out = capsys.readouterr().out
    assert "4 parts, one per collision chunk" in out and "from 1 to 4 entries" in out
    root = tmp_path / "Mods" / "zz-place"
    view = Game(game_dir, mod=root)
    toc = read_toc((root / "Win32" / (NEW + ".toc")).read_bytes())
    _, region, manifest = view.bundle(toc, "win32/" + SUBLEVEL)
    bp = read_document(view.ebx(region, manifest, f"levels/game/{LEVEL}/ground_bp"))
    entity = bp.instances[1].object
    boxes = entity.get("PartBoundingBoxes")
    assert len(entity.get("PhysicsPartInfos")) == 4 and len(boxes) == 4
    assert [boxes[2].get("Min").get(a) for a in "xyz"][0] == 10.0                  # the ledge chunk's box
    assert round(boxes[2].get("Max").get("y"), 2) == 0.55


def test_a_part_count_mismatch_is_refused(tmp_path):
    from reskate_mapbuilder.ground import new_ground_level
    from reskate_mapbuilder.mod import ModBuilder
    game = Game(fake_game(tmp_path / "game"))
    place = read_place(write_tiles(tmp_path / "t"))
    with pytest.raises(ValueError, match="our collision has 5 parts but the template's own .physics has 1"):
        new_ground_level(game, ModBuilder(game, "zz"), LEVEL, collision=lambda t, ts, n: collision(place, t))


def test_new_place_level_refuses_with_a_clear_message(tmp_path):
    game_dir = fake_game(tmp_path / "game")
    tiles = write_tiles(tmp_path / "tiles", version=3)
    with pytest.raises(SystemExit, match="new-place-level: .*version 3"):
        main(["new-place-level", "--game", str(game_dir), "--name", "zz-place", "--level", LEVEL, "--tiles",
              str(tiles), "--dry-run"])


def test_tall_chunks_are_noted_and_can_be_dropped(tmp_path):
    root = write_tiles(tmp_path / "t")
    place = read_place(root)
    notes = filter_collision(place)
    assert len(place.collision) == 5 and any("NOTE: 1 collision chunk(s) of 'buildings' rise to 159.0 m" in n for n in notes)
    place = read_place(root)
    notes = filter_collision(place, max_height=20)
    assert len(place.collision) == 4 and notes == ["  dropped 1 collision chunk(s) of buildings (top 159.0 m above 20 m)"]
    with pytest.raises(PlaceError, match="every collision chunk"):
        filter_collision(read_place(root), max_height=-1)


def test_place_info_needs_no_game_and_puts_the_spawn_at_the_origin(tmp_path, capsys):
    tiles = write_tiles(tmp_path / "t", spawn=(5.0, 0.25, 7.0))
    main(["place-info", "--tiles", str(tiles), "--max-height", "20"])
    out = capsys.readouterr().out
    assert "--at -5 300 -7: the export's spawn (5.0, 0.25, 7.0) goes to the game's (0, 300.25, 0)" in out
    assert "trainer ground 0.0 0.0    -> 300.0    (spawn)" in out and "4 collision chunks" in out
    with pytest.raises(SystemExit, match="place-info: .*not found"):
        main(["place-info", "--tiles", str(tmp_path / "nothing")])


def test_visuals_get_a_blueprint_mesh_and_harmless_collision_each(tmp_path, capsys):
    from reskate_mapbuilder.ebx import read_document
    from reskate_mapbuilder.meshset import read_layout
    from test_level import BOX, GROUNDPLANE
    game_dir = fake_game(tmp_path / "game")
    tiles = write_tiles(tmp_path / "tiles")
    main(["new-place-level", "--game", str(game_dir), "--mods", str(tmp_path / "Mods"), "--name", "zz-place",
          "--level", LEVEL, "--tiles", str(tiles), "--at", "0", "300", "0", "--skip-part", "buildings", "--visuals",
          "--visual-template", BOX])
    out = capsys.readouterr().out
    assert "visual objects: 1 of 1: ground_t0_0 (ground, 9 vertices, 8 triangles)" in out
    assert f"Visual template {BOX}" in out and "3 collision parts" in out
    # The real combination: the ground's template (util_groundplane) has 1 part, the visual template
    # (the box) 3 parts under one PhysicsPartInfos entry; each is checked against its own template.
    assert "physics parts: ours 1, the template's 1: consistent" in out
    assert "visual ground_t0_0: 9 vertices, 8 triangles" in out and "physics parts: ours 3, the template's 3" in out
    assert "PhysicsPartInfos[0]: PartIndex=0" in out
    root = tmp_path / "Mods" / "zz-place"
    view = Game(game_dir, mod=root)
    toc = read_toc((root / "Win32" / (NEW + ".toc")).read_bytes())
    _, region, manifest = view.bundle(toc, "win32/" + NEW)
    base = f"levels/game/{LEVEL}/visual_ground_t0_0"
    names = [a.name for a in manifest.ebx]
    assert {base + "_bp", base + "_mesh", base + "_bp.physics"} <= set(names)
    resources = {a.name: 1 + len(manifest.ebx) + i for i, a in enumerate(manifest.resources)}
    shapes, parts, _ = read_physics(view.payload(region, resources[base + "_bp.physics"]))
    assert len(parts) == 3 and all(v[1] == -500.0 for s in shapes for v in s.vertices)      # not standable
    layout = read_layout(view.payload(region, resources[base + "_mesh"]))
    assert layout[0].sections[0].vertex_count == 9 and layout[0].sections[1].vertex_count == 1
    bp = read_document(view.ebx(region, manifest, base + "_bp"))
    # The layer places our collision blueprint and the visual one, both at --at.
    _, region, manifest = view.bundle(toc, "win32/" + SUBLEVEL)
    layer = read_document(view.ebx(region, manifest, f"levels/game/{LEVEL}/ground_layer"))
    placed = [layer.instances[p.index].object for p in layer.instances[0].object.get("Objects")]
    assert len(placed) == 2 and layer.imports[placed[1].get("Blueprint").index].file_guid == bp.file_guid
    assert [placed[1].get("BlueprintTransform").get("trans").get(a) for a in "xyz"] == [0, 300, 0]
    # A donor of another mesh, and a filter matching nothing.
    main(["new-place-level", "--game", str(game_dir), "--name", "zz-place", "--level", LEVEL, "--tiles", str(tiles),
          "--visuals", "--visual-template", BOX, "--material-donor", f"ground={GROUNDPLANE}_mesh:1", "--dry-run"])
    out = capsys.readouterr().out
    assert f"material donors: ground = {GROUNDPLANE}_mesh:1" in out and f"material {GROUNDPLANE}_mesh:1 ->" in out
    with pytest.raises(SystemExit, match="no visual object matches"):
        main(["new-place-level", "--game", str(game_dir), "--name", "zz-place", "--level", LEVEL, "--tiles", str(tiles),
              "--visuals", "--visuals-only", "nothing", "--dry-run"])


def test_resource_ids_keep_the_tag_bits_clear_and_filters_take_printed_names(tmp_path, capsys):
    from reskate_mapbuilder.ground import resource_id
    from test_level import BOX
    # The crash value of 5924574 was this name's id with tag bits 10.
    assert resource_id("levels/game/dingolevel_reskate_place/visual_ground_0_0_tm2_m1_mesh") == 0x14e9899ef8f39c30
    assert all(resource_id(f"levels/game/x/visual_{i}_mesh") & 3 == 0 for i in range(64))
    game_dir = fake_game(tmp_path / "game")
    tiles = write_tiles(tmp_path / "tiles")
    main(["new-place-level", "--game", str(game_dir), "--name", "zz-place", "--level", LEVEL, "--tiles", str(tiles),
          "--visuals", "--visual-template", BOX, "--visuals-only", "ground_t0_0", "--visual-slab", "6", "1", "6",
          "--dry-run"])
    out = capsys.readouterr().out
    assert "visual objects: 1 of 1 plus the slab: ground_t0_0 (ground, 9 vertices, 8 triangles), slab (ground, 24 vertices" in out
    assert "visual slab: 24 vertices, 12 triangles" in out


def test_place_info_views_give_a_spot_and_heading_per_visual_object(tmp_path, capsys):
    tiles = write_tiles(tmp_path / "t")
    main(["place-info", "--tiles", str(tiles), "--at", "0", "300", "0", "--views"])
    out = capsys.readouterr().out
    # The visual ground (0..50 x 0..50): ground south of it? none there (z < 0), so north at z 50 + 30.
    line = next(line for line in out.splitlines() if line.strip().startswith("ground_t0_0:"))
    assert "trainer ground" in line and "face heading" in line and "it spans y 300.0..300.0" in line


def test_material_donors_lists_sections_and_proposes_defaults(tmp_path, capsys):
    from test_level import GROUNDPLANE
    main(["material-donors", "--game", str(fake_game(tmp_path / "game")), "--contains", "mesh"])
    out = capsys.readouterr().out
    assert f"{GROUNDPLANE}_mesh:0  material '{GROUNDPLANE}_mesh'" in out and "class ['ground']" in out
    assert "(not usable" in out                                     # the shadow-like section without TexCoord0
    assert f"  --material-donor ground={GROUNDPLANE}_mesh:0" in out and "wood: no candidate" in out
