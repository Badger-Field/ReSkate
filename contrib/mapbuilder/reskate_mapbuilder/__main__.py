"""Command line for the map builder.

  python -m reskate_mapbuilder inspect --game <dir> --superbundle S --bundle B --resource R [--near X Z]
  python -m reskate_mapbuilder raise-ground --game <dir> --mods <dir> --name zz-... \\
        --superbundle S --bundle B --resource R --parts 157,161,165 --by 1.0

  python -m reskate_mapbuilder rebuild --game <dir> --mods <dir> --name zz-... \\
        --superbundle S --bundle B --resource R [--by 1.0]

  python -m reskate_mapbuilder new-level --game <dir> --mods <dir> --name zz-... --level <name> [--dry-run]
  python -m reskate_mapbuilder ebx-roundtrip --game <dir> [--mod <mod dir>] --superbundle S [--bundle B] --ebx NAME [--detail [N]]
  python -m reskate_mapbuilder dump-ebx --game <dir> [--mod <mod dir>] --superbundle S [--bundle B] --ebx NAME

raise-ground repeats the collision test proven in game with ReSkate's C++ tools: it lifts the
named parts' shapes, recomputes their edge data over the whole resource by the rule in
FORMAT-NOTES 2.3, zeroes their tree nodes, and writes a one-resource override mod.
"""
from __future__ import annotations

import argparse
import sys

from .ebx import read_document
from .ebxcheck import roundtrip
from .ebxedit import describe
from .game import Game
from .level import new_level
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
    for name, text in (("dump-ebx", "print an EBX document (read-only)"),
                       ("ebx-roundtrip", "rewrite an EBX document in memory and compare (read-only)")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--game", required=True)
        p.add_argument("--mod", help="a mod folder laid over the game, to read its TOCs and archives")
        p.add_argument("--superbundle", required=True, help="path under Data/Win32 without .toc")
        p.add_argument("--bundle", help="default: the first bundle listing the EBX")
        p.add_argument("--ebx", required=True)
        if name == "dump-ebx":
            p.add_argument("--limit", type=int, default=20, help="array elements printed")
        else:
            p.add_argument("--detail", type=int, nargs="?", const=20, default=0, metavar="N",
                           help="also say where differing arrays (the first N, default 20) and strings are reached from")
    args = ap.parse_args(argv)
    {"inspect": inspect, "raise-ground": raise_ground, "rebuild": rebuild, "new-level": new_level_command,
     "dump-ebx": dump_ebx, "ebx-roundtrip": ebx_roundtrip}[args.command](args)


if __name__ == "__main__":
    main()
