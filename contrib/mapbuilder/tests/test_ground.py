"""new-ground-level against test_level's fake game: bam's level with one world-partition tile whose
placement, blueprint, mesh, texture and collision are copied or replaced (FORMAT-NOTES 6.3)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_level import (BAM, BP_GUID, CHUNK, LEVEL, MESH_CHUNK, MESH_GUID, NEW, OTHER_CHUNK, PLACEMENT_GUID, PROPS,  # noqa: E402
                        TEMPLATE_MATERIAL, TEXTURE_CHUNK, TILE, fake_game, genprefab, guid, ptr)
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
BLUEPRINT = f"levels/game/{LEVEL}/ground_bp"
PHYSICS = BLUEPRINT + "/physics"


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


def test_ground_level_writes_the_sublevel_blueprint_and_collision(tmp_path):
    game, mod, report, root, view = build(tmp_path, at=(10.0, 300.0, -20.0))
    text = "\n".join(report)
    assert "placement #4: " in text and "is not in" in text          # the blueprint kept elsewhere is skipped
    assert f"uses placement #5: {PROPS}floor_bp" in text
    assert "1 of 1 resource references named its id" in text
    assert "MeshSet " + PROPS + "floor: box [-2.0, 0.0, -3.0]..[2.0, 0.5, 3.0]" in text and "(in the bundle)" in text
    assert not any("MISSING" in line or "NOT semantically" in line for line in report)

    toc = read_toc((root / "Win32" / (NEW + ".toc")).read_bytes())
    names = {b.name for b in toc.bundles}
    assert "win32/" + SUBLEVEL in names and "win32/" + TILE in names          # --bam all keeps bam's tiles
    _, region, manifest = view.bundle(toc, "win32/" + SUBLEVEL)
    assert [a.name for a in manifest.ebx] == [SUBLEVEL, BLUEPRINT, PROPS + "floor", PROPS + "floor_d"]
    assert [a.name for a in manifest.resources] == [PHYSICS, PROPS + "floor", PROPS + "floor_d"]
    assert [c.guid for c in manifest.chunks] == [MESH_CHUNK, TEXTURE_CHUNK]
    assert OTHER_CHUNK not in [c.guid for c in manifest.chunks]
    patch = [f.location.patch for f in region.files]
    assert patch == [True, True, True, False, False, True, False, False, False, False]
    assert all(f.location.install_chunk == CHUNK for f in region.files)
    # Copied entries read the base archive's bytes.
    assert read_document(view.ebx(region, manifest, PROPS + "floor")).file_guid == MESH_GUID

    # The sublevel: root -> layer -> one placement, moved, pointing at our blueprint.
    sub = read_document(view.ebx(region, manifest, SUBLEVEL))
    bp = read_document(view.ebx(region, manifest, BLUEPRINT))
    assert [sub.types[i.descriptor].name for i in sub.instances] == ["SubWorldData", "LayerData",
                                                                     "ObjectReferenceObjectData"]
    assert sub.instances[0].object.get("Name") == SUBLEVEL
    assert [p.index for p in sub.instances[0].object.get("Objects")] == [1]
    assert [p.index for p in sub.instances[1].object.get("Objects")] == [2]
    placement = sub.instances[2]
    t = placement.object.get("BlueprintTransform")
    assert [[t.get(r).get(c) for c in "xyz"] for r in ("right", "up", "forward", "trans")] == [
        [1, 0, 0], [0, 1, 0], [0, 0, 1], [10, 300, -20]]
    assert placement.instance_guid != PLACEMENT_GUID
    assert placement.object.get("Flags") == guid_flags(placement.instance_guid, 0)   # the source followed the rule
    pointer = placement.object.get("Blueprint")
    assert pointer.kind == PointerKind.external and len(sub.imports) == 1
    assert (sub.imports[0].file_guid, sub.imports[0].class_guid) == (bp.file_guid, bp.instances[0].instance_guid)
    assert sub.file_guid != guid(62) and bp.file_guid != BP_GUID

    # The blueprint: our name, the template's mesh, our collision's id.
    assert bp.instances[0].object.get("Name") == BLUEPRINT
    entity = bp.instances[1].object
    assert entity.get("PhysicsResource").id == resource_id(PHYSICS) == manifest.resources[0].resource_id
    assert bp.imports[0].file_guid == MESH_GUID
    assert manifest.resources[0].resource_type == 0x41759364

    # The collision: one flat 64 m grid, the template part's material.
    _, res_region, _ = view.bundle(toc, "win32/" + SUBLEVEL)
    shapes, parts, version = read_physics(view.payload(res_region, 1 + 4))
    assert version == 2 and len(shapes) == 1 and len(parts) == 1
    assert len(shapes[0].vertices) == 17 * 17 and len(shapes[0].triangles) == 2 * 16 * 16
    xs = [v[0] for v in shapes[0].vertices]
    assert (min(xs), max(xs)) == (-32.0, 32.0) and {v[1] for v in shapes[0].vertices} == {0.0}
    assert parts[0].material == TEMPLATE_MATERIAL and parts[0].translation == (0.0, 0.0, 0.0)
    assert all(shapes[0].edge_value) and any(shapes[0].edge_flat)

    # The root: bam's references kept, one more loading the ground.
    doc, refs = references(view)
    assert [r.object.get("BundleName") for r in refs] == [
        "Levels/Game/BAM_LevelRoot/TOD_2_Noon", "Levels/Game/BAM_LevelRoot/wproot", SUBLEVEL]
    added = refs[2].object
    assert (added.get("AutoLoad"), added.get("IsDetachedSubLevel")) == (True, False)
    blueprint = added.get("Blueprint")
    assert blueprint.kind == PointerKind.external
    assert (doc.imports[blueprint.index].file_guid, doc.imports[blueprint.index].class_guid) == (
        sub.file_guid, sub.instances[0].instance_guid)
    assert refs[1].object.get("Blueprint").index == 0          # bam's own reference keeps its import

    # Registered as new-level registers.
    assert (root / "reskate-levels.json").is_file()


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
    # The ground's own bundle still carries what the blueprint needs from the left-out tile.
    _, _, manifest = view.bundle(toc, "win32/" + SUBLEVEL)
    assert PROPS + "floor" in [a.name for a in manifest.ebx]


def test_333_lists_only_the_bam_bundles_the_333_map_carries(tmp_path):
    _, _, _, root, _ = build(tmp_path, bam="333")
    toc = read_toc((root / "Win32" / (NEW + ".toc")).read_bytes())
    assert sorted(b.name for b in toc.bundles) == sorted([
        "win32/" + NEW, "win32/" + SUBLEVEL, "win32/levels/game/bam_levelroot/tod_2_noon",
        "win32/levels/game/bam_levelroot/startpoint_sublevels"])


def test_ground_level_refuses_an_unusable_placement_and_reports(tmp_path, capsys):
    game_dir = fake_game(tmp_path / "game")
    with pytest.raises(KeyError, match="placement #4"):
        main(["new-ground-level", "--game", str(game_dir), "--name", "zz-g", "--level", LEVEL, "--placement", "4",
              "--dry-run"])
    out = capsys.readouterr().out
    assert "placement #4: " in out and "placement #5: " in out          # the report is printed anyway
    with pytest.raises(KeyError, match="no ObjectReferenceObjectData"):
        new_ground_level(Game(game_dir), ModBuilder(Game(game_dir), "zz"), LEVEL, tile_template=PROPS + "floor_bp")


def test_cli_ground_dry_run_and_list_assets(tmp_path, capsys):
    game_dir = fake_game(tmp_path / "game")
    main(["new-ground-level", "--game", str(game_dir), "--mods", str(tmp_path / "Mods"), "--name", "zz-g",
          "--level", LEVEL, "--size", "32", "--at", "0", "200", "0", "--dry-run"])
    out = capsys.readouterr().out
    assert "dry run: nothing written" in out and "32.0 m square at [0.0, 200.0, 0.0]" in out
    assert "81 vertices, 128 triangles" in out
    assert not (tmp_path / "Mods").exists()
    main(["list-assets", "--game", str(game_dir), "--superbundle", BAM, "--bundle-contains", "s200_", "--contains", "floor"])
    out = capsys.readouterr().out
    assert f"res\t{PROPS}floor_bp/physics" in out and "type 0x41759364" in out
    assert f"ebx\t{PROPS}floor_d" in out and "staticmodelgroup" not in out


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
    main(["inspect-meshset", "--game", str(game_dir), "--superbundle", BAM, "--bundle", "win32/" + TILE,
          "--resource", PROPS + "floor"])
    out = capsys.readouterr().out
    assert "type 0x49b156d4" in out and "1 LODs" in out and MESH_CHUNK.string() in out
    assert "header 192, LOD struct 176, section struct 384" in out and "0 sections @" in out
