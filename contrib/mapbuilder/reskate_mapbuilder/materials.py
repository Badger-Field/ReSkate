"""Where real materials live: a read-only scan of the game's bundles (`scan-materials`).

A MeshSet section draws only with a shader-block key found in the ShaderBlockDepot of the bundle
holding it (FORMAT-NOTES 1), so a material can be borrowed only together with its bundle. This
scan walks superbundles and bundles, finds MeshSet sections whose material names (or mesh names)
say concrete, stone, brick, pavement, sidewalk, curb, wood, plank or metal, checks each one's key
against its own bundle's depots, and ranks the bundles by usable sections per MB of the bundle's
stored size (what `--material-bundle` would list in a level's TOC; its payloads stay in the
game's archives).
"""
from __future__ import annotations

import struct
from collections import Counter

from . import nativedb
from .meshset import DEPOT_TYPE, RESOURCE_TYPE as MESHSET_TYPE, depot_keys, read_layout, section_key

CLASSES = {
    "concrete": ("concrete", "cement", "curb", "kerb", "sidewalk", "pavement", "paving", "plaza"),
    "stone": ("stone", "brick", "cobble", "granite", "marble", "tile"),
    "wood": ("wood", "plank", "timber", "deck", "bench", "pallet"),
    "metal": ("metal", "steel", "iron", "rail", "chrome", "alum", "bollard", "hydrant", "bikerack"),
}
UV0_USAGE = 33


def classes_of(text: str) -> list[str]:
    low = text.lower()
    return [k for k, words in CLASSES.items() if any(w in low for w in words)]


def superbundles(game) -> list[str]:
    """The superbundles layout.toc lists, as paths under Win32 without .toc."""
    out = []
    for name in nativedb.layout_superbundles(game.layout):
        out.append(name[len("Win32/"):] if name.lower().startswith("win32/") else name)
    return out


def _material_name(data: bytes, section) -> str:
    (pointer,) = struct.unpack_from("<Q", data, section.at + 8)
    end = data.find(b"\0", pointer + 16) if pointer else -1
    return data[pointer + 16:end].decode("latin-1") if pointer and 0 < end else ""


def scan(game, only: list[str] | None = None, bundle_contains: str = "", limit: int | None = None,
         log=print) -> list[dict]:
    """One row per bundle with matching sections: superbundle, bundle, stored size, depots, and
    its sections [(mesh, index, material, classes, usable, strides)]. `log` gets progress lines."""
    rows = []
    scanned = 0
    for sb in only or superbundles(game):
        try:
            toc = game.superbundle(sb)
        except (OSError, ValueError) as e:
            log(f"{sb}: not read ({e})")
            continue
        for b in toc.bundles:
            if bundle_contains and bundle_contains.lower() not in b.name.lower():
                continue
            if limit is not None and scanned >= limit:
                return rows
            try:
                _, region, manifest = game.bundle(toc, b.name)
            except (OSError, ValueError, KeyError):
                continue
            meshes = [(i, r) for i, r in enumerate(manifest.resources) if r.resource_type == MESHSET_TYPE]
            if not meshes:
                continue
            scanned += 1
            base = 0 if region.inline_manifest else 1
            keys = set()
            depots = 0
            for i, r in enumerate(manifest.resources):
                if r.resource_type == DEPOT_TYPE:
                    depots += 1
                    try:
                        keys |= depot_keys(game.payload(region, base + len(manifest.ebx) + i))
                    except (OSError, ValueError):
                        pass
            sections = []
            for i, r in meshes:
                try:
                    data = game.payload(region, base + len(manifest.ebx) + i)
                    lod = read_layout(data)[0]
                except (OSError, ValueError, struct.error):
                    continue
                for n, s in enumerate(lod.sections):
                    material = _material_name(data, s)
                    kinds = classes_of(material) or classes_of(r.name)
                    if not kinds or "shadow" in material.lower():
                        continue
                    usable = section_key(data, s) in keys and any(u == UV0_USAGE for u, _, _, _ in s.elements)
                    sections.append((r.name, n, material, kinds, usable, s.strides))
            if sections:
                size = sum(f.size for f in region.files)
                rows.append({"superbundle": sb, "bundle": b.name, "bytes": size, "depots": depots,
                             "sections": sections, "ebx": len(manifest.ebx), "res": len(manifest.resources),
                             "sublevel": any(a.name.lower() == b.name.lower()[len("win32/"):] for a in manifest.ebx)})
    return rows


def report(rows: list[dict], top: int = 30) -> list[str]:
    """The bundles ranked by usable sections per MB, each with its usable sections."""
    def usable(row):
        return [s for s in row["sections"] if s[4]]

    ranked = sorted(rows, key=lambda r: -len(usable(r)) / max(r["bytes"] / 2 ** 20, 0.01))
    counts = Counter(k for r in rows for s in usable(r) for k in s[3])
    out = [f"{len(rows)} bundles with classed sections; usable sections per class: "
           + ", ".join(f"{k} {counts[k]}" for k in CLASSES)]
    for r in ranked[:top]:
        good = usable(r)
        if not good:
            continue
        mb = r["bytes"] / 2 ** 20
        out.append(f"{r['superbundle']}:{r['bundle']}  {mb:.1f} MB, {r['ebx']} EBX, {r['res']} res, "
                   f"{r['depots']} depot(s), {len(good)} usable ({len(good) / max(mb, 0.01):.1f} per MB)"
                   + (", a sublevel bundle" if r["sublevel"] else ""))
        for mesh, n, material, kinds, _, strides in good[:12]:
            out.append(f"    {mesh}:{n}  {material!r} {kinds} strides {strides}")
        if len(good) > 12:
            out.append(f"    ... {len(good) - 12} more")
    return out
