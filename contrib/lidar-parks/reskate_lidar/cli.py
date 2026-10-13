"""Command line: python -m reskate_lidar <convert|calibrate|synth> ..."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

from . import align, catalog as catalog_module, features as features_module, heightmap, park, scan_io

HERE = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = HERE / "catalog.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reskate_lidar", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    convert = commands.add_parser("convert", help="turn a skatepark scan into a ReSkate park mod")
    convert.add_argument("scan", help="PLY, OBJ, GLB, STL, LAS/LAZ or XYZ")
    convert.add_argument("--anchor", help="a park file with one measuring stick where the park goes "
                                          "(export it from the Park Editor's PARK MODS tab)")
    convert.add_argument("--map", help="base map key (default: the anchor's map, else bam)")
    convert.add_argument("--mods", help="the game's Mods folder (or any folder) to write the park mod into")
    convert.add_argument("--park", help="write only a .park.json here instead of a mod")
    convert.add_argument("--name", default="LiDAR Park", help="mod name (and folder)")
    convert.add_argument("--author", default="")
    convert.add_argument("--version", default="1.0.0")
    convert.add_argument("--description", default="")
    convert.add_argument("--up", default="auto", help="scan up axis: auto, z, y, -z, ... (default auto)")
    convert.add_argument("--units", type=float, default=1.0, help="multiply scan coordinates by this to get "
                                                                  "metres (0.3048 for feet, 0.01 for cm)")
    convert.add_argument("--cell", type=float, default=0.05, help="grid cell size in metres (default 0.05)")
    convert.add_argument("--turn", type=float, default=0.0, help="extra turn of the whole park, degrees")
    convert.add_argument("--mirror", action="store_true", help="flip the park if it comes out mirrored in game")
    convert.add_argument("--merge", action="store_true", help="keep the anchor park's other objects")
    convert.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    convert.add_argument("--preview", help="write a top-down PNG of what was found")
    convert.add_argument("--report", help="write the detected features and placements as JSON")
    convert.add_argument("--seed", type=int, default=0)

    calibrate = commands.add_parser("calibrate", help="a park with every catalog object in a row, "
                                                      "each beside a measuring stick, to check sizes")
    calibrate.add_argument("--anchor", required=True)
    calibrate.add_argument("--mods", required=True)
    calibrate.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    calibrate.add_argument("--name", default="LiDAR Calibration")

    mesh = commands.add_parser("mesh", help="the levelled scan as a clean surface mesh (GLB, OBJ, PLY, STL), "
                                             "for building a true custom map")
    mesh.add_argument("scan")
    mesh.add_argument("output", help=".glb, .obj, .ply or .stl")
    mesh.add_argument("--up", default="auto")
    mesh.add_argument("--units", type=float, default=1.0)
    mesh.add_argument("--cell", type=float, default=0.1, help="grid cell size in metres (default 0.1)")
    mesh.add_argument("--seed", type=int, default=0)

    synth = commands.add_parser("synth", help="write a synthetic park scan for trying the tool")
    synth.add_argument("output", help=".ply file to write")
    synth.add_argument("--seed", type=int, default=0)

    args = parser.parse_args(argv)
    try:
        return {"convert": run_convert, "calibrate": run_calibrate, "mesh": run_mesh,
                "synth": run_synth}[args.command](args)
    except (ValueError, FileNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def read_levelled(args) -> np.ndarray:
    print(f"Reading {args.scan}")
    points = scan_io.load_points(args.scan, seed=args.seed) * args.units
    print(f"  {len(points):,} points")
    alignment = align.level(points, args.up, seed=args.seed)
    levelled = alignment.apply(points)
    if getattr(args, "turn", 0.0):
        t = math.radians(args.turn)
        rotation = np.array([[math.cos(t), -math.sin(t), 0], [math.sin(t), math.cos(t), 0], [0, 0, 1]])
        levelled = levelled @ rotation.T
    extent = np.ptp(levelled[:, :2], axis=0)
    print(f"  ground: {alignment.ground_inliers:.0%} of points, levelled by {alignment.tilt_degrees:.1f} degrees; "
          f"park {extent[0]:.1f} x {extent[1]:.1f} m")
    if max(extent) > 400 or max(extent) < 3:
        print("  warning: that size looks wrong for a skatepark; check --units", file=sys.stderr)
    return levelled


def run_mesh(args) -> int:
    from . import meshing
    hm = heightmap.build(read_levelled(args), cell=args.cell)
    vertices, faces = meshing.write(hm, args.output)
    print(f"Wrote {args.output}: {vertices:,} vertices, {faces:,} triangles (metres, Y up, ground at 0)")
    return 0


def run_convert(args) -> int:
    cat = catalog_module.load_catalog(args.catalog)
    levelled = read_levelled(args)
    hm = heightmap.build(levelled, cell=args.cell)
    detection = features_module.detect(hm)
    placements, skipped = catalog_module.place(detection.features, cat)

    anchor = park.Anchor()
    if args.anchor:
        anchor = park.read_anchor(args.anchor, cat.marker)
    map_name = args.map or anchor.map or "bam"
    if anchor.map and map_name != anchor.map:
        raise ValueError(f"The anchor is on {anchor.map}, not {map_name}")
    document = park.build_park(placements, map_name, anchor, mirror=args.mirror, merge=args.merge)

    print(f"\nFound {len(detection.features)} features:")
    for index, f in enumerate(detection.features):
        extra = f" ({'; '.join(f.notes)})" if f.notes else ""
        print(f"  #{index:<3} {f.kind:<10} {f.along:5.2f} x {f.across:5.2f} m, {f.height:4.2f} m high{extra}")
    print(f"\nPlacing {len(placements)} objects on {map_name}:")
    counts: dict[str, int] = {}
    for p in placements:
        counts[p.item.item] = counts.get(p.item.item, 0) + 1
    for item, count in sorted(counts.items()):
        print(f"  {count:3} x {item}")
    for line in skipped + [f"ignored {line}" for line in detection.ignored]:
        print(f"  skipped: {line}")
    if any(p.item.estimated for p in placements):
        print("\nnote: some catalog sizes are estimates; run 'calibrate' once and correct catalog.json")
    if not args.anchor:
        print("note: no --anchor, so the park sits at the map's origin; see README for placing it")
    total = len(document["maps"][map_name])
    if total > park.DEFAULT_DROPPER_LIMIT:
        print(f"warning: {total} objects is over the Object Dropper's default limit of "
              f"{park.DEFAULT_DROPPER_LIMIT}; raise it in config/object-dropper.json if objects go missing",
              file=sys.stderr)

    if args.park:
        Path(args.park).write_text(park.encode(document), encoding="utf-8")
        print(f"\nWrote {args.park}")
    if args.mods:
        description = args.description or f"Built from a LiDAR scan with {len(placements)} Build Kit objects."
        written = park.write_mod(args.mods, args.name, document, args.name, args.author, args.version, description)
        print(f"\nWrote {written}")
    if not args.park and not args.mods:
        print("\n(nothing written: pass --mods or --park)")
    if args.preview:
        from . import preview
        preview.render(hm, detection.features, placements, args.preview)
        print(f"Wrote {args.preview}")
    if args.report:
        Path(args.report).write_text(json.dumps({
            "features": [{**f.__dict__, "yaw_degrees": math.degrees(f.yaw)} for f in detection.features],
            "placements": [{"item": p.item.item, "x": p.x, "y": p.y, "z": p.z, "yaw_degrees": math.degrees(p.yaw),
                            "pitch_degrees": math.degrees(p.pitch), "scale": p.scale, "feature": p.feature}
                           for p in placements],
            "skipped": skipped, "ignored": detection.ignored,
        }, indent=2, default=float), encoding="utf-8")
        print(f"Wrote {args.report}")
    return 0


def run_calibrate(args) -> int:
    cat = catalog_module.load_catalog(args.catalog)
    anchor = park.read_anchor(args.anchor, cat.marker)
    marker = catalog_module.CatalogItem(cat.marker, [], 0.1, 0.1, 1.0, origin=(0.0, 0.0, 0.0))
    placements = []
    x = 0.0
    for item in cat.items:
        x += item.along / 2
        placements.append(catalog_module.Placement(item, x, 0.0, 0.0, 0.0, 0.0, 1.0, -1))
        # A measuring stick at each end of the object's expected length, and one at its side.
        for px, py in ((x - item.along / 2, -item.across / 2 - 0.5), (x + item.along / 2, -item.across / 2 - 0.5)):
            placements.append(catalog_module.Placement(marker, px, py, 0.0, 0.0, 0.0, 1.0, -1))
        x += item.along / 2 + 2.0
    document = park.build_park(placements, anchor.map or "bam", anchor)
    written = park.write_mod(args.mods, args.name, document, args.name, "", "1.0.0",
                             "Each catalog object between two measuring sticks at its expected length.")
    print(f"Wrote {written}: {len(cat.items)} objects in a {x:.0f} m row along the marker's facing.")
    return 0


def run_synth(args) -> int:
    from . import synth
    points, truth, _ = synth.scan(seed=args.seed)
    synth.write_ply(args.output, points)
    print(f"Wrote {args.output}: {len(points):,} points, Y up, with:")
    for t in truth:
        print(f"  {t.kind:<10} {t.along:.2f} x {t.across:.2f} m, {t.height:.2f} m high")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
