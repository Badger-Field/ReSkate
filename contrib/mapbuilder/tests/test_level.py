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
                                       RESOURCE, read_bundle_region, write_binary_bundle, write_bundle_region)
from reskate_mapbuilder.bundle import CHUNK as CHUNK_KIND  # noqa: E402
from reskate_mapbuilder.cas import decode_raw, encode_raw  # noqa: E402
from reskate_mapbuilder.ebx import (Document, FieldCategory as C, FieldType as T, FieldValue, ImportReference,  # noqa: E402
                                    InstanceRecord, Object, PointerKind, PointerReference, ResourceReference,
                                    ensure_fixup_type, read_document, write_document)
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
    # A world-partition tile, its layer and what it places (new-ground-level, FORMAT-NOTES 6.3).
    ("QuadrantSubWorldData", 40, 8, [("$", enc(T.inherited, C.none), 1, 0), ("Objects", enc(T.pointer, C.array), 0, 24),
                                     ("Descriptor", enc(T.pointer, C.pointer), 0, 32)]),
    ("LayerData", 32, 8, [("$", enc(T.inherited, C.none), 1, 0), ("Objects", enc(T.pointer, C.array), 0, 24)]),
    ("ObjectReferenceObjectData", 96, 16, [("$", enc(T.inherited, C.none), 0, 0),
                                           ("BlueprintTransform", enc(T.structure, C.structure), 10, 16),
                                           ("Blueprint", enc(T.pointer, C.pointer), 0, 80)]),
    ("LayerReferenceObjectData", 96, 16, [("$", enc(T.inherited, C.none), 0, 0),
                                          ("BlueprintTransform", enc(T.structure, C.structure), 10, 16),
                                          ("Blueprint", enc(T.pointer, C.pointer), 0, 80)]),
    ("InterfaceDescriptorData", 16, 8, [("$", enc(T.inherited, C.none), 0, 0)]),
    ("StaticModelGroupEntityData", 16, 8, [("$", enc(T.inherited, C.none), 0, 0)]),
    ("ObjectBlueprint", 32, 8, [("$", enc(T.inherited, C.none), 1, 0), ("Object", enc(T.pointer, C.pointer), 0, 24)]),
    ("StaticModelEntityData", 24, 8, [("$", enc(T.inherited, C.none), 0, 0), ("Mesh", enc(T.pointer, C.pointer), 0, 16)]),
    ("RigidMeshAsset", 32, 8, [("$", enc(T.inherited, C.none), 1, 0),
                               ("MeshSetResource", enc(T.resourceRef, C.primitive), 0, 24)]),
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


def document(file_guid, instances, imports=()):
    """instances: (object builder, exported guid or None); the builders get the empty document.
    imports: (file guid, instance guid) pairs, for PointerReference(PointerKind.external, i)."""
    doc = read_document(write_document(Document(file_guid=file_guid, reflection_data=reflection())))
    doc.imports = [ImportReference(f, c) for f, c in imports]
    for build, exported in instances:
        o = build(doc)
        doc.instances.append(InstanceRecord(ensure_fixup_type(doc, o.descriptor), o.descriptor,
                                            exported=exported is not None, instance_guid=exported or Guid(), object=o))
    return read_document(write_document(doc))


def ptr(index):
    return PointerReference(PointerKind.internal, index)


BAM_ROOT_GUID = guid(1)


WPROOT_GUID = guid(60)


def bam_root():
    return document(BAM_ROOT_GUID, [
        (lambda d: obj(d, "DetachedSubWorldData", Name="Levels/Game/BAM_LevelRoot/BAM_LevelRoot", WorldSizeXZ=2048.0,
                       WorldSizeY=512.0, Objects=[ptr(1), ptr(2)]), guid(2)),
        (lambda d: obj(d, "SubWorldReferenceObjectData", BundleName="Levels/Game/BAM_LevelRoot/TOD_2_Noon",
                       StreamRealm=1), None),
        (lambda d: obj(d, "SubWorldReferenceObjectData", BundleName="Levels/Game/BAM_LevelRoot/wproot", AutoLoad=True,
                       StreamRealm=1, Blueprint=PointerReference(PointerKind.external, 0)), None),
        # A reference held by a field (as bam's root #0 holds some), not by an array.
        (lambda d: obj(d, "ObjectBlueprint", Name="holder", Object=ptr(4)), None),
        (lambda d: obj(d, "SubWorldReferenceObjectData", BundleName="Levels/Game/BAM_LevelRoot/wproot/wp/held",
                       StreamRealm=1), None)],
        imports=[(WPROOT_GUID, guid(61))])


# The chain new-ground-level copies (FORMAT-NOTES 6.3): a tile whose LayerReferenceObjectData
# imports a layer in the same bundle, whose ObjectReferenceObjectData places a blueprint kept
# elsewhere; and util_groundplane in bam's root bundle with its mesh and <name>.physics.
TILE = ("levels/game/bam_levelroot/wproot/wp/s9600_x0z0/s200_x500z900/s100_x450z850/s50_x425z825/"
        "s50_x425z825_sublevel")
TILE_LAYER = TILE[:-len("_sublevel")] + "_layer"
CORE_LAYER = "levels/game/bam_levelroot/game_coremeshes"
CORE_BUNDLE = "win32/levels/game/bam_levelroot/bam_coregameassets"
GROUNDPLANE = "common/utilities/meshes/util_groundplane"
TILE_GUID, LAYER_GUID, BP_GUID, MESH_GUID, CORE_GUID = guid(62), guid(63), guid(64), guid(65), guid(66)
LAYER_ROOT_GUID, BP_ROOT_GUID = guid(81), guid(92)
LAYER_REFERENCE_GUID = Guid.parse("00c4d5e6-0000-0000-0000-000000000003")
PLACEMENT_GUID = Guid.parse("00a1b2c3-0000-0000-0000-000000000002")
PHYSICS_ID, MESHSET_ID = 0x1111222233334444, 0x5555
MESH_CHUNK = guid(70)
TEMPLATE_MATERIAL = 0x00012345


def placed(d, kind, x, flags=0):
    return obj(d, kind, Flags=flags, Blueprint=PointerReference(PointerKind.external, x),
               BlueprintTransform=obj(d, "LinearTransform", trans=obj(d, "Vec3", x=5.0, y=6.0, z=7.0),
                                      right=obj(d, "Vec3", x=0.0, z=1.0)))


def tile():
    return document(TILE_GUID, [
        (lambda d: obj(d, "QuadrantSubWorldData", Name=TILE.replace("s50_x425z825_sublevel", "S50_x425z825_Sublevel"),
                       Objects=[ptr(1), ptr(2), ptr(3)], Descriptor=ptr(4)), guid(80)),
        (lambda d: placed(d, "LayerReferenceObjectData", 0, 0xC4D5E6), LAYER_REFERENCE_GUID),
        (lambda d: obj(d, "StaticModelGroupEntityData"), guid(82)),
        (lambda d: obj(d, "Asset", Name="audio tag"), guid(83)),
        (lambda d: obj(d, "InterfaceDescriptorData"), guid(84))],
        imports=[(LAYER_GUID, LAYER_ROOT_GUID), (guid(90), guid(91))])


def layer(file_guid=LAYER_GUID, name=TILE_LAYER):
    return document(file_guid, [
        (lambda d: obj(d, "LayerData", Name=name, Objects=[ptr(1), ptr(2)]), LAYER_ROOT_GUID),
        (lambda d: placed(d, "ObjectReferenceObjectData", 0, 0xA1B2C3), PLACEMENT_GUID),
        (lambda d: obj(d, "Asset", Name="decoy"), guid(85))],
        imports=[(guid(93), guid(94))])


def groundplane():
    return document(BP_GUID, [
        (lambda d: obj(d, "ObjectBlueprint", Name="Common/Utilities/Meshes/Util_GroundPlane", Object=ptr(1)), BP_ROOT_GUID),
        (lambda d: obj(d, "StaticModelEntityData", Mesh=PointerReference(PointerKind.external, 0)), guid(95))],
        imports=[(MESH_GUID, guid(96))])


def groundplane_mesh():
    return document(MESH_GUID, [(lambda d: obj(d, "RigidMeshAsset", Name=GROUNDPLANE + "_mesh",
                                               MeshSetResource=ResourceReference(MESHSET_ID)), guid(96))])


def meshset(chunk_guid, name=GROUNDPLANE + "_mesh", lods=1):
    """(MeshSet, chunk): a rigid MeshSet with `lods` LODs (LOD k names chunk_guid, then guid(200 +
    k)) of two sections, a 50 m quad each, laid out as FORMAT-NOTES 1 says: section 0 with float
    positions in stream 0 and a normal and UV in stream 1, section 1 (a shadow) with half-float
    positions only. Every LOD has the same geometry, so one chunk serves them all."""
    quad = [(-25.0, 0.0, -25.0), (25.0, 0.0, -25.0), (-25.0, 0.0, 25.0), (25.0, 0.0, 25.0)]
    tris = [(0, 1, 2), (1, 3, 2)]      # clockwise seen from above
    chunk = bytearray()
    for p in quad:
        chunk += struct.pack("<3f", *p)
    for _ in quad:
        chunk += bytes([0x7F, 0xFF, 0x80, 0x00]) + struct.pack("<2e", 0.5, 0.25)
    for p in quad:
        chunk += struct.pack("<4e", *p, 1.0)
    vertex_bytes = len(chunk)                       # 112
    for _ in range(2):
        chunk += struct.pack("<6H", *(i for t in tris for i in t))
    chunk += bytes(-len(chunk) % 16)
    tables = 208 + 176 * lods
    names = tables + 2 * 384 * lods
    data = bytearray(max(1376, names + 64))
    struct.pack_into("<III", data, 0, 192, 176, 384)
    struct.pack_into("<4f", data, 0x10, -25.0, 0.0, -25.0, 0.0)
    struct.pack_into("<4f", data, 0x20, 25.0, 0.0, 25.0, 0.0)
    struct.pack_into("<HH", data, 180, lods, 2 * lods)
    data[names:names + len(name) + 1] = name.encode() + b"\0"
    struct.pack_into("<Q", data, 0x68, names - 16)
    for k in range(lods):
        lod = 208 + 176 * k
        table = tables + 2 * 384 * k
        struct.pack_into("<Q", data, 0x30 + 8 * k, lod - 16)
        struct.pack_into("<I", data, lod + 8, 2)
        struct.pack_into("<Q", data, lod + 12, table - 16)
        struct.pack_into("<4I", data, lod + 80, 0x40, 33, 24, vertex_bytes)
        data[lod + 116:lod + 132] = (chunk_guid if k == 0 else guid(200 + k)).bytes
        for s, (per_vertex, start, first, elements, strides) in enumerate((
                (20, 0, 0, [(1, 3, 0, 0), (33, 6, 0, 1), (51, 12, 4, 1)], [12, 8]),
                (8, 80, 6, [(1, 8, 0, 0)], [8]))):
            at = table + 384 * s
            struct.pack_into("<Q", data, at + 8, names - 16)
            data[at + 30], data[at + 31] = per_vertex, 3
            struct.pack_into("<4I", data, at + 32, 2, first, start, 4)
            for e, element in enumerate(elements):
                data[at + 112 + 4 * e:at + 116 + 4 * e] = bytes(element)
            for e, stride in enumerate(strides):
                data[at + 112 + 64 + 2 * e] = stride
            data[at + 112 + 96], data[at + 112 + 97] = len(elements), len(strides)
    return bytes(data), bytes(chunk)


# An ordinary visible world object in its own superbundle and bundle (like the lighthouse the PC
# found): blueprint, mesh, texture, their resources, and <blueprint>.physics. Its MeshSet has two
# LODs: LOD 0's chunk is in the bundle and the superbundle's TOC, LOD 1's in the TOC only; the
# texture's chunk is in the TOC only.
FTUE = "levels/game/dingolevel_ftue_island/dingolevel_ftue_island"
ISLAND_BUNDLE = "win32/levels/game/dingolevel_ftue_island/wp_dst_ftue_island"
LIGHTHOUSE = "world/plots/iog_island/iog_lighthouse_island_02"
LIGHTHOUSE_GUID, LIGHTHOUSE_MESH_GUID, LIGHTHOUSE_TEXTURE_GUID = guid(100), guid(101), guid(102)
LIGHTHOUSE_CHUNK, LIGHTHOUSE_TEXTURE_CHUNK = guid(103), guid(104)
LIGHTHOUSE_LOD1_CHUNK = guid(201)


def lighthouse(bundle, toc_chunk):
    """(the bundle, the superbundle's TOC chunks)."""
    bp = document(LIGHTHOUSE_GUID, [
        (lambda d: obj(d, "ObjectBlueprint", Name=LIGHTHOUSE, Object=ptr(1)), guid(105)),
        (lambda d: obj(d, "StaticModelEntityData", Mesh=PointerReference(PointerKind.external, 0)), guid(106))],
        imports=[(LIGHTHOUSE_MESH_GUID, guid(107))])
    mesh = document(LIGHTHOUSE_MESH_GUID, [(lambda d: obj(d, "RigidMeshAsset", Name=LIGHTHOUSE + "_mesh",
                                                          MeshSetResource=ResourceReference(0x7777)), guid(107))],
                    imports=[(LIGHTHOUSE_TEXTURE_GUID, guid(108))])
    texture = document(LIGHTHOUSE_TEXTURE_GUID, [(lambda d: obj(d, "Asset", Name=LIGHTHOUSE + "_d"), guid(108))])
    ms, chunk = meshset(LIGHTHOUSE_CHUNK, LIGHTHOUSE + "_mesh", lods=2)
    b = bundle(ISLAND_BUNDLE, {"world/plots/iog_island/other": b"zz", LIGHTHOUSE: write_document(bp),
                               LIGHTHOUSE + "_mesh": write_document(mesh), LIGHTHOUSE + "_d": write_document(texture)},
               {LIGHTHOUSE + ".physics": (0x41759364, 0x8888, template_physics()),
                LIGHTHOUSE + "_mesh": (0x49B156D4, 0x7777, ms),
                LIGHTHOUSE + "_d": (0x6BDE20BA, 0x9999, b"texture" + LIGHTHOUSE_TEXTURE_CHUNK.bytes)},
               [(guid(109), b"unrelated"), (LIGHTHOUSE_CHUNK, chunk)])
    return b, [toc_chunk(LIGHTHOUSE_CHUNK, chunk), toc_chunk(LIGHTHOUSE_LOD1_CHUNK, chunk),
               toc_chunk(LIGHTHOUSE_TEXTURE_CHUNK, b"texels"), toc_chunk(guid(110), b"unrelated")]


def template_physics():
    from reskate_mapbuilder.physics import Part, compute_edge_data, flat_grid, write_physics
    shape = flat_grid(50.0, 25.0)
    part = Part(0, material=TEMPLATE_MATERIAL)
    compute_edge_data([shape], [part])
    return write_physics([shape], [part], 2)


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

    def bundle(name, ebx, res=None, chunks=None):
        """res: name -> (type, id, payload); chunks: (guid, payload) pairs."""
        res, chunks = res or {}, chunks or []
        files = []
        for payload in list(ebx.values()) + [r[2] for r in res.values()] + [c[1] for c in chunks]:
            stored = encode_raw(payload)
            files.append(BundleFileInfo(CasIdentifier(False, CHUNK, 2), len(cas), len(stored)))
            cas.extend(stored)
        manifest = write_binary_bundle(BinaryBundle(
            ebx=[BundleAsset(EBX, n, hashlib.sha1(p).digest(), original_size=len(p)) for n, p in ebx.items()],
            resources=[BundleAsset(RESOURCE, n, hashlib.sha1(p).digest(), resource_id=i, resource_type=t, original_size=len(p))
                       for n, (t, i, p) in res.items()],
            chunks=[BundleAsset(CHUNK_KIND, guid=g, sha1=hashlib.sha1(p).digest(), logical_size=len(p)) for g, p in chunks]))
        files.insert(0, BundleFileInfo(CasIdentifier(False, CHUNK, 2), len(cas), len(manifest)))
        cas.extend(manifest)
        return TocBundle(name, write_bundle_region(files), 1)

    tocs = {
        "globals": [bundle("win32/globals", {"some/other/asset": b"x" * 9}),
                    bundle("win32/globals_levels", {BAM + "/description": write_document(bam_description()),
                                                    ROOT + "/description": write_document(root_description())})],
        BAM: [bundle("win32/" + BAM, {BAM: write_document(bam_root()), "levels/game/bam_levelroot/pool": b"pool",
                                      GROUNDPLANE: write_document(groundplane()),
                                      GROUNDPLANE + "_mesh": write_document(groundplane_mesh())},
                     {GROUNDPLANE + ".physics": (0x41759364, PHYSICS_ID, template_physics()),
                      GROUNDPLANE + "_mesh": (0x49B156D4, MESHSET_ID, meshset(MESH_CHUNK)[0])},
                     [(MESH_CHUNK, meshset(MESH_CHUNK)[1])]),
              bundle("win32/levels/game/bam_levelroot/tod_2_noon", {"levels/game/bam_levelroot/tod_2_noon": b"tod"}),
              bundle("win32/levels/game/bam_levelroot/startpoint_sublevels", {"sp": b"sp"}),
              bundle(CORE_BUNDLE, {"levels/game/bam_levelroot/other": b"zz",
                                   CORE_LAYER: write_document(layer(CORE_GUID, CORE_LAYER))}),
              bundle("win32/levels/game/bam_levelroot/wproot", {"levels/game/bam_levelroot/wproot": b"wproot"}),
              bundle("win32/" + TILE, {TILE: write_document(tile()), "not_an_ebx": b"zz", TILE_LAYER: write_document(layer())},
                     {TILE[:-len("_sublevel")] + "/staticmodelgroup.physics": (0x41759364, 0x77, template_physics())})],
        ROOT: [bundle("win32/" + ROOT, {ROOT + "_dsublevelmanager_genprefab": write_document(genprefab())})],
    }
    def toc_chunk(g, payload):
        stored = encode_raw(payload)
        cas.extend(stored)
        return TocChunk(g, CasIdentifier(False, CHUNK, 2), len(cas) - len(stored), len(stored))

    island, island_chunks = lighthouse(bundle, toc_chunk)
    tocs[FTUE] = [island]
    chunks = {BAM: [TocChunk(guid(50), CasIdentifier(False, CHUNK, 2), 0, 4), toc_chunk(MESH_CHUNK, meshset(MESH_CHUNK)[1])],
              FTUE: island_chunks}
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
    assert roundtrip(rewritten)[0] == f"rewrite identical ({len(rewritten)} bytes)"


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
    assert sorted(c.guid.bytes for c in toc.chunks) == sorted([guid(50).bytes, MESH_CHUNK.bytes])
    _, region, manifest = view.bundle(toc, "win32/" + NEW)
    assert [a.name for a in manifest.ebx] == [BAM, "levels/game/bam_levelroot/pool", GROUNDPLANE, GROUNDPLANE + "_mesh", NEW]
    assert [f.location.patch for f in region.files] == [True, False, False, False, False, True, False, False, False]
    raw = view.ebx(region, manifest, NEW)
    assert manifest.ebx[4].sha1 == hashlib.sha1(raw).digest() and manifest.ebx[4].original_size == len(raw)
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
    from test_ebx_crosscheck import grouped
    doc = read_document(grouped(1))
    report = array_hash_report(doc)
    count = int(report[0].split()[0])
    assert report[1].startswith("every array's hash found in the document's group table: yes")
    assert f"  {count} of {count}: a key of the RFL2 group table" in report
    assert roundtrip(write_document(doc))[1] == report[1]
