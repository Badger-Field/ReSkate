"""What a mesh refers to, and where each reference resolves (read-only; `mesh-refs`).

For one RigidMeshAsset: its document (materials and what they import), every import resolved to an
EBX name in the bundles scanned, its MeshSet's header, LOD and section structs, and every value in
the MeshSet's bytes that matches something known: a GUID (chunk, EBX file, import, instance), a
resource id, or a 32-bit hash (djb2, FNV-1, FNV-1a, both cases) of a name in the mesh's bundle or of
a material name. Also the mesh's bundle's resources by type and those named like the mesh or like
shader, block, depot, material or VT data. With a level, says whether the level's TOC and bundles
list each thing found.

FORMAT-NOTES 6.3: nothing our level places is drawn, and a copied lighthouse crashes in a loop over
MeshSet sections on a null per-section object; this is the tool to find what that object is.
"""
from __future__ import annotations

import struct
from collections import Counter, defaultdict

from .bundle import Guid
from .ebx import read_document, read_file_guid
from .ebxcheck import _djb2_add, _djb2_xor, _fnv1, _fnv1a
from .ebxedit import describe
from .meshset import (DEPOT_TYPE, HEADER_SIZE, LOD_SIZE, SECTION_SIZE, depot_records, depot_keys, describe_meshset,
                      read_layout, section_key)

MESHSET_TYPE = 0x49B156D4
KNOWN_TYPES = {0x49B156D4: "MeshSet", 0x41759364: "PhysicsResource", 0x6BDE20BA: "Texture",
               0x73312045: "ShaderBlockDepot", 0x2FEC6526: "ShaderStateAssetId"}
WORDS = ("shader", "block", "depot", "material", "vt", "virtualtexture", "surface")
HASHES = (("djb2-xor", _djb2_xor), ("djb2-add", _djb2_add), ("fnv1", _fnv1), ("fnv1a", _fnv1a))


class _Bundle:
    def __init__(self, game, superbundle: str, name: str):
        self.game, self.superbundle = game, superbundle
        self.toc = game.superbundle(superbundle)
        self.toc_bundle, self.region, self.manifest = game.bundle(self.toc, name)
        self.name = self.toc_bundle.name
        self._guids = None

    def index(self, kind: str, i: int) -> int:
        base = 0 if self.region.inline_manifest else 1
        if kind == "ebx":
            return base + i
        if kind == "res":
            return base + len(self.manifest.ebx) + i
        return base + len(self.manifest.ebx) + len(self.manifest.resources) + i

    def payload(self, kind: str, i: int) -> bytes:
        return self.game.payload(self.region, self.index(kind, i))

    def guids(self) -> dict[Guid, str]:
        if self._guids is None:
            self._guids = {}
            for i, a in enumerate(self.manifest.ebx):
                try:
                    self._guids.setdefault(read_file_guid(self.payload("ebx", i)), a.name)
                except (ValueError, struct.error, IndexError):
                    pass
        return self._guids


def _regions(data: bytes):
    """(start, end, label) of the header, LOD and section structs of a rigid MeshSet."""
    out = [(0, HEADER_SIZE, "header")]
    try:
        for k, lod in enumerate(read_layout(data)):
            out.append((lod.at, lod.at + LOD_SIZE, f"LOD {k}"))
            for s, section in enumerate(lod.sections):
                out.append((section.at, section.at + SECTION_SIZE, f"LOD {k} section {s}"))
    except (ValueError, struct.error):
        pass
    return out


def _where(regions, offset: int) -> str:
    for start, end, label in regions:
        if start <= offset < end:
            return f"{label} +{offset - start}"
    return f"data @{offset}"


def _section_names(data: bytes) -> list[str]:
    names = []
    try:
        for lod in read_layout(data):
            for s in lod.sections:
                (pointer,) = struct.unpack_from("<Q", data, s.at + 8)
                at = pointer + 16
                end = data.find(b"\0", at)
                if pointer and 0 < at < len(data) and end > at:
                    names.append(data[at:end].decode("latin-1"))
    except (ValueError, struct.error):
        pass
    return names


def mesh_refs(game, superbundle: str, bundle: str, mesh: str, scan: list[tuple[str, str]] = (),
              level: str | None = None) -> list[str]:
    home = _Bundle(game, superbundle, bundle)
    scanned = [home] + [_Bundle(game, sb, b) for sb, b in scan]
    out = [f"mesh {mesh} in {superbundle} / {home.name}; imports resolved in: " + ", ".join(b.name for b in scanned)]
    by_guid: dict[Guid, str] = {}
    for b in scanned:
        for g, name in b.guids().items():
            by_guid.setdefault(g, f"{name} ({b.name})")

    # The RigidMeshAsset itself.
    i = next((i for i, a in enumerate(home.manifest.ebx) if a.name.lower() == mesh.lower()), None)
    if i is None:
        return out + [f"  {mesh} is not an EBX of {home.name}"]
    doc = read_document(home.payload("ebx", i))
    out.append(f"document: root {doc.root_type}, {len(doc.instances)} instances, {len(doc.imports)} imports")
    out += ["  " + line for line in describe(doc, 12)]
    out.append("imports:")
    for k, imp in enumerate(doc.imports):
        out.append(f"  [{k}] {imp.file_guid.string()} / {imp.class_guid.string()}: "
                   + by_guid.get(imp.file_guid, "UNRESOLVED in the bundles scanned"))

    # Its MeshSet and every recognisable value in it.
    found = [(j, r) for j, r in enumerate(home.manifest.resources) if r.name.lower() == mesh.lower()
             and r.resource_type == MESHSET_TYPE]
    if not found:
        out.append(f"no MeshSet named {mesh} in {home.name}")
        data = b""
    else:
        j, asset = found[0]
        data = home.payload("res", j)
        out.append(f"MeshSet {asset.name}: id {asset.resource_id:#018x}, meta {asset.resource_meta.hex()}")
        out += ["  " + line for line in describe_meshset(data)]
    # Each section's shader-block key and the depot that holds it.
    depots: dict[int, list[str]] = defaultdict(list)
    for b in scanned:
        for j2, r in enumerate(b.manifest.resources):
            if r.resource_type == DEPOT_TYPE:
                payload = b.payload("res", j2)
                try:
                    count = f"{len(depot_records(payload))} records"
                except (ValueError, struct.error) as e:
                    count = f"records not parsed ({e})"
                out.append(f"depot {r.name} in {b.name}: {len(payload)} bytes, id {r.resource_id:#018x}, meta "
                           f"{r.resource_meta.hex()}, {count}")
                for key in depot_keys(payload):
                    depots[key].append(f"{r.name} ({b.name})")
    section_keys = []
    try:
        for k, lod in enumerate(read_layout(data) if data else []):
            for n, sec in enumerate(lod.sections):
                key = section_key(data, sec)
                section_keys.append(key)
                held = depots.get(key)
                out.append(f"LOD {k} section {n}: shader-block key {key:#018x} (+0x138): "
                           + (f"in {', '.join(held)}" + ("" if any(f"({home.name})" in h for h in held)
                                                        else f"; NOT in a depot of {home.name}, the MeshSet's bundle")
                              if held else "in NO depot of the bundles scanned"))
    except (ValueError, struct.error):
        pass
    guids: dict[bytes, str] = {}
    for b in scanned:
        for c in b.manifest.chunks:
            guids.setdefault(c.guid.bytes, f"chunk entry of {b.name}")
    toc_chunks = {c.guid for c in home.toc.chunks}
    for c in home.toc.chunks:
        guids.setdefault(c.guid.bytes, f"chunk in {superbundle}'s TOC")
    for g, name in by_guid.items():
        guids.setdefault(g.bytes, f"EBX file {name}")
    for imp in doc.imports:
        guids.setdefault(imp.class_guid.bytes, "an import's instance GUID")
    for inst in doc.instances:
        if inst.exported:
            guids.setdefault(inst.instance_guid.bytes, "an instance of the mesh document")
    ids = {}
    for b in scanned:
        for r in b.manifest.resources:
            ids.setdefault(r.resource_id, f"res {r.name} ({KNOWN_TYPES.get(r.resource_type, hex(r.resource_type))}, {b.name})")
    names = {mesh} | set(_section_names(data)) | {a.name for a in home.manifest.ebx} | {
        a.name for a in home.manifest.resources}
    hashes: dict[int, str] = {}
    for name in names:
        for variant in {name, name.lower()}:
            for label, function in HASHES:
                hashes.setdefault(function(variant), f"{label}({variant!r})")
    regions = _regions(data)
    hits = []
    for at in range(0, max(len(data) - 15, 0)):
        if data[at:at + 16] in guids:
            hits.append(f"  {_where(regions, at)}: GUID {Guid(data[at:at + 16]).string()} = {guids[data[at:at + 16]]}")
    for at in range(0, len(data) - 7, 4):
        (value,) = struct.unpack_from("<Q", data, at)
        if value and value in ids:
            hits.append(f"  {_where(regions, at)}: u64 {value:#018x} = resource id of {ids[value]}")
    for at in range(0, len(data) - 3, 4):
        (value,) = struct.unpack_from("<I", data, at)
        if value > 0xFFFF and value in hashes:
            hits.append(f"  {_where(regions, at)}: u32 {value:#010x} = {hashes[value]}")
    out.append(f"references found in the MeshSet ({len(hits)}):")
    out += hits or ["  none"]

    # The bundle's resources: by type, and those named like the mesh or like shader data.
    out.append(f"{home.name}: {len(home.manifest.ebx)} EBX, {len(home.manifest.resources)} res, "
               f"{len(home.manifest.chunks)} chunks; res by type:")
    by_type: dict[int, list[str]] = defaultdict(list)
    for r in home.manifest.resources:
        by_type[r.resource_type].append(r.name)
    for t, members in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        out.append(f"  {t:#010x} {KNOWN_TYPES.get(t, '?')}: {len(members)}, e.g. {members[:4]}")
    leaf = mesh.rsplit("/", 1)[-1].lower()
    related = [(kind, a) for kind, assets in (("ebx", home.manifest.ebx), ("res", home.manifest.resources))
               for a in assets if leaf in a.name.lower() or any(w in a.name.lower() for w in WORDS)]
    out.append(f"entries named like the mesh or like shader/material data ({len(related)}):")
    for kind, a in related[:200]:
        extra = f" type {a.resource_type:#010x} id {a.resource_id:#018x}" if kind == "res" else ""
        out.append(f"  {kind} {a.name}{extra}")

    # What a level of ours lists of all this.
    if level:
        path = f"levels/game/{level}/{level}"
        toc = game.superbundle(path)
        ebx, res, chunks = Counter(), Counter(), {c.guid for c in toc.chunks}
        for b in toc.bundles:
            _, _, manifest = game.bundle(toc, b.name)
            ebx.update(a.name.lower() for a in manifest.ebx)
            res.update(a.name.lower() for a in manifest.resources)
            chunks |= {c.guid for c in manifest.chunks}
        out.append(f"level {path}: {len(toc.bundles)} bundles, {len(ebx)} EBX, {len(res)} res, {len(chunks)} chunks")
        out.append(f"  mesh EBX listed: {mesh.lower() in ebx}; its MeshSet listed: {mesh.lower() in res}")
        for b in toc.bundles:
            _, region, manifest = game.bundle(toc, b.name)
            if not any(a.name.lower() == mesh.lower() for a in manifest.resources):
                continue
            keys = set()
            base = 0 if region.inline_manifest else 1
            for j2, r in enumerate(manifest.resources):
                if r.resource_type == DEPOT_TYPE:
                    keys |= depot_keys(game.payload(region, base + len(manifest.ebx) + j2))
            missing = [k for k in section_keys if k not in keys]
            out.append(f"  the MeshSet is in our {b.name}: its depots hold {len(section_keys) - len(missing)} of "
                       f"{len(section_keys)} section keys" + (f"; MISSING {[f'{k:#018x}' for k in missing]}" if missing else ""))
        for imp in doc.imports:
            name = by_guid.get(imp.file_guid, "").split(" (")[0]
            out.append(f"  import {name or imp.file_guid.string()}: "
                       + ("listed" if name and name.lower() in ebx else "NOT listed" if name else "unresolved"))
        try:
            lods = read_layout(data) if data else []
        except (ValueError, struct.error):
            lods = []
        if lods:
            for k, lod in enumerate(lods):
                out.append(f"  LOD {k} chunk {lod.chunk.string()}: "
                           + ("listed" if lod.chunk in chunks else "NOT listed")
                           + (f" (in {superbundle}'s TOC)" if lod.chunk in toc_chunks else ""))
        for kind, a in related[:200]:
            listed = (ebx if kind == "ebx" else res)[a.name.lower()] > 0
            out.append(f"  {kind} {a.name}: {'listed' if listed else 'NOT listed'}")
    return out
