"""Adds a new level to the game (FORMAT-NOTES 6.1 and 6.2).

For the first test the level is a clone of the base map (bam): its superbundle lists every one of
bam's bundles, copied as they are (so every payload stays in the base archives), with bam's root
bundle renamed to the new level's and carrying a clone of bam's root under the new name. The
level is registered the way the 333 map registers its own: a level description next to bam's, an
OnDemandBundles entry in the root level's description and a detached sublevel in the root's
DetachedSetBlueprint.

Everything below marked GUESS comes from the notes or from names, not from the game's files;
`new-level --dry-run` prints what it found for each, read-only.
"""
from __future__ import annotations

import copy
import re
from collections import Counter

from . import nativedb
from .bundle import read_bundle_region
from .ebx import Document, Object, PointerKind, PointerReference, read_document, write_document
from .ebxcheck import roundtrip
from .ebxedit import (Found, after_last_of_type, clone_document, clone_instance, derived_guid, find_objects, guid_flags,
                      pointer_arrays, set_fields, strings)
from .toc import bundle_hash

# The base level the new one clones, and the segment of its names swapped for the new level's.
BASE_LEVEL = "bam_levelroot"
BASE_SUPERBUNDLE = "levels/game/bam_levelroot/bam_levelroot"          # under Data/Win32, no .toc
BASE_ROOT_BUNDLE = "win32/levels/game/bam_levelroot/bam_levelroot"
BASE_ROOT_EBX = "levels/game/bam_levelroot/bam_levelroot"
ROOT_SUPERBUNDLE = "levels/game/dingolevel_root/dingolevel_root"
# GUESS: the name of bam's description, by analogy with the root's (which live_mods.h names).
BASE_DESCRIPTION_EBX = "levels/game/bam_levelroot/bam_levelroot/description"
ROOT_DESCRIPTION_EBX = "levels/game/dingolevel_root/dingolevel_root/description"
GENPREFAB_EBX = "levels/game/dingolevel_root/dingolevel_root_dsublevelmanager_genprefab"
# Where each is looked for, in order. GUESS: descriptions are in globals (mod_merge.cpp: "a map
# registers itself in globals (its level description and the root's on-demand entry)"); the new
# description goes into the bundle that holds bam's.
DESCRIPTION_SEARCH = ("globals", BASE_SUPERBUNDLE)
ROOT_DESCRIPTION_SEARCH = ("globals", ROOT_SUPERBUNDLE)
GENPREFAB_SEARCH = (ROOT_SUPERBUNDLE, "globals")

# GUESS: type and field names, from the 333 map's inventory (FORMAT-NOTES 6.1).
ON_DEMAND_FIELD = "OnDemandBundles"           # array of {SuperBundle, BundleHash}
SUBLEVEL_INFO_TYPE = "DetachedSubLevelInfo"
SUBWORLD_REFERENCE_TYPE = "SubWorldReferenceObjectData"
START_POINT = "DefaultStartPoint"
AUTOLOAD_SUBLEVELS = ["Levels/Game/BAM_LevelRoot/StartPoint_Sublevels", "Levels/game/BAM_LevelRoot/TOD_2_Noon"]
# As the 333 map's own reference: identity transform, no blueprint or template (GUESS: field names).
REFERENCE_TRANSFORM = "BlueprintTransform"
REFERENCE_NULLED = ("Blueprint", "Template")
SUBWORLD_REFERENCE = {"IsDetachedSubLevel": True, "AutoLoad": False, "StreamRealm": 3, "ObjectSubVariation": 0xFFFFFFFF}
ROOT_WORLD = {"WorldSizeXZ": 4096, "WorldSizeY": 1024, "StartLocalServer": True, "UseExtendedStartPoints": True}

# Which of the base level's bundles the new superbundle lists. None: all of them (a whole clone of
# bam, so everything its root refers to resolves). The 333 map lists only 42 (bam_coregameassets,
# startpoint_sublevels, tod_*, lighting_*, vfx_*); a tuple of lower-case name prefixes selects.
COPY_BASE_BUNDLES: tuple[str, ...] | None = None
# GUESS: the base TOC's own chunk list is carried into the new TOC (unchecked whether 333's has one).
COPY_BASE_CHUNKS = True

LEVEL_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")


def swap_level(text: str, level: str) -> str:
    return re.sub(re.escape(BASE_LEVEL), level, text, flags=re.I)


def _locate(game, searched: tuple[str, ...], ebx_name: str) -> tuple[str, str]:
    for superbundle in searched:
        bundle = game.find_ebx(superbundle, ebx_name)
        if bundle:
            return superbundle, bundle
    raise KeyError(f"{ebx_name} is in none of {', '.join(searched)}")


def _read(raw: bytes, label: str, log: list[str]) -> Document:
    doc = read_document(raw)
    log.append(f"  {label}: {len(raw)} bytes, root {doc.root_type}, {len(doc.instances)} instances, "
               f"file guid {doc.file_guid.string()}")
    report = roundtrip(raw, brief=True)
    log.extend("    " + line for line in report)
    if "NOT semantically equal" in report[0]:
        raise ValueError(f"{label}: rewriting it unchanged loses or changes data, so it is not edited "
                         f"(run ebx-roundtrip --detail on it)")
    return doc


def _clone_found(doc: Document, found: Found, guid_salt: str) -> object:
    """A copy of a found struct or instance, added beside it: to the same array, or as a new
    instance that every pointer array holding the original also points at."""
    if found.array is not None:
        clone = copy.deepcopy(found.object)
        found.array.append(clone)
        return clone
    if found.instance >= 0:
        source = doc.instances[found.instance]
        holders = pointer_arrays(doc, found.instance)
        index = clone_instance(doc, found.instance,
                               derived_guid(guid_salt, source.instance_guid.string()) if source.exported else None,
                               after_last_of_type(doc, found.instance))
        for _, array in holders:
            array.append(PointerReference(PointerKind.internal, index))
        return doc.instances[index].object
    raise ValueError(f"{found.path} is a single struct field; there is nowhere to add another")


def _named(found: list[Found], field: str, needle: str) -> list[Found]:
    return [f for f in found if needle.lower() in str(f.object.get(field) or "").lower()]


def _whole(found: list[Found], field: str, tail: str) -> list[Found]:
    return [f for f in found if str(f.object.get(field) or "").lower().endswith("/" + tail)]


def _flags_text(doc: Document, found: Found) -> str:
    flags = found.object.get("Flags")
    if not isinstance(flags, int):
        return repr(flags)
    instance = doc.instances[found.instance] if found.instance >= 0 else None
    if instance is None or not instance.exported:
        return f"{flags:#x}"
    same = guid_flags(instance.instance_guid, flags) == flags
    return f"{flags:#x} (guid {instance.instance_guid.string()}, {'follows' if same else 'does NOT follow'} the guid rule)"


def _identity(transform: Object) -> bool:
    """Sets a LinearTransform {right, up, forward, trans} of Vec3 {x, y, z} to identity."""
    rows = {"right": (1.0, 0.0, 0.0), "up": (0.0, 1.0, 0.0), "forward": (0.0, 0.0, 1.0), "trans": (0.0, 0.0, 0.0)}
    vectors = {name: transform.get(name) for name in rows}
    if not all(isinstance(v, Object) and all(v.find(a) is not None for a in "xyz") for v in vectors.values()):
        return False
    for name, values in rows.items():
        for axis, value in zip("xyz", values):
            vectors[name].set(axis, value)
    return True


def new_level(game, mod, level: str, display_name: str | None = None, keep_bundle=None, edit_root=None) -> list[str]:
    """Adds everything for a new level to `mod` and returns a report of what it found and changed.

    `keep_bundle(name)` (lower-case bundle name) chooses which of bam's bundles the level's TOC
    lists (default: COPY_BASE_BUNDLES). `edit_root(new_root, listed, log)` may change the new root
    document before it is written; `listed` is the set of lower-case bundle names the level's TOC
    holds (new-ground-level, FORMAT-NOTES 6.3)."""
    if keep_bundle is None:
        def keep_bundle(name):
            return COPY_BASE_BUNDLES is None or name.startswith(COPY_BASE_BUNDLES)
    if not LEVEL_NAME.fullmatch(level) or level == BASE_LEVEL:
        raise ValueError("level names use lower-case letters, digits and _, starting with a letter")
    path = f"levels/game/{level}/{level}"          # superbundle (under Win32), root bundle and root EBX
    root_bundle = "win32/" + path
    log = [f"New level {path}"]

    # 1. The superbundle: layout entries and a TOC of copies of bam's bundles.
    layout = nativedb.read_layout(game.layout_bytes)
    if nativedb.write(layout) != game.layout_bytes[nativedb.ENVELOPE_SIZE:]:
        raise ValueError("layout.toc does not rewrite to the same bytes; refusing to edit it")
    base_toc = game.superbundle(BASE_SUPERBUNDLE)
    mod.add_superbundle(path, chunks=base_toc.chunks if COPY_BASE_CHUNKS else None)
    log.append(f"layout.toc: rewrites identically; adds Win32/{path} to superBundles and to "
               f"{nativedb.DEFAULT_INSTALL_CHUNK} ({len(game.layout_bytes)} -> {len(mod.layout_bytes)} bytes)")
    chunks = Counter()
    copied = 0
    listed = {root_bundle}
    for b in base_toc.bundles:
        name = b.name.lower()
        if name == BASE_ROOT_BUNDLE or not keep_bundle(name):
            continue
        mod.copy_bundle(path, b)
        listed.add(name)
        copied += 1
        for f in read_bundle_region(b.region).files:
            chunks[f.location.install_chunk] += 1
    log.append(f"Win32/{path}.toc: {copied} of bam's {len(base_toc.bundles)} bundles copied as they are, "
               f"{len(base_toc.chunks) if COPY_BASE_CHUNKS else 0} TOC chunks; their files are in install chunks "
               + ", ".join(f"{c:#x} ({n})" for c, n in sorted(chunks.items())))

    # 2. The root bundle: bam's, renamed, plus a clone of bam's root under the new name.
    if not any(b.name.lower() == BASE_ROOT_BUNDLE for b in base_toc.bundles):
        raise KeyError(f"{BASE_ROOT_BUNDLE} is not in {BASE_SUPERBUNDLE}")
    root = mod.edit_bundle(path, root_bundle, source=(BASE_SUPERBUNDLE, BASE_ROOT_BUNDLE))
    log.append(f"{root_bundle}: copy of {BASE_ROOT_BUNDLE} ({len(root.manifest.ebx)} EBX, "
               f"{len(root.manifest.resources)} res, {len(root.manifest.chunks)} chunks) plus the new root")
    base_root = _read(mod.ebx(path, root_bundle, BASE_ROOT_EBX), BASE_ROOT_EBX, log)
    new_root = clone_document(base_root, derived_guid(level, "root", base_root.file_guid.string()), level + ":root")
    root_object = new_root.instances[0].object
    if base_root.root_type != "DetachedSubWorldData":
        log.append(f"  WARNING: bam's root is a {base_root.root_type}, not a DetachedSubWorldData")
    changes = dict(ROOT_WORLD)
    if isinstance(root_object.get("Name"), str):
        changes["Name"] = swap_level(root_object.get("Name"), level)
    missing = set_fields(root_object, changes)
    log.append(f"  new root {path}: file guid {new_root.file_guid.string()}, instance guid "
               f"{new_root.instances[0].instance_guid.string()}, set {sorted(set(changes) - set(missing))}"
               + (f", MISSING fields {missing}" if missing else ""))
    if edit_root is not None:
        edit_root(new_root, listed, log)
    mod.add_ebx(path, root_bundle, path, write_document(new_root, refuse_new_array_hashes=True))

    # 3. The level description, next to bam's.
    where = _locate(game, DESCRIPTION_SEARCH, BASE_DESCRIPTION_EBX)
    log.append(f"Level description: bam's is in {where[0]} / {where[1]}")
    base_description = _read(mod.ebx(*where, BASE_DESCRIPTION_EBX), BASE_DESCRIPTION_EBX, log)
    description = clone_document(base_description, derived_guid(level, "description", base_description.file_guid.string()),
                                 level + ":description")
    obj = description.instances[0].object
    old_guid = obj.get("LevelGuid")
    if old_guid == base_root.file_guid:
        new_guid, why = new_root.file_guid, "bam's equals its root's file guid, so the new root's"
    elif base_root.instances[0].exported and old_guid == base_root.instances[0].instance_guid:
        new_guid, why = new_root.instances[0].instance_guid, "bam's equals its root's instance guid, so the new root's"
    else:
        new_guid, why = derived_guid(level, "LevelGuid"), "bam's matches nothing in its root, so a new one"
    changes = {"LevelGuid": new_guid}
    for name in ("Name", "LevelName"):
        if isinstance(obj.get(name), str):
            changes[name] = swap_level(obj.get(name), level)
    missing = set_fields(obj, changes)
    description_name = swap_level(BASE_DESCRIPTION_EBX, level)
    log.append(f"  new {description_name}: file guid {description.file_guid.string()}, LevelName "
               f"{obj.get('LevelName')!r}, LevelGuid {new_guid.string() if new_guid else None} ({why})"
               + (f", MISSING fields {missing}" if missing else ""))
    for at, text in strings(description):
        if BASE_LEVEL in text.lower():
            log.append(f"  still names bam: {at} = {text!r}")
    mod.add_ebx(*where, description_name, write_document(description, refuse_new_array_hashes=True))

    # 4. The OnDemandBundles entry in the root level's description.
    where = _locate(game, ROOT_DESCRIPTION_SEARCH, ROOT_DESCRIPTION_EBX)
    log.append(f"Root description: {where[0]} / {where[1]}")
    doc = _read(mod.ebx(*where, ROOT_DESCRIPTION_EBX), ROOT_DESCRIPTION_EBX, log)
    holder = next((i.object for i in doc.instances if i.object is not None
                   and isinstance(i.object.get(ON_DEMAND_FIELD), list)), None)
    if holder is None or not holder.get(ON_DEMAND_FIELD):
        raise KeyError(f"{ROOT_DESCRIPTION_EBX} has no {ON_DEMAND_FIELD} entries to copy")
    entries = holder.get(ON_DEMAND_FIELD)
    for e in entries:
        name, value = e.get("SuperBundle"), e.get("BundleHash")
        log.append(f"  {ON_DEMAND_FIELD}: {name!r} {value} "
                   f"({'matches bundle_hash' if bundle_hash(name or '') == value else 'does NOT match bundle_hash'})")
        if level in (name or "").lower():
            raise ValueError(f"{level} is already registered")
    # bam's own entry: the root list holds others naming bam (e.g. levels/game/BAM_LevelRoot/_DEBUG_/...).
    template = next((e for e in entries if (e.get("SuperBundle") or "").lower() == BASE_SUPERBUNDLE), None)
    if template is None:
        raise KeyError(f"no {ON_DEMAND_FIELD} entry is {BASE_SUPERBUNDLE}")
    entry = copy.deepcopy(template)
    superbundle_field = swap_level(template.get("SuperBundle"), level)
    set_fields(entry, {"SuperBundle": superbundle_field, "BundleHash": bundle_hash(superbundle_field)})
    entries.append(entry)
    log.append(f"  adds {superbundle_field!r} {bundle_hash(superbundle_field)}")
    mod.replace_ebx(*where, ROOT_DESCRIPTION_EBX, write_document(doc, refuse_new_array_hashes=True))

    # 5. The detached sublevel in the root's DetachedSetBlueprint.
    where = _locate(game, GENPREFAB_SEARCH, GENPREFAB_EBX)
    log.append(f"Detached sublevels: {where[0]} / {where[1]}")
    doc = _read(mod.ebx(*where, GENPREFAB_EBX), GENPREFAB_EBX, log)
    infos = find_objects(doc, SUBLEVEL_INFO_TYPE)
    references = find_objects(doc, SUBWORLD_REFERENCE_TYPE)
    for f in infos:
        points = [p.get("Name") for p in f.object.get("StartPoints") or []]
        log.append(f"  {SUBLEVEL_INFO_TYPE} {f.path}: {f.object.get('DetachedSubLevelName')!r}, start points {points}")
    for f in references:
        log.append(f"  {SUBWORLD_REFERENCE_TYPE} {f.path}: {f.object.get('BundleName')!r}, "
                   f"detached {f.object.get('IsDetachedSubLevel')}, flags {_flags_text(doc, f)}")
    if _named(infos, "DetachedSubLevelName", level) or _named(references, "BundleName", level):
        raise ValueError(f"{level} is already a detached sublevel")
    whole = f"{BASE_LEVEL}/{BASE_LEVEL}"     # bam's own entry, not one of its sublevels or _DEBUG_ levels
    templates = _whole(infos, "DetachedSubLevelName", whole)
    if not templates:
        raise KeyError(f"no {SUBLEVEL_INFO_TYPE} names .../{whole}")
    info = _clone_found(doc, templates[0], level + ":info")
    info.set("DetachedSubLevelName", swap_level(info.get("DetachedSubLevelName"), level))
    points = info.get("StartPoints")
    if not points:
        raise KeyError(f"bam's {SUBLEVEL_INFO_TYPE} has no start points to copy")
    point = next((p for p in points if p.get("Name") == START_POINT), points[0])
    point.set("Name", START_POINT)
    point.set("AutoloadSublevels", list(AUTOLOAD_SUBLEVELS))
    point.set("IsDefault", True)
    points[:] = [point]
    log.append(f"  adds {SUBLEVEL_INFO_TYPE} {info.get('DetachedSubLevelName')!r} with {START_POINT} "
               f"autoloading {AUTOLOAD_SUBLEVELS}")
    templates = _whole(references, "BundleName", whole)
    templates.sort(key=lambda f: f.object.get("IsDetachedSubLevel") is not True)
    if not templates:
        raise KeyError(f"no {SUBWORLD_REFERENCE_TYPE} names .../{whole}")
    template = templates[0]
    reference = _clone_found(doc, template, level + ":reference")
    changes = dict(SUBWORLD_REFERENCE, BundleName=swap_level(reference.get("BundleName"), level))
    for name in REFERENCE_NULLED:
        if isinstance(reference.get(name), PointerReference):
            changes[name] = PointerReference()
    transform = reference.get(REFERENCE_TRANSFORM)
    if isinstance(transform, Object) and not _identity(transform):
        log.append(f"  WARNING: {REFERENCE_TRANSFORM} is not a LinearTransform; left as bam's")
    new_index = next((i for i, r in enumerate(doc.instances) if r.object is reference), -1)
    if new_index >= 0 and doc.instances[new_index].exported and isinstance(reference.get("Flags"), int):
        changes["Flags"] = guid_flags(doc.instances[new_index].instance_guid, reference.get("Flags"))
    missing = set_fields(reference, changes)
    where_put = f"instance #{new_index}" if new_index >= 0 else "a struct"
    log.append(f"  adds {SUBWORLD_REFERENCE_TYPE} {reference.get('BundleName')!r} as {where_put} (copy of "
               f"{template.path}), guid {doc.instances[new_index].instance_guid.string() if new_index >= 0 else '-'}, "
               f"flags {reference.get('Flags')}" + (f", MISSING fields {missing}" if missing else ""))
    mod.replace_ebx(*where, GENPREFAB_EBX, write_document(doc, refuse_new_array_hashes=True))

    mod.levels.append({"asset": path, "displayName": display_name or level, "startPoints": [START_POINT]})
    return log
