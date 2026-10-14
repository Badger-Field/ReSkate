"""Command line for the map builder.

  python -m reskate_mapbuilder inspect --game <dir> --superbundle S --bundle B --resource R [--near X Z]
  python -m reskate_mapbuilder raise-ground --game <dir> --mods <dir> --name zz-... \\
        --superbundle S --bundle B --resource R --parts 157,161,165 --by 1.0

  python -m reskate_mapbuilder rebuild --game <dir> --mods <dir> --name zz-... \\
        --superbundle S --bundle B --resource R [--by 1.0]

raise-ground repeats the collision test proven in game with ReSkate's C++ tools: it lifts the
named parts' shapes, recomputes their edge data over the whole resource by the rule in
FORMAT-NOTES 2.3, zeroes their tree nodes, and writes a one-resource override mod.
"""
from __future__ import annotations

import argparse
import sys

from .game import Game
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
    args = ap.parse_args(argv)
    {"inspect": inspect, "raise-ground": raise_ground, "rebuild": rebuild}[args.command](args)


if __name__ == "__main__":
    main()
