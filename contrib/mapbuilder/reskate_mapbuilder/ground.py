"""A level with its own ground (FORMAT-NOTES 6.3).

The level is registered exactly as `new-level` registers its clone of bam (level.py), and gets one
more bundle, `win32/levels/game/<level>/ground_sublevel`, holding:

- the sublevel `levels/game/<level>/ground_sublevel`: a copy of one of bam's world-partition tiles,
  cut down to the chain of objects that leads from its root to one `ObjectReferenceObjectData`
  placement, which is moved to `--at` and points at our blueprint;
- the blueprint `levels/game/<level>/ground_bp`: a copy of the ObjectBlueprint that placement used,
  so the visible mesh is that blueprint's own (a base-game RigidMeshAsset and its MeshSet, reused
  as they are), with its collision swapped for ours;
- our collision: a version-2 PhysicsResource holding a flat grid of `--size` metres, named and
  referenced the way the template blueprint's own is;
- the template blueprint's dependencies from the tile's bundle (its mesh, the mesh's MeshSet, the
  chunks those name, and what they import in turn), listed as they are, so their payloads stay in
  the base archives and the ground does not depend on bam's tile being loaded.

The level's root (a clone of bam's) gets a `SubWorldReferenceObjectData` naming the new bundle.
With `--bam no-world` the level's TOC leaves out bam's world-partition tiles (and the root loses
the references to them); with `--bam 333` it lists only the 42 bam bundles the 333 map carries.

Everything marked GUESS below comes from names or analogy, not from the game's files;
`new-ground-level --dry-run` prints what it found for each, read-only.
"""
from __future__ import annotations

import hashlib
import struct
from collections import Counter

from . import nativedb
from .bundle import CHUNK, EBX, Guid
from .ebx import Document, Object, PointerKind, PointerReference, ResourceReference, read_document, read_file_guid, write_document
from .ebxcheck import semantically_equal
from .ebxedit import (add_import, find_objects, path_to, prune_imports, prune_unreachable, refresh_flags, remove_instances,
                      clone_document, derived_guid, guid_flags, set_fields, type_name, used_imports)
from .level import (BASE_ROOT_BUNDLE, BASE_SUPERBUNDLE, LEVEL_NAME, SUBWORLD_REFERENCE_TYPE, _clone_found, _identity, _read,
                    new_level)
from .physics import KIND_TRIANGLE_MESH, RESOURCE_TYPE as PHYSICS_TYPE, Part, compute_edge_data, flat_grid, read_physics, write_physics

# Names of what the level adds (EBX names; the bundle is "win32/" + the sublevel's name).
GROUND_SUBLEVEL = "levels/game/{level}/ground_sublevel"
GROUND_BLUEPRINT = "levels/game/{level}/ground_bp"
# GUESS: the tile copied for the sublevel and its placement. This tile's name is known from its
# collision (FORMAT-NOTES 2.5); that its EBX is named like its bundle, and that it holds
# ObjectReferenceObjectData placements of ObjectBlueprints in its own bundle, is not.
TILE_TEMPLATE = "levels/game/bam_levelroot/wproot/wp/s9600_x0z0/s200_x700z1100_sublevel"
PLACEMENT_TYPE = "ObjectReferenceObjectData"
PLACEMENT_TRANSFORM = "BlueprintTransform"
PLACEMENT_BLUEPRINT = "Blueprint"
BLUEPRINT_TYPE = "ObjectBlueprint"
MESHSET_TYPE = 0x49B156D4
# GUESS: a blueprint's collision is a physics resource whose name starts with the blueprint's
# name, in the blueprint's bundle; the copy's is named the same way after the new blueprint.
# GUESS: a new resource's id is free to choose (derived from its name, see resource_id).
# GUESS: the reference that loads the ground: copied from one of the root's own sub-world
# references (preferring a non-detached one that auto-loads), set to auto-load.
GROUND_REFERENCE = {"AutoLoad": True, "IsDetachedSubLevel": False}
# GUESS: the collision is in the blueprint's own frame (the placement's transform moves it, as it
# moves the mesh). The 333 map bakes both in world space under identity placements, so if this is
# wrong the ground is found at height 0 under x, z of --at while the mesh is drawn at --at.
PHYSICS_IN_OBJECT_SPACE = True
GRID_CELL = 4.0
DEFAULT_SIZE = 64.0
DEFAULT_AT = (0.0, 300.0, 0.0)   # clear of bam's ground (about 191 to 200 m) when bam's world stays
# Which of bam's bundles the level's TOC lists.
BAM_WORLD = ("win32/levels/game/bam_levelroot/wproot", "win32/levels/game/bam_levelroot/wpvista")
BAM_333 = tuple("win32/levels/game/bam_levelroot/" + p for p in
                ("bam_coregameassets", "startpoint_sublevels", "vfx_global", "vfx_season_04", "lighting_", "tod_"))
BAM_MODES = {
    "all": lambda name: True,                                    # as new-level: everything but bam's root
    "no-world": lambda name: not name.startswith(BAM_WORLD),     # bam's world-partition tiles left out
    "333": lambda name: name.startswith(BAM_333),                # the bam bundles the 333 map carries
}


def resource_id(name: str) -> int:
    return int.from_bytes(hashlib.md5(("resource|" + name.lower()).encode()).digest()[:8], "little")


class _Source:
    """One base bundle read for copying: its manifest, region and EBX by file GUID."""

    def __init__(self, game, superbundle: str, bundle: str):
        self.game, self.superbundle = game, superbundle
        self.toc_bundle, self.region, self.manifest = game.bundle(game.superbundle(superbundle), bundle)
        if self.region.inline_manifest:
            raise NotImplementedError(f"{bundle} has an inline manifest; not supported yet")
        self.name = self.toc_bundle.name
        self._payloads: dict[int, bytes] = {}
        self._guids: dict[Guid, str] | None = None

    def file_index(self, kind: str, index: int) -> int:
        offset = 1 if kind == EBX else 1 + len(self.manifest.ebx)
        if kind == CHUNK:
            offset += len(self.manifest.resources)
        return offset + index

    def payload(self, kind: str, index: int) -> bytes:
        at = self.file_index(kind, index)
        if at not in self._payloads:
            self._payloads[at] = self.game.payload(self.region, at)
        return self._payloads[at]

    def ebx(self, name: str) -> bytes:
        i = next(i for i, a in enumerate(self.manifest.ebx) if a.name.lower() == name.lower())
        return self.payload(EBX, i)

    def guids(self) -> dict[Guid, str]:
        if self._guids is None:
            self._guids = {}
            for i, asset in enumerate(self.manifest.ebx):
                try:
                    self._guids.setdefault(read_file_guid(self.payload(EBX, i)), asset.name)
                except (ValueError, struct.error):
                    pass
        return self._guids

    def resources_named(self, prefix: str, resource_type: int | None = None, exact: bool = False):
        key = prefix.lower()
        return [(i, r) for i, r in enumerate(self.manifest.resources)
                if (r.name.lower() == key if exact else r.name.lower().startswith(key))
                and (resource_type is None or r.resource_type == resource_type)]


def _meshset_summary(data: bytes) -> tuple[str, list[Guid]]:
    """Box, LOD count and each LOD's geometry chunk GUID of a rigid MeshSet (FORMAT-NOTES 1)."""
    try:
        lo = struct.unpack_from("<3f", data, 0x10)
        hi = struct.unpack_from("<3f", data, 0x20)
        (lods,) = struct.unpack_from("<H", data, 180)
        chunks = []
        for k in range(min(lods, 7)):
            (pointer,) = struct.unpack_from("<Q", data, 0x30 + 8 * k)
            chunks.append(Guid(data[pointer + 16 + 116:pointer + 16 + 132]))
        size = [round(h - lo_, 2) for lo_, h in zip(lo, hi)]
        return (f"box {[round(v, 2) for v in lo]}..{[round(v, 2) for v in hi]} (size {size}), {lods} LODs"), chunks
    except (struct.error, ValueError):
        return "not readable as a rigid MeshSet", []


def _resource_refs(value) -> list[tuple[Object, str, ResourceReference]]:
    out = []
    if isinstance(value, Object):
        for f in value.fields:
            if isinstance(f.value, ResourceReference):
                out.append((value, f.name, f.value))
            else:
                out += _resource_refs(f.value)
    elif isinstance(value, list):
        for v in value:
            out += _resource_refs(v)
    return out


def _checked_bytes(doc: Document, label: str) -> bytes:
    """The document written, refused unless it reads back to the same values and rewrites the same."""
    data = write_document(doc, refuse_new_array_hashes=True)
    if not semantically_equal(data) or write_document(read_document(data), refuse_new_array_hashes=True) != data:
        raise ValueError(f"{label}: the written document does not read back to itself; not written")
    return data


def _placements(tile: Document, source: _Source, log: list[str]) -> list[dict]:
    """Every ObjectReferenceObjectData instance of the tile, with what it places."""
    out = []
    guids = source.guids()
    seen: dict[str, tuple] = {}
    for found in find_objects(tile, PLACEMENT_TYPE):
        if found.instance < 0:
            continue
        row = {"instance": found.instance, "usable": False}
        pointer = found.object.get(PLACEMENT_BLUEPRINT)
        text = f"  placement #{found.instance}: "
        if not isinstance(pointer, PointerReference) or pointer.kind != PointerKind.external:
            out.append(row)
            log.append(text + f"{PLACEMENT_BLUEPRINT} is not an import; skipped")
            continue
        imp = tile.imports[pointer.index]
        name = guids.get(imp.file_guid)
        row.update(import_=imp, blueprint=name)
        if name is None:
            out.append(row)
            log.append(text + f"blueprint {imp.file_guid.string()} is not in {source.name}; skipped")
            continue
        if name not in seen:
            try:
                bp = read_document(source.ebx(name))
            except ValueError as e:
                out.append(row)
                log.append(text + f"{name} does not read ({e}); skipped")
                continue
            physics = sorted(source.resources_named(name, PHYSICS_TYPE), key=lambda ir: len(ir[1].name))
            meshes = []
            for imp2 in used_imports(bp):
                mesh = guids.get(imp2.file_guid)
                for i, r in source.resources_named(mesh or "\0", MESHSET_TYPE, exact=True):
                    meshes.append(f"{mesh} ({_meshset_summary(source.payload('res', i))[0]})")
            seen[name] = (bp.root_type, physics, meshes)
        root_type, physics, meshes = seen[name]
        row.update(physics=physics, root_type=root_type, usable=root_type == BLUEPRINT_TYPE and bool(physics))
        log.append(text + f"{name} ({root_type}), physics {[r.name for _, r in physics] or 'NONE'}, "
                   f"meshes {meshes or 'none found in the bundle'}" + ("" if row["usable"] else "; not usable"))
        out.append(row)
    return out


def new_ground_level(game, mod, level: str, display_name: str | None = None, size: float = DEFAULT_SIZE,
                     at: tuple[float, float, float] = DEFAULT_AT, bam: str = "all", tile_template: str = TILE_TEMPLATE,
                     placement: int | None = None, log: list[str] | None = None) -> list[str]:
    """Adds a level whose content is our own ground to `mod` (see the module's text). Appends a
    report to `log` (also when it refuses) and returns it."""
    log = [] if log is None else log
    if not LEVEL_NAME.fullmatch(level):
        raise ValueError("level names use lower-case letters, digits and _, starting with a letter")
    if bam not in BAM_MODES:
        raise ValueError(f"--bam is one of {', '.join(BAM_MODES)}")
    if not 0 < size <= 1024:
        raise ValueError("--size is between 0 and 1024 metres")
    path = f"levels/game/{level}/{level}"
    sublevel_name = GROUND_SUBLEVEL.format(level=level)
    ground_bundle = "win32/" + sublevel_name
    bp_name = GROUND_BLUEPRINT.format(level=level)
    log.append(f"Ground for {path}: {size} m square at {list(at)}, bam bundles: {bam}")

    # 1. The template tile and the placement it lends.
    toc = game.superbundle(BASE_SUPERBUNDLE)
    bundle = "win32/" + tile_template.lower()
    if not any(b.name.lower() == bundle for b in toc.bundles):
        bundle = game.find_ebx(BASE_SUPERBUNDLE, tile_template)
        if bundle is None:
            raise KeyError(f"{tile_template} is in no bundle of {BASE_SUPERBUNDLE}")
    source = _Source(game, BASE_SUPERBUNDLE, bundle)
    log.append(f"Template tile {tile_template} in {source.name}: {len(source.manifest.ebx)} EBX, "
               f"{len(source.manifest.resources)} res, {len(source.manifest.chunks)} chunks, chunk metadata "
               f"{len(source.manifest.chunk_metadata)} bytes")
    tile = _read(source.ebx(tile_template), tile_template, log)
    log.append(f"  instance types: {dict(Counter(type_name(tile, i.object) for i in tile.instances if i.object))}")
    rows = _placements(tile, source, log)
    if placement is None:
        chosen = next((r for r in rows if r["usable"]), None)
        if chosen is None:
            raise KeyError(f"no {PLACEMENT_TYPE} of {tile_template} places an {BLUEPRINT_TYPE} of its own bundle with "
                           f"a physics resource named after it; try another --tile-template")
    else:
        chosen = next((r for r in rows if r["instance"] == placement), None)
        if chosen is None or not chosen["usable"]:
            raise KeyError(f"placement #{placement} is not a usable {PLACEMENT_TYPE} (see the list)")
    template_bp = chosen["blueprint"]
    log.append(f"  uses placement #{chosen['instance']}: {template_bp}")

    # 2. The blueprint: a copy under our name, its collision reference moved to our resource.
    bp_old = _read(source.ebx(template_bp), template_bp, log)
    bp = clone_document(bp_old, derived_guid(level, "ground_bp", bp_old.file_guid.string()), level + ":ground_bp")
    flags = refresh_flags(bp_old, bp)
    if isinstance(bp.instances[0].object.get("Name"), str):
        bp.instances[0].object.set("Name", bp_name)
    physics_index, physics_asset = chosen["physics"][0]
    physics_name = bp_name + physics_asset.name[len(template_bp):]
    physics_id = resource_id(physics_name)
    refs = _resource_refs([i.object for i in bp.instances if i.object is not None])
    moved = 0
    for holder, field_name, ref in refs:
        if ref.id == physics_asset.resource_id:
            holder.set(field_name, ResourceReference(physics_id))
            moved += 1
    log.append(f"  new {bp_name}: file guid {bp.file_guid.string()}, {flags} Flags renewed; physics "
               f"{physics_asset.name} (id {physics_asset.resource_id:#x}) -> {physics_name} (id {physics_id:#x}); "
               f"{moved} of {len(refs)} resource references named its id"
               + ("" if moved else " (so the link is by name, GUESS)"))

    # 3. Our collision, with the template part's material and words.
    shapes, parts, version = read_physics(source.payload("res", physics_index))
    template_part = next((p for p in parts if p.shape >= 0 and p.kind == KIND_TRIANGLE_MESH),
                         next((p for p in parts if p.shape >= 0), parts[0] if parts else Part(0)))
    grid = flat_grid(size, GRID_CELL)
    part = Part(0, (0.0, 0.0, 0.0) if PHYSICS_IN_OBJECT_SPACE else tuple(at), (0.0, 0.0, 0.0, 1.0), 1.0,
                template_part.material, KIND_TRIANGLE_MESH, template_part.hashes, 0, template_part.float_30, 0,
                template_part.word_3c, template_part.word_44)
    compute_edge_data([grid], [part])
    physics = write_physics([grid], [part], 2)
    back_shapes, back_parts, back_version = read_physics(physics)
    if back_version != 2 or len(back_shapes) != 1 or back_shapes[0].triangles != grid.triangles:
        raise ValueError("the collision written does not read back")
    log.append(f"  collision: template is version {version} with {len(shapes)} shapes, {len(parts)} parts (material "
               f"{template_part.material:#010x}, kind {template_part.kind}); ours: version 2, one part, "
               f"{len(grid.vertices)} vertices, {len(grid.triangles)} triangles, {len(physics)} bytes, "
               f"{'object space' if PHYSICS_IN_OBJECT_SPACE else 'world space'}")

    # 4. The sublevel: the tile cut down to the chain leading to the placement.
    sub = clone_document(tile, derived_guid(level, "ground_sublevel", tile.file_guid.string()), level + ":ground_sublevel")
    refresh_flags(tile, sub)
    target = sub.instances[chosen["instance"]].object
    chain = path_to(sub, chosen["instance"])
    if chain is None:
        raise ValueError(f"placement #{chosen['instance']} is not reached from the tile's root")
    for holder, array, i in chain:
        if array is not None:
            array[:] = [array[i]]
    log.append("  chain from the root: " + " -> ".join(
        f"#{h} {type_name(sub, sub.instances[h].object)}" + ("[1 kept]" if a is not None else "") for h, a, _ in chain)
        + f" -> {PLACEMENT_TYPE}")
    removed = prune_unreachable(sub)
    placed = next(i for i, r in enumerate(sub.instances) if r.object is target)
    transform = target.get(PLACEMENT_TRANSFORM)
    if not isinstance(transform, Object) or not _identity(transform):
        raise ValueError(f"{PLACEMENT_TYPE}.{PLACEMENT_TRANSFORM} is not a LinearTransform")
    for axis, value in zip("xyz", at):
        transform.get("trans").set(axis, float(value))
    old_import = chosen["import_"]
    k = next((k for k, r in enumerate(bp_old.instances) if r.exported and r.instance_guid == old_import.class_guid), None)
    instance_guid = bp.instances[k if k is not None else 0].instance_guid
    target.set(PLACEMENT_BLUEPRINT, add_import(sub, bp.file_guid, instance_guid))
    dropped = prune_imports(sub)
    if isinstance(sub.instances[0].object.get("Name"), str):
        sub.instances[0].object.set("Name", sublevel_name)
    log.append(f"  new {sublevel_name}: file guid {sub.file_guid.string()}, {len(sub.instances)} instances kept "
               f"({dict(Counter(type_name(sub, i.object) for i in sub.instances if i.object))}), {len(removed)} removed, "
               f"placement #{placed} at {list(at)} -> {bp_name} instance {instance_guid.string()}"
               + (" (the import named no exported instance of the blueprint: its root's used)" if k is None else "")
               + f", {'imports kept (boxed values)' if dropped is None else f'{dropped} unused imports dropped'}, "
               f"{len(sub.imports)} left")

    # 5. What the blueprint and the sublevel need from the tile's bundle, listed as it is.
    copied = {EBX: [], "res": [], CHUNK: []}
    if source.name.lower() == BASE_ROOT_BUNDLE:
        log.append("  the template is in bam's root bundle, which the level's root bundle copies: nothing more listed")
    else:
        guids = source.guids()
        wanted = [imp.file_guid for d in (sub, bp) for imp in used_imports(d) if imp.file_guid != bp.file_guid]
        names, outside = [], set()
        while wanted:
            g = wanted.pop()
            name = guids.get(g)
            if name is None:
                outside.add(g)
                continue
            if name in names or name.lower() in (tile_template.lower(), template_bp.lower()):
                continue
            names.append(name)
            try:
                wanted += [imp.file_guid for imp in read_document(source.ebx(name)).imports]
            except ValueError as e:
                log.append(f"  {name} does not read ({e}); its own imports are not followed")
        lower = {n.lower() for n in names}
        copied[EBX] = [i for i, a in enumerate(source.manifest.ebx) if a.name.lower() in lower]
        copied["res"] = [i for i, r in enumerate(source.manifest.resources) if r.name.lower() in lower]
        payloads = [source.payload("res", i) for i in copied["res"]]
        copied[CHUNK] = [i for i, c in enumerate(source.manifest.chunks) if any(c.guid.bytes in p for p in payloads)]
        log.append(f"  listed from {source.name}: {len(copied[EBX])} EBX {sorted(names)}, "
                   f"{len(copied['res'])} res, {len(copied[CHUNK])} chunks; {len(outside)} imported files are not in "
                   f"that bundle and must come from another bundle the level lists")
        for i in copied["res"]:
            r = source.manifest.resources[i]
            if r.resource_type == MESHSET_TYPE:
                text, chunk_guids = _meshset_summary(source.payload("res", i))
                held = {c.guid for c in source.manifest.chunks}
                carried = {c.guid for c in toc.chunks}
                log.append(f"    MeshSet {r.name}: {text}; LOD chunks " + ", ".join(
                    f"{g.string()} (" + ("in the bundle" if g in held else "in bam's TOC chunks, carried" if g in carried
                                         else "NOT in the bundle or bam's TOC chunks") + ")" for g in chunk_guids))

    # 6. The level, registered as new-level does, its root loading the ground.
    def edit_root(root: Document, listed: set[str], lines: list[str]) -> None:
        listed = listed | {ground_bundle}
        references = find_objects(root, SUBWORLD_REFERENCE_TYPE)
        unresolved = []
        for f in references:
            name = str(f.object.get("BundleName") or "")
            ok = "win32/" + name.lower() in listed
            lines.append(f"  root {SUBWORLD_REFERENCE_TYPE} {f.path}: {name!r}, auto-load {f.object.get('AutoLoad')}, "
                         f"detached {f.object.get('IsDetachedSubLevel')}" + ("" if ok else ", NOT in the level's TOC"))
            if not ok:
                unresolved.append(f)
        if unresolved and bam != "all":
            for f in unresolved:
                if f.instance < 0:
                    f.array[:] = [v for v in f.array if v is not f.object]
            taken = remove_instances(root, {f.instance for f in unresolved if f.instance >= 0})
            lines.append(f"  removed {len(unresolved)} references to bundles the TOC does not list"
                         + (f" (and {len(taken)} pointers to them: {taken})" if taken else ""))
        references = find_objects(root, SUBWORLD_REFERENCE_TYPE)
        if not references:
            raise KeyError(f"the root has no {SUBWORLD_REFERENCE_TYPE} to copy for the ground")
        references.sort(key=lambda f: (f.object.get("IsDetachedSubLevel") is True, f.object.get("AutoLoad") is not True))
        template = references[0]
        reference = _clone_found(root, template, level + ":ground_reference")
        changes = dict(GROUND_REFERENCE, BundleName=sublevel_name)
        blueprint = reference.get("Blueprint")
        if isinstance(blueprint, PointerReference) and blueprint.kind != PointerKind.null:
            changes["Blueprint"] = (add_import(root, sub.file_guid, sub.instances[0].instance_guid)
                                    if blueprint.kind == PointerKind.external else PointerReference())
        if isinstance(reference.get("Template"), PointerReference):
            changes["Template"] = PointerReference()
        transform = reference.get("BlueprintTransform")
        if isinstance(transform, Object) and not _identity(transform):
            lines.append("  WARNING: the reference's BlueprintTransform is not a LinearTransform; left as copied")
        index = next((i for i, r in enumerate(root.instances) if r.object is reference), -1)
        if index >= 0 and root.instances[index].exported and isinstance(reference.get("Flags"), int):
            changes["Flags"] = guid_flags(root.instances[index].instance_guid, reference.get("Flags"))
        missing = set_fields(reference, changes)
        lines.append(f"  adds {SUBWORLD_REFERENCE_TYPE} {sublevel_name!r} (copy of {template.path}) as "
                     + (f"instance #{index}" if index >= 0 else "a struct") + f", auto-load "
                     f"{reference.get('AutoLoad')}, Blueprint {'-> the sublevel' if 'Blueprint' in changes else 'none'}"
                     + (f", MISSING fields {missing}" if missing else ""))

    log.extend(new_level(game, mod, level, display_name, keep_bundle=BAM_MODES[bam], edit_root=edit_root))

    # 7. The ground bundle: our sublevel, blueprint and collision (GUESS: in that order, before
    # the copied entries), then the copied entries in their bundle's order.
    chunk = game.chunk_index(nativedb.DEFAULT_INSTALL_CHUNK)
    mod.new_bundle(path, ground_bundle, chunk)
    mod.add_ebx(path, ground_bundle, sublevel_name, _checked_bytes(sub, sublevel_name))
    mod.add_ebx(path, ground_bundle, bp_name, _checked_bytes(bp, bp_name))
    mod.add_resource(path, ground_bundle, physics_name, physics_asset.resource_type, physics, physics_id,
                     physics_asset.resource_meta)
    for kind, assets in ((EBX, source.manifest.ebx), ("res", source.manifest.resources), (CHUNK, source.manifest.chunks)):
        for i in copied[kind]:
            mod.add_copied(path, ground_bundle, assets[i], source.region.files[source.file_index(kind, i)])
    log.append(f"{ground_bundle}: 2 new EBX, 1 new res, {sum(len(v) for v in copied.values())} entries listed from "
               f"{source.name}; new payloads in install chunk {chunk:#x}")
    return log
