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

Collision parts are welded first: positions equal to the last bit share one index, and
triangles left with a repeated corner, or repeating another, are dropped. `collision_checks` in
level.json counts what was fixed, and the edges a half-edge structure couldn't hold (used by
three or more triangles, or twice in the same direction), which are reported, not fixed.

Collision objects come in spatially compact chunks of at most 256 triangles (`"chunk": k` of
`"chunks": n` for the same `name`): a collision shape keeps its counts in 16 bits. A vertex
shared by neighbouring chunks (or tiles) has bit-identical coordinates in each, so edges can be
matched across shapes. Visual objects are not split, and carry `"crease_degrees"`: split the
normals where neighbouring faces turn by more than that (45 for ground, 30 for boxes).

**Grinds** (in level.json): polylines along ledge and bench edges, rails and coping, with their
surface, in the same frame."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FORMAT = "reskate-level-tiles"
VERSION = 2
CHUNK_TRIANGLES = 256      # a collision shape stores its counts as u16, and is scanned whole without a tree
CREASE = {"ground": 45.0}  # degrees: split normals where neighbouring faces turn more than this
CREASE_DEFAULT = 30.0      # buildings and spots are boxes and wedges: sharp edges
MATERIALS = ("ground", "concrete", "wood", "metal", "grass")
_FROM_SURFACE = {"asphalt": "ground", "ground": "ground", "concrete": "concrete", "paving_stones": "concrete",
                 "stone": "concrete", "wood": "wood", "metal": "metal", "grass": "grass"}


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


def weld(vertices: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    """For collision: one index per position (positions equal to the last bit merge, nothing
    else moves), then drop the triangles that leaves with a repeated corner or that repeat
    another triangle. A half-edge shape needs each edge's two sides to share their vertices."""
    bits = np.ascontiguousarray(vertices.astype("<f4")).view(np.uint32).reshape(-1, 3)
    _, first, inverse = np.unique(bits, axis=0, return_index=True, return_inverse=True)
    inverse = inverse.reshape(-1)
    welded_faces = inverse[faces]
    keep = ((welded_faces[:, 0] != welded_faces[:, 1]) & (welded_faces[:, 1] != welded_faces[:, 2])
            & (welded_faces[:, 0] != welded_faces[:, 2]))
    degenerate = int((~keep).sum())
    welded_faces = welded_faces[keep]
    _, unique_rows = np.unique(np.sort(welded_faces, axis=1), axis=0, return_index=True)
    repeated = len(welded_faces) - len(unique_rows)
    welded_faces = welded_faces[np.sort(unique_rows)]
    return vertices[first], welded_faces, {"merged_vertices": int(len(vertices) - len(first)),
                                           "dropped_degenerate": degenerate, "dropped_repeated": int(repeated)}


def edge_checks(faces: np.ndarray) -> dict[str, int]:
    """Edges a half-edge structure can't hold: used by more than two triangles, or twice the same way."""
    directed = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    _, same_way = np.unique(directed, axis=0, return_counts=True)
    _, either_way = np.unique(np.sort(directed, axis=1), axis=0, return_counts=True)
    return {"edges_same_direction_twice": int((same_way > 1).sum()),
            "edges_shared_by_3_or_more": int((either_way > 2).sum())}


def chunks(vertices: np.ndarray, faces: np.ndarray, limit: int = CHUNK_TRIANGLES) -> list[np.ndarray]:
    """Faces split into spatially compact groups of at most `limit`: halved at the median of
    their centroids along the longest side, until each is small enough."""
    out, pending = [], [np.arange(len(faces))]
    centroids = vertices[faces].mean(axis=1)
    while pending:
        group = pending.pop()
        if len(group) <= limit:
            out.append(faces[group])
            continue
        c = centroids[group]
        axis = int(np.argmax(np.ptp(c, axis=0)))
        order = group[np.argsort(c[:, axis], kind="stable")]
        half = len(order) // 2
        pending += [order[half:], order[:half]]
    return out


def export(model, out_dir: str | Path, tile_size: float = 50.0, attribution: list[str] = (),
           origin: dict | None = None) -> dict:
    """Write the tiles and manifest; return the manifest."""
    from . import citymesh
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for old in list(out.glob("tile_*.json")) + list(out.glob("tile_*.bin")):
        old.unlink()
    tiles: dict[tuple[int, int], list[tuple]] = {}
    checks = {"merged_vertices": 0, "dropped_degenerate": 0, "dropped_repeated": 0,
              "edges_same_direction_twice": 0, "edges_shared_by_3_or_more": 0}
    for role in ("visual", "collision"):
        for name, part_extras, vertices, faces in _parts(model, role == "collision", tile_size):
            if role == "collision":
                vertices, faces, fixed = weld(vertices, faces)
                for key, value in {**fixed, **edge_checks(faces)}.items():
                    checks[key] += value
                if not len(faces):
                    continue
            centroids = vertices[faces].mean(axis=1)
            keys = np.floor(centroids[:, [0, 2]] / tile_size).astype(int)
            # float32 once, for the whole part: a vertex shared by two chunks or tiles is the same bits in each.
            vertices = vertices.astype("<f4")
            for key in sorted({tuple(k) for k in keys}):
                mine = faces[np.all(keys == key, axis=1)]
                groups = chunks(vertices, mine) if role == "collision" else [mine]
                for k, group in enumerate(groups):
                    used, local = np.unique(group, return_inverse=True)
                    tiles.setdefault(key, []).append((name, role, part_extras, vertices[used],
                                                      local.reshape(-1, 3).astype(np.uint32), k, len(groups)))
    listed = []
    for (i, j), objects in sorted(tiles.items()):
        i, j = int(i), int(j)
        stem = f"tile_{i}_{j}"
        blob, described = bytearray(), []
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        for name, role, part_extras, vertices, faces, k, n in objects:
            v = vertices.astype("<f4")
            f = faces.astype("<u4")
            lo, hi = np.minimum(lo, v.min(axis=0)), np.maximum(hi, v.max(axis=0))
            part = part_extras.get("reskate_part", name)
            entry = {
                "name": name, "role": role, "material": material(part_extras),
                "part": part, "source": part_extras.get("reskate_source"),
                "vertices": {"offset": len(blob), "count": int(len(v))},
                "indices": {"offset": len(blob) + v.nbytes, "count": int(f.size)},
            }
            if role == "collision":
                entry.update(chunk=int(k), chunks=int(n))
            else:
                entry["crease_degrees"] = CREASE.get(part, CREASE_DEFAULT)
            described.append(entry)
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
        "tile_size": tile_size, "materials": list(MATERIALS), "collision_chunk_triangles": CHUNK_TRIANGLES,
        "collision_checks": checks,
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
