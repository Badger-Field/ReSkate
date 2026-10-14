"""Writes a mod folder that ReSkate merges at launch (FORMAT-NOTES.md, sections 3 and 4).

<Mods>/<name>/
  layout.toc                     byte copy of the game's Data/layout.toc
  .reskate-studio-patch          stamp naming the supported game build's SHA-256
  manifest.json                  name, author, version, description
  Win32/<superbundle>.toc        patch TOC listing only the bundles the mod changes
  Win32/<package>/cas_01.cas     the mod's payloads (raw CAS blocks) and manifests

A changed bundle replaces the base bundle wholesale, so it lists every entry; unchanged
entries keep their base location. Changed payloads point at {patch, same install chunk,
archive 1}.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .bundle import BundleFileInfo, CasIdentifier, write_binary_bundle, write_bundle_region
from .cas import encode_raw
from .toc import TocBundle, read_toc, verify_toc, write_patch_toc

SUPPORTED_SKATE_SHA256 = "fbce74d5e28ef525dbba2cb4adbebc13405bdbd88f31bc940bca45e4ae88b8f9"
_NAME = re.compile(r"[A-Za-z0-9 _.\-]{1,64}")


@dataclass
class _Bundle:
    superbundle: str
    toc_bundle: TocBundle
    region: object
    manifest: object
    replaced: dict = field(default_factory=dict)   # resource name -> decoded bytes


class ModBuilder:
    def __init__(self, game, name: str, author: str = "ReSkate map builder", version: str = "0.0.1",
                 description: str = "", skate_sha256: str = SUPPORTED_SKATE_SHA256):
        if not _NAME.fullmatch(name) or name.startswith("."):
            raise ValueError("mod names use letters, digits, space, _ - . (at most 64, not starting with a dot)")
        self.game, self.name, self.author, self.version, self.description = game, name, author, version, description
        self.skate_sha256 = skate_sha256
        self.bundles: list[_Bundle] = []

    def edit_bundle(self, superbundle: str, bundle_name: str) -> _Bundle:
        for b in self.bundles:
            if b.superbundle == superbundle and b.toc_bundle.name.lower() == bundle_name.lower():
                return b
        toc = self.game.superbundle(superbundle)
        toc_bundle, region, manifest = self.game.bundle(toc, bundle_name)
        entry = _Bundle(superbundle, toc_bundle, region, manifest)
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

    @staticmethod
    def _file_index(b: _Bundle, resource_name: str) -> int:
        for i, asset in enumerate(b.manifest.resources):
            if asset.name.lower() == resource_name.lower():
                offset = 0 if b.region.inline_manifest else 1
                return offset + len(b.manifest.ebx) + i
        raise KeyError(f"resource {resource_name} not in bundle {b.toc_bundle.name}")

    def write(self, mods_dir: str | Path) -> Path:
        root = Path(mods_dir) / self.name
        archives: dict[str, bytearray] = {}   # package dir -> cas_01 bytes
        tocs: dict[str, list[TocBundle]] = {}
        for b in self.bundles:
            if b.region.inline_manifest:
                raise NotImplementedError("bundles with an inline manifest are not written yet")
            files = [BundleFileInfo(f.location, f.offset, f.size) for f in b.region.files]
            for name, decoded in b.replaced.items():
                index = self._file_index(b, name)
                chunk = files[index].location.install_chunk
                package = self.game.directory(chunk)
                cas = archives.setdefault(package, bytearray())
                stored = encode_raw(decoded)
                files[index] = BundleFileInfo(CasIdentifier(True, chunk, 1), len(cas), len(stored))
                cas += stored
                asset = b.manifest.resources[index - 1 - len(b.manifest.ebx)]
                asset.original_size = len(decoded)
                asset.sha1 = hashlib.sha1(decoded).digest()
            manifest = write_binary_bundle(b.manifest)
            chunk = files[0].location.install_chunk
            cas = archives.setdefault(self.game.directory(chunk), bytearray())
            files[0] = BundleFileInfo(CasIdentifier(True, chunk, 1), len(cas), len(manifest))
            cas += manifest  # manifests are stored without a block header
            region = write_bundle_region(files)
            tocs.setdefault(b.superbundle, []).append(TocBundle(b.toc_bundle.name, region, b.toc_bundle.load_flag))

        root.mkdir(parents=True, exist_ok=True)
        (root / "layout.toc").write_bytes(self.game.layout_bytes)
        (root / ".reskate-studio-patch").write_text(
            f"ReSkate Studio native Patch v1\nskate_sha256={self.skate_sha256}\n", newline="\n")
        (root / "manifest.json").write_text(json.dumps({
            "name": self.name, "author": self.author, "version_number": self.version,
            "description": self.description}, indent=2) + "\n")
        for superbundle, bundles in tocs.items():
            data = write_patch_toc(bundles)
            verify_toc(read_toc(data))
            path = root / "Win32" / (superbundle + ".toc")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        for package, data in archives.items():
            path = root / "Win32" / package / "cas_01.cas"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bytes(data))
        return root
