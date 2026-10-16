"""Read access to an installed game's data (port of Engine/Vfs/game_archives.cpp).

<game>/Data/layout.toc names each install chunk's package directory; archives live at
<game>/Data/Win32/<package>/cas_NN.cas. Compressed payloads are decoded with the game's own
Oodle DLL (Windows only).

With `mod`, a mod folder is laid over the game for reading: its TOCs replace the game's and
its patch locations ({patch, chunk, archive N}) read <mod>/Win32/<package>/cas_NN.cas. That is
how another mod (the 333 map) is dumped for comparison.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

from . import nativedb
from .bundle import BinaryBundle, BundleRegion, CasIdentifier, read_binary_bundle, read_bundle_region
from .cas import decode_cas
from .toc import TocBundle, read_toc


def archive_file(archive: int) -> str:
    return f"cas_{archive:02d}.cas"


class Game:
    def __init__(self, game_root: str | Path, mod: str | Path | None = None):
        self.root = Path(game_root)
        self.mod = Path(mod) if mod else None
        self.data = self.root / "Data"
        self.layout_bytes = (self.data / "layout.toc").read_bytes()
        self.layout = nativedb.read_layout(self.layout_bytes)
        self.chunk_directory: dict[int, str] = {}
        win32 = self.data / "Win32"
        directories: dict[str, set[int]] = {}
        for cas in win32.rglob("*.cas"):
            m = re.fullmatch(r"cas_(\d+)", cas.stem, re.I)
            if m:
                directories.setdefault(cas.parent.relative_to(win32).as_posix(), set()).add(int(m.group(1)))
        manifest = self.layout.field("installManifest")
        chunks = manifest.field("installChunks") if manifest else None
        for entry in (chunks.children if chunks else []):
            name, index = entry.field("name"), entry.field("persistentIndex")
            if name and name.type == 7 and name.text and index and index.type == 8 and len(index.payload) == 4:
                self.chunk_directory.setdefault(struct.unpack("<I", index.payload)[0], name.text.lower())
        by_archives: dict[frozenset, list[str]] = {}
        for directory, archives in directories.items():
            by_archives.setdefault(frozenset(archives), []).append(directory)
        for key in ("layeredInstallChunkFiles", "unlayeredInstallChunkFiles"):
            node = self.layout.field(key)
            if not node or node.type != 19:
                continue
            declared: dict[int, set[int]] = {}
            table = node.payload
            for at in range(0, len(table) - 7, 8):
                archive = table[at] | table[at + 1] << 8
                (chunk,) = struct.unpack_from("<I", table, at + 2)
                declared.setdefault(chunk, set()).add(archive)
            for chunk, archives in declared.items():
                found = by_archives.get(frozenset(archives))
                if found and len(found) == 1:
                    self.chunk_directory.setdefault(chunk, found[0].lower())

    def directory(self, install_chunk: int) -> str:
        try:
            return self.chunk_directory[install_chunk]
        except KeyError:
            raise KeyError(f"install chunk {install_chunk} has no known package directory") from None

    def chunk_index(self, name: str) -> int:
        """The persistentIndex of an install chunk, by its (package directory) name."""
        for index, directory in self.chunk_directory.items():
            if directory == name.lower():
                return index
        raise KeyError(f"layout has no install chunk {name}")

    def read(self, location: CasIdentifier, offset: int, size: int) -> bytes:
        if location.patch and not self.mod:
            raise ValueError("patch locations point into a mod, not the game's data")
        base = self.mod if location.patch else self.data
        path = base / "Win32" / self.directory(location.install_chunk) / archive_file(location.archive)
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(size)
        if len(data) != size:
            raise ValueError(f"{path} is shorter than the bundle says")
        return data

    def superbundle(self, relative: str):
        """A superbundle TOC by its path under Data/Win32, e.g. 'levels/game/bam_levelroot/bam_levelroot'."""
        if self.mod and (self.mod / "Win32" / (relative + ".toc")).is_file():
            return read_toc((self.mod / "Win32" / (relative + ".toc")).read_bytes())
        return read_toc((self.data / "Win32" / (relative + ".toc")).read_bytes())

    def bundle(self, toc, name: str) -> tuple[TocBundle, BundleRegion, BinaryBundle]:
        wanted = name.lower()
        for b in toc.bundles:
            if b.name.lower() == wanted:
                region = read_bundle_region(b.region)
                if region.inline_manifest:
                    manifest = read_binary_bundle(region.inline_manifest)
                else:
                    first = region.files[0]
                    raw = self.read(first.location, first.offset, first.size)
                    try:
                        manifest = read_binary_bundle(raw)
                    except ValueError:
                        manifest = read_binary_bundle(decode_cas(raw, self.root))
                return b, region, manifest
        raise KeyError(f"bundle {name} not found")

    def find_ebx(self, superbundle: str, ebx_name: str) -> str | None:
        """The name of the first bundle in a superbundle whose manifest lists this EBX (reads every
        manifest until one does, so it can take a while on a large superbundle)."""
        wanted = ebx_name.lower()
        toc = self.superbundle(superbundle)
        for b in toc.bundles:
            _, _, manifest = self.bundle(toc, b.name)
            if any(asset.name.lower() == wanted for asset in manifest.ebx):
                return b.name
        return None

    def ebx(self, region: BundleRegion, manifest: BinaryBundle, ebx_name: str) -> bytes:
        for i, asset in enumerate(manifest.ebx):
            if asset.name.lower() == ebx_name.lower():
                return self.payload(region, (0 if region.inline_manifest else 1) + i)
        raise KeyError(f"EBX {ebx_name} not in the bundle")

    def payload(self, region: BundleRegion, file_index: int) -> bytes:
        f = region.files[file_index]
        return decode_cas(self.read(f.location, f.offset, f.size), self.root)
