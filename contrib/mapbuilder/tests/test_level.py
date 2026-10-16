"""new-level against a fake game: bam's level, globals and the root level, with small EBX documents
of the types FORMAT-NOTES 6.1 names, written with the package's own EBX writer."""
import hashlib
import json
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reskate_mapbuilder import nativedb  # noqa: E402
from reskate_mapbuilder.__main__ import main  # noqa: E402
from reskate_mapbuilder.bundle import (BinaryBundle, BundleAsset, BundleFileInfo, CasIdentifier, EBX, Guid,  # noqa: E402
                                       read_bundle_region, write_binary_bundle, write_bundle_region)
from reskate_mapbuilder.cas import decode_raw, encode_raw  # noqa: E402
from reskate_mapbuilder.ebx import (Document, FieldCategory as C, FieldType as T, FieldValue, InstanceRecord,  # noqa: E402
                                    Object, PointerKind, PointerReference, ensure_fixup_type, read_document,
                                    write_document)
from reskate_mapbuilder.ebxcheck import roundtrip  # noqa: E402
from reskate_mapbuilder.ebxedit import (after_last_of_type, clone_document, clone_instance, derived_guid,  # noqa: E402
                                        describe, find_objects, guid_flags, pointer_arrays)
from reskate_mapbuilder.game import Game  # noqa: E402
from reskate_mapbuilder.level import new_level  # noqa: E402
from reskate_mapbuilder.mod import ModBuilder  # noqa: E402
from reskate_mapbuilder.toc import TocBundle, TocChunk, bundle_hash, read_toc, write_patch_toc  # noqa: E402

CHUNK = 0x7837152C
PACKAGE = "configurations/layout/defaultinstallpackage"
BAM = "levels/game/bam_levelroot/bam_levelroot"
ROOT = "levels/game/dingolevel_root/dingolevel_root"
LEVEL = "dingolevel_reskate_test"
NEW = f"levels/game/{LEVEL}/{LEVEL}"


def enc(kind, category):
    return (kind << 4) | category


# (name, size, alignment, fields); a field is (name, encoded type, class ref, offset).
TYPES = [
    ("DataContainer", 16, 8, [("Flags", enc(T.uint32, C.primitive), 0, 8)]),
    ("Asset", 24, 8, [("$", enc(T.inherited, C.none), 0, 0), ("Name", enc(T.cString, C.primitive), 0, 16)]),
    ("OnDemandBundle", 16, 8, [("SuperBundle", enc(T.cString, C.primitive), 0, 0),
                               ("BundleHash", enc(T.uint32, C.primitive), 0, 8)]),
    ("LevelDescriptionAsset", 56, 8, [("$", enc(T.inherited, C.none), 1, 0),
                                      ("LevelName", enc(T.cString, C.primitive), 0, 24),
                                      ("LevelGuid", enc(T.guid, C.primitive), 0, 32),
                                      ("OnDemandBundles", enc(T.structure, C.array), 2, 48)]),
    ("LevelStartPoint", 24, 8, [("Name", enc(T.cString, C.primitive), 0, 0),
                                ("AutoloadSublevels", enc(T.cString, C.array), 0, 8),
                                ("IsDefault", enc(T.boolean, C.primitive), 0, 16)]),
    ("DetachedSubLevelInfo", 16, 8, [("DetachedSubLevelName", enc(T.cString, C.primitive), 0, 0),
                                     ("StartPoints", enc(T.structure, C.array), 4, 8)]),
    ("SubWorldReferenceObjectData", 128, 16, [("$", enc(T.inherited, C.none), 0, 0),
                                              ("BundleName", enc(T.cString, C.primitive), 0, 16),
                                              ("IsDetachedSubLevel", enc(T.boolean, C.primitive), 0, 24),
                                              ("AutoLoad", enc(T.boolean, C.primitive), 0, 25),
                                              ("StreamRealm", enc(T.enumeration, C.enumeration), 0, 28),
                                              ("ObjectSubVariation", enc(T.uint32, C.primitive), 0, 32),
                                              ("BlueprintTransform", enc(T.structure, C.structure), 10, 48),
                                              ("Blueprint", enc(T.pointer, C.pointer), 0, 112)]),
    ("DetachedSetBlueprint", 40, 8, [("$", enc(T.inherited, C.none), 1, 0),
                                     ("Objects", enc(T.pointer, C.array), 0, 24),
                                     ("DetachedSubLevels", enc(T.structure, C.array), 5, 32)]),
    ("DetachedSubWorldData", 48, 8, [("$", enc(T.inherited, C.none), 1, 0),
                                     ("WorldSizeXZ", enc(T.float32, C.primitive), 0, 24),
                                     ("WorldSizeY", enc(T.float32, C.primitive), 0, 28),
                                     ("StartLocalServer", enc(T.boolean, C.primitive), 0, 32),
                                     ("UseExtendedStartPoints", enc(T.boolean, C.primitive), 0, 33),
                                     ("Objects", enc(T.pointer, C.array), 0, 40)]),
    ("Vec3", 16, 16, [("x", enc(T.float32, C.primitive), 0, 0), ("y", enc(T.float32, C.primitive), 0, 4),
                      ("z", enc(T.float32, C.primitive), 0, 8)]),
    ("LinearTransform", 64, 16, [(n, enc(T.structure, C.structure), 9, o)
                                 for n, o in (("right", 0), ("up", 16), ("forward", 32), ("trans", 48))]),
]
INDEX = {t[0]: i for i, t in enumerate(TYPES)}


def guid(seed):
    return Guid(bytes((seed * 29 + k * 11 + 3) & 0xFF for k in range(16)))


def reflection():
    strings, offsets = bytearray(), {}

    def name(text):
        if text not in offsets:
            offsets[text] = len(strings)
            strings.extend(text.encode() + b"\0")
        return offsets[text]

    body = bytearray(struct.pack("<I", len(TYPES)))
    for index in range(len(TYPES)):
        body += guid(100 + index).bytes + struct.pack("<I", 0xBEEF0000 + index)
    body += struct.pack("<I", len(TYPES))
    first = 0
    for type_name, size, alignment, fields in TYPES:
        body += struct.pack("<IiHHHH", name(type_name), first, len(fields), enc(T.dbObject, 0) << 1, size, alignment)
        first += len(fields)
    body += struct.pack("<I", first)
    for _, _, _, fields in TYPES:
        for field_name, encoded, class_ref, offset in fields:
            body += struct.pack("<IIHH", name(field_name), offset, encoded << 1, class_ref)
    body += struct.pack("<II", 0, 0) + strings
    return bytes(body)


def obj(doc, type_name, **values):
    out = Object(INDEX[type_name])

    def walk(index):
        t = doc.types[index]
        for i in range(t.field_index, t.field_index + t.field_count):
            f = doc.fields[i]
            if f.type == T.inherited:
                walk(f.class_ref)
            else:
                out.fields.append(FieldValue(i, f.name, values.get(f.name, [] if f.category == C.array else None)))
    walk(INDEX[type_name])
    return out


def document(file_guid, instances):
    """instances: (object builder, exported guid or None); the builders get the empty document."""
    doc = read_document(write_document(Document(file_guid=file_guid, reflection_data=reflection())))
    for build, exported in instances:
        o = build(doc)
        doc.instances.append(InstanceRecord(ensure_fixup_type(doc, o.descriptor), o.descriptor,
                                            exported=exported is not None, instance_guid=exported or Guid(), object=o))
    return read_document(write_document(doc))


def ptr(index):
    return PointerReference(PointerKind.internal, index)


BAM_ROOT_GUID = guid(1)


def bam_root():
    return document(BAM_ROOT_GUID, [
        (lambda d: obj(d, "DetachedSubWorldData", Name="Levels/Game/BAM_LevelRoot/BAM_LevelRoot", WorldSizeXZ=2048.0,
                       WorldSizeY=512.0, Objects=[ptr(1)]), guid(2)),
        (lambda d: obj(d, "SubWorldReferenceObjectData", BundleName="Levels/Game/BAM_LevelRoot/TOD_2_Noon",
                       StreamRealm=1), None)])


def bam_description():
    return document(guid(3), [(lambda d: obj(d, "LevelDescriptionAsset",
                                             Name="Levels/Game/BAM_LevelRoot/BAM_LevelRoot/Description",
                                             LevelName="Levels/Game/BAM_LevelRoot/BAM_LevelRoot",
                                             LevelGuid=BAM_ROOT_GUID), guid(4))])


def root_description():
    def build(d):
        entries = [obj(d, "OnDemandBundle", SuperBundle=name, BundleHash=bundle_hash(name))
                   for name in ("Levels/game/BAM_LevelRoot/_DEBUG_/BuildkitPerfTests",
                                "levels/game/BAM_LevelRoot/BAM_LevelRoot", "Levels/Game/DingoLevel_MPR/DingoLevel_MPR")]
        return obj(d, "LevelDescriptionAsset", Name="Levels/Game/DingoLevel_Root/DingoLevel_Root/Description",
                   OnDemandBundles=entries)
    return document(guid(5), [(build, guid(6))])


def genprefab():
    def start(d, name, default):
        return obj(d, "LevelStartPoint", Name=name, IsDefault=default,
                   AutoloadSublevels=["Levels/Game/BAM_LevelRoot/StartPoint_Sublevels", "Levels/Game/BAM_LevelRoot/X"])

    def blueprint(d):
        infos = [obj(d, "DetachedSubLevelInfo", DetachedSubLevelName="Levels/Game/BAM_LevelRoot/BAM_LevelRoot",
                     StartPoints=[start(d, "Elsewhere", False), start(d, "DefaultStartPoint", True)]),
                 obj(d, "DetachedSubLevelInfo", DetachedSubLevelName="Levels/Game/DingoLevel_MPR/DingoLevel_MPR",
                     StartPoints=[start(d, "DefaultStartPoint", True)])]
        return obj(d, "DetachedSetBlueprint", Name="Levels/Game/DingoLevel_Root/DingoLevel_Root_DSubLevelManager_GenPrefab",
                   Objects=[ptr(1), ptr(2), ptr(3)], DetachedSubLevels=infos)

    def transform(d, x):
        return obj(d, "LinearTransform", trans=obj(d, "Vec3", x=x, y=2.0, z=3.0), right=obj(d, "Vec3", x=0.0, z=1.0))
    debug = "Levels/Game/BAM_LevelRoot/_DEBUG_/BuildkitPerfTests"
    return document(guid(7), [
        (blueprint, guid(8)),
        (lambda d: obj(d, "SubWorldReferenceObjectData", Flags=0x1000000 | 0x334455, BundleName=debug,
                       IsDetachedSubLevel=True, StreamRealm=3), Guid.parse("00334455-0000-0000-0000-000000000001")),
        (lambda d: obj(d, "SubWorldReferenceObjectData", Flags=0xB69B78, BundleName="Levels/Game/BAM_LevelRoot/BAM_LevelRoot",
                       IsDetachedSubLevel=True, StreamRealm=3, ObjectSubVariation=0xFFFFFFFF,
                       BlueprintTransform=transform(d, 5.0), Blueprint=ptr(4)),
         Guid.parse("6ab69b78-1111-2222-3333-444455556666")),
        (lambda d: obj(d, "SubWorldReferenceObjectData", BundleName="Levels/Game/DingoLevel_MPR/DingoLevel_MPR",
                       IsDetachedSubLevel=True, StreamRealm=3), guid(10)),
        (lambda d: obj(d, "Asset", Name="a later instance of another kind"), guid(11))])


def node(type_, name=None, payload=b"", children=None):
    return nativedb.Node(type_, name is not None, children is not None, name or "", payload, children or [])


def layout_bytes():
    rows = [node(2, None, children=[node(7, "name", f"Win32/{n}\0".encode())]) for n in ("globals", BAM, ROOT)]
    chunk = node(2, None, children=[node(7, "name", b"Configurations/Layout/DefaultInstallPackage\0"),
                                    node(8, "persistentIndex", struct.pack("<I", CHUNK)),
                                    node(1, "superbundles", children=[node(7, None, f"Win32/{n}\0".encode())
                                                                      for n in ("globals", BAM, ROOT)])])
    root = node(2, None, children=[node(1, "superBundles", children=rows),
                                   node(2, "installManifest", children=[node(1, "installChunks", children=[chunk])])])
    return nativedb.MAGIC + bytes(nativedb.ENVELOPE_SIZE - 4) + nativedb.write(root)


def fake_game(tmp_path):
    data = tmp_path / "Data"
    (data / "Win32" / PACKAGE).mkdir(parents=True)
    (data / "layout.toc").write_bytes(layout_bytes())
    cas = bytearray()

    def bundle(name, ebx):
        files = []
        for payload in ebx.values():
            stored = encode_raw(payload)
            files.append(BundleFileInfo(CasIdentifier(False, CHUNK, 2), len(cas), len(stored)))
            cas.extend(stored)
        manifest = write_binary_bundle(BinaryBundle(ebx=[
            BundleAsset(EBX, n, hashlib.sha1(p).digest(), original_size=len(p)) for n, p in ebx.items()]))
        files.insert(0, BundleFileInfo(CasIdentifier(False, CHUNK, 2), len(cas), len(manifest)))
        cas.extend(manifest)
        return TocBundle(name, write_bundle_region(files), 1)

    tocs = {
        "globals": [bundle("win32/globals", {"some/other/asset": b"x" * 9}),
                    bundle("win32/globals_levels", {BAM + "/description": write_document(bam_description()),
                                                    ROOT + "/description": write_document(root_description())})],
        BAM: [bundle("win32/" + BAM, {BAM: write_document(bam_root()), "levels/game/bam_levelroot/pool": b"pool"}),
              bundle("win32/levels/game/bam_levelroot/tod_2_noon", {"levels/game/bam_levelroot/tod_2_noon": b"tod"}),
              bundle("win32/levels/game/bam_levelroot/startpoint_sublevels", {"sp": b"sp"})],
        ROOT: [bundle("win32/" + ROOT, {ROOT + "_dsublevelmanager_genprefab": write_document(genprefab())})],
    }
    chunks = {BAM: [TocChunk(guid(50), CasIdentifier(False, CHUNK, 2), 0, 4)]}
    for path, bundles in tocs.items():
        target = data / "Win32" / (path + ".toc")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(write_patch_toc(bundles, chunks.get(path)))
    (data / "Win32" / PACKAGE / "cas_02.cas").write_bytes(bytes(cas))
    return tmp_path


# ---- Layout -------------------------------------------------------------------------------------

def test_layout_round_trips_and_gains_a_superbundle_in_both_lists():
    original = layout_bytes()
    assert nativedb.write(nativedb.read_layout(original)) == original[nativedb.ENVELOPE_SIZE:]
    edited = nativedb.add_superbundle(original, "Win32/" + NEW)
    assert edited[:nativedb.ENVELOPE_SIZE] == original[:nativedb.ENVELOPE_SIZE]
    root = nativedb.read_layout(edited)
    names = nativedb.layout_superbundles(root)
    assert names == sorted(names, key=str.lower) and "Win32/" + NEW in names and len(names) == 4
    held = [n.text for n in nativedb.install_chunk(root, PACKAGE).field("superbundles").children]
    assert held == sorted(held, key=str.lower) and "Win32/" + NEW in held
    added = next(r for r in root.field("superBundles").children if r.field("name").text == "Win32/" + NEW)
    assert added.type == 2 and added.terminated and not added.named
    with pytest.raises(ValueError):
        nativedb.add_superbundle(edited, "win32/" + NEW.upper())
    with pytest.raises(KeyError):
        nativedb.add_superbundle(original, "Win32/x", "configurations/layout/nothere")


# ---- EBX editing --------------------------------------------------------------------------------

def test_clone_instance_renumbers_pointers_and_joins_the_arrays():
    doc = genprefab()
    found = find_objects(doc, "SubWorldReferenceObjectData")
    assert [f.instance for f in found] == [1, 2, 3]
    holders = pointer_arrays(doc, 2)
    index = clone_instance(doc, 2, derived_guid("t"), after_last_of_type(doc, 2))
    assert index == 4                      # after the last of its kind, as the 333 map puts its own
    holders[0][1].append(PointerReference(PointerKind.internal, index))
    back = read_document(write_document(doc))
    objects = back.instances[0].object.get("Objects")
    assert [p.index for p in objects] == [1, 2, 3, 4]
    assert back.instances[5].object.get("Name") == "a later instance of another kind"
    assert back.instances[4].object.get("Blueprint").index == 5     # moved with the instance it points at
    assert back.instances[4].instance_guid == derived_guid("t") and back.instances[4].exported
    with pytest.raises(ValueError):
        clone_instance(doc, 2, derived_guid("u"), at=0)     # the root stays first


def test_flags_follow_the_guid_first_group():
    for text, template, flags in (("742c6106-0000-0000-0000-000000000000", 0, 0x2C6106),
                                  ("6ab69b78-0000-0000-0000-000000000000", 0xB69B78, 11967352),
                                  ("8fd8c2da-0000-0000-0000-000000000000", 0x1000000, 0x1D8C2DA),
                                  ("4914e18a-0000-0000-0000-000000000000", 0x1ABCDEF, 0x114E18A)):
        g = Guid.parse(text)
        assert g.string() == text
        assert guid_flags(g, template) == flags


def test_roundtrip_explains_a_rewrite_that_differs():
    from test_ebx_crosscheck import imports_only
    data = imports_only(True)            # hand-built: EFIX first, no empty-array block
    report = roundtrip(data)
    assert report[0].startswith("rewrite DIFFERS") and "SEMANTICALLY EQUAL" in report[0]
    assert any("DIFFERENT ORDER" in line for line in report)
    rewritten = write_document(read_document(data))
    assert roundtrip(rewritten) == [f"rewrite identical ({len(rewritten)} bytes)"]


def test_clone_document_takes_new_guids_and_keeps_the_rest():
    doc = bam_root()
    clone = clone_document(doc, guid(77), "salt")
    assert clone.file_guid == guid(77) and doc.file_guid == BAM_ROOT_GUID
    assert clone.instances[0].instance_guid == derived_guid("salt", guid(2).string())
    back = read_document(write_document(clone))
    assert back.instances[0].object.get("Objects")[0].index == 1
    assert any("SubWorldReferenceObjectData" in line for line in describe(back))


# ---- new-level ----------------------------------------------------------------------------------

def test_new_level_writes_the_registration(tmp_path):
    game = Game(fake_game(tmp_path / "game"))
    mod = ModBuilder(game, "zz-new-level")
    report = new_level(game, mod, LEVEL, "Test level")
    assert sum("rewrite identical" in line for line in report) == 4
    assert not any("DIFFERS" in line or "MISSING" in line or "NOT match" in line for line in report)
    root = mod.write(tmp_path / "Mods")
    view = Game(tmp_path / "game", mod=root)

    # Layout: the game's plus the new superbundle.
    assert nativedb.add_superbundle(game.layout_bytes, "Win32/" + NEW) == (root / "layout.toc").read_bytes()
    assert json.loads((root / "reskate-levels.json").read_text()) == {
        "schema": 1, "levels": [{"asset": NEW, "displayName": "Test level", "startPoints": ["DefaultStartPoint"]}]}

    # The level's TOC: bam's bundles as they are, bam's root renamed, the TOC chunk carried.
    toc = read_toc((root / "Win32" / (NEW + ".toc")).read_bytes())
    base = {b.name: b.region for b in game.superbundle(BAM).bundles}
    names = sorted(b.name for b in toc.bundles)
    assert names == sorted(["win32/" + NEW] + [n for n in base if n != "win32/" + BAM])
    for b in toc.bundles:
        if b.name in base:
            assert b.region == base[b.name]
    assert [c.guid for c in toc.chunks] == [guid(50)]
    _, region, manifest = view.bundle(toc, "win32/" + NEW)
    assert [a.name for a in manifest.ebx] == [BAM, "levels/game/bam_levelroot/pool", NEW]
    assert [f.location.patch for f in region.files] == [True, False, False, True]
    raw = view.ebx(region, manifest, NEW)
    assert manifest.ebx[2].sha1 == hashlib.sha1(raw).digest() and manifest.ebx[2].original_size == len(raw)
    new_root = read_document(raw)
    o = new_root.instances[0].object
    assert new_root.file_guid != BAM_ROOT_GUID and new_root.instances[0].instance_guid != guid(2)
    assert (o.get("Name"), o.get("WorldSizeXZ"), o.get("WorldSizeY"), o.get("StartLocalServer"),
            o.get("UseExtendedStartPoints")) == (f"Levels/Game/{LEVEL}/{LEVEL}", 4096.0, 1024.0, True, True)

    # globals: the new description next to bam's, and the root's on-demand entry.
    globals_toc = view.superbundle("globals")
    assert [b.name for b in globals_toc.bundles] == ["win32/globals_levels"]
    _, region, manifest = view.bundle(globals_toc, "win32/globals_levels")
    description = read_document(view.ebx(region, manifest, NEW + "/description"))
    d = description.instances[0].object
    assert d.get("LevelName") == f"Levels/Game/{LEVEL}/{LEVEL}" and d.get("LevelGuid") == new_root.file_guid
    assert d.get("Name") == f"Levels/Game/{LEVEL}/{LEVEL}/Description"
    entries = read_document(view.ebx(region, manifest, ROOT + "/description")).instances[0].object.get("OnDemandBundles")
    assert len(entries) == 4 and (entries[3].get("SuperBundle"), entries[3].get("BundleHash")) == (
        f"levels/game/{LEVEL}/{LEVEL}", bundle_hash(NEW))

    # The root level: a detached sublevel and its sub-world reference.
    root_toc = view.superbundle(ROOT)
    _, region, manifest = view.bundle(root_toc, "win32/" + ROOT)
    doc = read_document(view.ebx(region, manifest, ROOT + "_dsublevelmanager_genprefab"))
    infos = doc.instances[0].object.get("DetachedSubLevels")
    assert len(infos) == 3 and infos[2].get("DetachedSubLevelName") == f"Levels/Game/{LEVEL}/{LEVEL}"
    points = infos[2].get("StartPoints")
    assert [(p.get("Name"), p.get("AutoloadSublevels"), p.get("IsDefault")) for p in points] == [
        ("DefaultStartPoint", ["Levels/Game/BAM_LevelRoot/StartPoint_Sublevels", "Levels/game/BAM_LevelRoot/TOD_2_Noon"],
         True)]
    assert len(infos[0].get("StartPoints")) == 2           # bam's own entry is untouched
    objects = doc.instances[0].object.get("Objects")
    targets = [doc.instances[p.index].object.get("BundleName") for p in objects]
    assert targets == ["Levels/Game/BAM_LevelRoot/_DEBUG_/BuildkitPerfTests", "Levels/Game/BAM_LevelRoot/BAM_LevelRoot",
                       "Levels/Game/DingoLevel_MPR/DingoLevel_MPR", f"Levels/Game/{LEVEL}/{LEVEL}"]
    assert objects[3].index == 4 and doc.instances[5].object.get("Name") == "a later instance of another kind"
    added = doc.instances[4]
    assert added.exported and added.instance_guid != doc.instances[2].instance_guid
    a = added.object
    assert [a.get(k) for k in ("IsDetachedSubLevel", "AutoLoad", "StreamRealm", "ObjectSubVariation")] == [
        True, False, 3, 0xFFFFFFFF]
    assert a.get("Flags") == int.from_bytes(added.instance_guid.bytes[:4], "little") & 0xFFFFFF
    assert a.get("Blueprint").kind == PointerKind.null
    t = a.get("BlueprintTransform")
    assert [[t.get(r).get(c) for c in "xyz"] for r in ("right", "up", "forward", "trans")] == [
        [1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, 0]]
    assert doc.instances[2].object.get("BlueprintTransform").get("trans").get("x") == 5.0   # bam's untouched

    # bam's own TOC is not part of the mod.
    assert not (root / "Win32" / (BAM + ".toc")).exists()


def test_new_level_refuses_a_level_already_there(tmp_path):
    game = Game(fake_game(tmp_path / "game"))
    mod = ModBuilder(game, "zz-new-level")
    mod.layout_bytes = nativedb.add_superbundle(game.layout_bytes, "Win32/" + NEW)
    with pytest.raises(ValueError):
        new_level(game, mod, LEVEL)
    with pytest.raises(ValueError):
        new_level(game, ModBuilder(game, "zz"), "Bad-Name")


def test_new_bundle_holds_new_ebx(tmp_path):
    game = Game(fake_game(tmp_path / "game"))
    mod = ModBuilder(game, "zz-new-bundle")
    mod.add_superbundle(NEW)
    mod.new_bundle(NEW, "win32/" + NEW + "/extra", CHUNK)
    mod.add_ebx(NEW, "win32/" + NEW + "/extra", "levels/x/a", b"first")
    mod.add_ebx(NEW, "win32/" + NEW + "/extra", "levels/x/b", b"second")
    root = mod.write(tmp_path / "Mods")
    view = Game(tmp_path / "game", mod=root)
    _, region, manifest = view.bundle(view.superbundle(NEW), "win32/" + NEW + "/extra")
    assert [a.name for a in manifest.ebx] == ["levels/x/a", "levels/x/b"]
    assert all(f.location == CasIdentifier(True, CHUNK, 1) for f in region.files)
    assert view.ebx(region, manifest, "levels/x/b") == b"second"
    cas = (root / "Win32" / PACKAGE / "cas_01.cas").read_bytes()
    assert decode_raw(cas[region.files[1].offset:region.files[1].offset + region.files[1].size]) == b"first"
    assert len(read_bundle_region(read_toc((root / "Win32" / (NEW + ".toc")).read_bytes()).bundles[0].region).files) == 3


def test_cli_dry_run_writes_nothing(tmp_path, capsys):
    game_dir = fake_game(tmp_path / "game")
    main(["new-level", "--game", str(game_dir), "--mods", str(tmp_path / "Mods"), "--name", "zz-new-level",
          "--level", LEVEL, "--dry-run"])
    out = capsys.readouterr().out
    assert "dry run: nothing written" in out and f"Win32/{NEW}.toc" in out and str(bundle_hash(NEW)) in out
    assert not (tmp_path / "Mods").exists()
    main(["dump-ebx", "--game", str(game_dir), "--superbundle", "globals", "--ebx", ROOT + "/description"])
    assert "OnDemandBundles: [3]" in capsys.readouterr().out
    main(["ebx-roundtrip", "--game", str(game_dir), "--superbundle", "globals", "--ebx", ROOT + "/description"])
    assert "rewrite identical" in capsys.readouterr().out


def test_roundtrip_detail_names_what_a_boxed_value_reaches():
    from test_ebx_crosscheck import boxed_pointing
    from reskate_mapbuilder.ebxcheck import semantic, semantically_equal
    data = boxed_pointing()
    assert any("string b'Gameflow test running'" in line for line in semantic(read_document(data)))
    assert semantically_equal(data)
    report = roundtrip(data, detail=5)
    assert any("from ['boxed #0" in line for line in report)


def test_new_level_refuses_a_document_that_does_not_survive_a_rewrite(tmp_path, monkeypatch):
    import reskate_mapbuilder.level as level
    monkeypatch.setattr(level, "roundtrip", lambda raw, brief=False: ["rewrite DIFFERS: ...; read back NOT semantically equal"])
    game = Game(fake_game(tmp_path / "game"))
    with pytest.raises(ValueError, match="loses or changes data"):
        new_level(game, ModBuilder(game, "zz-new-level"), LEVEL)


def test_array_hash_report_scores_rules():
    from test_ebx_crosscheck import synthetic
    from reskate_mapbuilder.ebxcheck import array_hash_report
    report = array_hash_report(read_document(write_document(synthetic(2, hashed=True))))
    assert report[0].startswith("3 arrays") and any("djb2-xor(field)" in line for line in report)
