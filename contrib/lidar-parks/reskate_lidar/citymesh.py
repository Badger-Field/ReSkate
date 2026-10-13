"""A city model as meshes for a true custom map (CUSTOM-MAPS.md, route 2).

Two GLB files, both in the game's frame (metres, x east, y up, z north, the origin at the area's
centre on the ground):
- <name>.glb: what is drawn. The ground in tiles (`tile` metres square, one node each, so a level
  can stream or cull them), the buildings, and the skate features as solid blocks.
- <name>-collision.glb: what is ridden. The same shapes, but the ground at a coarser grid and
  the buildings as their footprints' blocks; this is the copy to give the level's physics.
Node names say what each part is (ground_<i>_<j>, buildings, spot_<n>_<kind>), and the extras
of each node carry its surface ("concrete", "wood", "metal", "asphalt"), for when the level
builder learns to set surface types from them."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .city import CityModel, Grid, Spot

SURFACE = {"stairs": "concrete", "ledge": "concrete", "manual_pad": "concrete", "box": "concrete",
           "bank": "concrete", "kicker": "concrete", "quarter": "concrete", "rail": "metal"}


def to_game(points: np.ndarray, origin_elevation: float) -> np.ndarray:
    """Local (x east, y north, z elevation) to game (x, y up, z): as park.py and meshing.py do."""
    return np.column_stack([points[:, 0], points[:, 2] - origin_elevation, points[:, 1]])


def ground_mesh(grid: Grid, origin_elevation: float, rows: slice = slice(None), cols: slice = slice(None),
                step: int = 1):
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
    vertices = to_game(np.column_stack([x.ravel(), y.ravel(), heights.ravel()]), origin_elevation)
    index = np.arange(n_rows * n_cols).reshape(n_rows, n_cols)
    a, b = index[:-1, :-1].ravel(), index[:-1, 1:].ravel()
    d, e = index[1:, :-1].ravel(), index[1:, 1:].ravel()
    faces = np.concatenate([np.column_stack([a, d, b]), np.column_stack([b, d, e])])  # normals up (+y)
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


def _box(centre_xy, yaw, along, across, bottom, top, pitch: float = 0.0, origin_elevation: float = 0.0):
    """A block in the local frame, as a game-frame mesh. pitch tilts it along yaw about its centre."""
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
    return trimesh.Trimesh(vertices=to_game(mesh.vertices, origin_elevation), faces=mesh.faces[:, ::-1], process=False)


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
    return trimesh.Trimesh(vertices=to_game(hull.vertices, origin_elevation), faces=hull.faces[:, ::-1], process=False)


def spot_mesh(spot: Spot, origin_elevation: float):
    import trimesh
    f = spot.feature
    if f.kind == "stairs":
        u = np.array([math.cos(f.yaw), math.sin(f.yaw)])
        steps = max(f.steps, 2)
        tread, riser = f.along / steps, f.height / steps
        parts = []
        for k in range(steps):
            # Step k covers its own tread up to the top of the flight, so the flight is solid.
            start = -f.along / 2 + k * tread
            centre = np.array(f.centre) + u * (start + (f.along / 2 - start) / 2)
            parts.append(_box(centre, f.yaw, f.along / 2 - start, f.across, spot.footing,
                              spot.ground + riser * (k + 1), origin_elevation=origin_elevation))
        return trimesh.util.concatenate(parts)
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
            parts.append(trimesh.Trimesh(vertices=to_game(mesh.vertices, model.origin_elevation),
                                         faces=mesh.faces[:, ::-1], process=False))
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
    import trimesh
    scene = trimesh.Scene()
    grid = _ground_for_mesh(model)
    step = 2 if collision else 1
    per_tile = max(2, int(round(tile / grid.cell)))
    rows, cols = grid.shape
    for i, r in enumerate(range(0, rows - 1, per_tile)):
        for j, c in enumerate(range(0, cols - 1, per_tile)):
            # Tiles share their edge row and column, so there are no cracks between them.
            mesh = ground_mesh(grid, model.origin_elevation, slice(r, min(r + per_tile + 1, rows)),
                               slice(c, min(c + per_tile + 1, cols)), step)
            if mesh is not None:
                mesh.metadata["extras"] = {"surface": "asphalt", "part": "ground"}
                scene.add_geometry(mesh, node_name=f"ground_{i}_{j}", geom_name=f"ground_{i}_{j}")
    buildings = building_mesh(model, blocks_only=collision)
    if buildings is not None:
        buildings.metadata["extras"] = {"surface": "concrete", "part": "buildings"}
        scene.add_geometry(buildings, node_name="buildings", geom_name="buildings")
    for n, spot in enumerate(model.spots):
        mesh = spot_mesh(spot, model.origin_elevation)
        surface = "wood" if spot.what == "bench" and not collision else SURFACE.get(spot.feature.kind, "concrete")
        mesh.metadata["extras"] = {"surface": surface, "part": spot.what, "source": spot.source}
        scene.add_geometry(mesh, node_name=f"spot_{n}_{spot.feature.kind}", geom_name=f"spot_{n}_{spot.feature.kind}")
    return scene


def triangle_count(s) -> int:
    return sum(len(g.faces) for g in s.geometry.values())


def write(model: CityModel, path: str | Path, tile: float = 64.0) -> dict:
    """<path> (visual) and <path stem>-collision<suffix>; returns their triangle counts."""
    path = Path(path)
    visual = scene(model, collision=False, tile=tile)
    collision = scene(model, collision=True, tile=tile)
    collision_path = path.with_name(f"{path.stem}-collision{path.suffix}")
    visual.export(path)
    collision.export(collision_path)
    return {"visual": str(path), "visual_triangles": triangle_count(visual),
            "collision": str(collision_path), "collision_triangles": triangle_count(collision)}
