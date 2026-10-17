"""new-ground-level against test_level's fake game: bam's level with the base game's own chain
tile -> LayerReferenceObjectData -> layer -> ObjectReferenceObjectData -> util_groundplane, whose
collision is found by name (FORMAT-NOTES 6.3)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_level import (BAM, BP_GUID, BP_ROOT_GUID, CHUNK, CORE_BUNDLE, CORE_LAYER, GROUNDPLANE, LAYER_GUID,  # noqa: E402
                        LAYER_REFERENCE_GUID, LEVEL, MESH_CHUNK, MESH_GUID, NEW, PLACEMENT_GUID, TEMPLATE_MATERIAL, TILE,
                        TILE_GUID, fake_game, genprefab, guid, ptr)
from reskate_mapbuilder.__main__ import main  # noqa: E402
from reskate_mapbuilder.ebx import PointerKind, PointerReference, read_document, write_document  # noqa: E402
from reskate_mapbuilder.ebxedit import (add_import, clone_instance, derived_guid, guid_flags, path_to,  # noqa: E402
                                        prune_imports, prune_unreachable, remove_instances)
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
    assert f"collision {GROUNDPLANE}.physics" in text and "matched by name" in text
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
    assert "header 192, LOD struct 176, section struct 384" in out and "0 sections @" in out
