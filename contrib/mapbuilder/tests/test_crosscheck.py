"""Byte-for-byte check of the Python format code against ReSkate's C++ (Engine/Resource).

Builds tests/crosscheck/crosscheck.cpp with the repository's toc.cpp, binary_bundle.cpp
and binary_io.cpp, has it write reference files, and compares them with what the Python
port writes from the same inputs. Then the C++ reads the Python files back. Skipped when
no C++20 compiler is available.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))

from reskate_mapbuilder.bundle import (BinaryBundle, BundleAsset, BundleFileInfo, CasIdentifier, Guid,  # noqa: E402
                                       EBX, RESOURCE, CHUNK, read_binary_bundle, write_binary_bundle,
                                       write_bundle_region, read_bundle_region)
from reskate_mapbuilder.toc import TocBundle, TocChunk, read_toc, verify_toc, write_patch_toc  # noqa: E402

COUNTS = [0, 1, 2, 7, 64, 513]


def guid_for(i):
    raw = bytearray(((i * 37 + k * 11 + 5) & 0xFF) for k in range(16))
    raw[0], raw[1] = i & 0xFF, (i >> 8) & 0xFF  # unique for i below 65536
    return Guid(bytes(raw))


def build(count):
    bundles, files = [], []
    for i in range(count):
        files.append(BundleFileInfo(CasIdentifier(i % 3 == 0, 3 + (i % 2), 1 + (i % 4)), i * 100, i * 7 + 1))
        bundles.append(TocBundle(f"win32/levels/game/Test_{i}/Sub_Level", write_bundle_region(list(files)), 1))
    chunks = [TocChunk(guid_for(i), CasIdentifier(True, 2, i % 5), i * 64, i * 3 + 9, i % 7 == 6) for i in range(count)]
    manifest = BinaryBundle()
    for i in range(count):
        manifest.ebx.append(BundleAsset(EBX, f"ebx/asset_{i}", bytes((i + k) & 0xFF for k in range(20)),
                                        original_size=i * 13))
        manifest.resources.append(BundleAsset(RESOURCE, f"res/thing_{i}", resource_type=0x41759364,
                                              resource_meta=bytes([i & 0xFF]) * 16,
                                              resource_id=0x1122334455667788 + i, original_size=i * 17))
        manifest.chunks.append(BundleAsset(CHUNK, guid=guid_for(i + 100), logical_offset=0, logical_size=i * 19))
    return write_patch_toc(bundles, chunks, 3), write_binary_bundle(manifest), bundles, chunks


def compiler():
    return shutil.which(os.environ.get("CXX", "")) or shutil.which("g++") or shutil.which("clang++")


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    cxx = compiler()
    if not cxx:
        pytest.skip("no C++ compiler")
    out = tmp_path_factory.mktemp("xc") / "crosscheck"
    src = [HERE / "crosscheck" / "crosscheck.cpp"] + [REPO / "Engine" / "Resource" / f for f in
                                                       ("toc.cpp", "binary_bundle.cpp", "binary_io.cpp")]
    result = subprocess.run([cxx, "-std=c++20", "-O1", f"-I{REPO}", *map(str, src), "-o", str(out)],
                            capture_output=True, text=True)
    if result.returncode:
        pytest.skip("C++ harness did not build: " + result.stderr[-2000:])
    return out


@pytest.mark.parametrize("count", COUNTS)
def test_python_writes_the_same_bytes_as_cpp(harness, tmp_path, count):
    subprocess.run([str(harness), "write", str(tmp_path), str(count)], check=True)
    toc, manifest, _, _ = build(count)
    assert (tmp_path / "cpp.toc").read_bytes() == toc
    assert (tmp_path / "cpp.manifest").read_bytes() == manifest


@pytest.mark.parametrize("count", COUNTS)
def test_cpp_reads_what_python_writes(harness, tmp_path, count):
    toc, manifest, bundles, chunks = build(count)
    (tmp_path / "py.toc").write_bytes(toc)
    (tmp_path / "py.manifest").write_bytes(manifest)
    out = subprocess.run([str(harness), "read", str(tmp_path)], check=True, capture_output=True, text=True).stdout
    lines = out.splitlines()
    assert lines[0] == f"{count} {count}"
    names = sorted(line.split()[0] for line in lines[1:1 + count])
    assert names == sorted(b.name for b in bundles)


@pytest.mark.parametrize("count", COUNTS)
def test_python_round_trip(count):
    toc, manifest, bundles, chunks = build(count)
    doc = read_toc(toc)
    verify_toc(doc)
    assert sorted(b.name for b in doc.bundles) == sorted(b.name for b in bundles)
    by_name = {b.name: b for b in doc.bundles}
    for b in bundles:
        assert by_name[b.name].region == b.region
        assert len(read_bundle_region(b.region).files) == len(read_bundle_region(by_name[b.name].region).files)
    assert {(c.guid, c.removed) for c in doc.chunks} == {(c.guid, c.removed) for c in chunks}
    back = read_binary_bundle(manifest)
    assert [a.name for a in back.resources] == [f"res/thing_{i}" for i in range(count)]
    assert write_binary_bundle(back) == manifest
