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


def write_tiles(root: Path, version=2, checks=None, spawn=(5.0, 0.0, 5.0), corrupt=None, extra=()):
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
    tiles[(0, 0)] += list(extra)
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
    assert entity.get("PartStates") == [0, 0, 0, 0] and entity.get("PartLinks") == []      # siblings grown; empty stays
    assert "PartLinks is empty: left empty" in out and "PartStates 4, PartLinks 0 (empty)" not in out
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


def test_resource_ids_are_odd_or_clear_and_filters_take_printed_names(tmp_path, capsys):
    from reskate_mapbuilder.ground import resource_id
    from test_level import BOX
    name = "levels/game/dingolevel_reskate_place/visual_ground_0_0_tm2_m1_mesh"
    # The crash value of 5924574 was this name's id with tag bits 10; "clear" is the 00 fallback.
    assert resource_id(name, style="clear") == 0x14e9899ef8f39c30
    assert resource_id(name, style="odd") == 0x14e9899ef8f39c33  # raw hash ...32 | 1
    assert resource_id(name) == resource_id(name, style="odd")  # stock ids are all odd, so that is the default
    names = [f"levels/game/x/visual_{i}_mesh" for i in range(64)]
    assert all(resource_id(n, style="clear") & 3 == 0 and resource_id(n, style="clear") for n in names)
    assert all(resource_id(n) & 1 == 1 for n in names)
    with pytest.raises(ValueError):
        resource_id(name, style="even")
    game_dir = fake_game(tmp_path / "game")
    tiles = write_tiles(tmp_path / "tiles")
    main(["new-place-level", "--game", str(game_dir), "--name", "zz-place", "--level", LEVEL, "--tiles", str(tiles),
          "--visuals", "--visual-template", BOX, "--visuals-only", "ground_t0_0", "--visual-slab", "6", "1", "6",
          "--dry-run"])
    out = capsys.readouterr().out
    assert "visual objects: 1 of 1 plus the slab: ground_t0_0 (ground, 9 vertices, 8 triangles), slab (ground, 24 vertices" in out
    assert "visual slab: 24 vertices, 12 triangles" in out
    assert "New resource ids: odd (low bit set" in out
    main(["new-place-level", "--game", str(game_dir), "--name", "zz-place", "--level", LEVEL, "--tiles", str(tiles),
          "--resource-ids", "clear", "--dry-run"])
    assert "New resource ids: clear (low two bits clear" in capsys.readouterr().out
    main(["new-place-level", "--game", str(game_dir), "--name", "zz-place", "--level", LEVEL, "--tiles", str(tiles),
          "--dry-run"])
    assert "New resource ids: odd" in capsys.readouterr().out  # each run sets the style again


def test_place_info_views_give_a_spot_and_heading_per_visual_object(tmp_path, capsys):
    tiles = write_tiles(tmp_path / "t")
    main(["place-info", "--tiles", str(tiles), "--at", "0", "300", "0", "--views"])
    out = capsys.readouterr().out
    # The visual ground (0..50 x 0..50): ground south of it? none there (z < 0), so north at z 50 + 30.
    line = next(line for line in out.splitlines() if line.strip().startswith("ground_t0_0:"))
    assert "trainer ground" in line and "face heading" in line and "it spans y 300.0..300.0" in line
    # No ground south or north of it in this export: west of it, facing east.
    assert "face heading 90" in line and "west of it" in line
    assert "after `trainer ground` the camera faces heading 0" in out


def test_material_donors_lists_sections_and_proposes_defaults(tmp_path, capsys):
    from test_level import GROUNDPLANE
    main(["material-donors", "--game", str(fake_game(tmp_path / "game")), "--contains", "mesh"])
    out = capsys.readouterr().out
    assert f"{GROUNDPLANE}_mesh:0  material '{GROUNDPLANE}_mesh'" in out and "class ['ground']" in out
    assert "(not usable" in out                                     # the shadow-like section without TexCoord0
    assert f"  --material-donor ground={GROUNDPLANE}_mesh:0" in out and "wood: no candidate" in out


def test_scan_materials_ranks_bundles_with_usable_materials(tmp_path, capsys):
    from reskate_mapbuilder import nativedb
    from test_level import BIG_PARK_BUNDLE, FTUE, PARK_BENCH, PARK_BUNDLE, PLAZA_BUNDLE, PLAZA_MESH, VISTA_BUNDLE
    game_dir = fake_game(tmp_path / "game")
    main(["scan-materials", "--game", str(game_dir)])                  # layout.toc's superbundles: no FTUE here
    assert "0 bundles with classed sections" in capsys.readouterr().out
    # A superbundle layout.toc lists but that is not on disk (a locale's, e.g. loc/wc) is skipped silently.
    layout = game_dir / "Data" / "layout.toc"
    layout.write_bytes(nativedb.add_superbundle(nativedb.add_superbundle(layout.read_bytes(), "Win32/loc/wc"),
                                                "Win32/" + FTUE))
    main(["scan-materials", "--game", str(game_dir)])
    out = capsys.readouterr().out
    assert "loc/wc" not in out and "not read" not in out and "4 bundles with classed sections" in out
    main(["scan-materials", "--game", str(game_dir), "--superbundle", FTUE, "--superbundle", "levels/game/nothing"])
    out = capsys.readouterr().out
    assert "levels/game/nothing: not read" in out                     # asked for by name: said
    # The vista's baked stone-and-bench material is never usable (and so not listed).
    assert "usable sections per class: concrete 5, stone 0, wood 2, metal 0" in out and VISTA_BUNDLE not in out
    assert f"{FTUE}:{PLAZA_BUNDLE}" in out and "1 depot(s), 1 usable" in out and "a sublevel bundle" in out
    assert f"    {PLAZA_MESH}:0  '{PLAZA_MESH}' ['concrete']" in out
    main(["scan-materials", "--game", str(game_dir), "--superbundle", FTUE, "--class", "wood"])
    out = capsys.readouterr().out
    assert "2 bundles with classed sections; usable sections per class: wood 2" in out
    assert PLAZA_BUNDLE not in out and f"    {PARK_BENCH}:0" in out and "pavement" not in out
    main(["scan-materials", "--game", str(game_dir), "--superbundle", FTUE, "--both", "--top", "1"])
    # (--both: auto_donors' rules: wood only from a material name with a wood word.)
    out = capsys.readouterr().out
    assert "2 bundles with classed sections (usable concrete or stone and usable wood)" in out
    assert PLAZA_BUNDLE not in out and f"{FTUE}:{PARK_BUNDLE}" in out and BIG_PARK_BUNDLE not in out


def test_scan_materials_every_lists_past_twelve(monkeypatch):
    from reskate_mapbuilder.materials import report
    row = {"superbundle": "s", "bundle": "b", "bytes": 2 ** 20, "depots": 1, "ebx": 1, "res": 1, "sublevel": True,
           "sections": [(f"m{i}", 0, "Wood_Accent", ["wood"], True, [8]) for i in range(15)]}
    assert "    ... 3 more (--every lists them all)" in report([row])
    lines = report([row], every=True)
    assert "    m14:0  'Wood_Accent' ['wood'] strides [8]" in lines and not any("more" in line for line in lines)


def test_material_bundle_auto_picks_the_best_tile_with_distinct_concrete_ground_and_wood(tmp_path, capsys,
                                                                                         monkeypatch):
    import reskate_mapbuilder.materials as materials
    from test_level import BOX, FTUE, PARK_BENCH, PARK_BUNDLE, PARK_PAVEMENT, PARK_STEP, VISTA_BUNDLE, VISTA_MESH
    monkeypatch.setattr(materials, "AUTO_MIN_BYTES", 0)                 # the fixture bundles are tiny
    game_dir = fake_game(tmp_path / "game")
    tiles = write_tiles(tmp_path / "tiles")
    args = ["new-place-level", "--game", str(game_dir), "--name", "zz-place", "--level", LEVEL, "--tiles", str(tiles),
            "--at", "0", "300", "0", "--skip-part", "buildings", "--visuals", "--visual-template", BOX,
            "--material-bundle", f"auto:{FTUE}", "--dry-run"]
    main(args)
    out = capsys.readouterr().out
    # The vista (the smallest bundle, its one baked material naming stone and a bench) is left out.
    assert f"--material-bundle auto: 3 sublevel bundles in {FTUE} (1 vista or proxy bundles left out)" in out
    assert VISTA_BUNDLE not in out and VISTA_MESH not in out
    assert f"--material-bundle auto: {FTUE}:{PARK_BUNDLE} (" in out and "the best of 2 qualifying" in out
    assert "pavers or stone ground 1, poured concrete 1, ground differs 1" in out
    assert f"--material-bundle auto: --material-donor ground={PARK_PAVEMENT}:0" in out
    assert f"--material-bundle auto: --material-donor concrete={PARK_STEP}:0" in out
    assert f"--material-bundle auto: --material-donor wood={PARK_BENCH}:0" in out
    assert f"it becomes our sublevel {PARK_BUNDLE[len('win32/'):]}" in out
    # A given donor wins; too few usable sections, or nothing in bam's superbundle: refused.
    main(args + ["--material-donor", f"ground={PARK_BENCH}:0"])
    assert f"--material-donor ground={PARK_BENCH}:0 (given)" in capsys.readouterr().out
    monkeypatch.setattr(materials, "AUTO_MIN_USABLE", 10)
    with pytest.raises(SystemExit, match="no sublevel bundle of .* has usable"):
        main(args)
    out = capsys.readouterr().out
    assert f"  rejected {PARK_BUNDLE} (" in out and "3 usable classed sections, under 10" in out
    with pytest.raises(SystemExit, match="no sublevel bundle of levels/game/bam_levelroot/bam_levelroot has usable"):
        main(args[:-2] + ["auto", "--dry-run"])


def test_a_baked_vista_material_is_never_a_donor():
    """The PC's bad M1 pick: one vista section classed as stone and wood (its baked name holds
    'benchbacklessstone_01_3m_Stone_mat') was every donor, and drew black and impostor-grey."""
    from reskate_mapbuilder.materials import auto_donors, has_ground_and_wood, is_proxy, material_words
    baked = "vista_medium_quadrant_095x095_vista_m_benchbacklessstone_01_3m_Stone_mat"
    assert is_proxy("levels/game/bam_levelroot/vistas/vista_medium_quadrant_095x095_vista_m_vista_0_mesh")
    assert is_proxy("win32/levels/game/bam_levelroot/wp_vista_medium_vistarootquadrant3/wp/s100_x4750z4750_sublevel")
    assert "wood" not in material_words(baked) and "bench" not in material_words(baked)
    assert "wood" in material_words("Wood_Accent") and "wood" in material_words("Planks_Wood_mat")
    # Even outside a vista: one section never serves two classes, and no wood word means no wood.
    row = {"sections": [("x_mesh", 0, "benchbacklessstone_01_3m_Stone_mat", ["stone", "wood"], True, [8])]}
    assert "wood" not in auto_donors(row) and not has_ground_and_wood(row)
    row["sections"].append(("y_mesh", 0, "Concrete_Poured_01_M", ["concrete"], True, [8]))
    picks = auto_donors(row)
    assert picks["concrete"] == "y_mesh:0" and picks["ground"] == "x_mesh:0" and "wood" not in picks



def test_material_bundle_hosts_our_sublevel_and_its_material_objects(tmp_path, capsys):
    from reskate_mapbuilder.ebx import read_document
    from test_level import BOX, FTUE, NEW, PLAZA_BUNDLE, PLAZA_MESH, PLAZA_TEXTURE_CHUNK, section_keys
    from reskate_mapbuilder.meshset import read_layout
    game_dir = fake_game(tmp_path / "game")
    tiles = write_tiles(tmp_path / "tiles")
    main(["new-place-level", "--game", str(game_dir), "--mods", str(tmp_path / "Mods"), "--name", "zz-place",
          "--level", LEVEL, "--tiles", str(tiles), "--at", "0", "300", "0", "--skip-part", "buildings", "--visuals",
          "--visual-template", BOX, "--material-bundle", f"{FTUE}:{PLAZA_BUNDLE}",
          "--material-donor", f"ground={PLAZA_MESH}:0"])
    out = capsys.readouterr().out
    host_sub = PLAZA_BUNDLE[len("win32/"):]
    assert f"it becomes our sublevel {host_sub} (its own sublevel EBX replaced by our tile)" in out
    assert "(in the material bundle: objects of this material go there)" in out
    assert f"its small resources name 1 chunks of {FTUE}'s TOC: 1 added to ours, 0 already in it" in out
    assert f"{PLAZA_BUNDLE}: 1 visual objects drawn with its materials, their keys in its depot" in out
    root = tmp_path / "Mods" / "zz-place"
    view = Game(game_dir, mod=root)
    toc = read_toc((root / "Win32" / (NEW + ".toc")).read_bytes())
    names = {b.name for b in toc.bundles}
    assert PLAZA_BUNDLE in names and "win32/" + SUBLEVEL not in names
    assert PLAZA_TEXTURE_CHUNK in {c.guid for c in toc.chunks}
    _, region, manifest = view.bundle(toc, PLAZA_BUNDLE)
    ebx = [a.name for a in manifest.ebx]
    assert ebx[:2] == [host_sub, PLAZA_MESH] and f"levels/game/{LEVEL}/ground_layer" in ebx
    assert read_document(view.ebx(region, manifest, host_sub)).root_type == "QuadrantSubWorldData"     # ours now
    base = f"levels/game/{LEVEL}/visual_ground_t0_0"
    assert base + "_bp" in ebx and base + "_mesh" in ebx
    resources = {a.name: 1 + len(manifest.ebx) + i for i, a in enumerate(manifest.resources)}
    layout = read_layout(view.payload(region, resources[base + "_mesh"]))
    key = section_keys(PLAZA_MESH)[0]
    assert all(int.from_bytes(view.payload(region, resources[base + "_mesh"])[s.at + 0x138:s.at + 0x140], "little") == key
               for lod in layout for s in lod.sections)
    # The root loads it.
    _, region, manifest = view.bundle(toc, "win32/" + NEW)
    doc = read_document(view.ebx(region, manifest, NEW))
    assert host_sub in [i.object.get("BundleName") for i in doc.instances if i.object.get("BundleName")]


def test_part_arrays_must_share_physicspartinfos_length():
    from reskate_mapbuilder.ground import _check_part_tables
    from test_level import document, guid, obj, ptr, groundplane
    lines = _check_part_tables(groundplane(), [], [object()], "own", False, 1)
    assert any("PhysicsPartInfos [1] and its siblings: PartBoundingBoxes 1, PartLinks 0 (empty), PartStates 1: "
               "consistent" in line for line in lines)
    bad = document(guid(250), [
        (lambda d: obj(d, "ObjectBlueprint", Name="x", Object=ptr(1)), None),
        (lambda d: obj(d, "StaticModelEntityData", PhysicsPartInfos=[obj(d, "PhysicsPartInfo", PartIndex=0)],
                       PartStates=[0, 0]), None)])
    with pytest.raises(ValueError, match="PartStates has 2 entries but .*PhysicsPartInfos has 1"):
        _check_part_tables(bad, [], [object()], "own", False, 1)


def column(x0, z0, w, h):
    """A welded w x h x w column: 8 shared corners, 12 outward triangles (as the export's buildings)."""
    verts = [(x0 + x, y, z0 + z) for y in (0.0, h) for z in (0.0, w) for x in (0.0, w)]
    quads = [(4, 6, 7, 5), (0, 1, 3, 2), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return verts, [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]


def test_buildings_are_their_own_class_and_skip_visual_leaves_them_out(tmp_path, capsys, monkeypatch):
    from test_level import BOX, FTUE, PARK_STEP
    game_dir = fake_game(tmp_path / "game")
    extra = [("buildings", "visual", "concrete", "buildings", column(40, 40, 4, 20), 0, 1),
             ("spot_3_steps", "visual", "concrete", "steps", column(20, 20, 2, 0.5), 0, 1)]
    tiles = write_tiles(tmp_path / "tiles", extra=extra)
    args = ["new-place-level", "--game", str(game_dir), "--name", "zz-place", "--level", LEVEL, "--tiles", str(tiles),
            "--at", "0", "300", "0", "--skip-part", "buildings", "--visuals", "--visual-template", BOX, "--dry-run"]
    import reskate_mapbuilder.materials as materials
    monkeypatch.setattr(materials, "AUTO_MIN_BYTES", 0)
    main(args + ["--material-bundle", f"auto:{FTUE}"])
    out = capsys.readouterr().out
    assert "visual classes (each drawn with its --material-donor): building <- buildings (1); " \
           "concrete <- steps (1); ground <- ground (1)" in out
    # The park has no wall material: buildings take its poured concrete, as the steps do.
    assert f"--material-bundle auto: --material-donor building={PARK_STEP}:0" in out
    assert f"visual class building (1 objects): drawn with {PARK_STEP}:0" in out
    assert "vertices split by face axis for per-face UVs: 8 -> 24" in out        # the welded building
    # Without a building donor, buildings take the concrete donor.
    main(args + ["--material-donor", f"concrete={BOX}_mesh:1"])
    out = capsys.readouterr().out
    assert f"visual class building (1 objects): drawn with concrete's donor {BOX}_mesh:1" in out
    assert f"visual class concrete (1 objects): drawn with {BOX}_mesh:1" in out
    main(args + ["--skip-visual", "buildings"])
    out = capsys.readouterr().out
    assert "visual objects: 2 of 3" in out and "building <-" not in out


def test_auto_donors_give_ground_and_concrete_different_materials():
    from reskate_mapbuilder.materials import auto_donors
    def section(mesh, n, material):
        return (mesh, n, material, ["concrete"], True, [8])
    row = {"sections": [section("a_mesh", 0, "Concrete_Poured_01_M"), section("b_mesh", 1, "Pavers_Grey_M"),
                        section("c_mesh", 2, "Brick_Wall_Red_M"), section("d_mesh", 0, "Wood_Accent")]}
    picks = auto_donors(row)
    assert picks["ground"] == "b_mesh:1" and picks["concrete"] == "a_mesh:0" and picks["building"] == "c_mesh:2"
    assert picks["wood"] == "d_mesh:0"
    only = {"sections": [section("a_mesh", 0, "Concrete_Poured_01_M"), section("d_mesh", 0, "Wood_Accent")]}
    picks = auto_donors(only)
    # One concrete section: concrete takes it, the ground gets none (distinct sections), the building shares it.
    assert picks["concrete"] == picks["building"] == "a_mesh:0" and "ground" not in picks
    two = {"sections": only["sections"] + [section("e_mesh", 3, "Concrete_Poured_01_M")]}
    assert auto_donors(two)["ground"] == "e_mesh:3"                       # same material, another section


def test_the_known_good_tile_qualifies(monkeypatch):
    """The PC's M3: the trial tile (ground granite, poured concrete, Wood_Accent; worked in game)
    was refused. Its exact names must qualify, and give those three donors."""
    import reskate_mapbuilder.materials as materials
    from reskate_mapbuilder.materials import auto_donors, auto_rejection, section_problem
    bundle = "win32/levels/game/bam_levelroot/wproot/wp/s9600_x0z0/s200_x900z1700/s100_x850z1650/s50_x825z1675_sublevel"
    pillar = "levels/game/bam_levelroot/wproot/wp/props/dtn_plt_23_base_e_pillar_base_01_mesh"
    menu = "common/props/cmn_prp_menuboard_01_mesh"
    names = [(pillar, 0, "Stone_Granite_02_2x2m_M_SgGrdSlabs01_mat"), (pillar, 1, "Metal_Dark_01_mat"),
             (pillar, 2, "Concrete_Poured_01_2x2m_M_Grey_mat"), (menu, 4, "Wood_Accent"),
             (menu, 0, "Shadow_mat"), ("levels/game/x/blod_mesh", 0, "Plastic_Red_mat")]
    for mesh, _, material in names[:4] + names[5:]:
        assert section_problem(bundle, mesh, material, True, True) == ""     # no proxy word inside a word
    assert section_problem(bundle, menu, "Shadow_mat", True, True) == "material name has the proxy word 'shadow'"
    assert section_problem(bundle, "x/rock_lod1_mesh", "Stone_mat", True, True) == "mesh name has the proxy word 'lod1'"
    sections = [(mesh, n, material, materials.classes_of(material) or ["metal"], material != "Shadow_mat", [8])
                for mesh, n, material in names]
    row = {"bundle": bundle, "bytes": 900 * 1024, "sublevel": True, "sections": sections}
    assert auto_rejection(row) == ""
    picks = auto_donors(row)
    assert picks["ground"] == f"{pillar}:0" and picks["concrete"] == f"{pillar}:2" and picks["wood"] == f"{menu}:4"
    assert materials.auto_score(row) == (1, 1, 1, 1)


def test_scan_materials_explain_prints_every_section_and_the_verdict(tmp_path, capsys, monkeypatch):
    import reskate_mapbuilder.materials as materials
    from test_level import FTUE, PARK_BUNDLE, PARK_STEP, VISTA_BUNDLE
    monkeypatch.setattr(materials, "AUTO_MIN_BYTES", 0)
    game_dir = fake_game(tmp_path / "game")
    main(["scan-materials", "--game", str(game_dir), "--explain", f"{FTUE}:{PARK_BUNDLE}"])
    out = capsys.readouterr().out
    assert f"  {PARK_STEP}:0  'Concrete_Poured_01_2x2m_M_Grey_mat'" in out
    assert "words ['01', '2x2m', 'concrete', 'grey', 'm', 'mat', 'poured']" in out and "auto roles ['concrete'" in out
    assert "NOT usable: no TexCoord0" in out and "auto pick: qualifies, score (1, 1, 1, 0)" in out
    assert f"  concrete = {PARK_STEP}:0 ('Concrete_Poured_01_2x2m_M_Grey_mat')" in out
    main(["scan-materials", "--game", str(game_dir), "--explain", f"{FTUE}:{VISTA_BUNDLE}"])
    out = capsys.readouterr().out
    assert "bundle name has the proxy word 'vista'" in out and "auto pick: rejected: " in out
