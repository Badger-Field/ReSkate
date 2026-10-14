"""ModBuilder against a small fake game folder written with the same format code."""
import hashlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reskate_mapbuilder import nativedb  # noqa: E402
from reskate_mapbuilder.bundle import (BinaryBundle, BundleAsset, BundleFileInfo, CasIdentifier, EBX,  # noqa: E402
                                       RESOURCE, read_binary_bundle, read_bundle_region, write_binary_bundle,
                                       write_bundle_region)
from reskate_mapbuilder.cas import decode_raw, encode_raw  # noqa: E402
from reskate_mapbuilder.game import Game  # noqa: E402
from reskate_mapbuilder.mod import ModBuilder  # noqa: E402
from reskate_mapbuilder.physics import Part, Shape, compute_edge_data, read_physics, replace_shape, write_physics  # noqa: E402
from reskate_mapbuilder.toc import TocBundle, read_toc, verify_toc, write_patch_toc  # noqa: E402

CHUNK = 7
PACKAGE = "configurations/layout/defaultinstallpackage"
SUPER = "levels/game/test_levelroot/test_levelroot"
BUNDLE = "win32/levels/game/test_levelroot/wproot/s200_x0z0_sublevel"
RES = "levels/game/test_levelroot/wproot/s200_x0z0_sublevel/staticmodelgroup.physics"


def node(type_, name=None, payload=b"", children=None):
    return nativedb.Node(type_, name is not None, children is not None, name or "", payload, children or [])


def fake_game(tmp_path):
    data = tmp_path / "Data"
    (data / "Win32" / PACKAGE).mkdir(parents=True)
    layout = node(2, None, children=[node(2, "installManifest", children=[node(1, "installChunks", children=[
        node(2, None, children=[node(7, "name", b"Configurations/Layout/DefaultInstallPackage\0"),
                                node(8, "persistentIndex", struct.pack("<I", CHUNK))])])])])
    (data / "layout.toc").write_bytes(nativedb.MAGIC + bytes(nativedb.ENVELOPE_SIZE - 4) + nativedb.write(layout))

    floor = Shape([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 0.0, 10.0), (10.0, 0.0, 10.0)], [(0, 2, 1), (1, 2, 3)])
    compute_edge_data([floor], [Part(0)])
    physics = write_physics([floor], [Part(0, (100.0, 199.0, 200.0))])
    ebx_payload = b"ebx payload"
    cas = bytearray()
    entries = []
    for payload in (ebx_payload, physics):
        stored = encode_raw(payload)
        entries.append(BundleFileInfo(CasIdentifier(False, CHUNK, 2), len(cas), len(stored)))
        cas += stored
    manifest = BinaryBundle(
        ebx=[BundleAsset(EBX, "levels/game/test_levelroot/thing", hashlib.sha1(ebx_payload).digest(),
                         original_size=len(ebx_payload))],
        resources=[BundleAsset(RESOURCE, RES, b"\1" * 20, resource_type=0x41759364, original_size=len(physics))])
    raw_manifest = write_binary_bundle(manifest)
    files = [BundleFileInfo(CasIdentifier(False, CHUNK, 2), len(cas), len(raw_manifest))] + entries
    cas += raw_manifest
    (data / "Win32" / PACKAGE / "cas_02.cas").write_bytes(bytes(cas))
    toc = write_patch_toc([TocBundle(BUNDLE, write_bundle_region(files), 1)])
    (data / "Win32" / (SUPER + ".toc")).parent.mkdir(parents=True)
    (data / "Win32" / (SUPER + ".toc")).write_bytes(toc)
    return tmp_path


def test_game_reads_its_own_bundles(tmp_path):
    game = Game(fake_game(tmp_path))
    assert game.directory(CHUNK) == PACKAGE
    toc = game.superbundle(SUPER)
    _, region, manifest = game.bundle(toc, BUNDLE)
    shapes, parts, version = read_physics(game.payload(region, 2))
    assert version == 2 and parts[0].translation == (100.0, 199.0, 200.0)


def test_override_mod_raises_ground_and_keeps_the_rest(tmp_path):
    game = Game(fake_game(tmp_path / "game"))
    mod = ModBuilder(game, "zz-python-test")
    original = mod.resource(SUPER, BUNDLE, RES)
    shapes, parts, _ = read_physics(original)
    raised = Shape([(x, y + 1.0, z) for x, y, z in shapes[0].vertices], shapes[0].triangles)
    compute_edge_data([raised], parts)
    mod.replace_resource(SUPER, BUNDLE, RES, replace_shape(original, 0, raised))
    root = mod.write(tmp_path / "Mods")

    assert (root / "layout.toc").read_bytes() == game.layout_bytes
    assert "skate_sha256=fbce74d5" in (root / ".reskate-studio-patch").read_text()
    doc = read_toc((root / "Win32" / (SUPER + ".toc")).read_bytes())
    verify_toc(doc)
    assert [b.name for b in doc.bundles] == [BUNDLE]
    region = read_bundle_region(doc.bundles[0].region)
    cas = (root / "Win32" / PACKAGE / "cas_01.cas").read_bytes()
    first = region.files[0]
    assert first.location == CasIdentifier(True, CHUNK, 1)
    manifest = read_binary_bundle(cas[first.offset:first.offset + first.size])
    # The ebx entry is untouched and still points at the base game.
    assert region.files[1].location == CasIdentifier(False, CHUNK, 2)
    res_file = region.files[2]
    assert res_file.location == CasIdentifier(True, CHUNK, 1)
    decoded = decode_raw(cas[res_file.offset:res_file.offset + res_file.size])
    assert manifest.resources[0].original_size == len(decoded)
    assert manifest.resources[0].sha1 == hashlib.sha1(decoded).digest()
    back, back_parts, _ = read_physics(decoded)
    assert [v[1] for v in back[0].vertices] == [1.0, 1.0, 1.0, 1.0]
