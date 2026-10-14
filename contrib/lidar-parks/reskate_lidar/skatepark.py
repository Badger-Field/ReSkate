"""A mapped skatepark as its real shape, for a custom map: bowls, transitions and coping, which
the Build Kit cannot make.

The park's outline comes from the map (leisure=skatepark, or leisure=pitch with
sport=skateboard). OpenStreetMap often maps a bowl as a hole in that outline, and its edge is then
the coping. The surface is the aerial LiDAR's ground at a fine grid. Points standing above the
ground are dropped, so skaters, palms and lamps leave no trace; so are pulses that went through
leaves. Each bowl's coping becomes a closed grind curve at deck height. Where the map has no holes,
bowls are found as ground well below the deck.

Aerial LiDAR (USGS 3DEP is about 10-20 points/m² in cities) gets a bowl's shape and depth, but not
the coping's lip or a vertical wall's last half metre: those need a ground scan (CAPTURE.md)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import cKDTree

from . import ept
from .city import CityModel, Grid
from .citymesh import Grind
from .features import Feature
from .mapdata import MapItem


@dataclass
class Bowl:
    outline: object        # shapely Polygon, local metres
    points: int            # LiDAR ground points inside
    rim: float             # deck elevation round it
    floor: float           # its lowest (2nd percentile) elevation
    steepest: float        # degrees, near the top of the grid's steepest walls
    from_map: bool         # its outline is the map's (a hole in the park); else found in the ground

    @property
    def depth(self) -> float:
        return self.rim - self.floor

    @property
    def density(self) -> float:
        return self.points / max(self.outline.area, 1e-6)


@dataclass
class Park:
    model: CityModel
    outline: object                       # the park, holes filled
    bowls: list[Bowl]
    deck: float                           # deck elevation
    density: float                        # ground points per m² over the park
    removed_above: int                    # points dropped as standing above the ground
    notes: list[str] = field(default_factory=list)


def is_skatepark(item: MapItem) -> bool:
    tags = item.tags
    return item.geometry.geom_type in ("Polygon", "MultiPolygon") and (
        tags.get("leisure") == "skatepark" or (tags.get("sport") in ("skateboard", "skateboarding")
                                               and tags.get("leisure") in ("pitch", "sports_centre", None)))


def find_park(items: list[MapItem], near=(0.0, 0.0), reach: float = 150.0):
    """The mapped skatepark under `near`, else the nearest one within `reach` metres."""
    import shapely
    point = shapely.Point(near)
    parks = [i for i in items if is_skatepark(i)]
    if not parks:
        return None
    best = min(parks, key=lambda i: i.geometry.distance(point))
    if best.geometry.distance(point) > reach:
        return None
    geometry = best.geometry
    if geometry.geom_type == "MultiPolygon":
        geometry = max(geometry.geoms, key=lambda g: g.area)
    return geometry


def _surface_points(points: ept.Points, region, max_above: float = 0.15) -> tuple[np.ndarray, int]:
    """Ground points in the region, plus unclassified single-return points lying on the ground
    (bowl walls are often left unclassified); anything standing above the ground is dropped."""
    import shapely
    xy = points.xyz[:, :2]
    inside = shapely.contains_xy(region, xy[:, 0], xy[:, 1])
    ground = points.xyz[inside & (points.classes == ept.GROUND)]
    other_mask = inside & np.isin(points.classes, (0, 1)) & ~points.layered
    other = points.xyz[other_mask]
    removed = int((inside & np.isin(points.classes, (0, 1))).sum())
    if len(ground) < 20:
        return ground, removed
    surface = LinearNDInterpolator(ground[:, :2], ground[:, 2])
    near = cKDTree(ground[:, :2]).query(other[:, :2])[0] if len(other) else np.zeros(0)
    z = surface(other[:, :2]) if len(other) else np.zeros(0)
    on_ground = (np.abs(other[:, 2] - z) < max_above) & (near < 1.0)
    kept = np.vstack([ground, other[on_ground]])
    return kept, removed - int(on_ground.sum())


def _grid(points: np.ndarray, bounds, cell: float) -> Grid:
    xmin, ymin, xmax, ymax = bounds
    cols = int(math.ceil((xmax - xmin) / cell)) + 1
    rows = int(math.ceil((ymax - ymin) / cell)) + 1
    xs = xmin + np.arange(cols) * cell
    ys = ymin + np.arange(rows) * cell
    X, Y = np.meshgrid(xs, ys)
    heights = LinearNDInterpolator(points[:, :2], points[:, 2])(X, Y)
    missing = np.isnan(heights)
    if missing.any():
        _, (r, c) = ndimage.distance_transform_edt(missing, return_indices=True)
        heights = heights[r, c]
    # A 3x3 median takes off the survey's few centimetres of noise and keeps edges.
    heights = ndimage.median_filter(heights, size=3, mode="nearest")
    return Grid(heights, np.array([xmin, ymin]), cell)


def filled(heights: np.ndarray) -> np.ndarray:
    """The grid with every depression filled to its spill level (priority flood from the edges):
    how deep a spot is relative to where water would stand, so a hillside's slope is no bowl."""
    import heapq
    rows, cols = heights.shape
    out = heights.copy()
    done = np.zeros(heights.shape, bool)
    queue = []
    for r in range(rows):
        for c in (0, cols - 1):
            heapq.heappush(queue, (out[r, c], r, c))
            done[r, c] = True
    for c in range(1, cols - 1):
        for r in (0, rows - 1):
            heapq.heappush(queue, (out[r, c], r, c))
            done[r, c] = True
    while queue:
        level, r, c = heapq.heappop(queue)
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            rr, cc = r + dr, c + dc
            if 0 <= rr < rows and 0 <= cc < cols and not done[rr, cc]:
                done[rr, cc] = True
                out[rr, cc] = max(out[rr, cc], level)
                heapq.heappush(queue, (out[rr, cc], rr, cc))
    return out


def sunk(grid: Grid, reach: float = 6.0) -> np.ndarray:
    """How far each spot lies below the ground around it: the deeper of the depression's spill
    level (filled) and a closing with a disc `reach` metres across in radius, which also catches
    a bowl that opens downhill. A plain slope is neither."""
    radius = max(1, int(round(reach / grid.cell)))
    yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1]
    disc = xx * xx + yy * yy <= radius * radius
    closed = ndimage.grey_closing(grid.heights, footprint=disc, mode="nearest")
    return np.maximum(filled(grid.heights), closed) - grid.heights


def _bowls_from_ground(grid: Grid, park, depth: float = 0.4, lip: float = 0.25, min_area: float = 6.0):
    """Depressions in the park at least `depth` deep, grown out to where they are `lip` deep
    (near the coping), as outlines."""
    import shapely
    from .city import rasterise
    in_park = rasterise(park, grid)
    sunk_by = sunk(grid)
    seeds = in_park & (sunk_by >= depth)
    shallow = in_park & (sunk_by >= lip)
    grown = ndimage.binary_propagation(seeds, mask=shallow)
    labels, _ = ndimage.label(grown)
    outlines = []
    for index, slices in enumerate(ndimage.find_objects(labels), start=1):
        rows, cols = np.nonzero(labels[slices] == index)
        if len(rows) * grid.cell ** 2 < min_area:
            continue
        rows, cols = rows + slices[0].start, cols + slices[1].start
        half = grid.cell / 2
        boxes = [shapely.box(grid.origin[0] + c * grid.cell - half, grid.origin[1] + r * grid.cell - half,
                             grid.origin[0] + c * grid.cell + half, grid.origin[1] + r * grid.cell + half)
                 for r, c in zip(rows, cols)]
        shape = shapely.union_all(boxes).buffer(grid.cell * 0.6).buffer(-grid.cell * 0.6).simplify(grid.cell / 2)
        if shape.geom_type == "MultiPolygon":
            shape = max(shape.geoms, key=lambda g: g.area)
        outlines.append(shapely.Polygon(shape.exterior))
    return outlines


def _coping(outline, grid: Grid, spacing: float = 0.25) -> np.ndarray:
    """A bowl's edge as a closed line at deck height (the grid just outside the edge)."""
    import shapely
    ring = outline.exterior
    count = max(8, int(ring.length / spacing))
    points = np.array([ring.interpolate(i / count, normalized=True).coords[0] for i in range(count)])
    outward = []
    for p in points:
        a = np.array(ring.interpolate(max(ring.project(shapely.Point(p)) - 0.2, 0)).coords[0])
        b = np.array(ring.interpolate(min(ring.project(shapely.Point(p)) + 0.2, ring.length)).coords[0])
        t = b - a
        n = np.array([t[1], -t[0]]) / max(np.hypot(*t), 1e-9)
        if outline.contains(shapely.Point(p + n * 0.3)):
            n = -n
        outward.append(n)
    outward = np.array(outward)
    z = np.maximum(grid.sample(points + outward * 0.3), grid.sample(points + outward * 0.6))
    return np.column_stack([points, z, outward])


def _lip_runs(coping: np.ndarray, grid: Grid, steep: float = 25.0, min_length: float = 2.0) -> list[np.ndarray]:
    """The stretches of an edge that are a lip: where the ground just inside drops at least
    `steep` degrees. A found bowl's edge also crosses flat deck and the foot of banks."""
    points, outward = coping[:, :2], coping[:, 3:5]
    drop = grid.sample(points + outward * 0.25) - grid.sample(points - outward * 0.5)
    lip = np.degrees(np.arctan2(drop, 0.75)) >= steep
    runs, start = [], None
    for i, on in enumerate(np.append(lip, False)):
        if on and start is None:
            start = i
        elif not on and start is not None:
            run = coping[start:i, :3]
            if len(run) >= 2 and np.sum(np.hypot(*np.diff(run[:, :2], axis=0).T)) >= min_length:
                runs.append(run)
            start = None
    return runs


def build(items: list[MapItem], points: ept.Points, near=(0.0, 0.0), cell: float = 0.25,
          margin: float = 3.0) -> Park:
    import shapely
    from .city import rasterise
    mapped = find_park(items, near)
    if mapped is None:
        raise ValueError("No mapped skatepark (leisure=skatepark, or sport=skateboard) near the centre; "
                         "give --centre on the park")
    outline = shapely.Polygon(mapped.exterior)
    holes = [shapely.Polygon(ring) for ring in mapped.interiors if shapely.Polygon(ring).area >= 4.0]
    region = outline.buffer(margin)
    kept, removed = _surface_points(points, region.buffer(1.0))
    if len(kept) < 50:
        raise ValueError("The LiDAR has too few ground points over the park")
    grid = _grid(kept, region.buffer(0.5).bounds, cell)
    deck_cells = rasterise(outline.difference(shapely.union_all(holes)) if holes else outline, grid)
    deck = float(np.percentile(grid.heights[deck_cells], 70))
    notes = []
    from_map = bool(holes)
    outlines = holes if holes else _bowls_from_ground(grid, outline)
    if not holes:
        notes.append("the map has no bowl outlines (holes in the park); bowls were found in the ground")
    gy, gx = np.gradient(grid.heights, grid.cell)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    tree_xy = kept[:, :2]
    bowls, grinds = [], []
    for shape in outlines:
        inside = rasterise(shape, grid)
        if not inside.any():
            continue
        count = int(shapely.contains_xy(shape, tree_xy[:, 0], tree_xy[:, 1]).sum())
        coping = _coping(shape, grid)
        bowls.append(Bowl(shape, count, float(np.median(coping[:, 2])), float(np.percentile(grid.heights[inside], 2)),
                          float(np.percentile(slope[inside], 98)), from_map))
        if from_map:  # the map's outline is the coping, all round
            grinds.append(Grind(coping[:, :3], 0.05, "metal", "coping", closed=True))
        else:  # a found outline is coping only where it tops a steep wall
            grinds += [Grind(run, 0.05, "metal", "coping") for run in _lip_runs(coping, grid)]
    park_cells = rasterise(outline, grid)
    density = int(shapely.contains_xy(outline, tree_xy[:, 0], tree_xy[:, 1]).sum()) / max(outline.area, 1e-6)
    model = CityModel(region.bounds, grid, True, [], [], outline, outline, deck, notes, {"lidar"})
    model.grinds = grinds
    spawn = outline.difference(shapely.union_all([b.outline for b in bowls]).buffer(1.0)) if bowls else outline
    model.spawn = tuple(spawn.representative_point().coords[0]) if not spawn.is_empty else (0.0, 0.0)
    model.ground_keep = rasterise(region, grid)
    model.ground_surface = "concrete"
    if park_cells.any() and density < 20:
        notes.append(f"{density:.0f} ground points/m² over the park: bowl shapes and depths come through, "
                     "the coping lip and the top of vertical walls do not (a ground scan adds them)")
    return Park(model, outline, bowls, deck, density, removed, notes)


# ---------------------------------------------------------------- as a park mod

RUN_PER_HEIGHT = 1.4      # a Build Kit quarter pipe's run for its height (1.5 m for 1.0 m, 3 m for 2.4 m)
MIN_EDGE = 1.0            # metres: shorter outline edges are left to their neighbours' quarters
SIMPLIFY = 0.75           # metres: a bowl's outline is straightened at least this much into runs of quarters
FLAT_BOTTOM = 0.8         # a quarter's run is at most this share of the bowl's inner radius
MAX_OVERLAP = 0.4         # a quarter mostly on top of ones already placed is left out
MIN_QUARTER = 0.5         # metres: a hollow with room only for lower quarters is not built as a bowl


def inner_radius(outline) -> float:
    """The radius of the largest circle inside the outline (to 5 cm)."""
    lo, hi = 0.0, math.sqrt(outline.area)
    while hi - lo > 0.05:
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if not outline.buffer(-mid).is_empty else (lo, mid)
    return lo


def bowl_features(park: Park, max_height: float = 2.4) -> tuple[list[Feature], list[str]]:
    """Each bowl as a ring of quarter pipes, for a park mod: Build Kit objects stand on the base
    map and cannot dig, so a bowl becomes transitions rising out of the ground to its measured
    depth, with the coping along its real coping line.

    The outline is straightened into edges; along each, quarters face into the bowl, their tops
    on the edge. A quarter's run grows with its height, so in a narrow bowl the height is capped
    to leave a flat bottom. Quarters that would mostly sit on top of others (round bowls' many
    short edges) are left out, longest edges first: an approximation of the bowl's plan, not its
    exact shape."""
    import shapely
    from shapely.geometry.polygon import orient
    features, notes = [], []
    for number, bowl in enumerate(park.bowls):
        outline = bowl.outline
        if outline.geom_type == "MultiPolygon":
            outline = max(outline.geoms, key=lambda g: g.area)
        height = float(min(bowl.depth, max_height))
        room = FLAT_BOTTOM * inner_radius(outline) / RUN_PER_HEIGHT
        if bowl.depth > max_height:
            notes.append(f"bowl {number}: {bowl.depth:.1f} m deep; its quarters stop at {max_height:.1f} m")
        if room < MIN_QUARTER:
            notes.append(f"bowl {number}: too small for quarter pipes ({inner_radius(outline) * 2:.1f} m across)")
            continue
        if room < height:
            notes.append(f"bowl {number}: {inner_radius(outline) * 2:.1f} m across at its narrowest, so its quarters "
                         f"are {room:.1f} m high, not {height:.1f} m, to leave a flat bottom")
            height = room
        run = height * RUN_PER_HEIGHT
        straight = orient(outline.simplify(max(SIMPLIFY, run / 4), preserve_topology=True), 1.0)   # CCW
        ring = np.asarray(straight.exterior.coords)
        candidates = []
        for a, b in zip(ring[:-1], ring[1:]):
            edge = b - a
            length = float(np.hypot(*edge))
            if length < MIN_EDGE:
                continue
            outward = np.array([edge[1], -edge[0]]) / length          # right of a CCW edge: outside
            centre = (a + b) / 2 - outward * run / 2
            if not shapely.contains_xy(outline, *centre):
                continue
            candidates.append(Feature("quarter", (float(centre[0]), float(centre[1])),
                                      math.atan2(outward[1], outward[0]), run, length, height,
                                      notes=[f"bowl {number}"]))
        kept, taken = [], None
        for f in sorted(candidates, key=lambda f: -f.across):
            footprint = shapely.Polygon(f.footprint())
            if taken is not None and footprint.intersection(taken).area > MAX_OVERLAP * footprint.area:
                continue
            kept.append(f)
            taken = footprint if taken is None else taken.union(footprint)
        if not kept:
            notes.append(f"bowl {number}: too small for quarter pipes of {height:.1f} m")
        features += kept
    return features, notes


# ---------------------------------------------------------------- what's mapped in an area

def mapped_parks(items: list[MapItem], frame, names: dict[str, str | None] | None = None) -> list[dict]:
    """The skateparks mapped in an area (localised items), largest first: where, how big, and
    the `city --skatepark` area that would take in each with some margin."""
    found = []
    for item in items:
        if not is_skatepark(item):
            continue
        shape = item.geometry
        x0, y0, x1, y1 = shape.bounds
        centre = shape.centroid
        lon, lat = frame.to_lonlat(np.array([[centre.x, centre.y]]))[0]
        found.append({
            "name": (names or {}).get(item.id) or item.tag("name") or None, "id": item.id, "source": item.source,
            "area": round(float(shape.area), 1), "centre_local": [round(centre.x, 1), round(centre.y, 1)],
            "lat": round(float(lat), 7), "lon": round(float(lon), 7),
            "size": int(math.ceil((max(x1 - x0, y1 - y0) + 20) / 10) * 10),   # metres, with 10 m round it
        })
    return sorted(found, key=lambda p: -p["area"])
