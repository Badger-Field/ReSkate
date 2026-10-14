"""A place's meshes as plain tiles for a level builder: triangle lists and a material per object.

`city --level DIR` writes, next to (or instead of) `--mesh`:

    DIR/level.json            the manifest (below)
    DIR/tile_<i>_<j>.json     one tile's objects
    DIR/tile_<i>_<j>.bin      their vertices and indices

**Frame.** Metres, x east, y up, z north: the frame park mods are placed in (park.to_game, with
the anchor at the origin and no turn), so a level and a park built at the same anchor line up.
The origin is the area's centre at `origin.elevation` (y = 0). Note that the GLB from `--mesh`
is glTF's frame, where z points south; here z = -z_gltf.

**Winding.** For every triangle (a, b, c), the plain cross product (b - a) x (c - a), computed
with the usual component formula on these coordinates, points out of the surface: up for
ground, outwards for walls.

**Tiles.** Square, `tile_size` metres (default 50) on the x-z plane, tile (i, j) covering
x in [i * size, (i + 1) * size) and z in [j * size, (j + 1) * size). Each triangle goes to the
tile holding its centroid, so a triangle may stick a little out of its tile; a tile's
`bounds` are what its triangles actually cover.

**Objects.** Per tile, each source part (a ground tile, the buildings, one skate spot) that
has triangles there is one object:

    {"name": "spot_3_ledge", "role": "visual" | "collision", "material": "ground" | "concrete" |
     "wood" | "metal" | "grass", "part": "ground" | "buildings" | "bench" | ..., "source": ...,
     "vertices": {"offset": bytes, "count": n},      float32 x, y, z, little-endian
     "indices":  {"offset": bytes, "count": 3 * t}}  uint32, little-endian, into this object's vertices

`visual` objects are the full-detail meshes; `collision` ones the coarser copy (half-resolution
ground, buildings as plain blocks) to build the level's collision from.

**Grinds** (in level.json): polylines along ledge and bench edges, rails and coping, with their
surface, in the same frame."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FORMAT = "reskate-level-tiles"
VERSION = 1
MATERIALS = ("ground", "concrete", "wood", "metal", "grass")
_FROM_SURFACE = {"asphalt": "ground", "ground": "ground", "concrete": "concrete", "paving_stones": "concrete",
                 "wood": "wood", "metal": "metal", "grass": "grass"}


def material(part_extras: dict) -> str:
    if part_extras.get("reskate_part") == "ground":
        return _FROM_SURFACE.get(part_extras.get("reskate_surface"), "ground")
    return _FROM_SURFACE.get(part_extras.get("reskate_surface"), "concrete")


def to_level(points_gltf: np.ndarray) -> np.ndarray:
    """glTF (x east, y up, z south) to the level frame (x east, y up, z north)."""
    out = np.array(points_gltf, dtype=np.float64, copy=True)
    out[:, 2] = -out[:, 2]
    return out


def from_local(points: np.ndarray, origin_elevation: float) -> np.ndarray:
    """The model's local frame (x east, y north, z elevation) to the level frame."""
    points = np.atleast_2d(np.asarray(points, float))
    return np.column_stack([points[:, 0], points[:, 2] - origin_elevation, points[:, 1]])


def _parts(model, collision: bool, tile: float):
    """(name, extras, vertices in the level frame, faces wound for it) for each triangle mesh."""
    import trimesh
    from . import citymesh
    scene, extras = citymesh.scene(model, collision=collision, tile=tile)
    for name, geometry in scene.geometry.items():
        if not isinstance(geometry, trimesh.Trimesh) or len(geometry.faces) == 0:
            continue                                     # grind curves are paths, exported apart
        vertices = to_level(geometry.vertices)
        faces = np.asarray(geometry.faces)[:, [0, 2, 1]]  # flipping z mirrors: swap to keep faces outward
        yield name, extras.get(name, {}), vertices, faces


def export(model, out_dir: str | Path, tile_size: float = 50.0, attribution: list[str] = (),
           origin: dict | None = None) -> dict:
    """Write the tiles and manifest; return the manifest."""
    from . import citymesh
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for old in list(out.glob("tile_*.json")) + list(out.glob("tile_*.bin")):
        old.unlink()
    tiles: dict[tuple[int, int], list[tuple]] = {}
    for role in ("visual", "collision"):
        for name, part_extras, vertices, faces in _parts(model, role == "collision", tile_size):
            centroids = vertices[faces].mean(axis=1)
            keys = np.floor(centroids[:, [0, 2]] / tile_size).astype(int)
            for key in {tuple(k) for k in keys}:
                mine = faces[np.all(keys == key, axis=1)]
                used, local = np.unique(mine, return_inverse=True)
                tiles.setdefault(key, []).append((name, role, part_extras, vertices[used],
                                                  local.reshape(-1, 3).astype(np.uint32)))
    listed = []
    for (i, j), objects in sorted(tiles.items()):
        i, j = int(i), int(j)
        stem = f"tile_{i}_{j}"
        blob, described = bytearray(), []
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        for name, role, part_extras, vertices, faces in objects:
            v = vertices.astype("<f4")
            f = faces.astype("<u4")
            lo, hi = np.minimum(lo, v.min(axis=0)), np.maximum(hi, v.max(axis=0))
            described.append({
                "name": name, "role": role, "material": material(part_extras),
                "part": part_extras.get("reskate_part", name), "source": part_extras.get("reskate_source"),
                "vertices": {"offset": len(blob), "count": int(len(v))},
                "indices": {"offset": len(blob) + v.nbytes, "count": int(f.size)},
            })
            blob += v.tobytes() + f.tobytes()
        (out / f"{stem}.bin").write_bytes(bytes(blob))
        bounds = [round(float(c), 3) for c in (*lo, *hi)]
        (out / f"{stem}.json").write_text(json.dumps({"tile": [i, j], "bin": f"{stem}.bin", "bounds": bounds,
                                                      "objects": described}, indent=1), encoding="utf-8")
        listed.append({"tile": [i, j], "json": f"{stem}.json", "bin": f"{stem}.bin", "bounds": bounds,
                       "objects": len(described), "triangles": {
                           role: sum(o["indices"]["count"] // 3 for o in described if o["role"] == role)
                           for role in ("visual", "collision")}})
    grinds = [{"surface": g.surface, "what": g.what, "closed": bool(g.closed), "radius": g.radius,
               "points": [[round(float(c), 4) for c in p]
                          for p in from_local(g.points, model.origin_elevation)]}
              for g in list(getattr(model, "grinds", [])) + [g for s in model.spots for g in citymesh.spot_grinds(s)]]
    spawn = citymesh.spawn_point(model)
    manifest = {
        "format": FORMAT, "version": VERSION, "units": "metres",
        "frame": "x east, y up, z north; origin at the area's centre, y = 0 at origin.elevation",
        "winding": "(b - a) x (c - a) points out of the surface",
        "binary": {"vertices": "float32 x, y, z little-endian", "indices": "uint32 little-endian, per object"},
        "origin": {**(origin or {}), "elevation": float(model.origin_elevation)},
        "tile_size": tile_size, "materials": list(MATERIALS),
        "spawn": [round(float(c), 3) for c in from_local(spawn, model.origin_elevation)[0]],
        "grinds": grinds, "tiles": listed, "attribution": list(attribution),
    }
    (out / "level.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def read_object(tile_dir: str | Path, tile_json: dict, obj: dict) -> tuple[np.ndarray, np.ndarray]:
    """One object's vertices (n, 3) and triangles (t, 3), as a builder would read them."""
    data = (Path(tile_dir) / tile_json["bin"]).read_bytes()
    v = np.frombuffer(data, "<f4", obj["vertices"]["count"] * 3, obj["vertices"]["offset"]).reshape(-1, 3)
    f = np.frombuffer(data, "<u4", obj["indices"]["count"], obj["indices"]["offset"]).reshape(-1, 3)
    return v, f
