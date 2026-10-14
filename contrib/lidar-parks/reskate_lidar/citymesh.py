"""A city model as meshes for a true custom map (CUSTOM-MAPS.md, route 2), ready for ReSkate
Studio's Blender add-on.

Two GLB files, in glTF's own frame: metres, +Y up, +X east and -Z north (right-handed, as every
glTF reader expects). Blender's importer turns that into its Z-up frame with east and north the
right way round, and Studio takes the map from Blender to the game. The origin is the area's
centre, on the ground.
- <name>.glb: the map. The ground in tiles (`tile` metres square, one node each), the buildings,
  the skate features as solid blocks, grind curves, and a `spawn` node.
- <name>-collision.glb: a lighter copy (ground at half the resolution, simplified buildings) for
  when the full ground is too many collision triangles.

Each node carries what Studio's add-on reads as object properties (Blender's glTF importer turns
node extras into custom properties): `sk8_collision_mode` ("triangle_mesh"; "none" for grind
curves), and for grind curves `sk8_grind_curve`. The surface each part should have is in
`reskate_surface` (concrete, metal, wood, asphalt) and its grind radius in `reskate_grind_radius`,
for the converter (studio_prep.py) to map onto Studio's own surface presets. The names Studio uses
are from public sources (CUSTOM-MAPS.md) and must be checked against Studio itself."""
from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .city import CityModel, Grid, Spot

SURFACE = {"stairs": "concrete", "ledge": "concrete", "manual_pad": "concrete", "box": "concrete",
           "bank": "concrete", "kicker": "concrete", "quarter": "concrete", "rail": "metal"}


@dataclass
class Grind:
    """A grind curve: points (n, 3) in the local frame (x east, y north, z elevation)."""
    points: np.ndarray
    radius: float = 0.04
    surface: str = "concrete"
    what: str = ""
    closed: bool = False


def to_gltf(points: np.ndarray, origin_elevation: float) -> np.ndarray:
    """Local (x east, y north, z elevation) to glTF (x east, y up, z south): a rotation, so no
    face is turned inside out."""
    points = np.asarray(points, float)
    return np.column_stack([points[:, 0], points[:, 2] - origin_elevation, -points[:, 1]])


def ground_mesh(grid: Grid, origin_elevation: float, rows: slice = slice(None), cols: slice = slice(None),
                step: int = 1, keep: np.ndarray | None = None):
    """The grid as triangles, faces up. `keep` (a mask on the grid) drops cells outside it."""
    import trimesh
    heights = grid.heights[rows, cols][::step, ::step]
    r0 = rows.start or 0
    c0 = cols.start or 0
    n_rows, n_cols = heights.shape
    if n_rows < 2 or n_cols < 2:
        return None
    rr, cc = np.mgrid[0:n_rows, 0:n_cols]
    x = grid.origin[0] + (c0 + cc * step) * grid.cell
    y = grid.origin[1] + (r0 + rr * step) * grid.cell
    vertices = to_gltf(np.column_stack([x.ravel(), y.ravel(), heights.ravel()]), origin_elevation)
    index = np.arange(n_rows * n_cols).reshape(n_rows, n_cols)
    quads = np.ones((n_rows - 1, n_cols - 1), bool)
    if keep is not None:
        k = keep[rows, cols][::step, ::step]
        quads = k[:-1, :-1] | k[1:, :-1] | k[:-1, 1:] | k[1:, 1:]
    a, b = index[:-1, :-1][quads], index[:-1, 1:][quads]
    d, e = index[1:, :-1][quads], index[1:, 1:][quads]
    if not len(a):
        return None
    # East then north is counter-clockwise from above: normals up.
    faces = np.concatenate([np.column_stack([a, b, d]), np.column_stack([b, e, d])])
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    mesh.remove_unreferenced_vertices()
    return mesh


def _box(centre_xy, yaw, along, across, bottom, top, pitch: float = 0.0, origin_elevation: float = 0.0):
    """A block in the local frame, as a glTF mesh. pitch tilts it along yaw about its centre."""
    import trimesh
    height = max(top - bottom, 0.02)
    mesh = trimesh.creation.box(extents=(along, across, height))
    mesh.apply_translation((0, 0, bottom + height / 2))
    if pitch:
        mid = bottom + height / 2
        mesh.apply_translation((0, 0, -mid))
        mesh.apply_transform(trimesh.transformations.rotation_matrix(-pitch, (0, 1, 0)))
        mesh.apply_translation((0, 0, mid))
    mesh.apply_transform(trimesh.transformations.rotation_matrix(yaw, (0, 0, 1)))
    mesh.apply_translation((centre_xy[0], centre_xy[1], 0))
    return trimesh.Trimesh(vertices=to_gltf(mesh.vertices, origin_elevation), faces=mesh.faces, process=False)


def _wedge(spot: Spot, origin_elevation: float):
    """A planar bank: rising along the feature's yaw from its ground to its height."""
    import trimesh
    f = spot.feature
    u = np.array([math.cos(f.yaw), math.sin(f.yaw)])
    v = np.array([-u[1], u[0]])
    c = np.array(f.centre)
    low, high = spot.ground, spot.ground + f.height
    a, b = f.along / 2, f.across / 2
    foot = [c - a * u - b * v, c - a * u + b * v]
    head = [c + a * u - b * v, c + a * u + b * v]
    points = np.array([[*foot[0], low], [*foot[1], low], [*head[0], low], [*head[1], low],
                       [*head[0], high], [*head[1], high]])
    hull = trimesh.Trimesh(vertices=points).convex_hull
    return trimesh.Trimesh(vertices=to_gltf(hull.vertices, origin_elevation), faces=hull.faces, process=False)


def _stairs(spot: Spot, origin_elevation: float):
    """A flight as one closed solid: its sawtooth profile (rising along the feature's yaw, from
    the footing to each step's top) extruded across its width. One shell, so every edge has
    exactly two faces, as a collision shape's half-edges need."""
    import shapely
    import trimesh
    f = spot.feature
    steps = max(f.steps, 2)
    tread, riser = f.along / steps, f.height / steps
    a = f.along / 2
    profile = [(-a, spot.footing)]
    for k in range(steps):
        profile += [(-a + k * tread, spot.ground + riser * (k + 1)), (-a + (k + 1) * tread, spot.ground + riser * (k + 1))]
    profile.append((a, spot.footing))
    solid = trimesh.creation.extrude_polygon(shapely.Polygon(profile), f.across)
    # Profile x along the flight, profile y up, extrusion across it towards -v: a proper
    # rotation of (along, across, up), so the faces stay outward.
    u = np.array([math.cos(f.yaw), math.sin(f.yaw)])
    v = np.array([-u[1], u[0]])
    s_, h, w = solid.vertices.T
    xy = np.array(f.centre) + np.outer(s_, u) - np.outer(w - f.across / 2, v)
    local = np.column_stack([xy, h])
    return trimesh.Trimesh(vertices=to_gltf(local, origin_elevation), faces=solid.faces, process=False)


def spot_mesh(spot: Spot, origin_elevation: float):
    import trimesh
    f = spot.feature
    if f.kind == "stairs":
        return _stairs(spot, origin_elevation)
    if f.kind == "rail":
        # A bar on posts, its top at the feature's height.
        bar = _box(f.centre, f.yaw, f.along, 0.05, spot.ground + f.height - 0.05, spot.ground + f.height,
                   pitch=f.pitch, origin_elevation=origin_elevation)
        posts = []
        u = np.array([math.cos(f.yaw), math.sin(f.yaw)])
        horizontal = f.along * math.cos(f.pitch)
        for t in (-0.4, 0.4):
            at = np.array(f.centre) + u * horizontal * t
            top = spot.ground + f.height + horizontal * t * math.tan(f.pitch) - 0.05
            floor = spot.ground + (horizontal * t + horizontal / 2) * math.tan(f.pitch) if f.pitch else spot.ground
            posts.append(_box(at, f.yaw, 0.05, 0.05, min(floor, top - 0.1), top, origin_elevation=origin_elevation))
        return trimesh.util.concatenate([bar] + posts)
    if f.kind in ("bank", "kicker", "quarter"):
        return _wedge(spot, origin_elevation)
    return _box(f.centre, f.yaw, f.along, f.across, spot.footing, spot.ground + f.height,
                origin_elevation=origin_elevation)


def spot_grinds(spot: Spot) -> list[Grind]:
    """Where a spot is ground: a ledge's two top edges, a rail's bar."""
    f = spot.feature
    u = np.array([math.cos(f.yaw), math.sin(f.yaw)])
    v = np.array([-u[1], u[0]])
    c = np.array(f.centre)
    if f.kind == "rail":
        half = f.along / 2 * math.cos(f.pitch)
        rise = f.along / 2 * math.sin(f.pitch)
        top = spot.ground + f.height
        points = np.array([[*(c - u * half), top - rise], [*(c + u * half), top + rise]])
        return [Grind(points, 0.03, "metal", spot.what)]
    if f.kind in ("ledge", "manual_pad", "box"):
        top = spot.ground + f.height
        a = f.along / 2
        surface = "wood" if spot.what == "bench" else "concrete"
        return [Grind(np.array([[*(c - a * u + side * f.across / 2 * v), top], [*(c + a * u + side * f.across / 2 * v), top]]),
                      0.04, surface, spot.what) for side in (-1, 1)]
    return []


def building_mesh(model: CityModel, blocks_only: bool = False):
    import trimesh
    parts = []
    for b in model.buildings:
        polygons = list(b.footprint.geoms) if b.footprint.geom_type == "MultiPolygon" else [b.footprint]
        for polygon in polygons:
            if blocks_only:
                polygon = polygon.simplify(0.5)
            try:
                mesh = trimesh.creation.extrude_polygon(polygon, b.height + 1.0)
            except Exception:  # a footprint the triangulator refuses: its convex hull still stands in
                mesh = trimesh.creation.extrude_polygon(polygon.convex_hull, b.height + 1.0)
            # Sunk 1 m so the walls meet sloping ground.
            mesh.apply_translation((0, 0, b.base - 1.0))
            parts.append(trimesh.Trimesh(vertices=to_gltf(mesh.vertices, model.origin_elevation),
                                         faces=mesh.faces, process=False))
    return trimesh.util.concatenate(parts) if parts else None


def _ground_for_mesh(model: CityModel) -> Grid:
    """The ground with each flight of steps cut back to its foot, so the steps themselves are the surface."""
    import shapely
    from .city import rasterise
    heights = model.ground.heights.copy()
    grid = Grid(heights, model.ground.origin, model.ground.cell)
    for spot in model.spots:
        if spot.feature.kind == "stairs":
            inside = rasterise(shapely.Polygon(spot.feature.footprint()), grid)
            heights[inside] = np.minimum(heights[inside], spot.ground)
    return grid


def scene(model: CityModel, collision: bool = False, tile: float = 64.0):
    """The scene, and the extras for each node by name."""
    import trimesh
    scene = trimesh.Scene()
    extras: dict[str, dict] = {}
    grid = _ground_for_mesh(model)
    keep = getattr(model, "ground_keep", None)
    surface = getattr(model, "ground_surface", "asphalt")
    step = 2 if collision else 1
    per_tile = max(2, int(round(tile / grid.cell)))
    rows, cols = grid.shape

    def add(mesh, name, part_extras):
        scene.add_geometry(mesh, node_name=name, geom_name=name)
        extras[name] = part_extras

    for i, r in enumerate(range(0, rows - 1, per_tile)):
        for j, c in enumerate(range(0, cols - 1, per_tile)):
            # Tiles share their edge row and column, so there are no cracks between them.
            mesh = ground_mesh(grid, model.origin_elevation, slice(r, min(r + per_tile + 1, rows)),
                               slice(c, min(c + per_tile + 1, cols)), step, keep)
            if mesh is not None:
                add(mesh, f"ground_{i}_{j}", {"sk8_collision_mode": "triangle_mesh", "reskate_surface": surface,
                                              "reskate_part": "ground"})
    buildings = building_mesh(model, blocks_only=collision)
    if buildings is not None:
        add(buildings, "buildings", {"sk8_collision_mode": "triangle_mesh", "reskate_surface": "concrete",
                                     "reskate_part": "buildings"})
    grinds: list[Grind] = list(getattr(model, "grinds", []))
    for n, spot in enumerate(model.spots):
        part_surface = "wood" if spot.what == "bench" else SURFACE.get(spot.feature.kind, "concrete")
        add(spot_mesh(spot, model.origin_elevation), f"spot_{n}_{spot.feature.kind}",
            {"sk8_collision_mode": "triangle_mesh", "reskate_surface": part_surface, "reskate_part": spot.what,
             "reskate_source": spot.source})
        grinds += spot_grinds(spot)
    if not collision:
        for n, grind in enumerate(grinds):
            points = to_gltf(grind.points, model.origin_elevation)
            if grind.closed:
                points = np.vstack([points, points[:1]])
            add(trimesh.load_path(points), f"grind_{n}",
                {"sk8_collision_mode": "none", "sk8_grind_curve": True, "reskate_grind_radius": grind.radius,
                 "reskate_surface": grind.surface, "reskate_part": grind.what or "grind"})
    return scene, extras


def triangle_count(s) -> int:
    return sum(len(g.faces) for g in s.geometry.values() if hasattr(g, "faces"))


def spawn_point(model: CityModel) -> np.ndarray:
    """Where a skater starts: the model's spawn, else the origin, on the ground."""
    spawn = getattr(model, "spawn", None)
    xy = np.array(spawn if spawn is not None else (0.0, 0.0), float)
    return np.array([xy[0], xy[1], float(model.ground.sample(xy)[0]) + 0.1])


def _patch_glb(data: bytes, extras: dict[str, dict], spawn: np.ndarray | None) -> bytes:
    """Node extras by name, and an empty `spawn` node, written into a GLB's JSON chunk."""
    magic, version, _ = struct.unpack_from("<III", data, 0)
    length, kind = struct.unpack_from("<II", data, 12)
    document = json.loads(data[20:20 + length])
    rest = data[20 + length:]
    for node in document.get("nodes", []):
        if node.get("name") in extras:
            node["extras"] = extras[node["name"]]
    if spawn is not None:
        document.setdefault("nodes", []).append({"name": "spawn", "translation": [float(v) for v in spawn],
                                                 "extras": {"reskate_part": "spawn"}})
        scene = document["scenes"][document.get("scene", 0)]
        scene.setdefault("nodes", []).append(len(document["nodes"]) - 1)
    text = json.dumps(document, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), kind) + text + rest
    return struct.pack("<III", magic, version, 12 + len(body)) + body


def write(model: CityModel, path: str | Path, tile: float = 64.0) -> dict:
    """<path> (the map) and <path stem>-collision<suffix>; returns their triangle and grind counts."""
    path = Path(path)
    spawn = to_gltf(spawn_point(model)[None, :], model.origin_elevation)[0]
    info = {}
    for collision, target in ((False, path), (True, path.with_name(f"{path.stem}-collision{path.suffix}"))):
        built, extras = scene(model, collision=collision, tile=tile)
        target.write_bytes(_patch_glb(built.export(file_type="glb"), extras, spawn))
        key = "collision" if collision else "visual"
        info[key] = str(target)
        info[f"{key}_triangles"] = triangle_count(built)
        info[f"{key}_parts"] = sum(1 for e in extras.values() if e.get("sk8_collision_mode") == "triangle_mesh")
        if not collision:
            info["grinds"] = sum(1 for e in extras.values() if e.get("sk8_grind_curve"))
    return info
