"""new-ground-level against test_level's fake game: bam's level with the base game's own chain
tile -> LayerReferenceObjectData -> layer -> ObjectReferenceObjectData -> util_groundplane, whose
collision is found by name (FORMAT-NOTES 6.3)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_level import (FTUE, LIGHTHOUSE_LOD1_CHUNK, ISLAND_BUNDLE, LIGHTHOUSE, LIGHTHOUSE_CHUNK,  # noqa: E402
                        LIGHTHOUSE_TEXTURE_CHUNK, meshset)
from test_level import BOX, BOX_CHUNK, BOX_GUID, BOX_PHYSICS_GUID, section_keys  # noqa: E402
from test_level import (BAM, BP_GUID, BP_ROOT_GUID, CHUNK, CORE_BUNDLE, CORE_LAYER, GROUNDPLANE, LAYER_GUID,  # noqa: E402
                        LAYER_REFERENCE_GUID, LEVEL, MESH_CHUNK, MESH_GUID, NEW, PLACEMENT_GUID, TEMPLATE_MATERIAL, TILE,
                        TILE_GUID, fake_game, genprefab, guid, ptr)
from reskate_mapbuilder.__main__ import main  # noqa: E402
from reskate_mapbuilder.ebx import PointerKind, PointerReference, read_document, write_document  # noqa: E402
from reskate_mapbuilder.meshset import read_layout, rebuild_chunk, section_positions, section_triangles, write_mesh  # noqa: E402
from reskate_mapbuilder.ebxedit import (add_import, clone_instance, derived_guid, field_referrers, guid_flags, path_to,  # noqa: E402
                                        prune_imports, prune_unreachable, remove_instances)
from reskate_mapbuilder.cas import decode_raw  # noqa: E402
from reskate_mapbuilder.game import Game  # noqa: E402
from reskate_mapbuilder.ground import new_ground_level, resource_id  # noqa: E402
from reskate_mapbuilder.mod import ModBuilder  # noqa: E402
from reskate_mapbuilder.physics import read_physics  # noqa: E402
from reskate_mapbuilder.toc import read_toc  # noqa: E402

SUBLEVEL = f"levels/game/{LEVEL}/ground_sublevel"
LAYER = f"levels/game/{LEVEL}/ground_layer"
BLUEPRINT = f"levels/game/{LEVEL}/ground_bp"
PHYSICS = BLUEPRINT + ".physics"


def build(tmp_path, **options):
    game = Game(fake_game(tmp_path / "game"))
    mod = ModBuilder(game, "zz-ground-level")
    report = new_ground_level(game, mod, LEVEL, "Ground test", **options)
    root = mod.write(tmp_path / "Mods")
    return game, mod, report, root, Game(tmp_path / "game", mod=root)


def references(view):
    toc = view.superbundle(NEW)
    _, region, manifest = view.bundle(toc, "win32/" + NEW)
    doc = read_document(view.ebx(region, manifest, NEW))
    objects = doc.instances[0].object.get("Objects")
    return doc, [doc.instances[p.index] for p in objects]


def ground(view):
    toc = read_toc(Path(view.mod, "Win32", NEW + ".toc").read_bytes())
    _, region, manifest = view.bundle(toc, "win32/" + SUBLEVEL)
    docs = [read_document(view.ebx(region, manifest, n)) for n in (SUBLEVEL, LAYER, BLUEPRINT)]
    return toc, region, manifest, docs


def transform(o):
    t = o.get("BlueprintTransform")
    return [[t.get(r).get(c) for c in "xyz"] for r in ("right", "up", "forward", "trans")]


def test_ground_level_copies_the_chain_and_writes_the_collision(tmp_path):
    game, mod, report, root, view = build(tmp_path, at=(10.0, 300.0, -20.0))
    text = "\n".join(report)
    assert f"imports {TILE[:-len('_sublevel')]}_layer" in text
    assert "places 0b1a2948" in text or "(another bundle)" in text        # the worklight is elsewhere
    assert f"imports {GROUNDPLANE}_mesh: MeshSet box [-25.0, 0.0, -25.0]..[25.0, 0.0, 25.0]" in text
    assert f"collision {GROUNDPLANE}.physics" in text and f"our blueprint imports {GROUNDPLANE}_mesh" in text
    assert not any("MISSING" in line or "NOT semantically" in line or "WARNING" in line for line in report)

    toc, region, manifest, (sub, layer, bp) = ground(view)
    assert "win32/" + TILE in {b.name for b in toc.bundles}          # --bam all keeps bam's tiles
    assert [a.name for a in manifest.ebx] == [SUBLEVEL, LAYER, BLUEPRINT]
    assert [a.name for a in manifest.resources] == [PHYSICS] and not manifest.chunks
    assert manifest.resources[0].resource_type == 0x41759364
    assert manifest.resources[0].resource_id == resource_id(PHYSICS)
    assert all(f.location == region.files[0].location and f.location.patch and f.location.install_chunk == CHUNK
               for f in region.files)

    # The tile: its root, the layer reference and the interface descriptor; nothing else.
    assert [sub.types[i.descriptor].name for i in sub.instances] == [
        "QuadrantSubWorldData", "LayerReferenceObjectData", "InterfaceDescriptorData"]
    assert sub.instances[0].object.get("Name") == SUBLEVEL and sub.file_guid != TILE_GUID
    assert [p.index for p in sub.instances[0].object.get("Objects")] == [1]
    assert sub.instances[0].object.get("Descriptor").index == 2
    reference = sub.instances[1]
    assert reference.instance_guid != LAYER_REFERENCE_GUID
    assert reference.object.get("Flags") == guid_flags(reference.instance_guid, 0)
    assert transform(reference.object)[3] == [5, 6, 7]                # the layer reference is not moved
    assert len(sub.imports) == 1 and reference.object.get("Blueprint").kind == PointerKind.external
    assert (sub.imports[0].file_guid, sub.imports[0].class_guid) == (layer.file_guid, layer.instances[0].instance_guid)

    # The layer: one placement of our blueprint at --at.
    assert layer.file_guid != LAYER_GUID and layer.instances[0].object.get("Name") == LAYER
    assert [layer.types[i.descriptor].name for i in layer.instances] == ["LayerData", "ObjectReferenceObjectData"]
    placement = layer.instances[1]
    assert transform(placement.object) == [[1, 0, 0], [0, 1, 0], [0, 0, 1], [10, 300, -20]]
    assert placement.instance_guid != PLACEMENT_GUID
    assert placement.object.get("Flags") == guid_flags(placement.instance_guid, 0)
    assert len(layer.imports) == 1
    assert (layer.imports[0].file_guid, layer.imports[0].class_guid) == (bp.file_guid, bp.instances[0].instance_guid)

    # The blueprint: our name, the base mesh kept.
    assert bp.file_guid != BP_GUID and bp.instances[0].instance_guid != BP_ROOT_GUID
    assert bp.instances[0].object.get("Name") == BLUEPRINT
    assert [i.file_guid for i in bp.imports] == [MESH_GUID]

    # The collision: one flat 50 m grid, the template part's material.
    shapes, parts, version = read_physics(view.payload(region, 4))
    assert version == 2 and len(shapes) == 1 and len(parts) == 1
    assert len(shapes[0].vertices) == 11 * 11 and len(shapes[0].triangles) == 2 * 10 * 10
    xs = [v[0] for v in shapes[0].vertices]
    assert (min(xs), max(xs)) == (-25.0, 25.0) and {v[1] for v in shapes[0].vertices} == {0.0}
    assert parts[0].material == TEMPLATE_MATERIAL and parts[0].translation == (0.0, 0.0, 0.0)

    # The root: bam's references kept, one more loading the ground.
    doc, refs = references(view)
    assert [r.object.get("BundleName") for r in refs] == [
        "Levels/Game/BAM_LevelRoot/TOD_2_Noon", "Levels/Game/BAM_LevelRoot/wproot", SUBLEVEL]
    added = refs[2].object
    assert (added.get("AutoLoad"), added.get("IsDetachedSubLevel")) == (True, False)
    blueprint = added.get("Blueprint")
    assert (doc.imports[blueprint.index].file_guid, doc.imports[blueprint.index].class_guid) == (
        sub.file_guid, sub.instances[0].instance_guid)
    # The base mesh is reachable: the level's root bundle copies bam's, which holds it.
    _, region, manifest = view.bundle(toc, "win32/" + NEW)
    assert GROUNDPLANE + "_mesh" in [a.name for a in manifest.ebx] and MESH_CHUNK in [c.guid for c in manifest.chunks]
    assert (root / "reskate-levels.json").is_file()


def test_tiles_place_a_grid(tmp_path):
    _, _, _, _, view = build(tmp_path, tiles=2, at=(0.0, 300.0, 0.0))
    _, _, _, (_, layer, bp) = ground(view)
    placements = [layer.instances[p.index] for p in layer.instances[0].object.get("Objects")]
    assert sorted(tuple(transform(p.object)[3]) for p in placements) == [
        (-25, 300, -25), (-25, 300, 25), (25, 300, -25), (25, 300, 25)]
    assert len({p.instance_guid for p in placements}) == 4
    assert all(p.object.get("Flags") == guid_flags(p.instance_guid, 0) for p in placements)
    assert all(layer.imports[p.object.get("Blueprint").index].file_guid == bp.file_guid for p in placements)


def test_another_layer_template(tmp_path):
    _, _, report, _, view = build(tmp_path, layer_template=CORE_LAYER, layer_bundle=CORE_BUNDLE)
    assert any(line.startswith(f"Layer {CORE_LAYER} in {CORE_BUNDLE}") for line in report)
    _, _, _, (sub, layer, _) = ground(view)
    assert layer.instances[0].object.get("Name") == LAYER
    assert (sub.imports[0].file_guid, sub.imports[0].class_guid) == (layer.file_guid, layer.instances[0].instance_guid)


def test_ground_level_is_the_same_on_every_build(tmp_path):
    first = build(tmp_path / "a")[1].build()
    second = build(tmp_path / "b")[1].build()
    assert first == second


def test_no_world_leaves_out_bams_tiles_and_their_reference(tmp_path):
    _, _, report, root, view = build(tmp_path, bam="no-world")
    toc = read_toc((root / "Win32" / (NEW + ".toc")).read_bytes())
    names = {b.name for b in toc.bundles}
    assert "win32/" + TILE not in names and "win32/levels/game/bam_levelroot/wproot" not in names
    assert "win32/levels/game/bam_levelroot/tod_2_noon" in names and "win32/" + SUBLEVEL in names
    assert any("removed 1 references" in line for line in report)
    _, refs = references(view)
    assert [r.object.get("BundleName") for r in refs] == ["Levels/Game/BAM_LevelRoot/TOD_2_Noon", SUBLEVEL]
    added = refs[1].object
    assert added.get("AutoLoad") is True and added.get("Blueprint").kind == PointerKind.null   # copied from TOD's


def test_333_lists_only_the_bam_bundles_the_333_map_carries(tmp_path):
    _, _, _, root, _ = build(tmp_path, bam="333")
    toc = read_toc((root / "Win32" / (NEW + ".toc")).read_bytes())
    assert sorted(b.name for b in toc.bundles) == sorted([
        "win32/" + NEW, "win32/" + SUBLEVEL, "win32/levels/game/bam_levelroot/tod_2_noon",
        "win32/levels/game/bam_levelroot/startpoint_sublevels", CORE_BUNDLE])


def test_ground_level_refusals_report(tmp_path, capsys):
    game_dir = fake_game(tmp_path / "game")
    game = Game(game_dir)
    with pytest.raises(KeyError, match="no LayerReferenceObjectData"):
        main(["new-ground-level", "--game", str(game_dir), "--name", "zz-g", "--level", LEVEL,
              "--tile-template", CORE_LAYER, "--dry-run"])
    assert f"Tile {CORE_LAYER} in {CORE_BUNDLE}" in capsys.readouterr().out      # the report is printed anyway
    with pytest.raises(ValueError, match="not an ObjectBlueprint"):
        new_ground_level(game, ModBuilder(game, "zz"), LEVEL, blueprint_template=GROUNDPLANE + "_mesh")
    with pytest.raises(ValueError, match="tiles"):
        new_ground_level(game, ModBuilder(game, "zz"), LEVEL, tiles=0)


def test_cli_ground_dry_run_and_list_assets(tmp_path, capsys):
    game_dir = fake_game(tmp_path / "game")
    main(["new-ground-level", "--game", str(game_dir), "--mods", str(tmp_path / "Mods"), "--name", "zz-g",
          "--level", LEVEL, "--size", "40", "--at", "0", "200", "0", "--dry-run"])
    out = capsys.readouterr().out
    assert "dry run: nothing written" in out and "1 x 1 placements of 40.0 m at [0.0, 200.0, 0.0]" in out
    assert "81 vertices, 128 triangles" in out
    assert not (tmp_path / "Mods").exists()
    main(["list-assets", "--game", str(game_dir), "--superbundle", BAM, "--bundle", "win32/" + BAM, "--contains",
          "groundplane"])
    out = capsys.readouterr().out
    assert f"res\t{GROUNDPLANE}.physics" in out and "type 0x41759364" in out
    assert f"ebx\t{GROUNDPLANE}_mesh" in out and "pool" not in out


# ---- The EBX edits it uses --------------------------------------------------------------------

def test_remove_instances_takes_array_elements_and_refuses_other_pointers():
    doc = genprefab()
    # #2 is held by the blueprint's Objects array only: removed with its element.
    taken = remove_instances(doc, {2})
    assert taken == ["#0.Objects[1] -> #2"]
    back = read_document(write_document(doc))
    assert [back.instances[p.index].object.get("BundleName") for p in back.instances[0].object.get("Objects")] == [
        "Levels/Game/BAM_LevelRoot/_DEBUG_/BuildkitPerfTests", "Levels/Game/DingoLevel_MPR/DingoLevel_MPR"]
    doc = genprefab()
    with pytest.raises(ValueError, match="outside a pointer array"):
        remove_instances(doc, {4})        # #2's Blueprint field points at #4
    with pytest.raises(ValueError):
        remove_instances(genprefab(), {0})


def test_prune_and_imports():
    doc = genprefab()
    doc.instances[0].object.get("Objects")[:] = [ptr(1)]
    assert prune_unreachable(doc) == [2, 3, 4]
    assert len(doc.instances) == 2
    pointer = add_import(doc, guid(1), guid(2))
    assert add_import(doc, guid(1), guid(2)) == pointer
    assert prune_imports(doc) == 1                                  # nothing points at it yet
    doc.instances[1].object.set("Blueprint", add_import(doc, guid(1), guid(2)))
    assert prune_imports(doc) == 0 and len(doc.imports) == 1
    back = read_document(write_document(doc))
    assert back.instances[1].object.get("Blueprint") == PointerReference(PointerKind.external, 0)


def test_path_to_and_clone_offsets():
    doc = genprefab()
    chain = path_to(doc, 4)
    assert [(h, a is not None, i) for h, a, i in chain] == [(0, True, 1), (2, False, -1)]
    index = clone_instance(doc, 2, derived_guid("x"), 4)
    assert doc.instances[index].data_offset == -1 and doc.instances[2].data_offset >= 0
    assert path_to(doc, 99) is None


def test_inspect_meshset_prints_the_known_fields(tmp_path, capsys):
    game_dir = fake_game(tmp_path / "game")
    main(["inspect-meshset", "--game", str(game_dir), "--superbundle", BAM, "--bundle", "win32/" + BAM,
          "--resource", GROUNDPLANE + "_mesh"])
    out = capsys.readouterr().out
    assert "type 0x49b156d4" in out and "1 LODs" in out and MESH_CHUNK.string() in out
    assert "header 192, LOD struct 176, section struct 384" in out and "2 sections @" in out
    assert "(usage, format, offset, stream) [(1, 3, 0, 0), (33, 6, 0, 1), (52, 13, 4, 1)]" in out


def test_no_world_and_333_keep_references_a_field_holds(tmp_path):
    for bam in ("no-world", "333"):
        _, _, report, _, view = build(tmp_path / bam, bam=bam)
        text = "\n".join(report)
        assert "KEPT a reference to a bundle the TOC does not list" in text and "wproot/wp/held" in text
        assert "held by #3.Object" in text
        doc, refs = references(view)
        assert [r.object.get("BundleName") for r in refs] == ["Levels/Game/BAM_LevelRoot/TOD_2_Noon", SUBLEVEL]
        holder = next(i for i in doc.instances if i.object.get("Name") == "holder")
        assert doc.instances[holder.object.get("Object").index].object.get("BundleName").endswith("/held")


def test_field_referrers():
    doc = genprefab()
    assert field_referrers(doc, 4) == ["#2.Blueprint"] and field_referrers(doc, 2) == []


def test_visual_template_keeps_the_layers_own_placement(tmp_path):
    _, _, report, _, view = build(tmp_path, visual="template", at=(0.0, 300.0, 0.0))
    assert any("--visual template: the layer's own placement kept" in line for line in report)
    _, _, _, (_, layer, bp) = ground(view)
    placements = [layer.instances[p.index] for p in layer.instances[0].object.get("Objects")]
    assert len(placements) == 2
    ours, theirs = placements
    assert layer.imports[ours.object.get("Blueprint").index].file_guid == bp.file_guid
    assert layer.imports[theirs.object.get("Blueprint").index].file_guid == guid(93)     # the worklight, unchanged
    t = transform(theirs.object)
    assert t[3] == [10, 300, 10] and t[0] == [0, 0, 1]          # moved next to ours, rotation kept
    assert transform(ours.object)[3] == [0, 300, 0]
    assert theirs.instance_guid != ours.instance_guid


def test_blueprint_from_another_bundle_is_refused_for_its_shader_blocks(tmp_path):
    """Its MeshSet's sections name shader blocks of its own bundle's depot, which our bundles do
    not have: the lighthouse crashed so (FORMAT-NOTES 6.3)."""
    game = Game(fake_game(tmp_path / "game"))
    log = []
    with pytest.raises(ValueError, match="only win32/levels/game/dingolevel_ftue_island/wp_dst_ftue_island's depot"):
        new_ground_level(game, ModBuilder(game, "zz"), LEVEL, blueprint_template=LIGHTHOUSE,
                         blueprint_bundle=ISLAND_BUNDLE, blueprint_superbundle=FTUE, log=log)
    text = "\n".join(log)
    assert f"listed from {ISLAND_BUNDLE} with the blueprint: 2 EBX" in text and "flags #1 StaticModelEntityData" in text
    assert "3 chunks of " + FTUE in text                         # its TOC's chunks found for the level's TOC
    assert f"4 of 4 in {ISLAND_BUNDLE}'s own depots" in text


def test_mesh_own_writes_a_meshset_rigidmesh_and_chunk(tmp_path):
    _, _, report, _, view = build(tmp_path, mesh="own", size=20.0, mesh_shape="plane")
    text = "\n".join(report)
    assert "chunk 144 bytes rebuilds identically" in text and "winding flipped to match" in text
    assert f"mesh {GROUNDPLANE}_mesh (replaced by ours) LOD 0 chunk {MESH_CHUNK.string()}: in win32/{BAM}'s chunk " \
           f"entries and {BAM}'s TOC chunk list; listed by the level's TOC, the level's root bundle" in text
    assert "mesh ours (" in text and f"in the mod's archive; listed by the level's TOC, win32/{SUBLEVEL}" in text
    assert "1 resource references named the template's id" in text and "1 blueprint imports moved to it" in text
    _, region, manifest, (_, _, bp) = ground(view)
    mesh_name = f"levels/game/{LEVEL}/ground_mesh"
    assert [a.name for a in manifest.ebx] == [SUBLEVEL, LAYER, BLUEPRINT, mesh_name]
    assert [a.name for a in manifest.resources] == [PHYSICS, mesh_name]
    assert manifest.resources[1].resource_type == 0x49B156D4 and manifest.resources[1].resource_id == resource_id(mesh_name)
    mesh = read_document(view.ebx(region, manifest, mesh_name))
    assert mesh.instances[0].object.get("Name") == mesh_name
    assert mesh.instances[0].object.get("MeshSetResource").id == resource_id(mesh_name)
    assert (bp.imports[0].file_guid, bp.imports[0].class_guid) == (mesh.file_guid, mesh.instances[0].instance_guid)
    ms = view.payload(region, 1 + 4 + 1)
    chunk_asset = manifest.chunks[0]
    chunk = view.payload(region, 1 + 4 + 2)
    assert len(ms) == 1376 and chunk_asset.logical_size == len(chunk) and chunk_asset.logical_offset == 0
    toc_chunk = next(c for c in read_toc(Path(view.mod, "Win32", NEW + ".toc").read_bytes()).chunks
                     if c.guid == chunk_asset.guid)                    # also in the level's TOC, same bytes
    assert toc_chunk.location.patch and decode_raw(view.read(toc_chunk.location, toc_chunk.offset, toc_chunk.size)) == chunk
    lod = read_layout(ms)[0]
    assert lod.chunk == chunk_asset.guid and rebuild_chunk(lod, chunk) == chunk
    s = lod.sections[0]                                                # our geometry
    points = section_positions(s, chunk)
    assert len(points) == 25 and min(p[0] for p in points) == -10 and max(p[2] for p in points) == 10
    tris = section_triangles(lod, s, chunk)
    a, b, c = (points[i] for i in tris[0])
    assert (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]) < 0     # the template's winding
    unpack = __import__("struct").unpack_from
    assert unpack("<f", ms, s.at + 332)[0] == 400.0                     # its area: 20 x 20 m
    other = lod.sections[1]                                            # kept, one zero-area triangle
    assert (other.triangle_count, other.vertex_count) == (1, 1)
    assert section_triangles(lod, other, chunk) == [(0, 0, 0)] and unpack("<f", ms, other.at + 332)[0] == 0.0
    # The other elements are the template's first vertex's.
    first = lod.sections[0]
    # Stream 1 of vertex 0: the template's normal bytes, then our UV (x / 6, z / 6) at (-10, -10).
    # Stream 1 of vertex 0 (-10, 0, -10): TexCoord0 planar at 1 per metre from the template's
    # minimum (0.5, 0.25), then the template's most common colour.
    assert chunk[first.vertex_start + 25 * 12:first.vertex_start + 25 * 12 + 8] == \
        __import__("struct").pack("<2e", -9.5, -9.75) + bytes([0, 0, 255, 200])
    assert "usage 33 format 6 (Half2) at +0 of stream 1" in text and "usage 52 000000ffc8" not in text
    assert "usage 52 0000ffc8s" in text


def test_write_mesh_refuses_a_chunk_it_does_not_understand():
    ms, chunk = meshset(guid(1))
    broken = bytearray(chunk)
    broken[112 - 1] = 0 if broken[111] else 1        # inside section 1's positions: still rebuilds
    write_mesh(ms, [bytes(broken)], [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)], [(0, 2, 1)], [guid(2)])
    broken = chunk[:-1] + b"\1"                        # a non-zero byte in the end padding
    with pytest.raises(ValueError, match="does not rebuild"):
        write_mesh(ms, [broken], [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)], [(0, 2, 1)], [guid(2)])


def test_mesh_own_on_a_blueprint_from_another_bundle_keeps_its_texture(tmp_path):
    _, _, _, _, view = build(tmp_path, mesh="own", mesh_shape="plane", blueprint_template=LIGHTHOUSE,
                             blueprint_bundle=ISLAND_BUNDLE, blueprint_superbundle=FTUE)
    _, _, manifest, _ = ground(view)
    mesh_name = f"levels/game/{LEVEL}/ground_mesh"
    assert [a.name for a in manifest.ebx] == [SUBLEVEL, LAYER, BLUEPRINT, mesh_name, LIGHTHOUSE + "_d"]
    assert [a.name for a in manifest.resources] == [PHYSICS, mesh_name, LIGHTHOUSE + "_d"]
    ours = [c.guid for c in manifest.chunks]
    assert len(ours) == 2 and LIGHTHOUSE_CHUNK not in ours            # one chunk per LOD of ours
    toc = {c.guid for c in read_toc(Path(view.mod, "Win32", NEW + ".toc").read_bytes()).chunks}
    assert set(ours) <= toc and LIGHTHOUSE_TEXTURE_CHUNK in toc and LIGHTHOUSE_LOD1_CHUNK not in toc
    _, region, manifest, _ = ground(view)
    ms = view.payload(region, 1 + 5 + 1)
    layout = read_layout(ms)
    assert [lod.chunk for lod in layout] == ours
    for k, lod in enumerate(layout):
        chunk = view.payload(region, 1 + 5 + 3 + k)
        assert rebuild_chunk(lod, chunk) == chunk and len(section_positions(lod.sections[0], chunk)) == 121


def test_asset_bundle_coregameassets_holds_the_blueprint_and_mesh(tmp_path):
    _, _, report, root, view = build(tmp_path, bam="333", mesh="own", asset_bundle="coregameassets")
    assert any(line.startswith("--asset-bundle coregameassets") for line in report)
    toc = read_toc((root / "Win32" / (NEW + ".toc")).read_bytes())
    _, region, manifest = view.bundle(toc, "win32/" + SUBLEVEL)
    assert [a.name for a in manifest.ebx] == [SUBLEVEL, LAYER] and not manifest.resources
    _, region, manifest = view.bundle(toc, CORE_BUNDLE)
    mesh_name = f"levels/game/{LEVEL}/ground_mesh"
    assert [a.name for a in manifest.ebx] == ["levels/game/bam_levelroot/other", CORE_LAYER, BLUEPRINT, mesh_name]
    assert [a.name for a in manifest.resources] == [PHYSICS, mesh_name] and len(manifest.chunks) == 1
    assert [f.location.patch for f in region.files] == [True, False, False, True, True, True, True, True]
    assert [b.name for b in toc.bundles].count(CORE_BUNDLE) == 1
    with pytest.raises(KeyError, match="does not list"):
        game = Game(fake_game(tmp_path / "g2"))
        import reskate_mapbuilder.ground as g
        saved = g.BAM_MODES["333"]
        g.BAM_MODES["333"] = lambda name: name.startswith(g.BAM_333) and "coregameassets" not in name
        try:
            new_ground_level(game, ModBuilder(game, "zz"), LEVEL, bam="333", asset_bundle="coregameassets")
        finally:
            g.BAM_MODES["333"] = saved


def test_mesh_refs_finds_the_meshsets_references(tmp_path, capsys):
    game_dir = fake_game(tmp_path / "game")
    main(["mesh-refs", "--game", str(game_dir), "--superbundle", FTUE, "--bundle", ISLAND_BUNDLE,
          "--mesh", LIGHTHOUSE + "_mesh"])
    out = capsys.readouterr().out
    assert "document: root RigidMeshAsset" in out and f"{LIGHTHOUSE}_d ({ISLAND_BUNDLE})" in out
    assert f"LOD 0 +116: GUID {LIGHTHOUSE_CHUNK.string()} = chunk entry of {ISLAND_BUNDLE}" in out
    assert f"LOD 1 +116: GUID {LIGHTHOUSE_LOD1_CHUNK.string()} = chunk in {FTUE}'s TOC" in out
    assert "0x12345678 ?: 1" in out and f"res {LIGHTHOUSE}_mesh/blocks type 0x12345678" in out
    assert "depot " in out and "ShaderBlockDepot" in out
    assert "LOD 0 section 0: shader-block key" in out and f"_win32_shaderstate/shaderblockdepot_7 ({ISLAND_BUNDLE})" in out
    # Against our level: util_groundplane's mesh is in our root bundle, whose depot (bam's) holds its keys.
    _, _, _, root, _ = build(tmp_path / "b", bam="333")
    main(["mesh-refs", "--game", str(tmp_path / "b" / "game"), "--mod", str(root), "--superbundle", BAM,
          "--bundle", "win32/" + BAM, "--mesh", GROUNDPLANE + "_mesh", "--level", LEVEL])
    out = capsys.readouterr().out
    assert "mesh EBX listed: True; its MeshSet listed: True" in out
    assert f"the MeshSet is in our win32/{NEW}: its depots hold 2 of 2 section keys" in out
    assert f"in {BAM}_win32_shaderstate/shaderblockdepot_1 (win32/{BAM})" in out


def test_stock_prop_is_placed_as_it_is(tmp_path):
    _, _, report, root, view = build(tmp_path, bam="333", props=[(BOX, (3.0, 300.0, 4.0))])
    text = "\n".join(report)
    assert f"--prop {BOX} at [3.0, 300.0, 4.0]: its {BOX}.physics (version 2, 3 shapes, 3 parts, materials ['0x10e0'])" \
        in text and f"2 of 2 in win32/{BAM}'s depots" in text
    _, _, manifest, (_, layer, bp) = ground(view)
    placements = [layer.instances[p.index] for p in layer.instances[0].object.get("Objects")]
    assert len(placements) == 2
    prop = placements[1]
    assert (layer.imports[prop.object.get("Blueprint").index].file_guid,
            layer.imports[prop.object.get("Blueprint").index].class_guid) == (BOX_GUID, guid(123))
    assert transform(prop.object) == [[1, 0, 0], [0, 1, 0], [0, 0, 1], [3, 300, 4]]
    assert prop.instance_guid not in {placements[0].instance_guid, PLACEMENT_GUID}
    assert BOX not in [a.name for a in manifest.ebx]               # nothing of the box is copied: it is in the root copy


def test_shader_from_gives_our_mesh_a_stock_material_in_the_root_bundle(tmp_path):
    _, _, report, root, view = build(tmp_path, bam="333", mesh="own", shader_from=BOX + "_mesh:0",
                                     asset_bundle="root", size=20.0)
    text = "\n".join(report)
    key = section_keys(BOX + "_mesh")[0]
    assert f"--shader-from {BOX}_mesh section 0 (of 2): key {key:#018x}, in the depots" in text
    assert "donor section: key" in text and "section layout: usage 1 format 3 (Float3) at +0 of stream 0" in text
    assert "NOT DRAWN" not in text
    toc = read_toc((root / "Win32" / (NEW + ".toc")).read_bytes())
    _, region, manifest = view.bundle(toc, "win32/" + NEW)
    mesh_name = f"levels/game/{LEVEL}/ground_mesh"
    names = [a.name for a in manifest.ebx]
    assert BLUEPRINT in names and mesh_name in names
    assert {PHYSICS, mesh_name} <= {a.name for a in manifest.resources}
    index = next(i for i, a in enumerate(manifest.resources) if a.name == mesh_name)
    ms = view.payload(region, 1 + len(manifest.ebx) + index)
    layout = read_layout(ms)
    assert all(int.from_bytes(ms[s.at + 0x138:s.at + 0x140], "little") == key for lod in layout for s in lod.sections)
    assert all(s.elements == [(1, 3, 0, 0), (33, 6, 0, 1), (52, 13, 4, 1)] and s.strides == [12, 8]
               for lod in layout for s in lod.sections)                # the donor section's declaration everywhere
    # Without the root bundle the dry run says it will not be drawn.
    _, _, report, _, _ = build(tmp_path / "g", mesh="own", shader_from=BOX + "_mesh:0")
    assert any("WARNING: NOT DRAWN" in line for line in report)
    with pytest.raises(ValueError, match="add --mesh own"):
        build(tmp_path / "h", shader_from=BOX + "_mesh")


def test_cli_props_on_a_place(tmp_path, capsys):
    from test_place import write_tiles
    game_dir = fake_game(tmp_path / "game")
    tiles = write_tiles(tmp_path / "tiles")
    main(["new-place-level", "--game", str(game_dir), "--name", "zz-place", "--level", LEVEL, "--tiles", str(tiles),
          "--prop", BOX, "--prop-at", "1,300.5,2", "--dry-run"])
    out = capsys.readouterr().out
    assert f"--prop {BOX} at [1.0, 300.5, 2.0]" in out and "and 1 stock props" in out
    with pytest.raises(SystemExit, match="one --prop-at"):
        main(["new-place-level", "--game", str(game_dir), "--name", "zz-place", "--level", LEVEL, "--tiles",
              str(tiles), "--prop", BOX, "--dry-run"])


def test_coordinates_take_any_separator(tmp_path, capsys):
    game_dir = fake_game(tmp_path / "game")
    for at in (["1", "300", "2"], ["1,300,2"], ["1;300;2"], ["1 300 2"]):
        main(["new-ground-level", "--game", str(game_dir), "--name", "zz-g", "--level", LEVEL, "--dry-run", "--at", *at,
              "--prop", BOX, "--prop-at", *at])
        out = capsys.readouterr().out
        assert "placements of 50.0 m at [1.0, 300.0, 2.0]" in out and f"--prop {BOX} at [1.0, 300.0, 2.0]" in out
    with pytest.raises(SystemExit, match="three numbers"):
        main(["new-ground-level", "--game", str(game_dir), "--name", "zz-g", "--level", LEVEL, "--dry-run", "--at", "1,2"])


def test_own_mesh_on_a_stock_blueprint_clone(tmp_path):
    _, _, report, root, view = build(tmp_path, bam="333", mesh="own", blueprint_template=BOX, asset_bundle="root",
                                     collision_mode="template", size=20.0, mesh_shape="plane")
    text = "\n".join(report)
    assert "boxes found in the structs: section +336" in text and "is each template section's surface area" in text
    assert "    section 1: 1 triangles, 1 vertices, material" in text and "    section 0: 200 triangles" not in text
    assert "    section 0: 32 triangles, 25 vertices" in text                # 20 m in 5 m cells
    with pytest.raises(ValueError, match="crashed the game"):
        build(tmp_path / "k", mesh="own", blueprint_template=BOX, asset_bundle="root", keep_section=0)
    assert "the template's own .physics, unchanged" in text and "NOT DRAWN" not in text
    toc = read_toc((root / "Win32" / (NEW + ".toc")).read_bytes())
    _, region, manifest = view.bundle(toc, "win32/" + NEW)
    mesh_name = f"levels/game/{LEVEL}/ground_mesh"
    resources = {a.name: 1 + len(manifest.ebx) + i for i, a in enumerate(manifest.resources)}
    ms = view.payload(region, resources[mesh_name])
    lod = read_layout(ms)[0]
    assert len(lod.sections) == 2 and int.from_bytes(ms[182:184], "little") == 2     # every section kept
    section = lod.sections[0]
    assert int.from_bytes(ms[section.at + 0x138:section.at + 0x140], "little") == section_keys(BOX + "_mesh")[0]
    unpack = __import__("struct").unpack_from
    lo, hi = unpack("<3f", ms, section.at + 336), unpack("<3f", ms, section.at + 352)   # the section's box, ours now
    assert (lo[0], lo[2], hi[0], hi[2]) == (-10.0, -10.0, 10.0, 10.0) and hi[1] - lo[1] > 0.05     # never flat
    box_physics = Game(view.root).bundle(Game(view.root).superbundle(BAM), "win32/" + BAM)
    physics = view.payload(region, resources[PHYSICS])
    _, base_region, base_manifest = box_physics
    base = next(1 + len(base_manifest.ebx) + i for i, a in enumerate(base_manifest.resources) if a.name == BOX + ".physics")
    assert physics == Game(view.root).payload(base_region, base)           # the template's own collision
    bp = read_document(view.ebx(region, manifest, BLUEPRINT))
    assert bp.imports[0].file_guid != guid(121)                              # its mesh import moved to ours


def test_own_collision_takes_the_templates_part_count(tmp_path):
    _, _, report, root, view = build(tmp_path, bam="333", blueprint_template=BOX, asset_bundle="root")
    assert any("3 parts as the template has (strips of the square, materials ['0x10e0', '0x10e0', '0x10e0'])" in line
               for line in report)
    toc = read_toc((root / "Win32" / (NEW + ".toc")).read_bytes())
    _, region, manifest = view.bundle(toc, "win32/" + NEW)
    index = next(1 + len(manifest.ebx) + i for i, a in enumerate(manifest.resources) if a.name == PHYSICS)
    shapes, parts, _ = read_physics(view.payload(region, index))
    assert len(shapes) == 3 and [p.word_34 for p in parts] == [0, 1, 2]
    zs = sorted({v[2] for s in shapes for v in s.vertices})
    assert zs[0] == -25 and zs[-1] == 25


def test_mesh_shapes_box_and_donor_copy(tmp_path):
    _, _, report, _, _ = build(tmp_path / "a", mesh="own", blueprint_template=BOX, asset_bundle="root",
                               mesh_shape="box")
    assert any("--mesh-shape box: a closed box [-25.0, 0.0, -25.0]..[25.0, 0.0, 25.0]" in line for line in report)
    assert any("ours: 24 vertices, 12 triangles per section" in line for line in report)
    _, _, report, root, view = build(tmp_path / "b", mesh="own", blueprint_template=BOX, asset_bundle="root",
                                     mesh_shape="donor-copy")
    assert any("copy of the template's own geometry" in line for line in report)
    toc = read_toc((root / "Win32" / (NEW + ".toc")).read_bytes())
    _, region, manifest = view.bundle(toc, "win32/" + NEW)
    ours = manifest.chunks[-1]
    assert ours.guid != BOX_CHUNK
    from test_level import meshset as fixture_meshset
    chunk_index = 1 + len(manifest.ebx) + len(manifest.resources) + len(manifest.chunks) - 1
    assert view.payload(region, chunk_index) == fixture_meshset(BOX_CHUNK, BOX + "_mesh")[1]


def test_meshset_diff_prints_the_fields_that_differ(tmp_path, capsys):
    _, _, _, root, _ = build(tmp_path, bam="333", mesh="own", blueprint_template=BOX, asset_bundle="root",
                             size=20.0)
    main(["meshset-diff", "--game", str(tmp_path / "game"), "--mod-a", str(root),
          "--a", f"{NEW}:win32/{NEW}:levels/game/{LEVEL}/ground_mesh", "--b", f"{BAM}:win32/{BAM}:{BOX}_mesh"])
    out = capsys.readouterr().out
    assert "header +16 box min" in out and "LOD 0 +116 chunk GUID" in out and "LOD 0 +88 index bytes" in out
    assert "LOD 0: sections 2 vs 2" in out and "LOD 0 section 0 +32 triangle count" in out


def test_write_mesh_can_fill_every_section():
    ms, chunk = meshset(guid(1))
    verts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)]
    out, chunks, report = write_mesh(ms, [chunk], verts, [(0, 2, 1)], [guid(2)], geometry_section=None)
    lod = read_layout(out)[0]
    assert [s.triangle_count for s in lod.sections] == [1, 1] and [s.vertex_count for s in lod.sections] == [3, 3]
    assert not any("WARNING" in line for line in report)


def test_default_slab_with_padded_bounds_part_boxes_and_bounds_fields(tmp_path, capsys):
    game_dir = fake_game(tmp_path / "game")
    main(["new-ground-level", "--game", str(game_dir), "--name", "zz-g", "--level", LEVEL, "--bam", "333", "--mesh", "own",
          "--blueprint-template", BOX, "--asset-bundle", "root", "--size", "12,0.2,8", "--dry-run"])
    out = capsys.readouterr().out
    assert "--mesh-shape slab: 12.0 x 0.2 x 8.0 m, its top at the ground's height" in out
    assert "ours: 24 vertices, 12 triangles per section" in out and "box [-6.0, -0.2, -4.0]..[6.0, 0.0, 4.0]" in out
    assert "collision part 0: material 0x10e0, box [-6.0, 0.0, -4.0]..[6.0, 0.0, " in out
    assert "collision part 2: material 0x10e0, box [-6.0, 0.0, " in out and ", 0.0, 4.0]" in out
    assert "TexCoord0 (usage 33): planar per face" in out
    for size in ("12", "12 0.2 12", "12;0.2;12"):
        main(["new-ground-level", "--game", str(game_dir), "--name", "zz-g", "--level", LEVEL, "--dry-run",
              "--size", *size.split(" ")])
        assert "placements of 12.0 m" in capsys.readouterr().out


def test_vertex_helpers():
    from reskate_mapbuilder.meshset import decode_element, encode_element, padded_bounds, planar_uv, vertex_normals
    lo, hi = padded_bounds([(0.0, 1.0, 0.0), (2.0, 1.0, 3.0)])
    assert lo == [0.0, 0.95, 0.0] and hi == [2.0, 1.05, 3.0]
    assert planar_uv((2.0, 1.0, 3.0), (0.0, 1.0, 0.0), 0.5) == (1.0, 1.5)
    assert planar_uv((2.0, 1.0, 3.0), (1.0, 0.0, 0.0), 1.0) == (3.0, 1.0)
    assert planar_uv((2.0, 1.0, 3.0), (0.0, 0.0, -1.0), 1.0) == (2.0, 1.0)
    buffer = bytearray(struct_pack_half4())
    encode_element(8, buffer, 0, (1.5, -2.0, 3.0))                     # Half4: the fourth half is kept
    assert decode_element(8, buffer, 0) == [1.5, -2.0, 3.0, 1.0]
    buffer = bytearray(4)
    encode_element(13, buffer, 0, (0.0, 0.0, 1.0, 0.5))                 # UByte4N
    assert bytes(buffer) == bytes([0, 0, 255, 128]) and decode_element(12, buffer, 0) == [0.0, 0.0, 255.0, 128.0]
    encode_element(6, buffer, 0, (0.25, -1.0))                          # Half2
    assert decode_element(6, buffer, 0) == [0.25, -1.0]
    from reskate_mapbuilder.meshset import box_geometry
    vertices, triangles = box_geometry((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    normals = vertex_normals(vertices, triangles)
    assert sorted(set(normals)) == sorted({(0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (-1.0, 0.0, 0.0),
                                          (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)})


def test_a_blueprint_importing_its_collision_ebx_gets_ours(tmp_path, capsys):
    _, _, report, root, view = build(tmp_path, bam="333", mesh="own", blueprint_template=BOX, asset_bundle="root")
    text = "\n".join(report)
    assert f"collision EBX {BOX}.physics" in text and "imported 1 time(s) by the blueprint" in text
    assert "1 resource references moved to our resource, 1 blueprint imports moved to it" in text
    assert f"our blueprint imports {PHYSICS} (ours)" in text and f"our blueprint imports levels/game/{LEVEL}/ground_mesh (ours)" in text
    toc = read_toc((root / "Win32" / (NEW + ".toc")).read_bytes())
    _, region, manifest = view.bundle(toc, "win32/" + NEW)
    bp = read_document(view.ebx(region, manifest, BLUEPRINT))
    physics_ebx = read_document(view.ebx(region, manifest, PHYSICS))
    assert physics_ebx.instances[0].object.get("Name") == PHYSICS
    assert physics_ebx.instances[0].object.get("MeshSetResource").id == resource_id(PHYSICS)
    assert {i.file_guid for i in bp.imports} == {physics_ebx.file_guid, read_document(
        view.ebx(region, manifest, f"levels/game/{LEVEL}/ground_mesh")).file_guid}
    assert BOX_PHYSICS_GUID not in {i.file_guid for i in bp.imports}
    assert any("TexCoord0 (usage 33): planar per face at 1 per metre (the ground's default, 1 per metre)" in line
               for line in report)
    # --collision template keeps the box's collision EBX.
    _, _, report, root, view = build(tmp_path / "t", bam="333", mesh="own", blueprint_template=BOX, asset_bundle="root",
                                     collision_mode="template")
    assert f"our blueprint imports {BOX}.physics" in "\n".join(report)
    # --uv-per-metre reaches the mesh.
    main(["new-ground-level", "--game", str(tmp_path / "game"), "--name", "zz", "--level", LEVEL, "--mesh", "own",
          "--blueprint-template", BOX, "--asset-bundle", "root", "--uv-per-metre", "0.25", "--dry-run"])
    assert "TexCoord0 (usage 33): planar per face at 0.25 per metre (given)" in capsys.readouterr().out


def struct_pack_half4():
    return __import__("struct").pack("<4e", 0.0, 0.0, 0.0, 1.0)
