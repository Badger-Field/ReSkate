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

import re
import struct
import time
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


# Not real materials: distant-scenery vistas (baked, impostor-shaded: the PC's M1 auto pick of a
# vista drew the ground almost black and the features flat grey), impostors, proxies, LODs and
# shadows. A section of such a bundle, mesh or material is never usable.
# Matched against whole words of the name (name_words), never inside a word.
PROXY_WORDS = ("vista", "impostor", "imposter", "proxy", "billboard", "shadow", "lod")
LOD_WORD = re.compile(r"lod\d+$")


def name_words(text: str) -> set[str]:
    """The words of a name: split at anything not a letter or digit, each also without a plural s."""
    words = set()
    for token in re.split(r"[^a-z0-9]+", text.lower()):
        if token:
            words.add(token)
            if token.endswith("s") and len(token) > 3:
                words.add(token[:-1])
    return words


def proxy_word(text: str) -> str | None:
    """The proxy word a name holds as a whole word (vista, lod, lod1, ...), or None."""
    words = name_words(text)
    return next((w for w in sorted(words) if w in PROXY_WORDS or LOD_WORD.match(w)), None)


def is_proxy(text: str) -> bool:
    return proxy_word(text) is not None


def material_words(material: str) -> set[str]:
    """The words of a material name (name_words), so 'Wood_Accent' has wood but
    'benchbacklessstone_01_3m_Stone_mat' has only stone."""
    return name_words(material)


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


def section_problem(bundle: str, mesh: str, material: str, key_found: bool, has_uv0: bool) -> str:
    """Why a section is not usable ("" when it is)."""
    for label, text in (("bundle", bundle), ("mesh", mesh), ("material", material)):
        word = proxy_word(text)
        if word:
            return f"{label} name has the proxy word {word!r}"
    if not key_found:
        return "key not in the bundle's depots"
    if not has_uv0:
        return "no TexCoord0"
    return ""


def scan_bundle(game, superbundle: str, toc, bundle, every: bool = False) -> dict | None:
    """One bundle's row (see scan), or None when it has no MeshSets; {"meshes": True} when none of
    its sections is classed. row["why"][(mesh, n)] says why a section is not usable; `every`
    also lists unclassed sections (classes [])."""
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
    why = {}
    for i, r in meshes:
        try:
            data = game.payload(region, base + len(manifest.ebx) + i)
            lod = read_layout(data)[0]
        except (OSError, ValueError, struct.error):
            continue
        for n, s in enumerate(lod.sections):
            material = _material_name(data, s)
            kinds = classes_of(material) or classes_of(r.name)
            if not kinds and not every:
                continue
            problem = section_problem(bundle.name, r.name, material, section_key(data, s) in keys,
                                      any(u == UV0_USAGE for u, _, _, _ in s.elements))
            why[(r.name, n)] = problem
            sections.append((r.name, n, material, kinds, not problem, s.strides))
    if not any(s[3] for s in sections) and not every:
        return {"meshes": True}
    return {"superbundle": superbundle, "bundle": bundle.name, "bytes": sum(f.size for f in region.files), "why": why,
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
    """Usable concrete or stone and usable wood, in distinct sections whose material names say so
    (auto_donors' rules)."""
    picks = auto_donors(row)
    return ("ground" in picks or "concrete" in picks) and "wood" in picks


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


# --material-bundle auto: what each visual class takes from the picked bundle, by the words of a
# usable section's material name (material_words; not its mesh name, not a substring of a long
# baked name), tried in order. Concrete (steps, kerbs, banks) first, poured concrete preferred;
# then the ground, pavers or stone preferred, never concrete's section and a different material
# while one matches; wood never a section taken by the two before. Building, stone and metal
# may share a section already taken when nothing else matches.
AUTO_PICKS = (
    ("concrete", ("poured", "concrete", "cement", "curb", "kerb"), True),
    ("ground", ("paver", "paving", "pavement", "sidewalk", "cobble", "cobblestone", "flagstone", "tile", "granite",
                "stone", "concrete", "cement"), True),
    ("wood", ("wood", "plank", "timber"), True),
    ("building", ("brick", "wall", "facade", "plaster", "stucco", "stone", "concrete", "cement"), False),
    ("stone", ("stone", "granite", "marble", "cobble", "cobblestone", "brick"), False),
    ("metal", ("metal", "steel", "iron", "rail", "aluminium", "aluminum"), False),
)
AUTO_SCAN_LIMIT = None         # sublevel bundles read at most (None: every candidate; tiles are small)
AUTO_MIN_USABLE = 3            # usable classed sections a bundle needs (concrete, ground and wood at least)
AUTO_MIN_BYTES = 64 * 1024     # stored size a bundle needs (near-empty bundles are skipped)
AUTO_ENOUGH = 12               # qualifying bundles compared before choosing


def auto_donors(row: dict) -> dict[str, str]:
    """CLASS -> "MESH:SECTION" from one scanned bundle's usable sections (AUTO_PICKS)."""
    usable = [s for s in row["sections"] if s[4]]
    out: dict[str, str] = {}
    taken_sections: set[tuple[str, int]] = set()
    taken_materials: set[str] = set()
    for label, words, distinct in AUTO_PICKS:
        matches = [s for word in words for s in usable if word in material_words(s[2])]
        if distinct:
            matches = [s for s in matches if (s[0], s[1]) not in taken_sections]
        if not matches:
            continue
        pick = next((s for s in matches if s[2].lower() not in taken_materials and (s[0], s[1]) not in taken_sections),
                    matches[0])
        out[label] = f"{pick[0]}:{pick[1]}"
        taken_sections.add((pick[0], pick[1]))
        taken_materials.add(pick[2].lower())
    return out


def _material_of(row: dict, spec: str) -> str:
    mesh, _, n = spec.rpartition(":")
    return next(s[2] for s in row["sections"] if s[0] == mesh and s[1] == int(n))


def auto_score(row: dict) -> tuple[int, ...]:
    """How well a bundle suits a plaza: ground of pavers or stone (not concrete), poured concrete,
    a ground material different from concrete's, a world-partition tile (wproot/wp/)."""
    picks = auto_donors(row)
    ground = material_words(_material_of(row, picks["ground"])) if "ground" in picks else set()
    concrete = material_words(_material_of(row, picks["concrete"])) if "concrete" in picks else set()
    pavers = bool(ground & {"paver", "paving", "pavement", "sidewalk", "cobble", "cobblestone", "flagstone", "tile",
                            "granite", "stone"})
    different = "ground" in picks and "concrete" in picks and _material_of(row, picks["ground"]) != _material_of(
        row, picks["concrete"])
    return (int(pavers), int("poured" in concrete), int(different), int("/wproot/wp/" in row["bundle"].lower()))


def auto_rejection(row: dict) -> str:
    """Why a scanned bundle does not qualify for --material-bundle auto ("" when it does)."""
    if not row.get("sublevel", False):
        return "no sublevel EBX of its own name"
    if row["bytes"] < AUTO_MIN_BYTES:
        return f"{row['bytes']} bytes stored, under {AUTO_MIN_BYTES}"
    usable = sum(1 for s in row["sections"] if s[4] and s[3])
    if usable < AUTO_MIN_USABLE:
        return f"{usable} usable classed sections, under {AUTO_MIN_USABLE}"
    picks = auto_donors(row)
    missing = [k for k in ("concrete", "ground", "wood") if k not in picks]
    if missing:
        return (f"no distinct usable section for {', '.join(missing)} (by material-name words; picks so far: "
                + (", ".join(f"{k}={v}" for k, v in picks.items()) or "none") + ")")
    return ""


def auto_qualifies(row: dict) -> bool:
    return not auto_rejection(row)


def auto_bundle(game, superbundles_: list[str], log=print, limit: int | None = None):
    """The best sublevel bundle of `superbundles_` for a plaza: (superbundle, bundle, row), or
    None. Candidates: bundles named *_sublevel, not proxies (vistas and the like); world-partition
    tiles (wproot/wp/) first, each group smallest first by stored size, at most `limit` read. A
    bundle qualifies with distinct usable sections for concrete, ground and wood (auto_donors)
    and at least AUTO_MIN_USABLE usable sections; of the first AUTO_ENOUGH that qualify, the best
    auto_score wins, then the smallest."""
    limit = limit if limit is not None else AUTO_SCAN_LIMIT
    candidates = []
    skipped = 0
    for sb in superbundles_:
        toc = _read_toc(game, sb, False, log)
        if toc is None:
            continue
        for b in toc.bundles:
            if not b.name.lower().endswith("_sublevel"):
                continue
            if is_proxy(b.name):
                skipped += 1
                continue
            candidates.append(("/wproot/wp/" not in b.name.lower(), stored_size(b), sb, toc, b))
    candidates.sort(key=lambda c: (c[0], c[1], c[4].name))
    log(f"--material-bundle auto: {len(candidates)} sublevel bundles in {', '.join(superbundles_)} "
        f"({skipped} vista or proxy bundles left out), wproot/wp/ tiles first, smallest first"
        + (f" (at most {limit} read)" if limit is not None else "") + "; each needs distinct usable concrete, "
        f"ground and wood sections, {AUTO_MIN_USABLE}+ usable classed sections and {AUTO_MIN_BYTES // 1024}+ KB")
    found = []
    rejected = []
    tried = 0
    started = time.monotonic()
    for _, size, sb, toc, b in candidates[:limit]:
        tried += 1
        if tried % 100 == 0:
            log(f"  ... {tried} read, {len(found)} qualifying, {time.monotonic() - started:.0f} s")
        if size < AUTO_MIN_BYTES:
            continue
        row = scan_bundle(game, sb, toc, b)
        if not row or "sections" not in row:
            reason = "no MeshSets" if not row else "no classed sections"
        else:
            reason = auto_rejection(row)
        if not reason:
            found.append((auto_score(row), -size, sb, b.name, row))
            if len(found) >= AUTO_ENOUGH:
                break
        elif len(rejected) < 5 and reason not in ("no MeshSets", "no classed sections"):
            rejected.append(f"  rejected {b.name} ({size / 2 ** 20:.2f} MB): {reason}")
    log(f"--material-bundle auto: {tried} read in {time.monotonic() - started:.1f} s, {len(found)} qualifying")
    for line in rejected:
        log(line)
    if not found:
        return None
    found.sort(key=lambda f: (f[0], f[1]), reverse=True)
    for score, size, sb, name, row in found[:5]:
        log(f"  candidate {name} ({-size / 2 ** 20:.2f} MB, {sum(1 for s in row['sections'] if s[4])} usable): "
            f"pavers or stone ground {score[0]}, poured concrete {score[1]}, ground differs {score[2]}, "
            f"wproot tile {score[3]}")
    score, size, sb, name, row = found[0]
    log(f"--material-bundle auto: {sb}:{name} ({-size / 2 ** 20:.2f} MB; the best of {len(found)} qualifying, "
        f"{tried} read)")
    return sb, name, row


def explain(game, superbundle: str, bundle_name: str) -> list[str]:
    """scan-materials --explain: every section of one bundle with its material-name words, its
    classes (the report's, by substring) and auto-pick roles (by whole words), whether it is
    usable and why not; then the auto pick's verdict for the bundle."""
    toc = game.superbundle(superbundle)
    bundle = next((b for b in toc.bundles if b.name.lower() == bundle_name.lower()), None)
    if bundle is None:
        return [f"{superbundle}: no bundle {bundle_name}"]
    row = scan_bundle(game, superbundle, toc, bundle, every=True)
    if not row or "sections" not in row:
        return [f"{bundle.name}: no MeshSets"]
    out = [f"{superbundle}:{bundle.name}: {row['bytes'] / 2 ** 20:.2f} MB stored, {row['ebx']} EBX, {row['res']} res, "
           f"{row['depots']} depot(s), " + ("a sublevel bundle" if row["sublevel"] else "NOT a sublevel bundle")
           + (f"; bundle name has the proxy word {proxy_word(bundle.name)!r}" if proxy_word(bundle.name) else "")]
    for mesh, n, material, kinds, usable, strides in row["sections"]:
        words = sorted(material_words(material))
        roles = [label for label, wanted, _ in AUTO_PICKS if set(wanted) & set(words)]
        problem = row["why"].get((mesh, n), "")
        out.append(f"  {mesh}:{n}  {material!r}")
        out.append(f"      words {words}; classes {kinds or '-'}; auto roles {roles or '-'}; strides {strides}; "
                   + ("usable" if usable else f"NOT usable: {problem}"))
    reason = auto_rejection(row)
    picks = auto_donors(row)
    out.append("auto pick: " + (f"qualifies, score {auto_score(row)} (pavers or stone ground, poured concrete, "
                                "ground differs, wproot tile)" if not reason else f"rejected: {reason}"))
    for label, spec in picks.items():
        out.append(f"  {label} = {spec} ({_material_of(row, spec)!r})")
    return out
