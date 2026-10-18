"""A real place's collision, from the tiles `contrib/lidar-parks` writes with `city --level DIR`
(format "reskate-level-tiles", version 2; FORMAT-NOTES 6.4).

    DIR/level.json            manifest: format, version, units, tile_size, spawn, tiles[], ...
    DIR/tile_<i>_<j>.json     a tile's objects: role visual | collision, material, part,
                              vertices {offset, count} float32 xyz, indices {offset, count} uint32
    DIR/tile_<i>_<j>.bin      their bytes, little-endian

Frame: metres, x east, y up, z north, y = 0 at the origin's elevation. The game's frame is the one
ReSkate's park mods are placed in (lidar-parks `park.to_game` with no mirror: game x = east,
y = up, z = north), so a place maps to the game by a translation alone: game = place + `at`.
Winding: (b - a) x (c - a) points out of the surface, the convention physics.py writes (the grid
whose normals point up held the skater in game). `mirror` flips z (and so swaps each triangle's
winding to keep it outward) in case the game turns out to see the place mirrored.

Collision objects come welded, in chunks of at most 256 triangles; each chunk becomes one shape and
one part. Visual objects are read and counted only: drawing is not solved yet (6.3), and
`Place.visual` keeps them for a mesh writer to take later.

Pure Python, like the rest of the package (no numpy).
"""
from __future__ import annotations

import json
import math
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .physics import KIND_TRIANGLE_MESH, Part, Shape

FORMAT = "reskate-level-tiles"
VERSION = 2
MAX_SHAPE_TRIANGLES = 0xFFFF // 3      # a shape keeps its half-edge count in 16 bits


@dataclass
class Piece:
    """One object of one tile, in the place's frame."""
    tile: tuple[int, int]
    name: str
    role: str
    material: str
    part: str
    vertices: list[tuple[float, float, float]]
    triangles: list[tuple[int, int, int]]
    chunk: int = 0


@dataclass
class Place:
    directory: Path
    manifest: dict
    mirror: bool = False
    collision: list[Piece] = field(default_factory=list)
    visual: list[Piece] = field(default_factory=list)

    @property
    def spawn(self) -> tuple[float, float, float] | None:
        s = self.manifest.get("spawn")
        if not (isinstance(s, list) and len(s) == 3):
            return None
        x, y, z = (_number(c, "spawn") for c in s)
        return (x, y, -z if self.mirror else z)

    def bounds(self, pieces=None) -> tuple[tuple[float, ...], tuple[float, ...]]:
        points = [v for p in (self.collision if pieces is None else pieces) for v in p.vertices]
        if not points:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        return tuple(min(p[i] for p in points) for i in range(3)), tuple(max(p[i] for p in points) for i in range(3))

    def ground(self, x: float, z: float) -> float | None:
        """The highest collision surface straight above or below (x, z), in the place's frame, or
        None: what `trainer ground` should read there (minus the place's height in the game)."""
        best = None
        for piece in self.collision:
            v = piece.vertices
            for a, b, c in piece.triangles:
                (ax, ay, az), (bx, by, bz), (cx, cy, cz) = v[a], v[b], v[c]
                d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
                if abs(d) < 1e-12:
                    continue                                   # a wall seen from above
                w1 = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / d
                w2 = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / d
                w3 = 1.0 - w1 - w2
                if min(w1, w2, w3) < -1e-9:
                    continue
                y = w1 * ay + w2 * by + w3 * cy
                best = y if best is None else max(best, y)
        return best


class PlaceError(ValueError):
    pass


def _number(value, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise PlaceError(f"{label} is not a number")
    return float(value)


def read_place(directory: str | Path, mirror: bool = False) -> Place:
    """Reads and checks a tile directory; PlaceError says what is wrong."""
    root = Path(directory)
    path = root / "level.json"
    if not path.is_file():
        raise PlaceError(f"{path} not found: give the directory `city --level DIR` wrote")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise PlaceError(f"{path} is not JSON: {e}") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise PlaceError(f"{path}: format is {manifest.get('format') if isinstance(manifest, dict) else None!r}, "
                         f"not {FORMAT!r}")
    if manifest.get("version") != VERSION:
        raise PlaceError(f"{path}: version {manifest.get('version')!r}; this builder reads version {VERSION} only "
                         f"(re-export with a lidar-parks of the same format version)")
    if manifest.get("units", "metres") != "metres":
        raise PlaceError(f"{path}: units {manifest.get('units')!r}, not metres")
    checks = manifest.get("collision_checks") or {}
    bad = {k: checks.get(k) for k in ("edges_same_direction_twice", "edges_shared_by_3_or_more") if checks.get(k)}
    if bad:
        raise PlaceError(f"{path}: the exporter reports collision edges a half-edge shape cannot hold: {bad}")
    tiles = manifest.get("tiles")
    if not isinstance(tiles, list) or not tiles:
        raise PlaceError(f"{path}: no tiles listed")
    place = Place(root, manifest, mirror)
    for row in tiles:
        name = row.get("json") if isinstance(row, dict) else None
        if not isinstance(name, str) or "/" in name or "\\" in name or name.startswith("."):
            raise PlaceError(f"{path}: bad tile entry {row!r}")
        tile_path = root / name
        try:
            tile = json.loads(tile_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as e:
            raise PlaceError(f"{tile_path}: {e}") from None
        bin_name = tile.get("bin")
        if not isinstance(bin_name, str) or "/" in bin_name or "\\" in bin_name or bin_name.startswith("."):
            raise PlaceError(f"{tile_path}: bad bin {bin_name!r}")
        try:
            blob = (root / bin_name).read_bytes()
        except OSError as e:
            raise PlaceError(f"{tile_path}: {e}") from None
        key = tuple(tile.get("tile") or row.get("tile") or ())
        for k, obj in enumerate(tile.get("objects") or []):
            place_piece(place, tile_path, key, k, obj, blob, mirror)
    if not place.collision:
        raise PlaceError(f"{root}: no collision objects in any tile")
    return place


def place_piece(place: Place, tile_path: Path, key, k: int, obj: dict, blob: bytes, mirror: bool) -> None:
    label = f"{tile_path.name} object {k} ({obj.get('name')!r})"
    role = obj.get("role")
    if role not in ("visual", "collision"):
        raise PlaceError(f"{label}: role {role!r}")
    try:
        v_off, v_count = int(obj["vertices"]["offset"]), int(obj["vertices"]["count"])
        i_off, i_count = int(obj["indices"]["offset"]), int(obj["indices"]["count"])
    except (KeyError, TypeError, ValueError):
        raise PlaceError(f"{label}: vertices/indices need offset and count") from None
    if min(v_off, v_count, i_off, i_count) < 0 or v_off + 12 * v_count > len(blob) or i_off + 4 * i_count > len(blob):
        raise PlaceError(f"{label}: its data runs past the end of the .bin")
    if i_count % 3:
        raise PlaceError(f"{label}: {i_count} indices is not whole triangles")
    flat = struct.unpack_from(f"<{3 * v_count}f", blob, v_off)
    vertices = [(flat[i], flat[i + 1], -flat[i + 2] if mirror else flat[i + 2]) for i in range(0, len(flat), 3)]
    if not all(math.isfinite(c) for p in vertices for c in p):
        raise PlaceError(f"{label}: a vertex is not finite")
    index = struct.unpack_from(f"<{i_count}I", blob, i_off)
    if index and max(index) >= v_count:
        raise PlaceError(f"{label}: an index is past its {v_count} vertices")
    triangles = [(index[i], index[i + 2], index[i + 1]) if mirror else (index[i], index[i + 1], index[i + 2])
                 for i in range(0, len(index), 3)]
    piece = Piece(key, str(obj.get("name")), role, str(obj.get("material")), str(obj.get("part")), vertices, triangles,
                  int(obj.get("chunk", 0) or 0))
    if role == "collision":
        if len(triangles) > MAX_SHAPE_TRIANGLES or len(vertices) > 0xFFFF:
            raise PlaceError(f"{label}: {len(triangles)} triangles; a collision chunk must fit one shape "
                             f"({MAX_SHAPE_TRIANGLES} at most, {place.manifest.get('collision_chunk_triangles')} expected)")
        if any(len(set(t)) != 3 for t in triangles):
            raise PlaceError(f"{label}: a triangle repeats a corner (collision must come welded)")
        place.collision.append(piece)
    else:
        place.visual.append(piece)


TALL = 20.0      # metres above the origin: collision this high is reported (the 130 x 20 Embarcadero
                 # cut holds one clipped tower whose wall rises to 159 m at its west end)


def filter_collision(place: Place, max_height: float | None = None, skip: tuple[str, ...] = ()) -> list[str]:
    """Drops collision chunks whose part or name is in `skip`, or whose top is above `max_height`
    (place frame); whole chunks only, as a chunk is one welded shape. Returns what it did."""
    out, kept = [], []
    dropped = Counter()
    for piece in place.collision:
        top = max(v[1] for v in piece.vertices)
        if piece.part in skip or piece.name in skip:
            dropped[f"{piece.name} (skipped part {piece.part})"] += 1
        elif max_height is not None and top > max_height:
            dropped[f"{piece.name} (top {top:.1f} m above {max_height} m)"] += 1
        else:
            kept.append(piece)
    for what, n in dropped.items():
        out.append(f"  dropped {n} collision chunk(s) of {what}")
    place.collision[:] = kept
    if not kept:
        raise PlaceError("every collision chunk was dropped")
    tall = Counter(p.name for p in kept if max(v[1] for v in p.vertices) > TALL)
    for name, n in tall.items():
        top = max(v[1] for p in kept if p.name == name for v in p.vertices)
        out.append(f"  NOTE: {n} collision chunk(s) of {name!r} rise to {top:.1f} m (a tall wall: --max-height "
                   f"{TALL:g} or --skip-part {name} drops them)")
    return out


def spawn_at(place: Place, height: float = 300.0) -> tuple[float, float, float]:
    """The `--at` that puts the export's spawn at the game's (0, height + spawn y, 0); the origin
    when the export has no spawn."""
    if place.spawn is None:
        return (0.0, height, 0.0)
    return (0.0 - place.spawn[0] + 0.0, height, 0.0 - place.spawn[2] + 0.0)


def merge(pieces: list[Piece]) -> Shape:
    """One shape of several chunks: their triangles over one vertex list, positions equal to the
    last bit shared (the exporter writes shared vertices bit-identical), so edges inside the merged
    shape are a vertex pair as in a chunk."""
    index: dict[tuple, int] = {}
    vertices: list = []
    triangles: list = []
    for piece in pieces:
        local = []
        for v in piece.vertices:
            key = struct.pack("<3f", *v)
            if key not in index:
                index[key] = len(vertices)
                vertices.append(v)
            local.append(index[key])
        triangles += [(local[a], local[b], local[c]) for a, b, c in piece.triangles]
    if 3 * len(triangles) > 0xFFFF or len(vertices) > 0xFFFF:
        raise PlaceError(f"{len(triangles)} triangles do not fit one shape ({MAX_SHAPE_TRIANGLES} at most): "
                         f"use more parts")
    return Shape(vertices, [t for t in triangles if len(set(t)) == 3])


def groups(place: Place, count: int) -> list[list[Piece]]:
    """The collision chunks in `count` groups: by part (ground, bench, steps, ...) when there are
    exactly that many parts, otherwise in strips along x by the chunks' centres, equal in chunks."""
    kinds = sorted({p.part for p in place.collision})
    if len(kinds) == count:
        return [[p for p in place.collision if p.part == k] for k in kinds]
    order = sorted(place.collision, key=lambda p: sum(v[0] for v in p.vertices) / max(1, len(p.vertices)))
    size = -(-len(order) // count)
    out = [order[i:i + size] for i in range(0, len(order), size)]
    return out + [[] for _ in range(count - len(out))]


def collision(place: Place, template: Part, part_count: int | None = None,
              templates: list[Part] | None = None) -> tuple[list[Shape], list[Part], str]:
    """Shapes and parts in the place's frame (the placement moves them).

    `part_count` None: one shape and one part per collision chunk. Otherwise exactly that many
    parts (the template's own .physics part count, FORMAT-NOTES 6.4), each one shape merging its
    group of chunks (groups()). Part i copies `templates[i]` (or `template`): material, hashes and
    words; GUESS: word +0x34 the part's index when there are several parts."""
    if part_count is None:
        sets = [[p] for p in place.collision]
        what = f"{len(sets)} parts, one per collision chunk"
    else:
        sets = groups(place, part_count)
        if any(not group for group in sets):
            raise PlaceError(f"{len(place.collision)} collision chunks cannot fill {part_count} parts")
        what = (f"{part_count} part(s) as the template's .physics has, each one shape merging its chunks ("
                + ", ".join(f"{len(g)} chunks of {sorted({p.part for p in g})}" for g in sets) + ")")
    shapes, parts = [], []
    for i, group in enumerate(sets):
        shapes.append(merge(group) if len(group) > 1 else Shape(list(group[0].vertices), list(group[0].triangles)))
        model = templates[i] if templates and i < len(templates) else template
        parts.append(Part(i, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), 1.0, model.material, KIND_TRIANGLE_MESH,
                          model.hashes, 0, model.float_30, i if len(sets) > 1 else 0, model.word_3c, model.word_44))
    return shapes, parts, what


def summary(place: Place, at: tuple[float, float, float]) -> list[str]:
    lo, hi = place.bounds()
    tiles = {p.tile for p in place.collision + place.visual}
    out = [f"Place {place.directory}: {len(tiles)} tiles (tile size {place.manifest.get('tile_size')} m), "
           f"{len(place.collision)} collision chunks, {sum(len(p.triangles) for p in place.collision)} collision "
           f"triangles, {sum(len(p.vertices) for p in place.collision)} vertices; {len(place.visual)} visual objects "
           f"({sum(len(p.triangles) for p in place.visual)} triangles, not used yet)",
           f"  bounds (place frame, x east, y up, z north): {[round(c, 2) for c in lo]}..{[round(c, 2) for c in hi]}",
           f"  bounds in the game: {[round(c + a, 2) for c, a in zip(lo, at)]}..{[round(c + a, 2) for c, a in zip(hi, at)]}",
           "  collision by material: " + str(dict(Counter(p.material for p in place.collision))),
           "  collision by part: " + str(dict(Counter(p.part for p in place.collision))),
           f"  origin {place.manifest.get('origin')}, spawn {place.spawn}"]
    largest = max(len(p.triangles) for p in place.collision)
    out.append(f"  largest chunk {largest} triangles (the exporter's limit "
               f"{place.manifest.get('collision_chunk_triangles')})")
    return out


def check_spots(place: Place, at: tuple[float, float, float]) -> list[tuple[str, float, float, float | None]]:
    """(label, game x, game z, expected ground height in the game) at a few spots worth probing."""
    lo, hi = place.bounds()
    spots = []
    if place.spawn:
        spots.append(("spawn", place.spawn[0], place.spawn[2]))
    spots.append(("origin", 0.0, 0.0))
    cx, cz = (lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2
    spots.append(("centre of the bounds", cx, cz))
    for label, fx, fz in (("south-west quarter", 0.25, 0.25), ("south-east quarter", 0.75, 0.25),
                          ("north-west quarter", 0.25, 0.75), ("north-east quarter", 0.75, 0.75)):
        spots.append((label, lo[0] + fx * (hi[0] - lo[0]), lo[2] + fz * (hi[2] - lo[2])))
    for k, grind in enumerate((place.manifest.get("grinds") or [])[:3]):
        points = grind.get("points") if isinstance(grind, dict) else None
        if isinstance(points, list) and len(points) >= 2 and all(isinstance(p, list) and len(p) == 3 for p in points):
            mx = (points[0][0] + points[1][0]) / 2
            mz = (points[0][2] + points[1][2]) / 2
            spots.append((f"grind {k} ({grind.get('what')}, {grind.get('surface')}) midpoint, its top "
                          f"{(points[0][1] + points[1][1]) / 2 + at[1]:.2f}", mx, -mz if place.mirror else mz))
    spots.append(("outside, 20 m east of the bounds", hi[0] + 20.0, cz))
    out = []
    for label, x, z in spots:
        y = place.ground(x, z)
        out.append((label, round(x + at[0], 2), round(z + at[2], 2), None if y is None else round(y + at[1], 2)))
    return out


def visual_label(piece: Piece) -> str:
    """The name new-place-level --visuals gives a visual object (and --visuals-only takes)."""
    return f"{piece.name}_t{piece.tile[0]}_{piece.tile[1]}".replace("-", "m")


# The material class a visual object is drawn as (its --material-donor key): the export's
# material, except that the buildings part gets its own class (a wall material, not the
# concrete of steps and kerbs, which the export also calls concrete).
PART_CLASSES = {"buildings": "building"}


def visual_class(piece: Piece) -> str:
    return PART_CLASSES.get(piece.part, piece.material)


# The in-game compass (the PC, both places): heading 0 (+z) reads S, 90 (+x) E, 270 W. So the
# place's north is the compass's south; east and west agree.
GAME_COMPASS_SIDE = {"south": "north", "north": "south", "west": "west", "east": "east"}


def view_spots(place: Place, at: tuple[float, float, float]) -> list[tuple[str, float, float, float | None, float, str]]:
    """Per visual object: (label, game x, game z, expected ground there, heading to face, note).
    After `trainer tp` and `trainer ground` the camera faces heading 0, +z (the place's north; the
    PC, e3ed5ee: `trainer where` read 0 after every teleport), so the spot is on the object's south
    side when there is ground there, looking north; else north (heading 180), west (90) or east
    (270). Sides are the place's real ones; the in-game compass reads +z as S and -z as N (x as
    the place: 90 E, 270 W), so the note gives the compass's side too. The distance fits the object (4 m, more for big ones: up to 40 m, or 1.5 times a tall
    object's height so it is framed), shortened until the spot has ground."""
    out = []
    for piece in place.visual:
        lo, hi = place.bounds([piece])
        cx, cz = (lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2
        size = max(hi[0] - lo[0], hi[2] - lo[2])
        height = hi[1] - lo[1]
        far = max(4.0, min(40.0, 0.6 * size) if size > 10 else 4.0, min(150.0, 1.5 * height) if height > 10 else 0.0)
        best = None
        for side, heading, place_at in (("south", 0.0, lambda g: (cx, lo[2] - g)), ("north", 180.0, lambda g: (cx, hi[2] + g)),
                                        ("west", 90.0, lambda g: (lo[0] - g, cz)), ("east", 270.0, lambda g: (hi[0] + g, cz))):
            for gap in sorted({g for g in (far, far / 2, far / 4, 4.0, 2.0) if g >= 2.0}, reverse=True):
                x, z = place_at(gap)
                y = place.ground(x, z)
                if y is not None:
                    best = (side, heading, x, z, y, gap)
                    break
            if best:
                break
        if best is None:
            best = ("south, no ground found near it", 0.0, cx, lo[2] - far, None, far)
        side, heading, x, z, y, gap = best
        out.append((visual_label(piece), round(x + at[0], 2), round(z + at[2], 2),
                    None if y is None else round(y + at[1], 2), heading,
                    f"{side} of it ({GAME_COMPASS_SIDE.get(side.split(',')[0], '?')} on the game compass), "
                    f"{gap:.0f} m off; it spans y {lo[1] + at[1]:.1f}..{hi[1] + at[1]:.1f}"))
    return out
