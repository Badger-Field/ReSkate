"""Command line: python -m reskate_lidar <convert|city|imagery|anchor|calibrate|mesh|synth> ..."""
from __future__ import annotations

import argparse
import json
import math
import re
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

    city = commands.add_parser("city", help="a real place (plaza, block) from open map data and LiDAR, "
                                             "as a park mod and/or a mesh for a custom map")
    where = city.add_mutually_exclusive_group(required=True)
    where.add_argument("--place", help="a place name, looked up with OpenStreetMap's Nominatim")
    where.add_argument("--centre", help="latitude,longitude of the middle of the area")
    where.add_argument("--bbox", help="west,south,east,north in degrees")
    city.add_argument("--size", type=float, default=160.0,
                      help="width of the square around --place or --centre, metres (default 160)")
    city.add_argument("--lidar", default="auto", help="auto (USGS 3DEP), none, or an EPT url or folder (ept.json)")
    city.add_argument("--max-points", type=float, default=30e6, help="read at most about this many LiDAR points; "
                                                                      "bigger areas are read sparser (default 30 million)")
    city.add_argument("--sources", default="osm,overture", help="map data: osm, overture, or both (default)")
    city.add_argument("--overpass", help="Overpass API url, or a saved Overpass JSON answer")
    city.add_argument("--overture-release", help="e.g. 2026-09-23.1 (default: the latest)")
    city.add_argument("--cache", help="download cache folder (default ~/.cache/reskate-lidar)")
    city.add_argument("--ground-cell", type=float, default=0.5, help="ground grid, metres (default 0.5)")
    city.add_argument("--anchor", help="a park file with one measuring stick where the area's centre goes")
    city.add_argument("--map", help="base map key (default: the anchor's map, else bam)")
    city.add_argument("--mods", help="the game's Mods folder to write the park mod into")
    city.add_argument("--park", help="write only a .park.json here")
    city.add_argument("--name", default="", help="mod name (default: from the place)")
    city.add_argument("--author", default="")
    city.add_argument("--version", default="1.0.0")
    city.add_argument("--max-objects", type=int, default=park.DEFAULT_DROPPER_LIMIT,
                      help=f"Build Kit object budget (default {park.DEFAULT_DROPPER_LIMIT}, at most {park.MAX_OBJECTS_PER_MAP})")
    city.add_argument("--keep-elevation", action="store_true",
                      help="place spots at their real heights (default: each on the base map's ground)")
    city.add_argument("--turn", type=float, default=0.0, help="turn the whole place (degrees, counter-clockwise)")
    city.add_argument("--mesh", help="also write the place as a mesh for a custom map (.glb; a -collision.glb beside it)")
    city.add_argument("--tile", type=float, default=64.0, help="ground mesh tile size, metres (default 64)")
    city.add_argument("--skatepark", action="store_true",
                      help="just the mapped skatepark at the centre, as its real shape (bowls, coping) for a "
                           "custom map: needs --mesh; no park mod")
    city.add_argument("--park-cell", type=float, default=0.25, help="--skatepark surface grid, metres (default 0.25)")
    city.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    city.add_argument("--preview", help="write a top-down PNG of the place")
    city.add_argument("--report", help="write everything found as JSON")

    imagery = commands.add_parser("imagery", help="openly licensed street-level imagery around a place: "
                                                     "how much, how recent, which licence (metadata only)")
    imagery.add_argument("--centre", required=True, help="latitude,longitude")
    imagery.add_argument("--radius", type=float, default=100.0, help="metres (default 100)")
    imagery.add_argument("--verbose", action="store_true",
                         help="each service's request (no tokens), HTTP status and the count field it read")
    imagery.add_argument("--report", help="write the coverage as JSON")

    anchor = commands.add_parser("anchor", help="is a park's anchor on open, flat ground? probe the base map "
                                                   "in game with `trainer ground`, then read the heights back")
    anchor_steps = anchor.add_subparsers(dest="step", required=True)
    probe = anchor_steps.add_parser("probe", help="the probes over the park's footprint, as startup commands")
    probe.add_argument("--park", required=True, help="the .park.json to check")
    probe.add_argument("--spacing", type=float, default=10.0, help="metres between probes (default 10; "
                                                                    "widened to keep within --limit)")
    probe.add_argument("--limit", type=int, default=100, help="most probes (default 100)")
    probe.add_argument("--out", help="also write the console lines here, one per probe")
    check = anchor_steps.add_parser("check", help="read the probes' heights from ReSkate.log")
    check.add_argument("--park", required=True, help="the .park.json that was probed")
    check.add_argument("--log", required=True, help="ReSkate.log from the probing run")
    check.add_argument("--tolerance", type=float, default=0.3, help="metres from the park's ground still "
                                                                    "counted level (default 0.3)")
    check.add_argument("--report", help="write the verdict as JSON")

    synth = commands.add_parser("synth", help="write a synthetic park scan for trying the tool")
    synth.add_argument("output", help=".ply file to write")
    synth.add_argument("--seed", type=int, default=0)

    args = parser.parse_args(argv)
    try:
        return {"convert": run_convert, "city": run_city, "imagery": run_imagery, "anchor": run_anchor, "calibrate": run_calibrate, "mesh": run_mesh,
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


MAX_AREA_KM2 = 4.0


def _city_area(args, cache):
    from . import geo, osm
    label = args.place or args.centre or args.bbox
    if args.bbox:
        bbox = geo.parse_bbox(args.bbox)
    else:
        if args.place:
            found = osm.geocode(args.place, cache)
            print(f"{args.place}: {found['display_name']}")
            lat, lon = found["lat"], found["lon"]
        else:
            lat, lon = geo.parse_latlon(args.centre)
        bbox = geo.bbox_around(lat, lon, args.size)
    frame = geo.LocalFrame(*bbox.centre)
    bounds = frame.bounds(bbox)
    area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) / 1e6
    if area > MAX_AREA_KM2:
        raise ValueError(f"The area is {area:.1f} km²; keep to {MAX_AREA_KM2:.0f} km² a run "
                         "(the map servers' limits, and the game's object budget)")
    return label, bbox, frame, bounds


def _city_lidar(args, bbox, frame, bounds, cache):
    from . import ept
    if args.lidar == "none":
        return None, None
    url = args.lidar
    survey = None
    if url == "auto":
        surveys = ept.find_surveys(bbox, cache)
        if not surveys:
            print("  no USGS 3DEP survey covers this area; going on without LiDAR "
                  "(pass --lidar with another EPT dataset)", file=sys.stderr)
            return None, None
        survey = surveys[0]
        url = survey["url"]
        print(f"  LiDAR: USGS 3DEP {survey['name']}")
    reader = ept.EptReader(url, cache)
    area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
    # Aerial surveys run up to ~150 points/m²; past the budget, read a shallower octree.
    max_depth = None
    if area * 150 > args.max_points:
        spacing = math.sqrt(area / args.max_points)
        max_depth = reader.depth_for(spacing, bbox.centre[0])
        print(f"  large area: LiDAR read to about {spacing:.2f} m spacing")
    points = reader.read(bbox, frame, max_depth=max_depth, progress=print)
    print(f"  {len(points.xyz):,} LiDAR points")
    return points, (survey["name"] if survey else url)


def attribution_lines(sources: set[str], survey: str | None, usgs: bool = True) -> list[str]:
    from . import osm, overture
    lines = []
    if "osm" in sources or "overture" in sources:
        lines.append(f"Map data {osm.ATTRIBUTION}.")
    if "overture" in sources:
        lines.append(f"Map data from {overture.ATTRIBUTION}.")
    if "lidar" in sources and usgs:
        lines.append("Elevation: U.S. Geological Survey 3D Elevation Program (3DEP) LiDAR"
                     + (f", survey {survey}" if survey else "") + "; public domain.")
    elif "lidar" in sources:
        lines.append(f"Elevation: LiDAR point cloud {survey}; credit it as its licence asks (DATA-SOURCES.md).")
    return lines


def run_city(args) -> int:
    from . import city as city_module, mapdata, net, osm, overture, preview
    cache = net.cache_dir(args.cache)
    if not 1 <= args.max_objects <= park.MAX_OBJECTS_PER_MAP:
        raise ValueError(f"--max-objects: 1 to {park.MAX_OBJECTS_PER_MAP}")
    label, bbox, frame, bounds = _city_area(args, cache)
    print(f"Area {bounds[2] - bounds[0]:.0f} x {bounds[3] - bounds[1]:.0f} m around "
          f"{bbox.centre[0]:.6f}, {bbox.centre[1]:.6f}")

    items = []
    wanted = {s.strip() for s in args.sources.split(",") if s.strip()}
    if not wanted <= {"osm", "overture"}:
        raise ValueError("--sources: osm, overture, or osm,overture")
    if "osm" in wanted:
        try:
            found = osm.fetch(bbox, cache, args.overpass)
            print(f"  OpenStreetMap: {len(found)} items")
            items += found
        except OSError as error:
            print(f"  OpenStreetMap not reachable ({error}); going on without it", file=sys.stderr)
    if "overture" in wanted:
        try:
            items += overture.fetch(bbox, cache, args.overture_release, progress=print)
        except (OSError, ImportError) as error:
            print(f"  Overture not reachable ({error}); going on without it", file=sys.stderr)
    if not items:
        raise ValueError("No map data for the area (see the messages above)")
    items = mapdata.localise(items, frame)
    lidar, survey = _city_lidar(args, bbox, frame, bounds, cache)

    if args.turn:
        import shapely
        t = math.radians(args.turn)
        rotation = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
        for item in items:
            item.geometry = shapely.transform(item.geometry, lambda xy: xy @ rotation.T)
        if lidar is not None:
            lidar.xyz[:, :2] = lidar.xyz[:, :2] @ rotation.T
        corners = np.array([[bounds[0], bounds[1]], [bounds[2], bounds[1]], [bounds[2], bounds[3]], [bounds[0], bounds[3]]]) @ rotation.T
        bounds = (*corners.min(axis=0), *corners.max(axis=0))

    if args.skatepark:
        return _run_skatepark(args, label, bbox, items, lidar, survey)

    print("Building the model")
    model = city_module.build(bounds, items, lidar, city_module.Settings(ground_cell=args.ground_cell), progress=print)
    attribution = attribution_lines(model.sources, survey, usgs=args.lidar == "auto")

    print(f"\n{len(model.spots)} skate spots:")
    for index, spot in enumerate(model.spots):
        f = spot.feature
        print(f"  #{index:<3} {f.kind:<10} {f.along:5.1f} x {f.across:4.1f} m, {f.height:4.2f} m high  "
              f"{spot.what} ({spot.source}) at ({f.centre[0]:.0f}, {f.centre[1]:.0f})")
    for note in model.notes:
        print(f"  note: {note}")

    cat = catalog_module.load_catalog(args.catalog)
    chosen, placements, skipped = city_module.park_features(model, args.max_objects, cat, args.keep_elevation)
    print(f"\nPlacing {len(placements)} Build Kit objects for {len(chosen)} spots")
    for line in skipped:
        print(f"  skipped: {line}")

    name = args.name or re.sub(r"[^A-Za-z0-9 _\-.]", "", (args.place or "City block").split(",")[0]).strip()[:64] or "City block"
    if args.park or args.mods:
        anchor = park.read_anchor(args.anchor, cat.marker) if args.anchor else park.Anchor()
        map_name = args.map or anchor.map or "bam"
        document = park.build_park(placements, map_name, anchor)
        written = len(document["maps"][map_name])
        if written < len(placements):
            print(f"note: {len(placements) - written} placements were exact duplicates and were written once")
        if not args.anchor:
            print("note: no --anchor, so the place sits at the map's origin; see README for placing it")
        if args.park:
            Path(args.park).write_text(park.encode(document), encoding="utf-8")
            print(f"Wrote {args.park}")
        if args.mods:
            description = (f"{name}, built from open data with {len(placements)} Build Kit objects. "
                           + " ".join(attribution))[:512]
            written = park.write_mod(args.mods, name, document, name, args.author, args.version, description)
            readme = written.parent.parent / "README.md"
            readme.write_text(city_readme(name, label, bbox, model, placements, attribution), encoding="utf-8")
            print(f"Wrote {written}")

    mesh_info = None
    if args.mesh:
        from . import citymesh
        mesh_info = citymesh.write(model, args.mesh, tile=args.tile)
        print(f"Wrote {mesh_info['visual']} ({mesh_info['visual_triangles']:,} triangles) and "
              f"{mesh_info['collision']} ({mesh_info['collision_triangles']:,} triangles)")
        Path(args.mesh).with_suffix(".txt").write_text("\n".join(attribution) + "\n", encoding="utf-8")
    if args.preview:
        preview.render_city(model, placements, args.preview)
        print(f"Wrote {args.preview}")
    if args.report:
        Path(args.report).write_text(json.dumps(city_report(label, bbox, model, placements, skipped, attribution,
                                                            mesh_info), indent=2, default=float), encoding="utf-8")
        print(f"Wrote {args.report}")
    print("\n" + "\n".join(attribution))
    return 0


def _run_skatepark(args, label, bbox, items, lidar, survey) -> int:
    from . import citymesh, preview, skatepark
    if lidar is None:
        raise ValueError("--skatepark needs LiDAR")
    print("Building the skatepark")
    park_result = skatepark.build(items, lidar, cell=args.park_cell)
    model = park_result.model
    attribution = attribution_lines({"osm", "overture", "lidar"} & ({i.source for i in items} | {"lidar"}), survey,
                                    usgs=args.lidar == "auto")
    print(f"  park {park_result.outline.area:,.0f} m², deck at {park_result.deck:.2f} m, "
          f"{park_result.density:.1f} ground points/m², {park_result.removed_above:,} points above the ground dropped")
    for n, bowl in enumerate(park_result.bowls):
        print(f"  bowl {n}: {bowl.outline.area:5.0f} m², {bowl.depth:.2f} m deep, {bowl.density:4.1f} points/m², "
              f"walls to {bowl.steepest:.0f} degrees, coping {bowl.outline.exterior.length:.0f} m"
              + ("" if bowl.from_map else " (found in the ground)"))
    for note in park_result.notes:
        print(f"  note: {note}")
    info = None
    if args.mesh:
        info = citymesh.write(model, args.mesh, tile=args.tile)
        print(f"Wrote {info['visual']} ({info['visual_triangles']:,} triangles, {info['grinds']} grind curves) and "
              f"{info['collision']} ({info['collision_triangles']:,} triangles)")
        Path(args.mesh).with_suffix(".txt").write_text("\n".join(attribution) + "\n", encoding="utf-8")
    else:
        print("(no mesh written: pass --mesh park.glb)")
    if args.preview:
        preview.render_city(model, [], args.preview)
        print(f"Wrote {args.preview}")
    if args.report:
        Path(args.report).write_text(json.dumps({
            "place": label, "bbox": [bbox.west, bbox.south, bbox.east, bbox.north],
            "origin": {"lat": bbox.centre[0], "lon": bbox.centre[1], "deck_elevation": park_result.deck},
            "frame": "glTF: x east, y up, z south (metres); deck at y = 0",
            "park_area": park_result.outline.area, "density": park_result.density,
            "points_above_ground_dropped": park_result.removed_above,
            "bowls": [{"area": b.outline.area, "depth": b.depth, "rim": b.rim, "floor": b.floor,
                       "points_per_m2": b.density, "steepest_degrees": b.steepest, "from_map": b.from_map,
                       "coping_length": b.outline.exterior.length} for b in park_result.bowls],
            "spawn": model.spawn, "notes": park_result.notes, "mesh": info, "attribution": attribution,
        }, indent=2, default=float), encoding="utf-8")
        print(f"Wrote {args.report}")
    print("\n" + "\n".join(attribution))
    return 0


def city_report(label, bbox, model, placements, skipped, attribution, mesh_info) -> dict:
    return {
        "place": label,
        "bbox": [bbox.west, bbox.south, bbox.east, bbox.north],
        "origin": {"lat": bbox.centre[0], "lon": bbox.centre[1], "elevation": model.origin_elevation},
        "frame": "x east, y north, metres from the origin; in game x, y up, z north",
        "buildings": [{"centre": [b.footprint.centroid.x, b.footprint.centroid.y], "area": b.footprint.area,
                       "base": b.base, "height": b.height, "height_from": b.height_from, "source": b.source}
                      for b in model.buildings],
        "spots": [{**{k: v for k, v in s.feature.__dict__.items()}, "yaw_degrees": math.degrees(s.feature.yaw),
                   "ground": s.ground, "source": s.source, "what": s.what} for s in model.spots],
        "placements": [{"item": p.item.item, "x": p.x, "y": p.y, "z": p.z, "yaw_degrees": math.degrees(p.yaw),
                        "pitch_degrees": math.degrees(p.pitch), "scale": p.scale, "spot": p.feature}
                       for p in placements],
        "skipped": skipped, "notes": model.notes, "mesh": mesh_info, "attribution": attribution,
    }


def city_readme(name, label, bbox, model, placements, attribution) -> str:
    kinds: dict[str, int] = {}
    for s in model.spots:
        kinds[s.feature.kind] = kinds.get(s.feature.kind, 0) + 1
    found = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "nothing"
    return (f"# {name}\n\n"
            f"A real place in skate., made by ReSkate's `reskate_lidar city` from open data: {label} "
            f"({bbox.centre[0]:.5f}, {bbox.centre[1]:.5f}).\n\n"
            f"Skate spots: {found}, as {len(placements)} Build Kit objects.\n\n"
            "## Attribution\n\n" + "\n".join(f"- {line}" for line in attribution) + "\n\n"
            "OpenStreetMap data is available under the Open Database License (ODbL 1.0): "
            "https://www.openstreetmap.org/copyright. This park is a Produced Work from it.\n")


def run_imagery(args) -> int:
    from . import geo, imagery
    lat, lon = geo.parse_latlon(args.centre)
    if not 1 <= args.radius <= 2000:
        raise ValueError("--radius: 1 to 2000 metres")
    found = imagery.survey(lat, lon, args.radius)
    print(f"Street-level imagery within {args.radius:.0f} m of {lat:.6f}, {lon:.6f}:")
    for coverage in found:
        print(f"  {coverage.summary()}")
        if args.verbose:
            print(coverage.details())
    if args.report:
        Path(args.report).write_text(json.dumps([c.__dict__ for c in found], indent=2), encoding="utf-8")
        print(f"Wrote {args.report}")
    return 0 if all(c.outcome != "failed" for c in found) else 1


def run_anchor(args) -> int:
    from . import anchorcheck
    footprint = anchorcheck.read_park(args.park)
    if args.step == "probe":
        if args.spacing <= 0 or args.limit < 2:
            raise ValueError("--spacing must be positive and --limit at least 2")
        points = anchorcheck.probe_points(footprint, args.spacing, limit=args.limit)
        commands = anchorcheck.startup_commands(points)
        print(f"{len(points)} probes over {len(footprint.items)} objects on {footprint.map}. Load {footprint.map} "
              "without the park, then either set this before starting the game:")
        print(f"  RESKATE_STARTUP_COMMANDS={commands}")
        print("or type each line in the console (the park need not be loaded):")
        lines = [f"trainer ground {x:.1f} {z:.1f}" for x, z in points]
        for line in lines:
            print(f"  {line}")
        if args.out:
            Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"Wrote {args.out}")
        print(f"Then: python -m reskate_lidar anchor check --park {args.park} --log <ReSkate.log>")
        return 0
    heights, missing = anchorcheck.read_heights(Path(args.log).read_text(encoding="utf-8", errors="replace"))
    verdict = anchorcheck.check(footprint, heights, missing, args.tolerance)
    print(f"{args.park}: ground {footprint.base:.1f} on {footprint.map}")
    print(verdict.summary(args.tolerance))
    if args.report:
        Path(args.report).write_text(json.dumps(verdict.__dict__ | {"level_share": verdict.level_share}, indent=2),
                                     encoding="utf-8")
        print(f"Wrote {args.report}")
    return 0 if verdict.good else 1


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
