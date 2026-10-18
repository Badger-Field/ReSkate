"""A level with its own ground (FORMAT-NOTES 6.3).

The level is registered exactly as `new-level` registers its clone of bam (level.py), and gets one
more bundle, `win32/levels/game/<level>/ground_sublevel`, holding a copy of the chain the base game
itself uses to place objects in a world-partition tile (found on the PC in 127 of bam's tiles):

    tile (QuadrantSubWorldData) -> LayerReferenceObjectData -> layer (LayerData)
        -> ObjectReferenceObjectData (OROD) -> ObjectBlueprint -> mesh, and <blueprint>.physics

- `levels/game/<level>/ground_sublevel`: bam's tile `TILE_TEMPLATE` cut down to the chain from its
  root to its LayerReferenceObjectData (its StaticModelGroup and audio objects dropped), the
  reference pointing at our layer;
- `levels/game/<level>/ground_layer`: the tile's layer (or `--layer-template`) cut down to one
  OROD, placed at `--at` with identity rotation and scale 1 and pointing at our blueprint; with
  `--tiles N`, N x N copies of it `--size` metres apart;
- `levels/game/<level>/ground_bp`: a copy of `BLUEPRINT_TEMPLATE` (util_groundplane, whose mesh is
  a flat 50 x 50 m plane), renamed; its mesh import is kept, so the base mesh is drawn as it is
  (it is in bam's root bundle, which the level's root bundle copies);
- `levels/game/<level>/ground_bp.physics`: our collision, a version-2 flat grid of `--size` metres.
  The game finds a blueprint's collision by name: the resource `<blueprint>.physics` in the
  blueprint's bundle (9,661 of 10,883 base blueprints), so it sits beside our blueprint.

The level's root (a clone of bam's) gets a `SubWorldReferenceObjectData` naming the new bundle.
With `--bam no-world` the level's TOC leaves out bam's world-partition tiles (and the root loses
the references to them); with `--bam 333` it lists only the 42 bam bundles the 333 map carries.

Everything marked GUESS below comes from names or analogy, not from the game's files;
`new-ground-level --dry-run` prints what it found for each, read-only.
"""
from __future__ import annotations

import copy
import hashlib
import struct
from collections import Counter

from . import nativedb
from .bundle import CHUNK, EBX, Guid
from .cas import decode_cas
from .ebx import Document, Object, PointerKind, PointerReference, ResourceReference, read_document, read_file_guid, write_document
from .ebxcheck import semantically_equal
from .ebxedit import (add_import, after_last_of_type, clone_document, clone_instance, derived_guid, find_objects, guid_flags,
                      field_referrers, path_to, pointer_arrays, prune_imports, prune_unreachable, refresh_flags, remove_instances,
                      set_fields, type_name, used_imports)
from .meshset import describe_meshset, read_layout as read_mesh_layout, write_mesh
from .level import (BASE_ROOT_BUNDLE, BASE_SUPERBUNDLE, LEVEL_NAME, SUBWORLD_REFERENCE_TYPE, _clone_found, _identity, _read,
                    new_level)
from .physics import KIND_TRIANGLE_MESH, RESOURCE_TYPE as PHYSICS_TYPE, Part, compute_edge_data, flat_grid, read_physics, write_physics

# Names of what the level adds (EBX names; the bundle is "win32/" + the sublevel's name).
GROUND_SUBLEVEL = "levels/game/{level}/ground_sublevel"
GROUND_LAYER = "levels/game/{level}/ground_layer"
GROUND_BLUEPRINT = "levels/game/{level}/ground_bp"
GROUND_MESH = "levels/game/{level}/ground_mesh"     # with --mesh own: the RigidMeshAsset and its MeshSet
# --visual template: the layer's own placement (of its own blueprint) is kept beside ours, moved
# by this much from --at, to tell whether anything our sublevel places is drawn.
VISUAL_MODES = ("none", "template")
VISUAL_OFFSET = (10.0, 0.0, 10.0)
# --mesh own: the blueprint's mesh is replaced by a MeshSet of our own (meshset.write_mesh).
MESH_MODES = ("template", "own")
# Templates, all in bam's superbundle (found by the PC's scan of the base game, FORMAT-NOTES 6.3).
TILE_TEMPLATE = ("levels/game/bam_levelroot/wproot/wp/s9600_x0z0/s200_x500z900/s100_x450z850/s50_x425z825/"
                 "s50_x425z825_sublevel")
# The layer: by default the one the tile's LayerReferenceObjectData imports, from the tile's
# bundle. bam's coregameassets hold another (levels/game/bam_levelroot/game_coremeshes).
LAYER_TEMPLATE = None
BLUEPRINT_TEMPLATE = "common/utilities/meshes/util_groundplane"
BLUEPRINT_BUNDLE = BASE_ROOT_BUNDLE
LAYER_REFERENCE_TYPE = "LayerReferenceObjectData"
PLACEMENT_TYPE = "ObjectReferenceObjectData"
PLACEMENT_TRANSFORM = "BlueprintTransform"
PLACEMENT_BLUEPRINT = "Blueprint"
BLUEPRINT_TYPE = "ObjectBlueprint"
MESHSET_TYPE = 0x49B156D4
PHYSICS_SUFFIX = ".physics"     # a blueprint's collision is the resource <blueprint name>.physics in its bundle
# GUESS: a new resource's id is free to choose (derived from its name, see resource_id).
# GUESS: the reference that loads the ground: copied from one of the root's own sub-world
# references (preferring a non-detached one that auto-loads), set to auto-load. bam's tiles are
# loaded by its world partition instead; ours is loaded as a plain sub-world.
GROUND_REFERENCE = {"AutoLoad": True, "IsDetachedSubLevel": False}
# GUESS: the collision is in the blueprint's own frame (the placement's transform moves it, as it
# moves the mesh). The 333 map bakes both in world space under identity placements, so if this is
# wrong the ground is found at height 0 under x, z of --at while the mesh is drawn at --at.
PHYSICS_IN_OBJECT_SPACE = True
GRID_CELL = 5.0
DEFAULT_SIZE = 50.0              # util_groundplane_mesh's plane is 50 x 50 m
DEFAULT_AT = (0.0, 300.0, 0.0)   # clear of bam's ground (about 191 to 200 m) when bam's world stays
MAX_TILES = 16
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

    def toc_chunks(self) -> dict:
        """GUID -> TocChunk of the superbundle's own chunk list."""
        if getattr(self, "_toc_chunks", None) is None:
            self._toc_chunks = {c.guid: c for c in self.game.superbundle(self.superbundle).chunks}
        return self._toc_chunks

    def where_chunk(self, guid: Guid) -> str:
        places = []
        if any(c.guid == guid for c in self.manifest.chunks):
            places.append(f"{self.name}'s chunk entries")
        if guid in self.toc_chunks():
            places.append(f"{self.superbundle}'s TOC chunk list")
        return " and ".join(places) or f"NEITHER {self.name} nor {self.superbundle}'s TOC"

    def has_ebx(self, name: str) -> bool:
        return any(a.name.lower() == name.lower() for a in self.manifest.ebx)

    def ebx(self, name: str) -> bytes:
        i = next((i for i, a in enumerate(self.manifest.ebx) if a.name.lower() == name.lower()), None)
        if i is None:
            raise KeyError(f"{name} is not in {self.name}")
        return self.payload(EBX, i)

    def resource(self, name: str, resource_type: int | None = None):
        """(index, asset) of the resource of that name (and type), or None."""
        return next(((i, r) for i, r in enumerate(self.manifest.resources) if r.name.lower() == name.lower()
                     and (resource_type is None or r.resource_type == resource_type)), None)

    def guids(self) -> dict[Guid, str]:
        """File GUID -> EBX name, reading every EBX of the bundle once."""
        if self._guids is None:
            self._guids = {}
            for i, asset in enumerate(self.manifest.ebx):
                try:
                    self._guids.setdefault(read_file_guid(self.payload(EBX, i)), asset.name)
                except (ValueError, struct.error, IndexError):
                    pass
        return self._guids


def _source(game, bundle: str | None, ebx: str, superbundle: str = BASE_SUPERBUNDLE) -> _Source:
    """The bundle of a superbundle (bam's by default) holding `ebx`: the one given, the one named
    like it, or a search."""
    toc = game.superbundle(superbundle)
    names = {b.name.lower() for b in toc.bundles}
    if bundle is None:
        bundle = "win32/" + ebx.lower() if "win32/" + ebx.lower() in names else game.find_ebx(superbundle, ebx)
        if bundle is None:
            raise KeyError(f"{ebx} is in no bundle of {superbundle}")
    return _Source(game, superbundle, bundle)


# Field names worth printing on the blueprint's instances when looking for why it is not drawn.
FLAG_WORDS = ("visible", "enable", "render", "debug", "hidden", "editor", "draw", "shadow", "lod", "cull")


def _flag_fields(doc: Document) -> list[str]:
    """Per instance: its bool fields, and any field whose name suggests visibility or debugging."""
    out = []

    def walk(value, path, found):
        if isinstance(value, Object):
            for f in value.fields:
                if isinstance(f.value, bool) or (any(w in f.name.lower() for w in FLAG_WORDS)
                                                 and not isinstance(f.value, (Object, list))):
                    found.append(f"{path}{f.name}={f.value!r}")
                if isinstance(f.value, (Object, list)):
                    walk(f.value, f"{path}{f.name}.", found)
        elif isinstance(value, list):
            for i, v in enumerate(value[:8]):
                walk(v, f"{path[:-1]}[{i}].", found)

    for i, instance in enumerate(doc.instances):
        found: list[str] = []
        walk(instance.object, "", found)
        out.append(f"#{i} {type_name(doc, instance.object)}: " + (", ".join(found) if found else "no such fields"))
    return out


def _guids_in(data: bytes, candidates: set[bytes]) -> set[bytes]:
    """The candidate 16-byte GUIDs that occur anywhere in `data`."""
    if not candidates:
        return set()
    return {data[i:i + 16] for i in range(len(data) - 15) if data[i:i + 16] in candidates}


def _closure(source: _Source, roots: list[Guid], skip: set[str], log: list[str]) -> dict[str, list]:
    """What `roots` import from `source`'s bundle, followed through imports: the EBX, the resources
    named like them, and the chunks whose GUID appears in those resources' bytes."""
    guids = source.guids()
    names, outside, wanted = [], set(), list(roots)
    while wanted:
        g = wanted.pop()
        name = guids.get(g)
        if name is None:
            outside.add(g)
            continue
        if name in names or name.lower() in skip:
            continue
        names.append(name)
        try:
            wanted += [imp.file_guid for imp in read_document(source.ebx(name)).imports]
        except ValueError as e:
            log.append(f"  {name} does not read ({e}); its own imports are not followed")
    lower = {n.lower() for n in names}
    copied = {EBX: [i for i, a in enumerate(source.manifest.ebx) if a.name.lower() in lower]}
    copied["res"] = [i for i, r in enumerate(source.manifest.resources) if r.name.lower() in lower]
    payloads = [source.payload("res", i) for i in copied["res"]]
    bundle_guids = {c.guid.bytes for c in source.manifest.chunks}
    toc = source.toc_chunks()
    found: set[bytes] = set()
    for data in payloads:
        found |= _guids_in(data, bundle_guids | {g.bytes for g in toc})
    copied[CHUNK] = [i for i, c in enumerate(source.manifest.chunks) if c.guid.bytes in found]
    # Chunks the superbundle's TOC lists are listed in the level's TOC too (as the 333 map's own
    # TOC lists 80,006): the source superbundle's TOC is not read when the level loads.
    copied["toc"] = [c for g, c in toc.items() if g.bytes in found]
    log.append(f"  listed from {source.name} in the ground bundle: {len(copied[EBX])} EBX {sorted(names)}, "
               f"{len(copied['res'])} res, {len(copied[CHUNK])} chunks (and {len(copied['toc'])} chunks of "
               f"{source.superbundle}'s TOC for the level's TOC); {len(outside)} imported files are not in that "
               f"bundle and must come from another bundle the level loads")
    return copied


def _chunk_payload(game, source: _Source, guid: Guid) -> bytes:
    """A chunk's decoded bytes: from the bundle's manifest, or from its superbundle's TOC chunks."""
    for i, c in enumerate(source.manifest.chunks):
        if c.guid == guid:
            if c.logical_offset:
                raise ValueError(f"chunk {guid.string()} has logicalOffset {c.logical_offset}; not handled")
            data = source.payload(CHUNK, i)
            return data[:c.logical_size] if c.logical_size else data
    for c in game.superbundle(source.superbundle).chunks:
        if c.guid == guid:
            return decode_cas(game.read(c.location, c.offset, c.size), game.root)
    raise KeyError(f"chunk {guid.string()} is neither in {source.name} nor in {source.superbundle}'s TOC chunks")


def _meshset_summary(data: bytes) -> str:
    """Box and LOD count of a rigid MeshSet (FORMAT-NOTES 1)."""
    try:
        lo = struct.unpack_from("<3f", data, 0x10)
        hi = struct.unpack_from("<3f", data, 0x20)
        (lods,) = struct.unpack_from("<H", data, 180)
        size = [round(h - lo_, 2) for lo_, h in zip(lo, hi)]
        return f"box {[round(v, 2) for v in lo]}..{[round(v, 2) for v in hi]} (size {size}), {lods} LODs"
    except struct.error:
        return "not readable as a rigid MeshSet"


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


def _import_target(doc: Document, pointer, label: str):
    if not isinstance(pointer, PointerReference) or pointer.kind != PointerKind.external:
        raise ValueError(f"{label} is not an import")
    return doc.imports[pointer.index]


def _cut_to(doc: Document, index: int, label: str, log: list[str]) -> list[int]:
    """Cuts every pointer array on the chain from the root to instance `index` to that one element
    and removes what the root no longer reaches. Returns the removed (old) indices."""
    chain = path_to(doc, index)
    if chain is None:
        raise ValueError(f"{label}: #{index} is not reached from the root")
    for _, array, i in chain:
        if array is not None:
            array[:] = [array[i]]
    log.append(f"  {label} chain: " + " -> ".join(
        f"#{h} {type_name(doc, doc.instances[h].object)}" + ("[1 kept]" if a is not None else "") for h, a, _ in chain)
        + f" -> #{index} {type_name(doc, doc.instances[index].object)}")
    return prune_unreachable(doc)


def _new_guid_of(old_doc: Document, new_doc: Document, instance_guid: Guid) -> tuple[Guid, bool]:
    """The clone's GUID for the exported instance of `old_doc` with this GUID (the root's if none)."""
    k = next((k for k, r in enumerate(old_doc.instances) if r.exported and r.instance_guid == instance_guid), None)
    return new_doc.instances[k if k is not None else 0].instance_guid, k is not None


def _place(placement: Object, at: tuple[float, float, float]) -> None:
    transform = placement.get(PLACEMENT_TRANSFORM)
    if not isinstance(transform, Object) or not _identity(transform):
        raise ValueError(f"{PLACEMENT_TYPE}.{PLACEMENT_TRANSFORM} is not a LinearTransform")
    for axis, value in zip("xyz", at):
        transform.get("trans").set(axis, float(value))


def new_ground_level(game, mod, level: str, display_name: str | None = None, size: float = DEFAULT_SIZE,
                     at: tuple[float, float, float] = DEFAULT_AT, bam: str = "all", tile_template: str = TILE_TEMPLATE,
                     layer_template: str | None = LAYER_TEMPLATE, layer_bundle: str | None = None,
                     blueprint_template: str = BLUEPRINT_TEMPLATE, blueprint_bundle: str | None = BLUEPRINT_BUNDLE,
                     tiles: int = 1, log: list[str] | None = None, blueprint_superbundle: str = BASE_SUPERBUNDLE,
                     visual: str = "none", mesh: str = "template") -> list[str]:
    """Adds a level whose content is our own ground to `mod` (see the module's text). Appends a
    report to `log` (also when it refuses) and returns it."""
    log = [] if log is None else log
    if not LEVEL_NAME.fullmatch(level):
        raise ValueError("level names use lower-case letters, digits and _, starting with a letter")
    if bam not in BAM_MODES:
        raise ValueError(f"--bam is one of {', '.join(BAM_MODES)}")
    if not 0 < size <= 1024:
        raise ValueError("--size is between 0 and 1024 metres")
    if not 1 <= tiles <= MAX_TILES:
        raise ValueError(f"--tiles is between 1 and {MAX_TILES}")
    if visual not in VISUAL_MODES or mesh not in MESH_MODES:
        raise ValueError(f"--visual is one of {VISUAL_MODES}, --mesh one of {MESH_MODES}")
    path = f"levels/game/{level}/{level}"
    sublevel_name = GROUND_SUBLEVEL.format(level=level)
    layer_name = GROUND_LAYER.format(level=level)
    bp_name = GROUND_BLUEPRINT.format(level=level)
    physics_name = bp_name + PHYSICS_SUFFIX
    mesh_name = GROUND_MESH.format(level=level)
    ground_bundle = "win32/" + sublevel_name
    log.append(f"Ground for {path}: {tiles} x {tiles} placements of {size} m at {list(at)}, bam bundles: {bam}")

    # 1. The tile, and the layer its LayerReferenceObjectData imports.
    tile_source = _source(game, None, tile_template)
    log.append(f"Tile {tile_template} in {tile_source.name} ({len(tile_source.manifest.ebx)} EBX, "
               f"{len(tile_source.manifest.resources)} res)")
    tile = _read(tile_source.ebx(tile_template), tile_template, log)
    log.append(f"  instance types: {dict(Counter(type_name(tile, i.object) for i in tile.instances if i.object))}")
    references = [f for f in find_objects(tile, LAYER_REFERENCE_TYPE) if f.instance >= 0]
    if not references:
        raise KeyError(f"{tile_template} has no {LAYER_REFERENCE_TYPE}; try another --tile-template")
    layer_ref = references[0]
    layer_import = _import_target(tile, layer_ref.object.get(PLACEMENT_BLUEPRINT), f"{LAYER_REFERENCE_TYPE}.Blueprint")
    tile_layer = tile_source.guids().get(layer_import.file_guid)
    log.append(f"  {len(references)} {LAYER_REFERENCE_TYPE}; #{layer_ref.instance} imports "
               f"{tile_layer or layer_import.file_guid.string() + ' (not in the tile bundle)'}")

    layer_source = tile_source if layer_template is None and layer_bundle is None else None
    if layer_template is None:
        if tile_layer is None:
            raise KeyError("the tile's layer is not in its bundle; give --layer-template")
        layer_template = tile_layer
    if layer_source is None:
        layer_source = _source(game, layer_bundle, layer_template)
    log.append(f"Layer {layer_template} in {layer_source.name}")
    layer_old = _read(layer_source.ebx(layer_template), layer_template, log)
    placements = [f for f in find_objects(layer_old, PLACEMENT_TYPE) if f.instance >= 0]
    if not placements:
        raise KeyError(f"{layer_template} has no {PLACEMENT_TYPE}")
    for f in placements:
        try:
            imp = _import_target(layer_old, f.object.get(PLACEMENT_BLUEPRINT), "Blueprint")
            what = layer_source.guids().get(imp.file_guid) or imp.file_guid.string() + " (another bundle)"
        except ValueError as e:
            what = str(e)
        log.append(f"  {PLACEMENT_TYPE} #{f.instance} places {what}")
    placement_index = placements[0].instance

    # 2. The blueprint: a copy under our name; the mesh it imports is kept (or replaced, --mesh own).
    bp_source = _source(game, blueprint_bundle, blueprint_template, blueprint_superbundle)
    log.append(f"Blueprint {blueprint_template} in {blueprint_superbundle} / {bp_source.name}")
    bp_old = _read(bp_source.ebx(blueprint_template), blueprint_template, log)
    if bp_old.root_type != BLUEPRINT_TYPE:
        raise ValueError(f"{blueprint_template} is a {bp_old.root_type}, not an {BLUEPRINT_TYPE}")
    log.append(f"  instance types: {dict(Counter(type_name(bp_old, i.object) for i in bp_old.instances if i.object))}")
    log.extend("  flags " + line for line in _flag_fields(bp_old))
    found = bp_source.resource(blueprint_template + PHYSICS_SUFFIX, PHYSICS_TYPE)
    if found is None:
        raise KeyError(f"{bp_source.name} has no physics resource {blueprint_template + PHYSICS_SUFFIX}")
    physics_index, physics_asset = found
    log.append(f"  collision {physics_asset.name}: id {physics_asset.resource_id:#x}, meta {physics_asset.resource_meta.hex()}")
    guids = bp_source.guids()
    template_mesh = None      # (EBX name, import, MeshSet (index, asset))
    for imp in used_imports(bp_old):
        name = guids.get(imp.file_guid)
        found_ms = bp_source.resource(name, MESHSET_TYPE) if name else None
        log.append(f"  imports {name or imp.file_guid.string() + ' (another bundle)'}"
                   + (f": MeshSet {_meshset_summary(bp_source.payload('res', found_ms[0]))}, meta "
                      f"{found_ms[1].resource_meta.hex()}" if found_ms else ""))
        if found_ms and template_mesh is None:
            template_mesh = (name, imp, found_ms)
    bp = clone_document(bp_old, derived_guid(level, "ground_bp", bp_old.file_guid.string()), level + ":ground_bp")
    flags = refresh_flags(bp_old, bp)
    if isinstance(bp.instances[0].object.get("Name"), str):
        bp.instances[0].object.set("Name", bp_name)
    physics_id = resource_id(physics_name)
    refs = _resource_refs([i.object for i in bp.instances if i.object is not None])
    moved = 0
    for holder, field_name, ref in refs:
        if ref.id == physics_asset.resource_id:
            holder.set(field_name, ResourceReference(physics_id))
            moved += 1
    log.append(f"  new {bp_name}: file guid {bp.file_guid.string()}, {flags} Flags renewed; collision {physics_name} "
               f"(id {physics_id:#x}, matched by name); {len(refs)} resource references, {moved} named the template's id")

    # 2b. With --mesh own: a MeshSet of our own geometry (the collision's grid), patched into a
    # copy of the template's, a copy of its RigidMeshAsset naming it, and its geometry chunk.
    own = None
    if mesh == "own":
        if template_mesh is None:
            raise KeyError(f"{blueprint_template} imports no mesh whose MeshSet is in {bp_source.name}")
        template_mesh_name, template_import, (ms_index, ms_asset) = template_mesh
        mesh_old = _read(bp_source.ebx(template_mesh_name), template_mesh_name, log)
        ms_data = bp_source.payload("res", ms_index)
        layout = read_mesh_layout(ms_data)
        chunk_data = [_chunk_payload(game, bp_source, lod.chunk) for lod in layout]
        grid_mesh = flat_grid(size, GRID_CELL)
        chunk_guids = [derived_guid(level, "ground_mesh_chunk", *([str(k)] if k else [])) for k in range(len(layout))]
        new_ms, new_chunks, lines = write_mesh(ms_data, chunk_data, grid_mesh.vertices, grid_mesh.triangles, chunk_guids)
        mesh_new = clone_document(mesh_old, derived_guid(level, "ground_mesh", mesh_old.file_guid.string()),
                                  level + ":ground_mesh")
        refresh_flags(mesh_old, mesh_new)
        if isinstance(mesh_new.instances[0].object.get("Name"), str):
            mesh_new.instances[0].object.set("Name", mesh_name)
        meshset_id = resource_id(mesh_name)
        named = 0
        for holder, field_name, ref in _resource_refs([i.object for i in mesh_new.instances if i.object is not None]):
            if ref.id == ms_asset.resource_id:
                holder.set(field_name, ResourceReference(meshset_id))
                named += 1
        new_class, _ = _new_guid_of(mesh_old, mesh_new, template_import.class_guid)
        retargeted = 0
        for imp in bp.imports:
            if imp.file_guid == template_import.file_guid:
                imp.file_guid, imp.class_guid = mesh_new.file_guid, new_class
                retargeted += 1
        log.append(f"  --mesh own: template {template_mesh_name} (MeshSet id {ms_asset.resource_id:#x}, chunks "
                   + ", ".join(f"{lod.chunk.string()} ({len(c)} bytes, in {bp_source.where_chunk(lod.chunk)})"
                               for lod, c in zip(layout, chunk_data)) + ")")
        log.extend("    " + line for line in lines)
        log.append(f"    new {mesh_name}: file guid {mesh_new.file_guid.string()}, MeshSet {mesh_name} id {meshset_id:#x} "
                   f"({named} resource references named the template's id"
                   + ("" if named else "; GUESS: then it is found by name") + f"), chunks "
                   f"{[g.string() for g in chunk_guids]}; "
                   f"{retargeted} blueprint imports moved to it")
        for line in describe_meshset(new_ms)[:2]:
            log.append("    " + line)
        own = (mesh_new, new_ms, new_chunks, chunk_guids, ms_asset)

    # 3. Our collision, with the template part's material and words.
    shapes, parts, version = read_physics(bp_source.payload("res", physics_index))
    template_part = next((p for p in parts if p.shape >= 0 and p.kind == KIND_TRIANGLE_MESH),
                         next((p for p in parts if p.shape >= 0), parts[0] if parts else Part(0)))
    grid = flat_grid(size, GRID_CELL)
    part = Part(0, (0.0, 0.0, 0.0) if PHYSICS_IN_OBJECT_SPACE else tuple(at), (0.0, 0.0, 0.0, 1.0), 1.0,
                template_part.material, KIND_TRIANGLE_MESH, template_part.hashes, 0, template_part.float_30, 0,
                template_part.word_3c, template_part.word_44)
    compute_edge_data([grid], [part])
    physics = write_physics([grid], [part], 2)
    back_shapes, _, back_version = read_physics(physics)
    if back_version != 2 or len(back_shapes) != 1 or back_shapes[0].triangles != grid.triangles:
        raise ValueError("the collision written does not read back")
    template_box = ""
    if shapes:
        world = [template_part.to_world(v) for v in shapes[max(template_part.shape, 0)].vertices]
        template_box = (f", box {[round(min(p[i] for p in world), 2) for i in range(3)]}.."
                        f"{[round(max(p[i] for p in world), 2) for i in range(3)]}")
    log.append(f"  collision: template is version {version} with {len(shapes)} shapes, {len(parts)} parts (material "
               f"{template_part.material:#010x}, kind {template_part.kind}{template_box}); ours: version 2, one part, "
               f"{len(grid.vertices)} vertices, {len(grid.triangles)} triangles, {len(physics)} bytes, "
               f"{'object space' if PHYSICS_IN_OBJECT_SPACE else 'world space'}")

    # 4. The layer: one placement of our blueprint (or a grid of them).
    layer = clone_document(layer_old, derived_guid(level, "ground_layer", layer_old.file_guid.string()), level + ":ground_layer")
    refresh_flags(layer_old, layer)
    target = layer.instances[placement_index].object
    removed = _cut_to(layer, placement_index, "layer", log)
    placed = next(i for i, r in enumerate(layer.instances) if r.object is target)
    # GUESS: an import of a blueprint names its root instance (the template placement imports
    # another blueprint, so its own instance GUID says nothing about ours).
    instance_guid = bp.instances[0].instance_guid
    holders = pointer_arrays(layer, placed)
    if visual == "template":
        # The layer's own placement, of its own blueprint, kept beside ours (rotation and scale as
        # they were, moved next to --at).
        source = layer.instances[placed]
        index = clone_instance(layer, placed, derived_guid(level, "visual_placement") if source.exported else None,
                               after_last_of_type(layer, placed))
        for _, array in holders:
            array.append(PointerReference(PointerKind.internal, index))
        kept = layer.instances[index].object
        if source.exported and isinstance(kept.get("Flags"), int):
            kept.set("Flags", guid_flags(layer.instances[index].instance_guid, kept.get("Flags")))
        where = [a + d for a, d in zip(at, VISUAL_OFFSET)]
        for axis, value in zip("xyz", where):
            kept.get(PLACEMENT_TRANSFORM).get("trans").set(axis, float(value))
        imp = layer.imports[kept.get(PLACEMENT_BLUEPRINT).index]
        log.append(f"  --visual template: the layer's own placement kept as #{index} at {where}, placing "
                   f"{layer_source.guids().get(imp.file_guid) or imp.file_guid.string() + ' (not in the layer bundle)'}")
    target.set(PLACEMENT_BLUEPRINT, add_import(layer, bp.file_guid, instance_guid))
    offsets = [((i - (tiles - 1) / 2) * size, (j - (tiles - 1) / 2) * size) for j in range(tiles) for i in range(tiles)]
    _place(target, (at[0] + offsets[0][0], at[1], at[2] + offsets[0][1]))
    for n, (dx, dz) in enumerate(offsets[1:], 1):
        source = layer.instances[placed]
        index = clone_instance(layer, placed, derived_guid(level, "ground_placement", str(n)) if source.exported else None,
                               after_last_of_type(layer, placed))
        for _, array in holders:
            array.append(PointerReference(PointerKind.internal, index))
        copy_obj = layer.instances[index].object
        if source.exported and isinstance(copy_obj.get("Flags"), int):
            copy_obj.set("Flags", guid_flags(layer.instances[index].instance_guid, copy_obj.get("Flags")))
        _place(copy_obj, (at[0] + dx, at[1], at[2] + dz))
    dropped = prune_imports(layer)
    if isinstance(layer.instances[0].object.get("Name"), str):
        layer.instances[0].object.set("Name", layer_name)
    log.append(f"  new {layer_name}: file guid {layer.file_guid.string()}, {len(layer.instances)} instances "
               f"({dict(Counter(type_name(layer, i.object) for i in layer.instances if i.object))}), {len(removed)} removed, "
               f"{tiles * tiles} placements of {bp_name} (root instance {instance_guid.string()}) centred on "
               f"{list(at)}, {'imports kept (boxed values)' if dropped is None else f'{dropped} unused imports dropped'}, "
               f"{len(layer.imports)} left")

    # 5. The tile: cut to its layer reference, which now imports our layer.
    sub = clone_document(tile, derived_guid(level, "ground_sublevel", tile.file_guid.string()), level + ":ground_sublevel")
    refresh_flags(tile, sub)
    reference = sub.instances[layer_ref.instance].object
    removed = _cut_to(sub, layer_ref.instance, "tile", log)
    layer_guid, matched = _new_guid_of(layer_old, layer, layer_import.class_guid) if tile_layer == layer_template \
        else (layer.instances[0].instance_guid, False)
    reference.set(PLACEMENT_BLUEPRINT, add_import(sub, layer.file_guid, layer_guid))
    dropped = prune_imports(sub)
    if isinstance(sub.instances[0].object.get("Name"), str):
        sub.instances[0].object.set("Name", sublevel_name)
    log.append(f"  new {sublevel_name}: file guid {sub.file_guid.string()}, {len(sub.instances)} instances "
               f"({dict(Counter(type_name(sub, i.object) for i in sub.instances if i.object))}), {len(removed)} removed, "
               f"{LAYER_REFERENCE_TYPE} -> {layer_name} instance {layer_guid.string()}"
               + ("" if matched else " (the layer's root)") + ", "
               + ("imports kept (boxed values)" if dropped is None else f"{dropped} unused imports dropped")
               + f", {len(sub.imports)} left")
    for imp in sub.imports:
        if imp.file_guid != layer.file_guid:
            log.append(f"    still imports {tile_source.guids().get(imp.file_guid) or imp.file_guid.string()}")

    # 5b. What the blueprint (and our mesh) need from the blueprint's bundle, unless that is bam's
    # root bundle, which the level's root bundle copies (and which is loaded with the level).
    copied = {EBX: [], "res": [], CHUNK: [], "toc": []}
    if bp_source.name.lower() == BASE_ROOT_BUNDLE:
        log.append("  the blueprint's imports are in bam's root bundle, which the level's root bundle copies: nothing listed")
    else:
        docs = [bp] + ([own[0]] if own else [])
        new_files = {d.file_guid for d in docs}
        roots = [imp.file_guid for d in docs for imp in d.imports if imp.file_guid not in new_files]
        skip = {blueprint_template.lower()} | ({template_mesh[0].lower()} if own else set())
        copied = _closure(bp_source, roots, skip, log)

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
            # Only references nothing but pointer arrays point at are removed; the others are kept
            # (a field pointing at them would be left dangling) and reported.
            removable, kept, structs = set(), [], 0
            for f in unresolved:
                if f.instance < 0:
                    f.array[:] = [v for v in f.array if v is not f.object]
                    structs += 1
                    continue
                held = field_referrers(root, f.instance)
                if held:
                    kept.append(f"#{f.instance} {f.object.get('BundleName')!r} (held by {', '.join(held)})")
                else:
                    removable.add(f.instance)
            taken = remove_instances(root, removable)
            lines.append(f"  removed {len(removable) + structs} references to bundles the TOC does not list"
                         + (f" (and {len(taken)} pointers to them: {taken})" if taken else ""))
            for text in kept:
                lines.append(f"  KEPT a reference to a bundle the TOC does not list, as a field points at it: {text}")
        references = find_objects(root, SUBWORLD_REFERENCE_TYPE)
        if not references:
            raise KeyError(f"the root has no {SUBWORLD_REFERENCE_TYPE} to copy for the ground")
        references.sort(key=lambda f: (f.object.get("IsDetachedSubLevel") is True, f.object.get("AutoLoad") is not True))
        template = references[0]
        added = _clone_found(root, template, level + ":ground_reference")
        changes = dict(GROUND_REFERENCE, BundleName=sublevel_name)
        blueprint = added.get("Blueprint")
        if isinstance(blueprint, PointerReference) and blueprint.kind != PointerKind.null:
            changes["Blueprint"] = (add_import(root, sub.file_guid, sub.instances[0].instance_guid)
                                    if blueprint.kind == PointerKind.external else PointerReference())
        if isinstance(added.get("Template"), PointerReference):
            changes["Template"] = PointerReference()
        transform = added.get("BlueprintTransform")
        if isinstance(transform, Object) and not _identity(transform):
            lines.append("  WARNING: the reference's BlueprintTransform is not a LinearTransform; left as copied")
        index = next((i for i, r in enumerate(root.instances) if r.object is added), -1)
        if index >= 0 and root.instances[index].exported and isinstance(added.get("Flags"), int):
            changes["Flags"] = guid_flags(root.instances[index].instance_guid, added.get("Flags"))
        missing = set_fields(added, changes)
        lines.append(f"  adds {SUBWORLD_REFERENCE_TYPE} {sublevel_name!r} (copy of {template.path}) as "
                     + (f"instance #{index}" if index >= 0 else "a struct") + f", auto-load "
                     f"{added.get('AutoLoad')}, Blueprint {'-> the tile' if 'Blueprint' in changes else 'none'}"
                     + (f", MISSING fields {missing}" if missing else ""))

    log.extend(new_level(game, mod, level, display_name, keep_bundle=BAM_MODES[bam], edit_root=edit_root))

    # 7. The ground bundle (GUESS: tile, layer, blueprint, then the collision beside the blueprint).
    chunk = game.chunk_index(nativedb.DEFAULT_INSTALL_CHUNK)
    mod.new_bundle(path, ground_bundle, chunk)
    mod.add_ebx(path, ground_bundle, sublevel_name, _checked_bytes(sub, sublevel_name))
    mod.add_ebx(path, ground_bundle, layer_name, _checked_bytes(layer, layer_name))
    mod.add_ebx(path, ground_bundle, bp_name, _checked_bytes(bp, bp_name))
    mod.add_resource(path, ground_bundle, physics_name, physics_asset.resource_type, physics, physics_id,
                     copy.copy(physics_asset.resource_meta))
    new_counts = [3, 1, 0]
    if own is not None:
        mesh_new, new_ms, new_chunks, chunk_guids, ms_asset = own
        mod.add_ebx(path, ground_bundle, mesh_name, _checked_bytes(mesh_new, mesh_name))
        mod.add_resource(path, ground_bundle, mesh_name, ms_asset.resource_type, new_ms, resource_id(mesh_name),
                         copy.copy(ms_asset.resource_meta))
        # GUESS: a geometry chunk is looked up in the level's TOC chunk list as well as the bundle's
        # entries; ours are in both (the lighthouse crashed with its chunks in neither).
        for guid, data in zip(chunk_guids, new_chunks):
            mod.add_chunk(path, ground_bundle, guid, data)
            mod.add_toc_chunk_payload(path, guid, data, chunk)
        new_counts = [4, 2, len(new_chunks)]
    for kind, assets in ((EBX, bp_source.manifest.ebx), ("res", bp_source.manifest.resources),
                         (CHUNK, bp_source.manifest.chunks)):
        for i in copied[kind]:
            mod.add_copied(path, ground_bundle, assets[i], bp_source.region.files[bp_source.file_index(kind, i)])
    added = sum(mod.add_toc_chunk(path, c) for c in copied["toc"])
    log.append(f"{ground_bundle}: {new_counts[0]} new EBX, {new_counts[1]} new res, {new_counts[2]} new chunks, "
               f"{sum(len(copied[k]) for k in (EBX, 'res', CHUNK))} entries listed from {bp_source.name}; "
               f"{added} of {len(copied['toc'])} chunks of {bp_source.superbundle}'s TOC added to the level's TOC "
               f"(the others it already lists); new payloads in install chunk {chunk:#x}")
    log.extend(_mesh_chunk_report(mod, path, ground_bundle, bp_source, template_mesh, own))
    return log


def _mesh_chunk_report(mod, path: str, ground_bundle: str, source: _Source, template_mesh, own) -> list[str]:
    """Per mesh the ground draws: each LOD's chunk, where the base game keeps it and whether the
    level's TOC chunk list, the ground bundle or the level's root bundle now list it."""
    toc = {c.guid for c in mod.new_superbundles[path]} | set(mod.toc_chunk_payloads.get(path, {}))
    bundles = {}
    for name in (ground_bundle, "win32/" + path):
        found = mod._find(path, name)
        bundles[name] = {c.guid for c in found.manifest.chunks} if found is not None and found.manifest else set()
    out = []
    meshes = []
    if template_mesh is not None:
        name, _, (index, _) = template_mesh
        meshes.append((name + (" (replaced by ours)" if own else ""), read_mesh_layout(source.payload("res", index)),
                       True))
    if own is not None:
        meshes.append((f"ours ({own[0].instances[0].object.get('Name')})", read_mesh_layout(own[1]), False))
    for label, layout, base in meshes:
        for k, lod in enumerate(layout):
            where = source.where_chunk(lod.chunk) if base else "the mod's archive"
            listed = [n for n, ok in (("the level's TOC", lod.chunk in toc),
                                      ("the ground bundle", lod.chunk in bundles[ground_bundle]),
                                      ("the level's root bundle", lod.chunk in bundles["win32/" + path])) if ok]
            out.append(f"  mesh {label} LOD {k} chunk {lod.chunk.string()}: in {where}; listed by "
                       + (", ".join(listed) if listed else "NOTHING the level loads"))
    return out
