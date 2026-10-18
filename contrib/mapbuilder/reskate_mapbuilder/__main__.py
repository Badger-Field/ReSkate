"""Command line for the map builder.

  python -m reskate_mapbuilder inspect --game <dir> --superbundle S --bundle B --resource R [--near X Z]
  python -m reskate_mapbuilder raise-ground --game <dir> --mods <dir> --name zz-... \\
        --superbundle S --bundle B --resource R --parts 157,161,165 --by 1.0

  python -m reskate_mapbuilder rebuild --game <dir> --mods <dir> --name zz-... \\
        --superbundle S --bundle B --resource R [--by 1.0]

  python -m reskate_mapbuilder new-level --game <dir> --mods <dir> --name zz-... --level <name> [--dry-run]
  python -m reskate_mapbuilder new-ground-level --game <dir> --mods <dir> --name zz-... --level <name> \\
        [--size 50] [--tiles N] [--at X Y Z] [--bam all|no-world|333] [--tile-template EBX] \\
        [--layer-template EBX [--layer-bundle B]] [--blueprint-template EBX [--blueprint-bundle B]] \\
        [--blueprint-superbundle S] [--visual none|template] [--mesh template|own] \\
        [--asset-bundle ground|coregameassets|root] [--shader-from MESH[:SECTION]] \\
        [--prop BLUEPRINT --prop-at X,Y,Z ...] [--dry-run]
  python -m reskate_mapbuilder inspect-meshset --game <dir> [--mod <mod dir>] --superbundle S --bundle B --resource R
  python -m reskate_mapbuilder new-place-level --game <dir> --mods <dir> --name zz-... --level <name> --tiles <DIR> \\
        [--at X Y Z] [--mirror] [--bam 333|no-world|all] [--asset-bundle ...] [--dry-run]
  python -m reskate_mapbuilder place-info --tiles <DIR> [--at X Y Z] [--max-height M] [--skip-part P]
  python -m reskate_mapbuilder material-donors --game <dir> [--contains TEXT]
  python -m reskate_mapbuilder mesh-refs --game <dir> [--mod <mod dir>] --superbundle S --bundle B --mesh EBX \\
        [--scan SUPERBUNDLE:BUNDLE ...] [--level <name>]
  python -m reskate_mapbuilder meshset-diff --game <dir> [--mod-a M] --a S:BUNDLE:RES [--mod-b M] --b S:BUNDLE:RES
  python -m reskate_mapbuilder list-assets --game <dir> [--mod <mod dir>] --superbundle S [--bundle B] [--contains TEXT]
  python -m reskate_mapbuilder ebx-roundtrip --game <dir> [--mod <mod dir>] --superbundle S [--bundle B] --ebx NAME [--detail [N]]
  python -m reskate_mapbuilder ebx-arrayhash --game <dir> [--mod <mod dir>] --superbundle S [--bundle B] --ebx NAME
  python -m reskate_mapbuilder dump-ebx --game <dir> [--mod <mod dir>] --superbundle S [--bundle B] --ebx NAME

raise-ground repeats the collision test proven in game with ReSkate's C++ tools: it lifts the
named parts' shapes, recomputes their edge data over the whole resource by the rule in
FORMAT-NOTES 2.3, zeroes their tree nodes, and writes a one-resource override mod.
"""
from __future__ import annotations

import argparse
import re
import sys

from .ebx import read_document
from .ebxcheck import array_hash_report, roundtrip
from .ebxedit import describe
from .game import Game
from .ground import material_donors
from .ground import ASSET_BUNDLES, BAM_MODES, COLLISION_MODES, MESH_MODES, MESH_SHAPES, VISUAL_MODES, BLUEPRINT_BUNDLE, BLUEPRINT_TEMPLATE, DEFAULT_AT, DEFAULT_SIZE, TILE_TEMPLATE, new_ground_level
from .level import BASE_SUPERBUNDLE, new_level
from .meshrefs import mesh_refs
from .place import (PlaceError, check_spots, view_spots, collision as place_collision, filter_collision, read_place, spawn_at,
                    summary as place_summary)
from .meshset import describe_meshset, diff_meshsets
from .mod import ModBuilder
from .physics import (KIND_TRIANGLE_MESH, Shape, compute_edge_data, physics_tail, read_physics, replace_shape,
                      write_physics)


def _box(part, shape):
    world = [part.to_world(v) for v in shape.vertices]
    return [min(p[i] for p in world) for i in range(3)], [max(p[i] for p in world) for i in range(3)]


def inspect(args):
    game = Game(args.game)
    mod = ModBuilder(game, "inspect")
    data = mod.resource(args.superbundle, args.bundle, args.resource)
    shapes, parts, version = read_physics(data)
    print(f"version {version}: {len(shapes)} shapes, {len(parts)} parts with shapes, {len(data)} bytes")
    for i, part in enumerate(parts):
        if part.shape < 0:
            continue
        lo, hi = _box(part, shapes[part.shape])
        if args.near and not (lo[0] <= args.near[0] <= hi[0] and lo[2] <= args.near[1] <= hi[2]):
            continue
        s = shapes[part.shape]
        print(f"part {i}: shape {part.shape} kind {part.kind} V {len(s.vertices)} F {len(s.triangles)} "
              f"material {part.material:#010x} box y {lo[1]:.2f}..{hi[1]:.2f} x {lo[0]:.1f}..{hi[0]:.1f} "
              f"z {lo[2]:.1f}..{hi[2]:.1f}")


def raise_ground(args):
    game = Game(args.game)
    mod = ModBuilder(game, args.name, description=f"Collision test: parts {args.parts} raised {args.by} m")
    data = mod.resource(args.superbundle, args.bundle, args.resource)
    shapes, parts, version = read_physics(data)
    wanted = [int(p) for p in args.parts.split(",")]
    for p in wanted:
        if parts[p].shape < 0:
            sys.exit(f"part {p} has no shape")
        if parts[p].scale != 1.0 or parts[p].rotation[0] or parts[p].rotation[2]:
            sys.exit(f"part {p} is scaled or tilted; this test only lifts upright parts")
        if not shapes[parts[p].shape].triangles or len(shapes[parts[p].shape].triangles) * 3 > 0xFFFF:
            sys.exit(f"part {p}'s shape cannot be rewritten as triangles")
    raised = {}
    for p in wanted:
        old = shapes[parts[p].shape]
        raised[parts[p].shape] = Shape([(x, y + args.by, z) for x, y, z in old.vertices], old.triangles)
    all_shapes = [raised.get(i, s) for i, s in enumerate(shapes)]
    compute_edge_data(all_shapes, parts)
    for index, shape in raised.items():
        data = replace_shape(data, index, shape)
    mod.replace_resource(args.superbundle, args.bundle, args.resource, data)
    root = mod.write(args.mods)
    print(f"wrote {root} ({len(data)} byte resource, shapes {sorted(raised)} raised {args.by} m)")


def rebuild(args):
    """Rewrites a whole resource with write_physics in the source's own version, optionally raised.
    A version-0 source keeps its top-level part tree (it moves with the parts and the world box)."""
    game = Game(args.game)
    mod = ModBuilder(game, args.name, description=f"Collision test: resource rebuilt from scratch, raised {args.by} m")
    data = mod.resource(args.superbundle, args.bundle, args.resource)
    shapes, parts, version = read_physics(data)
    bad = [i for i, p in enumerate(parts) if p.shape < 0 or p.kind != KIND_TRIANGLE_MESH]
    if bad:
        sys.exit(f"parts {bad[:10]} are not triangle meshes (kind 5); this resource cannot be rebuilt yet")
    if any(len(s.triangles) * 3 > 0xFFFF for s in shapes):
        sys.exit("a shape is too large to rewrite as triangles")
    new = [Shape([(x, y + args.by, z) for x, y, z in s.vertices], s.triangles) for s in shapes]
    compute_edge_data(new, parts)
    out = write_physics(new, parts, version, physics_tail(data) if version == 0 else None)
    mod.replace_resource(args.superbundle, args.bundle, args.resource, out)
    root = mod.write(args.mods)
    print(f"wrote {root}: version {version} resource of {len(data)} bytes rebuilt as version {version}, {len(out)} bytes, "
          f"{len(new)} shapes, {len(parts)} parts, raised {args.by} m")


def new_level_command(args):
    """Builds the new-level mod (FORMAT-NOTES 6.2). With --dry-run every file is read, edited and
    encoded in memory and nothing is written."""
    game = Game(args.game)
    mod = ModBuilder(game, args.name, description=f"New level {args.level} (a clone of the base map)")
    for line in new_level(game, mod, args.level, args.display_name):
        print(line)
    files = mod.build()
    print("Mod files:")
    for relative, data in sorted(files.items()):
        print(f"  {relative}: {len(data)} bytes")
    if args.dry_run:
        print("dry run: nothing written")
        return
    if not args.mods:
        sys.exit("--mods is required unless --dry-run")
    print(f"wrote {mod.write(args.mods)}")


def new_ground_level_command(args, place=None):
    """Builds the own-ground level mod (FORMAT-NOTES 6.3), or with `place` the real-place one
    (6.4). --dry-run writes nothing. The report is printed also when the build refuses, so a dry
    run on the PC says what was found."""
    game = Game(args.game)
    what = f"the place in {args.tiles}" if place else "its own ground"
    mod = ModBuilder(game, args.name, description=f"New level {args.level} with {what}")
    log: list[str] = []
    def collision(template, templates, declared):
        # --parts merge (default): as many parts as the template's own .physics has; grow: one per
        # chunk (and the blueprint's tables grown to match).
        mode = getattr(args, "parts", "merge")
        count = None if mode in ("chunks", "grow") else (declared or len(templates) or 1)
        return place_collision(place, template, count, templates)
    grid = getattr(args, "grid", None)
    try:
        new_ground_level(game, mod, args.level, args.display_name, args.size, tuple(args.at), args.bam,
                         args.tile_template, args.layer_template, args.layer_bundle, args.blueprint_template,
                         args.blueprint_bundle, grid if grid is not None else 1, log, args.blueprint_superbundle,
                         args.visual, args.mesh, args.asset_bundle, collision if place is not None else None,
                         getattr(args, "shader_from", None), _props(args), getattr(args, "keep_section", None),
                         getattr(args, "mesh_shape", "slab"), getattr(args, "collision", "own"),
                         None if getattr(args, "other_sections", "degenerate") == "geometry"
                         else getattr(args, "geometry_section", 0), getattr(args, "uv_per_metre", None),
                         getattr(args, "parts", "merge") == "grow", _visuals(args, place),
                         getattr(args, "visual_template", None), _donors(args),
                         not getattr(args, "no_auto_materials", False))
    finally:
        for line in log:
            print(line)
    files = mod.build()
    print("Mod files:")
    for relative, data in sorted(files.items()):
        print(f"  {relative}: {len(data)} bytes")
    if place is not None:
        print("In game, after `load levels/game/{0}/{0}` (expected readings; - means no ground there):".format(args.level))
        for label, x, z, y in check_spots(place, tuple(args.at)):
            print(f"  trainer ground {x} {z}    -> {'-' if y is None else y}    ({label})")
    if args.dry_run:
        print("dry run: nothing written")
        return
    if not args.mods:
        sys.exit("--mods is required unless --dry-run")
    print(f"wrote {mod.write(args.mods)}")


def _xyz(tokens, label: str) -> tuple[float, float, float]:
    """Three numbers from argv tokens: "X,Y,Z", "X;Y;Z", "X Y Z" in one token, or three tokens
    (PowerShell passes 1,2,3 as three)."""
    parts = [t for t in re.split(r"[\s,;]+", " ".join(tokens if isinstance(tokens, list) else [tokens])) if t]
    try:
        x, y, z = (float(t) for t in parts)
    except ValueError:
        sys.exit(f"{label} {' '.join(tokens) if isinstance(tokens, list) else tokens!r}: give three numbers X Y Z")
    return x, y, z


def _size(tokens):
    if not tokens:
        return DEFAULT_SIZE
    parts = [t for t in re.split(r"[\s,;]+", " ".join(tokens)) if t]
    try:
        values = [float(t) for t in parts]
    except ValueError:
        values = []
    if len(values) == 1:
        return values[0]
    if len(values) == 3:
        return tuple(values)
    sys.exit(f"--size {' '.join(tokens)}: give W, or W,H,D")


def _props(args) -> list:
    names, places = getattr(args, "prop", None) or [], getattr(args, "prop_at", None) or []
    if len(names) != len(places):
        sys.exit("give one --prop-at X,Y,Z for each --prop")
    return [(name, _xyz(where, "--prop-at")) for name, where in zip(names, places)]


def _visuals(args, place) -> list:
    """(name, vertices, triangles, material) of the place's visual objects with --visuals, filtered
    by --visuals-only (an object's name, its part, or a name prefix)."""
    if place is None or not getattr(args, "visuals", False):
        return []
    only = getattr(args, "visuals_only", None) or []
    out = []
    for piece in place.visual:
        label = f"{piece.name}_t{piece.tile[0]}_{piece.tile[1]}".replace("-", "m")
        if only and not any(o in (piece.name, piece.part, label) or piece.name.startswith(o) for o in only):
            continue
        out.append((label, piece.vertices, piece.triangles, piece.material))
    if getattr(args, "visual_slab", None):
        from .meshset import box_geometry
        w, h, d = _xyz(args.visual_slab, "--visual-slab")
        vertices, triangles = box_geometry((-w / 2, -h, -d / 2), (w / 2, 0.0, d / 2))
        out.append(("slab", vertices, triangles, "ground"))
    if not out:
        sys.exit("--visuals: no visual object matches --visuals-only")
    extra = " plus the slab" if getattr(args, "visual_slab", None) else ""
    print(f"visual objects: {len(out) - bool(extra)} of {len(place.visual)}{extra}: " + ", ".join(
        f"{name} ({material}, {len(v)} vertices, {len(t)} triangles)" for name, v, t, material in out))
    return out


def _donors(args) -> dict:
    out = {}
    for spec in getattr(args, "material_donor", None) or []:
        material, _, mesh = spec.partition("=")
        if not material or not mesh:
            sys.exit(f"--material-donor {spec!r}: give MATERIAL=MESH[:SECTION]")
        out[material] = mesh
    return out


def _read_place_args(args, command: str):
    """The place, filtered, and the `--at` to use (from the spawn unless given); prints the summary."""
    try:
        place = read_place(args.tiles, args.mirror)
        notes = filter_collision(place, args.max_height, tuple(args.skip_part or ()))
    except PlaceError as e:
        sys.exit(f"{command}: {e}")
    if args.at is None:
        args.at = list(spawn_at(place))
        print(f"--at {args.at[0]:g} {args.at[1]:g} {args.at[2]:g}: the export's spawn {place.spawn} goes to the game's "
              f"(0, {args.at[1] + (place.spawn[1] if place.spawn else 0):g}, 0)")
    for line in place_summary(place, tuple(args.at)) + notes:
        print(line)
    return place


def new_place_level_command(args):
    """A level whose collision is a real place's (the lidar-parks tile export, FORMAT-NOTES 6.4)."""
    place = _read_place_args(args, "new-place-level")
    args.size, args.mesh = DEFAULT_SIZE, "template"
    new_ground_level_command(args, place)


def material_donors_command(args):
    """Stock meshes of bam's root bundle whose sections could lend their material (read-only)."""
    for line in material_donors(Game(args.game), args.contains, args.limit, args.every):
        print(line)


def place_info(args):
    """The tile export's summary and the trainer spots, read-only and without the game."""
    place = _read_place_args(args, "place-info")
    print("Expected `trainer ground` readings in the level (- means no ground there):")
    for label, x, z, y in check_spots(place, tuple(args.at)):
        print(f"  trainer ground {x} {z}    -> {'-' if y is None else y}    ({label})")
    if args.views:
        print("Where to look at each visual object from (after `trainer ground` the camera faces heading 0, +z, "
              "north; where the heading below is not 0, turn until `trainer where` shows it; 90 = east, +x):")
        for label, x, z, y, heading, note in view_spots(place, tuple(args.at)):
            print(f"  {label}: trainer ground {x} {z}    -> {'-' if y is None else y}, face heading {heading:g}  ({note})")


def list_assets(args):
    """Lists a superbundle's (or one bundle's) EBX, resources and chunks, filtered by name; read-only."""
    game = Game(args.game, args.mod)
    toc = game.superbundle(args.superbundle)
    needle = (args.contains or "").lower()
    shown = 0
    for b in toc.bundles:
        if args.bundle and b.name.lower() != args.bundle.lower():
            continue
        if args.bundle_contains and args.bundle_contains.lower() not in b.name.lower():
            continue
        _, region, manifest = game.bundle(toc, b.name)
        for kind, assets in (("ebx", manifest.ebx), ("res", manifest.resources), ("chunk", manifest.chunks)):
            for a in assets:
                label = a.name if kind != "chunk" else a.guid.string()
                if needle and needle not in label.lower():
                    continue
                extra = (f" type {a.resource_type:#010x} id {a.resource_id:#018x} meta {a.resource_meta.hex()}"
                         if kind == "res" else f" logical {a.logical_offset}+{a.logical_size}" if kind == "chunk" else "")
                print(f"{b.name}\t{kind}\t{label}\t{a.original_size or a.logical_size} bytes{extra}")
                shown += 1
                if shown >= args.limit:
                    print(f"... stopped at --limit {args.limit}")
                    return


def inspect_meshset(args):
    """Prints a MeshSet resource's header, LOD and section fields (read-only)."""
    game = Game(args.game, args.mod)
    _, region, manifest = game.bundle(game.superbundle(args.superbundle), args.bundle)
    for i, asset in enumerate(manifest.resources):
        if asset.name.lower() == args.resource.lower():
            index = (0 if region.inline_manifest else 1) + len(manifest.ebx) + i
            print(f"{asset.name}: type {asset.resource_type:#010x}, id {asset.resource_id:#018x}, meta {asset.resource_meta.hex()}")
            for line in describe_meshset(game.payload(region, index)):
                print(line)
            return
    sys.exit(f"resource {args.resource} not in {args.bundle}")


def _resource_bytes(game, spec: str) -> bytes:
    superbundle, bundle, resource = (spec.split(":", 2) + ["", ""])[:3]
    if not resource:
        sys.exit(f"{spec!r}: give SUPERBUNDLE:BUNDLE:RESOURCE")
    _, region, manifest = game.bundle(game.superbundle(superbundle), bundle)
    for i, asset in enumerate(manifest.resources):
        if asset.name.lower() == resource.lower():
            return game.payload(region, (0 if region.inline_manifest else 1) + len(manifest.ebx) + i)
    sys.exit(f"resource {resource} not in {bundle}")


def meshset_diff(args):
    """Field-by-field differences of two MeshSets (read-only)."""
    a = _resource_bytes(Game(args.game, args.mod_a), args.a)
    b = _resource_bytes(Game(args.game, args.mod_b), args.b)
    print(f"A {args.a}" + (f" (mod {args.mod_a})" if args.mod_a else ""))
    print(f"B {args.b}" + (f" (mod {args.mod_b})" if args.mod_b else ""))
    for line in diff_meshsets(a, b):
        print(line)


def mesh_refs_command(args):
    """What a RigidMeshAsset and its MeshSet refer to and where it resolves (read-only)."""
    game = Game(args.game, args.mod)
    scan = []
    for item in args.scan or []:
        superbundle, _, bundle = item.partition(":")
        if not bundle:
            sys.exit("--scan takes SUPERBUNDLE:BUNDLE")
        scan.append((superbundle, bundle))
    for line in mesh_refs(game, args.superbundle, args.bundle, args.mesh, scan, args.level):
        print(line)


def _read_ebx(args) -> bytes:
    game = Game(args.game, args.mod)
    bundle = args.bundle or game.find_ebx(args.superbundle, args.ebx)
    if not bundle:
        sys.exit(f"{args.ebx} is in no bundle of {args.superbundle}")
    _, region, manifest = game.bundle(game.superbundle(args.superbundle), bundle)
    print(f"{args.ebx} in {bundle}")
    return game.ebx(region, manifest, args.ebx)


def dump_ebx(args):
    for line in describe(read_document(_read_ebx(args)), args.limit):
        print(line)


def ebx_roundtrip(args):
    """Rewrites a document unchanged, in memory, and says where and why the bytes differ."""
    for line in roundtrip(_read_ebx(args), detail=args.detail):
        print(line)


def ebx_arrayhash(args):
    """Scores candidate rules for what an array record's hash is made from."""
    for line in array_hash_report(read_document(_read_ebx(args)), args.samples):
        print(line)


def _place_options(p):
    p.add_argument("--at", nargs="+", metavar="X Y Z",
                   help="where the place's origin (its centre, at its origin elevation) goes in the game: X Y Z, X,Y,Z "
                        "or X;Y;Z (default: the export's spawn at the game's x 0, z 0, the origin at height 300)")
    p.add_argument("--mirror", action="store_true", help="flip north and south (if the game shows the place mirrored)")
    p.add_argument("--max-height", type=float, metavar="M",
                   help="drop collision chunks whose top is above M metres (the place's frame), e.g. 20")
    p.add_argument("--skip-part", action="append", metavar="PART",
                   help="drop the collision chunks of this part or object name, e.g. buildings (repeatable)")
    p.add_argument("--visuals", action="store_true",
                   help="draw the place's visual objects: one blueprint and mesh each, cloned from --visual-template")
    p.add_argument("--visuals-only", action="append", metavar="OBJECT",
                   help="only these visual objects (name, part or name prefix; repeatable)")
    p.add_argument("--visual-slab", nargs="+", metavar="W,H,D",
                   help="also a slab W x H x D (top at the place's origin height) through the visual path, the d1b "
                        "slab's shape, to compare the two commands")
    p.add_argument("--visual-template", metavar="BLUEPRINT",
                   help="the stock blueprint (bam root bundle) each visual object copies (default the skatepark box)")
    p.add_argument("--material-donor", action="append", metavar="MATERIAL=MESH[:SECTION]",
                   help="the mesh section whose material draws objects of MATERIAL (default the template's section 0)")
    p.add_argument("--uv-per-metre", type=float, metavar="U", help="visual objects' texture repeats per metre (default 1)")
    p.add_argument("--no-auto-materials", action="store_true",
                   help="without --material-donor, keep the template's section 0 for every material instead of the "
                        "auto picks (ramp wedge surface for ground, a rail for metal, a concrete section)")
    p.add_argument("--parts", choices=("merge", "grow"), default="merge",
                   help="merge: the chunks merged into as many parts as the template's own .physics has (one shape "
                        "each); grow: one part per chunk, the blueprint's per-part tables grown (untested)")


def _template_options(p):
    p.add_argument("--tile-template", default=TILE_TEMPLATE, help="the bam tile copied for the sublevel")
    p.add_argument("--layer-template", help="the LayerData copied (default: the one the tile references)")
    p.add_argument("--layer-bundle", help="its bundle (default: the tile's, or found by search)")
    p.add_argument("--blueprint-template", default=BLUEPRINT_TEMPLATE, help="the ObjectBlueprint copied")
    p.add_argument("--blueprint-bundle", default=BLUEPRINT_BUNDLE, help="its bundle, which holds <blueprint>.physics")
    p.add_argument("--blueprint-superbundle", default=BASE_SUPERBUNDLE, help="the superbundle holding that bundle")
    p.add_argument("--visual", choices=VISUAL_MODES, default="none",
                   help="template: also keep the layer's own placement of its own blueprint, beside ours")
    p.add_argument("--prop", action="append", metavar="BLUEPRINT",
                   help="a stock blueprint of bam's root bundle placed as it is (repeatable; pair with --prop-at)")
    p.add_argument("--prop-at", action="append", nargs="+", metavar="X,Y,Z",
                   help="where the matching --prop goes, in the game: X,Y,Z or X;Y;Z or X Y Z (PowerShell splits commas)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m reskate_mapbuilder")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("inspect", "raise-ground", "rebuild"):
        p = sub.add_parser(name)
        p.add_argument("--game", required=True, help="the game folder (holding Data/)")
        p.add_argument("--superbundle", required=True, help="path under Data/Win32 without .toc")
        p.add_argument("--bundle", required=True)
        p.add_argument("--resource", required=True)
        if name == "inspect":
            p.add_argument("--near", type=float, nargs=2, metavar=("X", "Z"))
        else:
            p.add_argument("--mods", required=True, help="output folder; the mod is written inside it")
            p.add_argument("--name", required=True)
            if name == "raise-ground":
                p.add_argument("--parts", required=True, help="comma-separated part indices")
            p.add_argument("--by", type=float, default=1.0 if name == "raise-ground" else 0.0)
    p = sub.add_parser("new-level", help="a mod adding a level that clones the base map")
    p.add_argument("--game", required=True, help="the game folder (holding Data/)")
    p.add_argument("--mods", help="output folder; the mod is written inside it")
    p.add_argument("--name", required=True, help="the mod's folder name, e.g. zz-new-level")
    p.add_argument("--level", required=True, help="the level's name, e.g. dingolevel_reskate_test")
    p.add_argument("--display-name")
    p.add_argument("--dry-run", action="store_true", help="read and report only")
    p = sub.add_parser("new-ground-level", help="a mod adding a level whose content is our own ground")
    p.add_argument("--game", required=True, help="the game folder (holding Data/)")
    p.add_argument("--mods", help="output folder; the mod is written inside it")
    p.add_argument("--name", required=True, help="the mod's folder name, e.g. zz-ground-level")
    p.add_argument("--level", required=True, help="the level's name, e.g. dingolevel_reskate_ground")
    p.add_argument("--display-name")
    p.add_argument("--size", nargs="+", metavar="W[,H,D]",
                   help="the ground's size in metres: W (a W x W square, with --mesh own a W x 0.2 x W slab) or W,H,D "
                        f"(also W H D or W;H;D); also the placements' spacing (default {DEFAULT_SIZE})")
    p.add_argument("--tiles", type=int, default=1, dest="grid", help="N: an N x N grid of placements")
    p.add_argument("--at", nargs="+", metavar="X Y Z", help="where the ground's centre is placed: X Y Z, X,Y,Z "
                   f"or X;Y;Z (default {' '.join(str(c) for c in DEFAULT_AT)})")
    p.add_argument("--bam", choices=sorted(BAM_MODES), default="all",
                   help="which of bam's bundles the level lists: all (as new-level), no-world (without bam's "
                        "world-partition tiles), 333 (the 42 the 333 map carries)")
    _template_options(p)
    p.add_argument("--asset-bundle", choices=tuple(ASSET_BUNDLES), default="ground",
                   help="where the blueprint, collision and our mesh go: the ground bundle, the level's copy of "
                        "bam_coregameassets, or the level's root bundle (bam root's copy, with its shader depot)")
    p.add_argument("--mesh", choices=MESH_MODES, default="template",
                   help="own: draw a MeshSet of our own (a copy of the template's with our plane); untested")
    p.add_argument("--keep-section", type=int, metavar="N", help="refused: it crashed the game (FORMAT-NOTES 6.3)")
    p.add_argument("--geometry-section", type=int, default=0, metavar="N",
                   help="with --mesh own: the section (of each LOD) that draws our geometry; the others keep their "
                        "material and draw one zero-area triangle")
    p.add_argument("--other-sections", choices=("degenerate", "geometry"), default="degenerate",
                   help="geometry: every section draws our geometry instead")
    p.add_argument("--mesh-shape", choices=MESH_SHAPES, default="slab",
                   help="with --mesh own: our square, a box the size of the template mesh, or the template's own "
                        "geometry copied (plumbing test)")
    p.add_argument("--uv-per-metre", type=float, metavar="U",
                   help="with --mesh own: texture repeats per metre (default 1 for slab and plane; the template's "
                        "own density for box)")
    p.add_argument("--collision", choices=COLLISION_MODES, default="own",
                   help="own: our square, in as many parts as the template's .physics; template: its .physics unchanged")
    p.add_argument("--shader-from", metavar="MESH[:SECTION]",
                   help="with --mesh own: give our sections this stock mesh's (bam root bundle) shader-block key, vertex "
                        "declaration and vertex, so a stock material draws our geometry; use --asset-bundle root")
    p.add_argument("--dry-run", action="store_true", help="read and report only")
    p = sub.add_parser("new-place-level", help="a mod adding a level whose collision is a real place's tiles")
    p.add_argument("--game", required=True, help="the game folder (holding Data/)")
    p.add_argument("--mods", help="output folder; the mod is written inside it")
    p.add_argument("--name", required=True, help="the mod's folder name, e.g. zz-place-level")
    p.add_argument("--level", required=True, help="the level's name, e.g. dingolevel_reskate_place")
    p.add_argument("--display-name")
    p.add_argument("--tiles", required=True, help="the directory `city --level DIR` wrote (reskate-level-tiles v2)")
    _place_options(p)
    p.add_argument("--bam", choices=sorted(BAM_MODES), default="333",
                   help="which of bam's bundles the level lists (default 333: an empty world)")
    _template_options(p)
    p.add_argument("--asset-bundle", choices=tuple(ASSET_BUNDLES), default="ground",
                   help="where the blueprint and collision go: the ground bundle, the level's copy of "
                        "bam_coregameassets, or the level's root bundle")
    p.add_argument("--dry-run", action="store_true", help="read and report only")
    p = sub.add_parser("material-donors", help="stock mesh sections that could lend a material (read-only)")
    p.add_argument("--game", required=True)
    p.add_argument("--contains", default="", help="only MeshSets whose names hold this text (default: all)")
    p.add_argument("--limit", type=int, help="read at most this many MeshSets")
    p.add_argument("--every", action="store_true", help="print every section, not only the classed ones")
    p = sub.add_parser("place-info", help="a tile export's summary and trainer spots (read-only, no game needed)")
    p.add_argument("--tiles", required=True, help="the directory `city --level DIR` wrote (reskate-level-tiles v2)")
    p.add_argument("--views", action="store_true", help="also a spot and heading to look at each visual object from")
    _place_options(p)
    p = sub.add_parser("inspect-meshset", help="print a MeshSet resource's header, LODs and sections (read-only)")
    p.add_argument("--game", required=True)
    p.add_argument("--mod", help="a mod folder laid over the game, to read its TOCs and archives")
    p.add_argument("--superbundle", required=True, help="path under Data/Win32 without .toc")
    p.add_argument("--bundle", required=True)
    p.add_argument("--resource", required=True)
    p = sub.add_parser("meshset-diff", help="field-by-field differences of two MeshSets (read-only)")
    p.add_argument("--game", required=True)
    p.add_argument("--a", required=True, metavar="SUPERBUNDLE:BUNDLE:RESOURCE")
    p.add_argument("--b", required=True, metavar="SUPERBUNDLE:BUNDLE:RESOURCE")
    p.add_argument("--mod-a", help="a mod folder laid over the game when reading A")
    p.add_argument("--mod-b", help="a mod folder laid over the game when reading B")
    p = sub.add_parser("mesh-refs", help="what a mesh refers to and where each reference resolves (read-only)")
    p.add_argument("--game", required=True)
    p.add_argument("--mod", help="a mod folder laid over the game (the 333 map, or ours with --level)")
    p.add_argument("--superbundle", required=True, help="path under Data/Win32 without .toc")
    p.add_argument("--bundle", required=True)
    p.add_argument("--mesh", required=True, help="the RigidMeshAsset's EBX name")
    p.add_argument("--scan", action="append", metavar="SUPERBUNDLE:BUNDLE",
                   help="another bundle whose EBX resolve imports (repeatable)")
    p.add_argument("--level", help="a level of the --mod: say whether its TOC and bundles list what was found")
    p = sub.add_parser("list-assets", help="list a superbundle's EBX, res and chunks by name (read-only)")
    p.add_argument("--game", required=True)
    p.add_argument("--mod", help="a mod folder laid over the game, to read its TOCs and archives")
    p.add_argument("--superbundle", required=True, help="path under Data/Win32 without .toc")
    p.add_argument("--bundle", help="only this bundle")
    p.add_argument("--bundle-contains", help="only bundles whose name holds this text")
    p.add_argument("--contains", help="only entries whose name (or chunk GUID) holds this text")
    p.add_argument("--limit", type=int, default=2000)
    for name, text in (("dump-ebx", "print an EBX document (read-only)"),
                       ("ebx-roundtrip", "rewrite an EBX document in memory and compare (read-only)"),
                       ("ebx-arrayhash", "score rules for the array records' hashes (read-only)")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--game", required=True)
        p.add_argument("--mod", help="a mod folder laid over the game, to read its TOCs and archives")
        p.add_argument("--superbundle", required=True, help="path under Data/Win32 without .toc")
        p.add_argument("--bundle", help="default: the first bundle listing the EBX")
        p.add_argument("--ebx", required=True)
        if name == "dump-ebx":
            p.add_argument("--limit", type=int, default=20, help="array elements printed")
        elif name == "ebx-arrayhash":
            p.add_argument("--samples", type=int, default=20)
        else:
            p.add_argument("--detail", type=int, nargs="?", const=20, default=0, metavar="N",
                           help="also say where differing arrays (the first N, default 20) and strings are reached from")
    args = ap.parse_args(argv)
    if hasattr(args, "at"):
        places = args.command in ("new-place-level", "place-info")
        args.at = list(_xyz(args.at, "--at")) if args.at else (None if places else list(DEFAULT_AT))
    if args.command == "new-ground-level":
        args.size = _size(args.size)
    {"inspect": inspect, "raise-ground": raise_ground, "rebuild": rebuild, "new-level": new_level_command,
     "new-ground-level": new_ground_level_command, "new-place-level": new_place_level_command,
     "place-info": place_info, "material-donors": material_donors_command,
     "list-assets": list_assets, "inspect-meshset": inspect_meshset,
     "mesh-refs": mesh_refs_command, "meshset-diff": meshset_diff,
     "dump-ebx": dump_ebx, "ebx-roundtrip": ebx_roundtrip, "ebx-arrayhash": ebx_arrayhash}[args.command](args)


if __name__ == "__main__":
    main()
