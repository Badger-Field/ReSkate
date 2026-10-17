"""Writes a mod folder that ReSkate merges at launch (FORMAT-NOTES.md, sections 3 and 4).

<Mods>/<name>/
  layout.toc                     the game's Data/layout.toc, plus any superbundle the mod adds
  reskate-levels.json            levels the mod adds (only then)
  .reskate-studio-patch          stamp naming the supported game build's SHA-256
  manifest.json                  name, author, version, description
  Win32/<superbundle>.toc        patch TOC listing only the bundles the mod changes; a new
                                 superbundle's TOC lists all of its bundles
  Win32/<package>/cas_01.cas     the mod's payloads (raw CAS blocks) and manifests

A changed bundle replaces the base bundle wholesale, so it lists every entry; unchanged
entries keep their base location. Changed payloads point at {patch, same install chunk,
archive 1}.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import nativedb
from .bundle import (CHUNK, EBX, RESOURCE, BinaryBundle, BundleAsset, BundleFileInfo, BundleRegion, CasIdentifier,
                     write_binary_bundle, write_bundle_region)
from .cas import encode_raw
from .toc import TocBundle, TocChunk, read_toc, verify_toc, write_patch_toc

SUPPORTED_SKATE_SHA256 = "fbce74d5e28ef525dbba2cb4adbebc13405bdbd88f31bc940bca45e4ae88b8f9"
_NAME = re.compile(r"[A-Za-z0-9 _.\-]{1,64}")


@dataclass
class _Bundle:
    superbundle: str
    toc_bundle: TocBundle
    region: object
    manifest: object                                # None: a copy whose region is written as is
    replaced: dict = field(default_factory=dict)    # resource name -> decoded bytes
    ebx: dict = field(default_factory=dict)         # EBX name (lower case) -> decoded bytes, changed or added


class ModBuilder:
    def __init__(self, game, name: str, author: str = "ReSkate map builder", version: str = "0.0.1",
                 description: str = "", skate_sha256: str = SUPPORTED_SKATE_SHA256):
        if not _NAME.fullmatch(name) or name.startswith("."):
            raise ValueError("mod names use letters, digits, space, _ - . (at most 64, not starting with a dot)")
        self.game, self.name, self.author, self.version, self.description = game, name, author, version, description
        self.skate_sha256 = skate_sha256
        self.bundles: list[_Bundle] = []
        self.layout_bytes: bytes = game.layout_bytes
        self.new_superbundles: dict[str, list[TocChunk]] = {}   # path under Win32 -> chunks of its TOC
        self.levels: list[dict] = []                            # rows of reskate-levels.json

    def _find(self, superbundle: str, bundle_name: str) -> _Bundle | None:
        for b in self.bundles:
            if b.superbundle.lower() == superbundle.lower() and b.toc_bundle.name.lower() == bundle_name.lower():
                return b
        return None

    def edit_bundle(self, superbundle: str, bundle_name: str, source: tuple[str, str] | None = None) -> _Bundle:
        """A bundle the mod rewrites. With `source` (superbundle, bundle) it starts as a copy of that
        base bundle, under `bundle_name` in `superbundle`; otherwise as the base bundle itself."""
        found = self._find(superbundle, bundle_name)
        if found is not None:
            if found.manifest is None:
                raise ValueError(f"{bundle_name} was added as a plain copy")
            return found
        src_superbundle, src_bundle = source or (superbundle, bundle_name)
        toc = self.game.superbundle(src_superbundle)
        toc_bundle, region, manifest = self.game.bundle(toc, src_bundle)
        entry = _Bundle(superbundle, TocBundle(bundle_name, toc_bundle.region, toc_bundle.load_flag), region, manifest)
        self.bundles.append(entry)
        return entry

    def add_superbundle(self, path: str, chunk_name: str = nativedb.DEFAULT_INSTALL_CHUNK,
                        chunks: list[TocChunk] | None = None) -> None:
        """A superbundle the game does not have: listed in the mod's layout.toc (both lists) and
        written as Win32/<path>.toc with the bundles given to it."""
        self.layout_bytes = nativedb.add_superbundle(self.layout_bytes, "Win32/" + path, chunk_name)
        self.new_superbundles[path] = list(chunks or [])

    def copy_bundle(self, superbundle: str, toc_bundle: TocBundle) -> None:
        """A base bundle listed unchanged in another superbundle: its region is copied as is, so
        the manifest and every payload keep pointing at the base archives (the 333 map's copies)."""
        if self._find(superbundle, toc_bundle.name) is not None:
            raise ValueError(f"{toc_bundle.name} is already in {superbundle}")
        self.bundles.append(_Bundle(superbundle, TocBundle(toc_bundle.name, toc_bundle.region, toc_bundle.load_flag),
                                    None, None))

    def new_bundle(self, superbundle: str, bundle_name: str, install_chunk: int) -> _Bundle:
        """An empty bundle the mod creates; its manifest and payloads go to `install_chunk`'s archive."""
        if self._find(superbundle, bundle_name) is not None:
            raise ValueError(f"{bundle_name} is already in {superbundle}")
        region = BundleRegion([BundleFileInfo(CasIdentifier(True, install_chunk, 1), 0, 0)])
        entry = _Bundle(superbundle, TocBundle(bundle_name, b"", 1), region, BinaryBundle())
        self.bundles.append(entry)
        return entry

    def resource(self, superbundle: str, bundle_name: str, resource_name: str) -> bytes:
        b = self.edit_bundle(superbundle, bundle_name)
        if resource_name in b.replaced:
            return b.replaced[resource_name]
        return self.game.payload(b.region, self._file_index(b, resource_name))

    def replace_resource(self, superbundle: str, bundle_name: str, resource_name: str, decoded: bytes):
        b = self.edit_bundle(superbundle, bundle_name)
        self._file_index(b, resource_name)
        b.replaced[resource_name] = decoded

    def ebx(self, superbundle: str, bundle_name: str, ebx_name: str) -> bytes:
        b = self.edit_bundle(superbundle, bundle_name)
        if ebx_name.lower() in b.ebx:
            return b.ebx[ebx_name.lower()]
        return self.game.payload(b.region, self._ebx_index(b, ebx_name))

    def replace_ebx(self, superbundle: str, bundle_name: str, ebx_name: str, decoded: bytes):
        b = self.edit_bundle(superbundle, bundle_name)
        self._ebx_index(b, ebx_name)
        b.ebx[ebx_name.lower()] = decoded

    def add_ebx(self, superbundle: str, bundle_name: str, ebx_name: str, decoded: bytes):
        """A new EBX in a bundle the mod rewrites: listed after the bundle's other EBX, its payload
        stored in the mod's archive of the install chunk the bundle's manifest is in."""
        b = self.edit_bundle(superbundle, bundle_name)
        if any(a.name.lower() == ebx_name.lower() for a in b.manifest.ebx):
            raise ValueError(f"EBX {ebx_name} is already in {bundle_name}")
        if b.region.inline_manifest:
            raise NotImplementedError("bundles with an inline manifest are not written yet")
        index = 1 + len(b.manifest.ebx)
        b.region.files.insert(index, BundleFileInfo(b.region.files[0].location, 0, 0))
        b.manifest.ebx.append(BundleAsset(EBX, ebx_name))
        b.ebx[ebx_name.lower()] = decoded

    def add_resource(self, superbundle: str, bundle_name: str, name: str, resource_type: int, decoded: bytes,
                     resource_id: int, resource_meta: bytes = b"\0" * 16):
        """A new resource in a bundle the mod rewrites, listed after the bundle's other resources;
        its payload goes to the mod's archive of the manifest's install chunk."""
        b = self.edit_bundle(superbundle, bundle_name)
        if any(a.name.lower() == name.lower() for a in b.manifest.resources):
            raise ValueError(f"resource {name} is already in {bundle_name}")
        if b.region.inline_manifest:
            raise NotImplementedError("bundles with an inline manifest are not written yet")
        index = 1 + len(b.manifest.ebx) + len(b.manifest.resources)
        b.region.files.insert(index, BundleFileInfo(b.region.files[0].location, 0, 0))
        b.manifest.resources.append(BundleAsset(RESOURCE, name, resource_id=resource_id, resource_type=resource_type,
                                                resource_meta=bytes(resource_meta)))
        b.replaced[name] = decoded

    def add_copied(self, superbundle: str, bundle_name: str, asset: BundleAsset, location: BundleFileInfo):
        """An entry of another bundle listed in this one as it is: its manifest record copied and
        its region file kept pointing where it was (a base archive), as the 333 map's copies do."""
        b = self.edit_bundle(superbundle, bundle_name)
        if b.region.inline_manifest:
            raise NotImplementedError("bundles with an inline manifest are not written yet")
        lists = {EBX: b.manifest.ebx, RESOURCE: b.manifest.resources, CHUNK: b.manifest.chunks}
        same = (lambda a: a.guid == asset.guid) if asset.kind == CHUNK else (lambda a: a.name.lower() == asset.name.lower())
        if any(same(a) for a in lists[asset.kind]):
            raise ValueError(f"{asset.kind} {asset.name or asset.guid.string()} is already in {bundle_name}")
        index = 1 + len(b.manifest.ebx)
        if asset.kind != EBX:
            index += len(b.manifest.resources)
        if asset.kind == CHUNK:
            index += len(b.manifest.chunks)
        b.region.files.insert(index, BundleFileInfo(location.location, location.offset, location.size))
        lists[asset.kind].append(copy.deepcopy(asset))

    @staticmethod
    def _ebx_index(b: _Bundle, ebx_name: str) -> int:
        for i, asset in enumerate(b.manifest.ebx):
            if asset.name.lower() == ebx_name.lower():
                return (0 if b.region.inline_manifest else 1) + i
        raise KeyError(f"EBX {ebx_name} not in bundle {b.toc_bundle.name}")

    @staticmethod
    def _file_index(b: _Bundle, resource_name: str) -> int:
        for i, asset in enumerate(b.manifest.resources):
            if asset.name.lower() == resource_name.lower():
                offset = 0 if b.region.inline_manifest else 1
                return offset + len(b.manifest.ebx) + i
        raise KeyError(f"resource {resource_name} not in bundle {b.toc_bundle.name}")

    def build(self) -> dict[str, bytes]:
        """Every file of the mod folder, by path relative to it; nothing is written."""
        archives: dict[str, bytearray] = {}   # package dir -> cas_01 bytes
        tocs: dict[str, list[TocBundle]] = {path: [] for path in self.new_superbundles}

        def store(chunk: int, stored: bytes) -> BundleFileInfo:
            cas = archives.setdefault(self.game.directory(chunk), bytearray())
            info = BundleFileInfo(CasIdentifier(True, chunk, 1), len(cas), len(stored))
            cas += stored
            return info

        for b in self.bundles:
            if b.manifest is None:
                tocs.setdefault(b.superbundle, []).append(b.toc_bundle)
                continue
            if b.region.inline_manifest:
                raise NotImplementedError("bundles with an inline manifest are not written yet")
            files = [BundleFileInfo(f.location, f.offset, f.size) for f in b.region.files]
            for name, decoded in b.ebx.items():
                index = self._ebx_index(b, name)
                files[index] = store(files[index].location.install_chunk, encode_raw(decoded))
                asset = b.manifest.ebx[index - 1]
                asset.original_size = len(decoded)
                asset.sha1 = hashlib.sha1(decoded).digest()
            for name, decoded in b.replaced.items():
                index = self._file_index(b, name)
                files[index] = store(files[index].location.install_chunk, encode_raw(decoded))
                asset = b.manifest.resources[index - 1 - len(b.manifest.ebx)]
                asset.original_size = len(decoded)
                asset.sha1 = hashlib.sha1(decoded).digest()
            # Manifests are stored without a block header.
            files[0] = store(files[0].location.install_chunk, write_binary_bundle(b.manifest))
            region = write_bundle_region(files)
            tocs.setdefault(b.superbundle, []).append(TocBundle(b.toc_bundle.name, region, b.toc_bundle.load_flag))

        out: dict[str, bytes] = {
            "layout.toc": self.layout_bytes,
            ".reskate-studio-patch": f"ReSkate Studio native Patch v1\nskate_sha256={self.skate_sha256}\n".encode(),
            "manifest.json": (json.dumps({"name": self.name, "author": self.author, "version_number": self.version,
                                          "description": self.description}, indent=2) + "\n").encode(),
        }
        if self.levels:
            out["reskate-levels.json"] = (json.dumps({"schema": 1, "levels": self.levels}, indent=2) + "\n").encode()
        for superbundle, bundles in tocs.items():
            data = write_patch_toc(bundles, self.new_superbundles.get(superbundle))
            verify_toc(read_toc(data))
            out["Win32/" + superbundle + ".toc"] = data
        for package, data in archives.items():
            out["Win32/" + package + "/cas_01.cas"] = bytes(data)
        return out

    def write(self, mods_dir: str | Path) -> Path:
        files = self.build()
        root = Path(mods_dir) / self.name
        for relative, data in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return root
