"""A real place as a skateable model: ground, buildings and skate spots, from open map data and
aerial LiDAR.

Inputs, all in the local frame (geo.LocalFrame: x east, y north, metres):
- LiDAR points with ASPRS classes (ept.Points), or none;
- tagged map items (mapdata.MapItem) from OpenStreetMap and/or Overture.

What comes out (CityModel):
- ground: a bare-earth elevation grid from the ground-classified points;
- buildings: footprints extruded from their base to a roof height measured in the point cloud
  (or tagged, or guessed from levels);
- spots: skate features as features.Feature (the shapes the Build Kit converter already places),
  each with the ground elevation it stands on. They come from tags (steps and their handrails,
  benches, low walls and retaining walls, kerbs, planters) measured against the LiDAR, from
  ledges and boxes the point cloud shows in car-free public space, and from banks in the ground.

Privacy. Only ground, structures and street furniture are modelled. Buildings are closed blocks
(no interiors). Features are looked for only in public, car-free space (plazas, squares, parks,
sidewalks, footways), so parked cars and anything in a back yard are never read; heights above
1.2 m that are not buildings (people, vans, kiosks) are left out; and the bare-earth ground
outside public space and buildings is smoothed to a few metres, so private yards keep only the
lie of the land."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from . import ept, features as features_module, heightmap, mapdata
from .features import Feature
from .mapdata import MapItem

# Default widths (metres) of ways drawn as lines, by highway class.
ROAD_WIDTH = {"motorway": 14, "trunk": 13, "primary": 12, "secondary": 10, "tertiary": 9, "residential": 7,
              "unclassified": 7, "living_street": 6, "service": 4.5, "road": 7, "busway": 7}
WALK_WIDTH = {"footway": 2.5, "pedestrian": 6, "path": 2, "steps": 2.5, "cycleway": 2, "corridor": 2,
              "track": 3, "bridleway": 2}
SOFT = {("landuse", "grass"), ("landuse", "meadow"), ("leisure", "garden"), ("natural", "scrub"),
        ("natural", "grassland"), ("natural", "wood"), ("landuse", "forest"), ("surface", "grass"),
        ("overture:class", "grass"), ("overture:class", "garden"), ("overture:class", "meadow"),
        ("overture:class", "forest"), ("overture:class", "flowerbed"), ("landuse", "flowerbed")}
PUBLIC_AREAS = {("place", "square"), ("leisure", "park"), ("leisure", "skatepark"), ("leisure", "playground"),
                ("leisure", "pitch"), ("highway", "pedestrian"), ("amenity", "marketplace"),
                ("overture:class", "pedestrian"), ("overture:class", "plaza"), ("overture:class", "park"),
                ("landuse", "recreation_ground"), ("landuse", "village_green")}
MAX_FEATURE_HEIGHT = 1.2   # taller than this and not a building: not a skate feature (or a person, a van)
PRIORITY = {"stairs": 0, "rail": 0, "ledge": 1, "manual_pad": 2, "box": 2, "bank": 2, "kicker": 2, "quarter": 3}


@dataclass
class Grid:
    """Elevations on a regular grid: heights[row, col] at origin + (col, row) * cell."""
    heights: np.ndarray
    origin: np.ndarray
    cell: float

    @property
    def shape(self):
        return self.heights.shape

    def centres(self) -> tuple[np.ndarray, np.ndarray]:
        rows, cols = self.shape
        return np.meshgrid(self.origin[0] + np.arange(cols) * self.cell, self.origin[1] + np.arange(rows) * self.cell)

    def sample(self, xy) -> np.ndarray:
        """Bilinear elevation at points (n, 2); clamped at the edges."""
        xy = np.atleast_2d(np.asarray(xy, float))
        rows, cols = self.shape
        c = np.clip((xy[:, 0] - self.origin[0]) / self.cell, 0, cols - 1)
        r = np.clip((xy[:, 1] - self.origin[1]) / self.cell, 0, rows - 1)
        c0, r0 = np.minimum(c.astype(int), cols - 2 if cols > 1 else 0), np.minimum(r.astype(int), rows - 2 if rows > 1 else 0)
        c1, r1 = np.minimum(c0 + 1, cols - 1), np.minimum(r0 + 1, rows - 1)
        fc, fr = c - c0, r - r0
        h = self.heights
        return ((h[r0, c0] * (1 - fc) + h[r0, c1] * fc) * (1 - fr) + (h[r1, c0] * (1 - fc) + h[r1, c1] * fc) * fr)

    def cell_of(self, xy) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Row, column and an inside-the-grid flag for points (n, 2)."""
        xy = np.atleast_2d(np.asarray(xy, float))
        c = np.round((xy[:, 0] - self.origin[0]) / self.cell).astype(int)
        r = np.round((xy[:, 1] - self.origin[1]) / self.cell).astype(int)
        inside = (r >= 0) & (r < self.shape[0]) & (c >= 0) & (c < self.shape[1])
        return np.clip(r, 0, self.shape[0] - 1), np.clip(c, 0, self.shape[1] - 1), inside


@dataclass
class Building:
    footprint: object        # shapely Polygon, local metres
    base: float              # elevation of the ground around it
    height: float            # roof above base
    height_from: str         # "lidar", "tag", "levels" or "default"
    source: str


@dataclass
class Spot:
    feature: Feature         # local frame; heights above `ground`
    ground: float            # elevation the feature stands on
    footing: float           # lowest ground beside it (a retaining wall's low side); == ground usually
    source: str              # "osm", "overture", "lidar" or "ground"
    what: str                # a short description: "bench", "steps", "retaining wall"...

    @property
    def priority(self) -> int:
        return PRIORITY.get(self.feature.kind, 3) + (1 if self.source == "lidar" else 0)


@dataclass
class Settings:
    ground_cell: float = 0.5      # bare-earth grid
    detect_cell: float = 0.1      # height grid for finding ledges in the point cloud
    private_blur: float = 3.0     # metres: how much the ground outside public space is smoothed
    detect_lidar: bool = True     # look for untagged ledges and boxes in the point cloud
    detect_banks: bool = True     # look for banks in the ground


@dataclass
class CityModel:
    bounds: tuple[float, float, float, float]
    ground: Grid
    ground_measured: bool
    buildings: list[Building]
    spots: list[Spot]
    public: object                # shapely: public space
    car_free: object              # shapely: public space without roads, buildings or soft ground
    origin_elevation: float       # ground elevation at the frame's origin; the game's y = 0
    notes: list[str] = field(default_factory=list)
    sources: set[str] = field(default_factory=set)
    # For meshes (citymesh.py): grind curves beyond the spots' own, where a skater starts, which
    # ground cells to keep (None: all), and the ground's surface.
    grinds: list = field(default_factory=list)
    spawn: tuple[float, float] | None = None
    ground_keep: np.ndarray | None = None
    ground_surface: str = "asphalt"


# ---------------------------------------------------------------- ground

def ground_grid(points: ept.Points | None, bounds, cell: float) -> tuple[Grid, bool]:
    """Bare earth: the median of the ground points in each cell, holes filled smoothly."""
    xmin, ymin, xmax, ymax = bounds
    cols = max(2, int(math.ceil((xmax - xmin) / cell)) + 1)
    rows = max(2, int(math.ceil((ymax - ymin) / cell)) + 1)
    origin = np.array([xmin, ymin])
    ground = points.of(ept.GROUND, ept.WATER, ept.BRIDGE) if points is not None else np.zeros((0, 3))
    if len(ground) < 10:
        return Grid(np.zeros((rows, cols), np.float64), origin, cell), False
    c = np.round((ground[:, 0] - xmin) / cell).astype(int)
    r = np.round((ground[:, 1] - ymin) / cell).astype(int)
    inside = (r >= 0) & (r < rows) & (c >= 0) & (c < cols)
    flat, z = (r * cols + c)[inside], ground[inside, 2]
    order = np.lexsort((z, flat))
    flat, z = flat[order], z[order]
    counts = np.bincount(flat, minlength=rows * cols)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    has = counts > 0
    heights = np.full(rows * cols, np.nan)
    heights[has] = z[starts[has] + (counts[has] - 1) // 2]
    heights = heights.reshape(rows, cols)
    holes = np.isnan(heights)
    _, (near_r, near_c) = ndimage.distance_transform_edt(holes, return_indices=True)
    heights = heights[near_r, near_c]
    # Relax the filled cells towards their neighbours, so a building's ground is a smooth patch
    # between the streets around it, not a mosaic of the nearest ground points.
    for _ in range(30):
        relaxed = ndimage.uniform_filter(heights, size=3, mode="nearest")
        heights[holes] = relaxed[holes]
    return Grid(heights, origin, cell), True


def rasterise(geometry, grid: Grid) -> np.ndarray:
    import shapely
    if geometry is None or geometry.is_empty:
        return np.zeros(grid.shape, bool)
    xs, ys = grid.centres()
    shapely.prepare(geometry)
    return shapely.contains_xy(geometry, xs, ys)


# ---------------------------------------------------------------- areas

def _width(item: MapItem, table: dict, default: float) -> float:
    return mapdata.length_tag(item.tag("width")) or mapdata.length_tag(item.tag("est_width")) or table.get(item.tag("highway"), default)


def areas(items: list[MapItem]) -> tuple[object, object, object, object]:
    """Public space, roads (where cars go), buildings and soft ground (grass, beds), as shapely geometries."""
    from shapely.ops import unary_union

    public, roads, buildings, soft = [], [], [], []
    for item in items:
        g, tags = item.geometry, item.tags
        polygonal = g.geom_type in ("Polygon", "MultiPolygon")
        if polygonal and any(tags.get(k) == v for k, v in SOFT):
            soft.append(g)
        if "building" in tags and polygonal and tags.get("building") != "no":
            buildings.append(g)
            continue
        highway = tags.get("highway", "")
        if polygonal and (tags.get("area:highway") or any(tags.get(k) == v for k, v in PUBLIC_AREAS)):
            if tags.get("area:highway") in ROAD_WIDTH:
                roads.append(g)
            else:
                public.append(g)
        elif g.geom_type in ("LineString", "MultiLineString") and highway:
            if highway in ROAD_WIDTH:
                roads.append(g.buffer(_width(item, ROAD_WIDTH, 7) / 2, cap_style="flat"))
            elif highway in WALK_WIDTH:
                public.append(g.buffer(_width(item, WALK_WIDTH, 2) / 2))
    return tuple(unary_union(p) if p else None for p in (public, roads, buildings, soft))


# ---------------------------------------------------------------- buildings

def buildings_from(items: list[MapItem], ground: Grid, lidar: ept.Points | None,
                   tree: tuple[cKDTree, np.ndarray] | None) -> list[Building]:
    import shapely

    chosen: list[tuple[object, MapItem]] = []
    for item in sorted(items, key=lambda i: i.source != "osm"):  # OpenStreetMap first, Overture fills gaps
        g = item.geometry
        if "building" not in item.tags or item.tags.get("building") == "no" or g.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        if g.area < 4:
            continue
        if any(g.intersection(other).area > 0.5 * min(g.area, other.area) for other, _ in chosen):
            continue
        chosen.append((g, item))

    result = []
    for footprint, item in chosen:
        edge = np.array(footprint.exterior.coords if footprint.geom_type == "Polygon"
                        else [c for p in footprint.geoms for c in p.exterior.coords])
        edge = _densify(edge, ground.cell)
        base = float(np.percentile(ground.sample(edge), 10))
        height, how = None, ""
        if tree is not None:
            points_tree, z = tree
            inner = footprint.buffer(-0.75)
            if not inner.is_empty:
                xmin, ymin, xmax, ymax = inner.bounds
                candidates = points_tree.query_ball_point([(xmin + xmax) / 2, (ymin + ymax) / 2],
                                                          math.hypot(xmax - xmin, ymax - ymin) / 2)
                if candidates:
                    candidates = np.array(candidates)
                    xy = points_tree.data[candidates]
                    inside = candidates[shapely.contains_xy(inner, xy[:, 0], xy[:, 1])]
                    above = z[inside] - base
                    roof = above[above > 2.5]
                    if len(roof) >= 30 and len(roof) >= 0.2 * len(above):
                        height, how = float(np.percentile(roof, 75)), "lidar"
        if height is None and mapdata.length_tag(item.tag("height")):
            height, how = mapdata.length_tag(item.tag("height")), "tag"
        if height is None and mapdata.count_tag(item.tag("building:levels")):
            height, how = 3.2 * mapdata.count_tag(item.tag("building:levels")) + 1.0, "levels"
        if height is None:
            height, how = 8.0, "default"
        result.append(Building(footprint, base, float(height), how, item.source))
    return result


# ---------------------------------------------------------------- measuring tagged things

def _densify(coords: np.ndarray, step: float) -> np.ndarray:
    out = [coords[:1]]
    for a, b in zip(coords[:-1], coords[1:]):
        n = max(1, int(math.ceil(np.hypot(*(b - a)) / step)))
        out.append(a + (b - a) * (np.arange(1, n + 1) / n)[:, None])
    return np.vstack(out)


def straight_runs(coords: np.ndarray, turn_degrees: float = 20.0, min_length: float = 0.8) -> list[tuple[np.ndarray, np.ndarray]]:
    """A polyline as its straight stretches: consecutive segments turning less than turn_degrees join."""
    coords = np.asarray(coords, float)[:, :2]
    runs, start = [], 0
    for i in range(1, len(coords) - 1):
        a, b = coords[i] - coords[start], coords[i + 1] - coords[i]
        if np.hypot(*a) < 1e-6 or np.hypot(*b) < 1e-6:
            continue
        turn = abs(math.degrees(math.atan2(a[0] * b[1] - a[1] * b[0], a @ b)))
        if turn > turn_degrees:
            runs.append((coords[start], coords[i]))
            start = i
    runs.append((coords[start], coords[-1]))
    return [(a, b) for a, b in runs if np.hypot(*(b - a)) >= min_length]


class Measure:
    """Heights of things above the ground, from the non-ground LiDAR points near them."""

    def __init__(self, lidar: ept.Points | None, ground: Grid):
        self.ground = ground
        self.tree = None
        if lidar is not None and len(lidar.xyz):
            above = lidar.xyz[np.isin(lidar.classes, (0, 1, ept.BUILDING, ept.LOW_VEG))]
            if len(above):
                self.tree = cKDTree(above[:, :2])
                self.z = above[:, 2]

    def near_line(self, a: np.ndarray, b: np.ndarray, radius: float) -> np.ndarray:
        if self.tree is None:
            return np.zeros(0, int)
        samples = _densify(np.array([a, b]), radius / 2)
        found = set()
        for hits in self.tree.query_ball_point(samples, radius):
            found.update(hits)
        return np.fromiter(found, int, len(found))

    def top(self, a, b, radius: float = 0.3, offset: float = 0.9) -> float | None:
        """Height of the top of whatever runs from a to b above the higher ground beside it (a
        retaining wall's upper side), point by point; None when the scan shows nothing there."""
        hits = self.near_line(a, b, radius)
        if len(hits) < 8:
            return None
        u = (b - a) / max(np.hypot(*(b - a)), 1e-9)
        v = np.array([-u[1], u[0]])
        xy = self.tree.data[hits]
        beside = np.maximum(self.ground.sample(xy + v * offset), self.ground.sample(xy - v * offset))
        rise = self.z[hits] - beside
        rise = rise[(rise > 0.05) & (rise < MAX_FEATURE_HEIGHT + 0.6)]
        if len(rise) < max(8, 0.25 * len(hits)):
            return None
        return float(np.percentile(rise, 80))

    def orientation(self, centre: np.ndarray, base: float, radius: float = 1.4) -> tuple[float, float] | None:
        """Long axis (yaw) and length of a small object (a bench) from its points; None if unclear."""
        if self.tree is None:
            return None
        hits = np.array(self.tree.query_ball_point(centre, radius), int)
        if len(hits) < 15:
            return None
        rise = self.z[hits] - base
        hits = hits[(rise > 0.2) & (rise < MAX_FEATURE_HEIGHT)]
        if len(hits) < 15:
            return None
        xy = self.tree.data[hits] - centre
        values, vectors = np.linalg.eigh(np.cov(xy.T))
        if values[1] < 2.0 * max(values[0], 1e-6):
            return None
        major = vectors[:, 1]
        along = xy @ major
        return math.atan2(major[1], major[0]), float(np.ptp(along))

    def sides(self, a, b, offset: float = 0.9) -> tuple[float, float]:
        """Ground elevation either side of a line, at its middle: (higher, lower)."""
        u = (b - a) / max(np.hypot(*(b - a)), 1e-9)
        v = np.array([-u[1], u[0]])
        middle = (a + b) / 2
        left, right = self.ground.sample(np.array([middle + v * offset, middle - v * offset]))
        return float(max(left, right)), float(min(left, right))


def _linear_feature(kind: str, a, b, height: float, across: float, notes: list[str]) -> Feature:
    d = b - a
    return Feature(kind, tuple((a + b) / 2), math.atan2(d[1], d[0]), float(np.hypot(*d)), across, float(height),
                   notes=notes)


def spots_from_tags(items: list[MapItem], measure: Measure, ground: Grid, notes: list[str]) -> list[Spot]:
    spots: list[Spot] = []
    for item in items:
        tags, g = item.tags, item.geometry
        what = ""
        if tags.get("highway") == "steps" and g.geom_type == "LineString":
            spots += _steps(item, measure, ground, notes)
            continue
        if tags.get("amenity") == "bench" or tags.get("leisure") == "picnic_table":
            what = "bench"
        elif tags.get("barrier") in ("wall", "retaining_wall"):
            what = tags["barrier"].replace("_", " ")
        elif tags.get("barrier") == "kerb" or tags.get("kerb") in ("raised", "regular"):
            what = "kerb"
        elif tags.get("barrier") == "planter" or tags.get("man_made") == "planter":
            what = "planter"
        elif tags.get("barrier") == "handrail":
            what = "handrail"
        if not what:
            continue
        if g.geom_type == "Point":
            if what != "bench":
                continue
            spots += _point_bench(item, measure, ground)
            continue
        if g.geom_type in ("Polygon", "MultiPolygon"):
            g = g.boundary if what != "bench" else g.minimum_rotated_rectangle.boundary
        lines = list(g.geoms) if g.geom_type.startswith("Multi") else [g]
        for line in lines:
            for a, b in straight_runs(np.array(line.coords), min_length=1.0):
                spot = _ledge_like(item, what, a, b, measure)
                if spot:
                    spots.append(spot)
    return spots


def _ledge_like(item: MapItem, what: str, a, b, measure: Measure) -> Spot | None:
    high, low = measure.sides(a, b)
    tagged = mapdata.length_tag(item.tag("height"))
    measured = measure.top(a, b, radius=0.3)
    defaults = {"bench": 0.45, "kerb": 0.15, "planter": 0.5, "handrail": 0.9, "wall": None, "retaining wall": None}
    height = measured if measured is not None else tagged if tagged is not None else defaults.get(what)
    source_note = "measured" if measured is not None else "tagged" if tagged is not None else "assumed"
    if height is None or height < 0.08 or height > MAX_FEATURE_HEIGHT:
        return None
    width = mapdata.length_tag(item.tag("width")) or {"bench": 0.5, "kerb": 0.3, "handrail": 0.06}.get(what, 0.4)
    kind = "rail" if what == "handrail" else "ledge"
    feature = _linear_feature(kind, a, b, height, width, [f"{what}, {height:.2f} m ({source_note})"])
    return Spot(feature, high, low, item.source, what)


def _point_bench(item: MapItem, measure: Measure, ground: Grid) -> list[Spot]:
    centre = np.array(item.geometry.coords[0])
    base = float(ground.sample(centre)[0])
    yaw, length = None, 1.8
    found = measure.orientation(centre, base)
    if found:
        yaw, length = found[0], float(np.clip(found[1], 1.0, 4.0))
    elif item.tag("direction"):
        try:  # the way a sitter faces: the bench runs across it
            yaw = math.radians(90 - float(item.tag("direction"))) + math.pi / 2
        except ValueError:
            pass
    if yaw is None:
        return []  # a bench we cannot orient is better left out than placed crooked
    u = np.array([math.cos(yaw), math.sin(yaw)])
    a, b = centre - u * length / 2, centre + u * length / 2
    height = measure.top(a, b, radius=0.35) or mapdata.length_tag(item.tag("height")) or 0.45
    feature = _linear_feature("ledge", a, b, min(height, MAX_FEATURE_HEIGHT), 0.5, [f"bench, {height:.2f} m"])
    return [Spot(feature, base, base, item.source, "bench")]


def _steps(item: MapItem, measure: Measure, ground: Grid, notes: list[str]) -> list[Spot]:
    spots = []
    tags = item.tags
    count = mapdata.count_tag(tags.get("step_count"))
    for a, b in straight_runs(np.array(item.geometry.coords), min_length=0.8):
        u = (b - a) / np.hypot(*(b - a))
        # The landings just past each end.
        ends = ground.sample(np.array([a - u * 0.5, b + u * 0.5]))
        if ends[0] > ends[1]:
            a, b, u, ends = b, a, -u, ends[::-1]
        rise = float(ends[1] - ends[0])
        if rise >= 0.25:
            a, b, rise = _flight_extent(ground, a, b, u, rise)
        elif count:
            rise = count * 0.16
        if rise < 0.25:
            notes.append(f"steps {item.id or ''} at ({(a[0] + b[0]) / 2:.0f}, {(a[1] + b[1]) / 2:.0f}): "
                         f"no rise in the ground model, left out")
            continue
        width = mapdata.length_tag(tags.get("width")) or _stair_width(ground, a, b, u, rise)
        run = float(np.hypot(*(b - a)))
        steps = count or max(2, int(round(rise / 0.16)))
        yaw = math.atan2(u[1], u[0])
        centre = (a + b) / 2
        stairs = Feature("stairs", tuple(centre), yaw, run, width, rise, steps=steps,
                         notes=[f"{steps} steps, {rise:.2f} m rise over {run:.1f} m"])
        spots.append(Spot(stairs, float(ends[0]), float(ends[0]), item.source, "steps"))
        # Handrails: handrail=yes, or handrail:left/right/center.
        v = np.array([-u[1], u[0]])
        sides = []
        if tags.get("handrail:center") == "yes":
            sides.append(0.0)
        if tags.get("handrail:left") == "yes" or tags.get("handrail") == "yes":
            sides.append(width / 2 - 0.15)
        if tags.get("handrail:right") == "yes" or tags.get("handrail") == "yes":
            sides.append(-(width / 2 - 0.15))
        pitch = math.atan2(rise, run)
        for side in sides:
            rail_centre = centre + v * side
            rail = Feature("rail", tuple(rail_centre), yaw, run / math.cos(pitch), 0.05, rise / 2 + 0.85,
                           pitch=pitch, notes=[f"handrail down {steps} steps"])
            spots.append(Spot(rail, float(ends[0]), float(ends[0]), item.source, "handrail"))
    return spots


def _flight_extent(ground: Grid, a, b, u, rise: float) -> tuple[np.ndarray, np.ndarray, float]:
    """Where along a steps line the ground actually climbs: a mapped line often runs on past the
    flight. The climb from 10 % to 90 % of the rise is 80 % of the flight; its rise is measured
    just beyond each end."""
    length = float(np.hypot(*(b - a)))
    t = np.arange(-1.0, length + 1.0 + 1e-9, ground.cell / 4)
    profile = ground.sample(a + t[:, None] * u) - ground.sample(np.array([a - u * 0.5]))[0]
    low, high = np.argmax(profile > 0.1 * rise), np.argmax(profile >= 0.9 * rise)
    if high <= low:
        return a, b, rise
    middle, run = (t[low] + t[high]) / 2, max((t[high] - t[low]) / 0.8, 0.3)
    foot, head = a + u * (middle - run / 2), a + u * (middle + run / 2)
    ends = ground.sample(np.array([foot - u * 0.25, head + u * 0.25]))
    return foot, head, float(ends[1] - ends[0])


def _stair_width(ground: Grid, a, b, u, rise: float) -> float:
    """How wide a flight is: how far either side of its line the ground still climbs the same
    rise evenly from foot to head. A retaining wall beside the steps climbs it all at once."""
    v = np.array([-u[1], u[0]])
    offsets = np.arange(-8, 8.01, ground.cell / 2)
    fractions = np.linspace(0, 1, 7)
    run_points = a[None, :] + fractions[:, None] * (b - a)[None, :]
    probes = run_points[None, :, :] + offsets[:, None, None] * v[None, None, :]
    foot = ground.sample(np.array([a - u * 0.5]))[0]
    profile = ground.sample(probes.reshape(-1, 2)).reshape(len(offsets), len(fractions)) - foot
    linear = fractions[None, :] * rise
    even = (np.abs(profile - linear).max(axis=1) < 0.3 * rise) & (np.abs(profile[:, -1] - rise) < 0.3 * rise)
    centre = len(offsets) // 2
    if not even[centre]:
        return 2.5
    lo = hi = centre
    while lo > 0 and even[lo - 1]:
        lo -= 1
    while hi < len(offsets) - 1 and even[hi + 1]:
        hi += 1
    return float(np.clip(offsets[hi] - offsets[lo] + ground.cell / 2, 1.0, 16.0))


# ---------------------------------------------------------------- from the point cloud

def spots_from_lidar(lidar: ept.Points, ground: Grid, car_free_mask: np.ndarray, cell: float) -> list[Spot]:
    """Ledges, boxes and pads the point cloud shows in car-free public space.

    Aerial surveys often leave vegetation unclassified, and a shrub or a hedge reads as a raised
    patch too. So a candidate counts only when its top is a plane (to the survey's few
    centimetres of noise), most of the points over it are on that top, nothing hangs over it, and
    the pulses that hit it stopped there: a pulse that returns more than once went through leaves."""
    chosen = np.isin(lidar.classes, (0, 1, ept.GROUND, ept.LOW_VEG, ept.MED_VEG, ept.HIGH_VEG))
    pts, layered = lidar.xyz[chosen], lidar.layered[chosen]
    r, c, inside = ground.cell_of(pts[:, :2])
    keep = inside & car_free_mask[r, c]
    pts, layered = pts[keep], layered[keep]
    if len(pts) < 100:
        return []
    above = pts.copy()
    above[:, 2] -= ground.sample(pts[:, :2])
    # Ground points are the ground by definition: where the grid rounds off a terrace's edge,
    # they would otherwise stand up as a false box along it.
    above[lidar.classes[chosen][keep] == ept.GROUND, 2] = 0.0
    tree = cKDTree(above[:, :2])
    low = above[above[:, 2] < MAX_FEATURE_HEIGHT + 0.3]
    hm = heightmap.build(low, cell=cell, ceiling=MAX_FEATURE_HEIGHT + 0.3, floor=-1.0)
    settings = features_module.Settings(ground_tolerance=0.1, rail_min_length=1.5, min_plateau_area=0.4,
                                        min_slope_area=2.0)
    spots = []
    for f in features_module.detect(hm, settings).features:
        if f.kind not in ("ledge", "manual_pad", "box") or not 0.1 <= f.height <= MAX_FEATURE_HEIGHT:
            continue
        if f.along < 1.0 or not _hard_top(f, above, layered, tree):
            continue
        base = float(ground.sample(np.array(f.centre))[0])
        spots.append(Spot(f, base, base, "lidar", f"{f.kind} seen in the LiDAR"))
    return spots


def _hard_top(f: Feature, above: np.ndarray, layered: np.ndarray, tree: cKDTree, flatness: float = 0.06) -> bool:
    import shapely
    top = shapely.Polygon(f.footprint()).buffer(-min(0.15, f.across / 4))
    if top.is_empty:
        return False
    hits = np.array(tree.query_ball_point(f.centre, math.hypot(f.along, f.across) / 2), int)
    if len(hits) < 10:
        return False
    hits = hits[shapely.contains_xy(top, above[hits, 0], above[hits, 1])]
    if len(hits) < 10:
        return False
    z = above[hits, 2]
    on_top = np.abs(z - f.height) < 0.12
    overhang = z > f.height + 0.3
    if on_top.sum() < 0.7 * len(hits) or overhang.sum() > 0.05 * len(hits) or layered[hits].mean() > 0.1:
        return False
    xyz = above[hits][on_top]
    design = np.column_stack([xyz[:, 0], xyz[:, 1], np.ones(len(xyz))])
    coefficients, *_ = np.linalg.lstsq(design, xyz[:, 2], rcond=None)
    residual = xyz[:, 2] - design @ coefficients
    return float(np.std(residual)) <= flatness and math.hypot(*coefficients[:2]) < 0.1


def banks_from_ground(ground: Grid, car_free_mask: np.ndarray, avoid: list[Spot]) -> list[Spot]:
    """Sloped ground in car-free space: banks, like a plaza's ramps and embankments.

    A patch of ridable slope is fitted with a plane, and the cells off it (a drop at the bank's
    back, its sides' rounding) are let go; a patch that is not mostly one plane is a lawn's roll
    or a berm, not a bank."""
    import shapely
    smooth = ndimage.gaussian_filter(ground.heights, 0.5)
    gy, gx = np.gradient(smooth, ground.cell)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    mask = car_free_mask & (slope >= 12) & (slope <= 40)
    if avoid:
        blocked = shapely.union_all([shapely.Polygon(s.feature.footprint()).buffer(1.0) for s in avoid])
        mask &= ~rasterise(blocked, ground)
    labels, _ = ndimage.label(mask)
    spots = []
    xs, ys = ground.centres()
    for index, slices in enumerate(ndimage.find_objects(labels), start=1):
        region = labels[slices] == index
        if region.sum() * ground.cell ** 2 < 4.0:
            continue
        rows, cols = np.nonzero(region)
        rows, cols = rows + slices[0].start, cols + slices[1].start
        points = np.column_stack([xs[rows, cols], ys[rows, cols]])
        z = ground.heights[rows, cols]
        on = np.ones(len(z), bool)
        for _ in range(3):
            design = np.column_stack([points[on], np.ones(on.sum())])
            coefficients, *_ = np.linalg.lstsq(design, z[on], rcond=None)
            residual = np.abs(z - np.column_stack([points, np.ones(len(z))]) @ coefficients)
            on = residual < max(0.06, 2 * float(np.std(residual[on])))
        grade = math.hypot(*coefficients[:2])
        if on.sum() < 0.7 * len(z) or on.sum() * ground.cell ** 2 < 4.0 or not 0.2 <= grade <= 0.85:
            continue
        uphill = math.atan2(coefficients[1], coefficients[0])
        centre, run, width = features_module.extents(points[on], uphill, ground.cell / 2)
        height = grade * run
        if height < 0.3 or width < 1.0 or run < 1.5 or run > 20 or width > 40:
            continue
        low = float(np.percentile(z[on], 2))
        bank = Feature("bank", tuple(centre), uphill, run, width, height,
                       notes=[f"ground slope {math.degrees(math.atan(grade)):.0f} degrees"])
        spots.append(Spot(bank, low, low, "ground", "bank in the ground"))
    return spots


def _dedupe(tagged: list[Spot], found: list[Spot]) -> list[Spot]:
    """Drop point-cloud features that a tagged one already covers."""
    import shapely
    if not tagged:
        return found
    taken = shapely.union_all([shapely.Polygon(s.feature.footprint()).buffer(0.6) for s in tagged])
    kept = []
    for s in found:
        shape = shapely.Polygon(s.feature.footprint())
        if shape.intersection(taken).area < 0.3 * shape.area:
            kept.append(s)
    return kept


# ---------------------------------------------------------------- the whole place

def build(bounds, items: list[MapItem], lidar: ept.Points | None, settings: Settings | None = None,
          progress=None) -> CityModel:
    import shapely
    s = settings or Settings()
    say = progress or (lambda _text: None)
    notes: list[str] = []
    xmin, ymin, xmax, ymax = bounds
    area = shapely.box(xmin, ymin, xmax, ymax)
    items = [i for i in items if i.geometry.intersects(area)]
    for item in items:  # buildings end at the area's edge, where the LiDAR does
        if "building" in item.tags and item.geometry.geom_type in ("Polygon", "MultiPolygon"):
            item.geometry = item.geometry.intersection(area)

    ground, measured = ground_grid(lidar, bounds, s.ground_cell)
    if not measured:
        notes.append("no ground points: the ground is flat")
    public, roads, building_area, soft = areas(items)
    public = public.intersection(area) if public is not None else shapely.Polygon()
    car_free = public
    for cut in (roads, building_area, soft):
        if cut is not None:
            car_free = car_free.difference(cut)
    say(f"  public space {public.area:,.0f} m², hard and car-free {car_free.area:,.0f} m²")

    # Privacy: the bare earth outside public space and buildings keeps only the lie of the land.
    if measured and s.private_blur > 0:
        keep_detail = rasterise(public, ground) | rasterise(building_area, ground) | rasterise(roads, ground)
        blurred = ndimage.gaussian_filter(ground.heights, s.private_blur / ground.cell, mode="nearest")
        ground.heights = np.where(keep_detail, ground.heights, blurred)

    measure = Measure(lidar, ground)
    tree = (measure.tree, measure.z) if measure.tree is not None else None
    buildings = buildings_from(items, ground, lidar, tree)
    say(f"  {len(buildings)} buildings")

    car_free_mask = rasterise(car_free, ground)
    tagged = [sp for sp in spots_from_tags(items, measure, ground, notes)
              if car_free.buffer(1.0).intersects(shapely.Point(sp.feature.centre)) or sp.what in ("steps", "handrail")]
    say(f"  {len(tagged)} skate spots from map tags")
    found: list[Spot] = []
    if lidar is not None and s.detect_lidar:
        found = _dedupe(tagged, spots_from_lidar(lidar, ground, car_free_mask, s.detect_cell))
        say(f"  {len(found)} more ledges and boxes from the LiDAR")
    if measured and s.detect_banks:
        banks = _dedupe(tagged + found, banks_from_ground(ground, car_free_mask, [t for t in tagged if t.what == "steps"]))
        say(f"  {len(banks)} banks in the ground")
        found += banks
    spots = sorted(tagged + found, key=lambda sp: (sp.priority, -sp.feature.along))
    for b in buildings:
        if b.height_from == "default":
            notes.append(f"building at ({b.footprint.centroid.x:.0f}, {b.footprint.centroid.y:.0f}): "
                         "no height measured or tagged, assumed 8 m")
    origin_elevation = float(ground.sample(np.array([0.0, 0.0]))[0]) if xmin <= 0 <= xmax and ymin <= 0 <= ymax \
        else float(np.median(ground.heights))
    sources = {i.source for i in items}
    if measured:
        sources.add("lidar")
    return CityModel(tuple(bounds), ground, measured, buildings, spots, public, car_free, origin_elevation, notes, sources)


# ---------------------------------------------------------------- as a park

def park_features(model: CityModel, max_objects: int, catalog, keep_elevation: bool = False):
    """The spots as Features for catalog.place, most worthwhile first, within an object budget.
    Each stands on its own ground (z = 0) unless keep_elevation, when it sits at its real
    elevation relative to the frame's origin."""
    from . import catalog as catalog_module
    chosen, placements, skipped = [], [], []
    for spot in model.spots:
        feature = spot.feature
        new, why = catalog_module.place([feature], catalog)
        if not new:
            skipped += why
            continue
        if len(placements) + len(new) > max_objects:
            skipped.append(f"{spot.what} at ({feature.centre[0]:.0f}, {feature.centre[1]:.0f}): over the "
                           f"{max_objects}-object budget")
            continue
        index = len(chosen)
        lift = spot.ground - model.origin_elevation if keep_elevation else 0.0
        for p in new:
            p.feature = index
            p.z += lift
        chosen.append(feature)
        placements += new
    return chosen, placements, skipped
