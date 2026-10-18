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
from .bundle import read_bundle_region
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


def stored_size(bundle) -> int:
    """A TOC bundle's stored size (its region's files), read without touching the archives."""
    return sum(f.size for f in read_bundle_region(bundle.region).files)


def scan_bundle(game, superbundle: str, toc, bundle) -> dict | None:
    """One bundle's row (see scan), or None when it has no classed MeshSet sections."""
    try:
        _, region, manifest = game.bundle(toc, bundle.name)
    except (OSError, ValueError, KeyError):
        return None
    meshes = [(i, r) for i, r in enumerate(manifest.resources) if r.resource_type == MESHSET_TYPE]
    if not meshes:
        return None
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
    if not sections:
        return {"meshes": True}
    return {"superbundle": superbundle, "bundle": bundle.name, "bytes": sum(f.size for f in region.files),
            "depots": depots, "sections": sections, "ebx": len(manifest.ebx), "res": len(manifest.resources),
            "sublevel": any(a.name.lower() == bundle.name.lower()[len("win32/"):] for a in manifest.ebx)}


def _read_toc(game, superbundle: str, listed: bool, log):
    """A superbundle's TOC, or None. One that layout.toc lists but is not on disk (the locale
    superbundles, e.g. loc/wc, of languages not installed) is skipped silently; any other failure,
    or a superbundle asked for by name, is reported."""
    try:
        return game.superbundle(superbundle)
    except FileNotFoundError as e:
        if not listed:
            log(f"{superbundle}: not read ({e})")
    except (OSError, ValueError) as e:
        log(f"{superbundle}: not read ({e})")
    return None


def scan(game, only: list[str] | None = None, bundle_contains: str = "", limit: int | None = None,
         log=print) -> list[dict]:
    """One row per bundle with matching sections: superbundle, bundle, stored size, depots, and
    its sections [(mesh, index, material, classes, usable, strides)]. `log` gets progress lines."""
    rows = []
    scanned = 0
    for sb in only or superbundles(game):
        toc = _read_toc(game, sb, not only, log)
        if toc is None:
            continue
        for b in toc.bundles:
            if bundle_contains and bundle_contains.lower() not in b.name.lower():
                continue
            if limit is not None and scanned >= limit:
                return rows
            row = scan_bundle(game, sb, toc, b)
            if row is None:
                continue
            scanned += 1
            if "sections" in row:
                rows.append(row)
    return rows


GROUND_CLASSES = ("concrete", "stone")


def usable_classes(row: dict, by_material: bool = False) -> set[str]:
    return {k for s in row["sections"] if s[4] for k in (classes_of(s[2]) if by_material else s[3])}


def has_ground_and_wood(row: dict) -> bool:
    """Usable concrete (or stone) and usable wood sections in one bundle."""
    kinds = usable_classes(row)
    return bool(kinds & set(GROUND_CLASSES)) and "wood" in kinds


def report(rows: list[dict], top: int = 30, every: bool = False, only_classes: list[str] | None = None,
           ground_and_wood: bool = False) -> list[str]:
    """The bundles ranked by usable sections per MB, each with its usable sections (the first 12,
    or all with `every`); `only_classes` keeps only sections (and bundles) of those classes;
    `ground_and_wood` keeps only bundles with usable concrete or stone and usable wood."""
    def usable(row):
        return [s for s in row["sections"] if s[4] and (not only_classes or set(s[3]) & set(only_classes))]

    if ground_and_wood:
        rows = [r for r in rows if has_ground_and_wood(r)]
    rows = [r for r in rows if usable(r)] if only_classes else rows
    ranked = sorted(rows, key=lambda r: -len(usable(r)) / max(r["bytes"] / 2 ** 20, 0.01))
    counts = Counter(k for r in rows for s in usable(r) for k in s[3])
    shown = [k for k in CLASSES if not only_classes or k in only_classes]
    out = [f"{len(rows)} bundles with classed sections"
           + (" (usable concrete or stone and usable wood)" if ground_and_wood else "")
           + "; usable sections per class: " + ", ".join(f"{k} {counts[k]}" for k in shown)]
    for r in ranked[:top]:
        good = usable(r)
        if not good:
            continue
        mb = r["bytes"] / 2 ** 20
        out.append(f"{r['superbundle']}:{r['bundle']}  {mb:.1f} MB, {r['ebx']} EBX, {r['res']} res, "
                   f"{r['depots']} depot(s), {len(good)} usable ({len(good) / max(mb, 0.01):.1f} per MB)"
                   + (", a sublevel bundle" if r["sublevel"] else ""))
        listed = good if every else good[:12]
        for mesh, n, material, kinds, _, strides in listed:
            out.append(f"    {mesh}:{n}  {material!r} {kinds} strides {strides}")
        if len(good) > len(listed):
            out.append(f"    ... {len(good) - len(listed)} more (--every lists them all)")
    return out


# --material-bundle auto: what each visual class takes from the picked bundle. Per class, words
# tried in order, first in a usable section's material name, then in its mesh name; a section
# already taken by an earlier class (by material name) is skipped while another matches, so the
# ground (pavers or stone first) and steps, kerbs and banks (poured concrete first) differ when
# the bundle has both, and buildings get a wall material (brick, stone) else concrete.
AUTO_PICKS = (
    ("ground", ("paver", "paving", "pavement", "sidewalk", "cobble", "tile", "granite", "stone", "concrete")),
    ("concrete", ("poured", "concrete", "cement", "curb", "kerb", "stone", "paver", "paving", "pavement")),
    ("building", ("brick", "wall", "facade", "plaster", "stucco", "stone", "concrete", "cement")),
    ("stone", ("stone", "granite", "marble", "cobble", "brick")),
    ("wood", ("wood", "plank", "timber", "deck", "bench")),
    ("metal", ("metal", "steel", "iron", "rail", "alum")),
)
AUTO_SCAN_LIMIT = 400


def auto_donors(row: dict) -> dict[str, str]:
    """CLASS -> "MESH:SECTION" from one scanned bundle's usable sections (AUTO_PICKS)."""
    usable = [s for s in row["sections"] if s[4]]
    out: dict[str, str] = {}
    taken: set[str] = set()
    for label, words in AUTO_PICKS:
        matches = [s for word in words for s in usable if word in s[2].lower()]
        matches += [s for word in words for s in usable if word in s[0].lower()]
        if not matches:
            continue
        pick = next((s for s in matches if s[2].lower() not in taken), matches[0])
        out[label] = f"{pick[0]}:{pick[1]}"
        taken.add(pick[2].lower())
    return out


def auto_bundle(game, superbundles_: list[str], log=print, limit: int = AUTO_SCAN_LIMIT):
    """The smallest sublevel bundle (by stored size) of `superbundles_` with usable concrete (or
    stone) and usable wood sections: (superbundle, bundle, row), or None. Bundles named
    *_sublevel are tried smallest first, at most `limit` of them."""
    candidates = []
    for sb in superbundles_:
        toc = _read_toc(game, sb, False, log)
        if toc is None:
            continue
        for b in toc.bundles:
            if b.name.lower().endswith("_sublevel"):
                candidates.append((stored_size(b), sb, toc, b))
    candidates.sort(key=lambda c: (c[0], c[3].name))
    log(f"--material-bundle auto: {len(candidates)} sublevel bundles in {', '.join(superbundles_)}, "
        f"tried smallest first (at most {limit})")
    for tried, (size, sb, toc, b) in enumerate(candidates[:limit], 1):
        row = scan_bundle(game, sb, toc, b)
        if row and "sections" in row and row["sublevel"] and has_ground_and_wood(row):
            log(f"--material-bundle auto: {sb}:{b.name} ({size / 2 ** 20:.2f} MB, the smallest of "
                f"{tried} tried with usable concrete or stone and wood)")
            return sb, b.name, row
    return None
