"""Read access to an installed game's data (port of Engine/Vfs/game_archives.cpp).

<game>/Data/layout.toc names each install chunk's package directory; archives live at
<game>/Data/Win32/<package>/cas_NN.cas. Compressed payloads are decoded with the game's own
Oodle DLL (Windows only).
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
    def __init__(self, game_root: str | Path):
        self.root = Path(game_root)
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

    def read(self, location: CasIdentifier, offset: int, size: int) -> bytes:
        if location.patch:
            raise ValueError("patch locations point into a mod, not the game's data")
        path = self.data / "Win32" / self.directory(location.install_chunk) / archive_file(location.archive)
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(size)
        if len(data) != size:
            raise ValueError(f"{path} is shorter than the bundle says")
        return data

    def superbundle(self, relative: str):
        """A superbundle TOC by its path under Data/Win32, e.g. 'levels/game/bam_levelroot/bam_levelroot'."""
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

    def payload(self, region: BundleRegion, file_index: int) -> bytes:
        f = region.files[file_index]
        return decode_cas(self.read(f.location, f.offset, f.size), self.root)
