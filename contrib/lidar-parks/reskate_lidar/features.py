"""Finding a skatepark's obstacles in a height grid.

Each obstacle comes out as a simple shape the Build Kit can stand in for: a rail, a ledge, a
manual pad, a box, a set of stairs, a flat bank or kicker, a quarter pipe. Bowls (ground below
the park floor) are found and reported, but the Build Kit has no way to dig, so they are not
converted.

How:
1. Thin raised lines that a 0.35 m disc cannot fit inside are rails.
2. The rest of the raised ground is split by slope: flat tops (plateaus), and slopes (ramps).
3. Narrow plateaus stepping up one after another are stairs.
4. Other plateaus are ledges, manual pads or boxes by width and height.
5. Slopes are kickers or banks when planar, quarter pipes when they curve up.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from scipy.spatial import ConvexHull, QhullError

from .heightmap import HeightMap

KINDS = ("rail", "ledge", "manual_pad", "box", "stairs", "kicker", "bank", "quarter", "bowl")


@dataclass
class Feature:
    kind: str
    centre: tuple[float, float]  # footprint centre, levelled scan frame (metres)
    yaw: float                   # radians from +x towards +y: the long axis, or uphill for slopes
    along: float                 # extent along yaw
    across: float                # extent across yaw
    height: float                # top above the ground
    base: float = 0.0            # bottom above the ground (a rail's underside, a bowl's floor)
    pitch: float = 0.0           # radians, rising along yaw (handrails down stairs)
    steps: int = 0               # stairs only
    cells: int = 0
    notes: list[str] = field(default_factory=list)

    def footprint(self) -> np.ndarray:
        """Four corners (x, y) of the footprint rectangle."""
        u = np.array([math.cos(self.yaw), math.sin(self.yaw)])
        v = np.array([-u[1], u[0]])
        c = np.array(self.centre)
        a, b = self.along / 2, self.across / 2
        return np.array([c + a * u + b * v, c - a * u + b * v, c - a * u - b * v, c + a * u - b * v])


@dataclass
class Settings:
    ground_tolerance: float = 0.06  # heights within this of the ground are ground
    rail_disc: float = 0.35         # a rail is raised ground narrower than this disc
    rail_min_length: float = 0.8
    flat_degrees: float = 12.0      # flatter than this: a plateau
    steep_degrees: float = 65.0     # steeper than this: a side wall, not a ridable slope
    min_plateau_area: float = 0.08
    min_slope_area: float = 0.3
    bowl_depth: float = 0.25


@dataclass
class Detection:
    features: list[Feature]
    ignored: list[str]


# ---------------------------------------------------------------- shapes

def rectangle(points: np.ndarray, pad: float) -> tuple[np.ndarray, float, float, float]:
    """Minimum-area rectangle around 2-D points: centre, yaw of its long side, long, short."""
    if len(points) < 3:
        centre = points.mean(axis=0)
        return centre, 0.0, pad * 2, pad * 2
    try:
        hull = points[ConvexHull(points).vertices]
    except QhullError:
        hull = points
    best = None
    edges = np.roll(hull, -1, axis=0) - hull
    for edge in edges:
        length = np.hypot(*edge)
        if length < 1e-9:
            continue
        u = edge / length
        v = np.array([-u[1], u[0]])
        pu, pv = hull @ u, hull @ v
        area = (pu.max() - pu.min()) * (pv.max() - pv.min())
        if best is None or area < best[0]:
            best = (area, u, v, pu.min(), pu.max(), pv.min(), pv.max())
    if best is None:
        centre = points.mean(axis=0)
        return centre, 0.0, pad * 2, pad * 2
    _, u, v, u0, u1, v0, v1 = best
    centre = u * (u0 + u1) / 2 + v * (v0 + v1) / 2
    lu, lv = u1 - u0 + 2 * pad, v1 - v0 + 2 * pad
    yaw = math.atan2(u[1], u[0])
    if lv > lu:
        lu, lv = lv, lu
        yaw += math.pi / 2
    return centre, normalise_axis(yaw), lu, lv


def extents(points: np.ndarray, yaw: float, pad: float) -> tuple[np.ndarray, float, float]:
    """Rectangle aligned to `yaw` around points: centre, along, across."""
    u = np.array([math.cos(yaw), math.sin(yaw)])
    v = np.array([-u[1], u[0]])
    pu, pv = points @ u, points @ v
    centre = u * (pu.min() + pu.max()) / 2 + v * (pv.min() + pv.max()) / 2
    return centre, pu.max() - pu.min() + 2 * pad, pv.max() - pv.min() + 2 * pad


def normalise_axis(yaw: float) -> float:
    """An undirected axis as an angle in [-pi/2, pi/2)."""
    return (yaw + math.pi / 2) % math.pi - math.pi / 2


def normalise_angle(yaw: float) -> float:
    return (yaw + math.pi) % (2 * math.pi) - math.pi


# ---------------------------------------------------------------- detection

def detect(hm: HeightMap, settings: Settings | None = None) -> Detection:
    s = settings or Settings()
    h = hm.heights.astype(np.float64)
    peaks = hm.peaks.astype(np.float64)
    features: list[Feature] = []
    ignored: list[str] = []
    # A noisy scan's ground wanders: keep the ground tolerance well clear of it.
    tolerance = max(s.ground_tolerance, 4 * hm.noise)
    raised = hm.observed & (h > tolerance)

    # 1. Rails: raised cells a rail-sized disc cannot sit in. A rail may cover part of a cell,
    # so it is looked for in each cell's highest point, well above the ground's noise.
    radius = max(1, int(round(s.rail_disc / 2 / hm.cell)))
    yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1]
    disc = xx * xx + yy * yy <= radius * radius
    peaked = hm.observed & (peaks > max(0.15, tolerance + 6 * hm.noise))
    wide = ndimage.binary_opening(peaked | raised, structure=disc)
    thin = peaked & ~wide
    rail_mask = np.zeros_like(raised)
    labels, count = ndimage.label(thin, structure=np.ones((3, 3)))
    pieces = []
    for index, slices in enumerate(ndimage.find_objects(labels), start=1):
        rows, cols = np.nonzero(labels[slices] == index)
        rows, cols = rows + slices[0].start, cols + slices[1].start
        points = hm.cell_centres(rows, cols)
        centre, yaw, along, across = rectangle(points, hm.cell / 2)
        if along >= 0.3 and across <= 0.3 and along / max(across, 1e-6) >= 3:
            pieces.append(dict(rows=rows, cols=cols, centre=centre, yaw=yaw, along=along,
                               height=float(np.median(peaks[rows, cols]))))
    # A sparse or noisy scan catches a rail in pieces: join pieces that line up.
    for rows, cols in _join_rail_pieces(pieces):
        points = hm.cell_centres(rows, cols)
        centre, yaw, along, across = rectangle(points, hm.cell / 2)
        if along < s.rail_min_length or across > 0.3 or along / max(across, 1e-6) < 4:
            continue
        features.append(_rail(hm, peaks, rows, cols, points, centre, yaw, along, across))
        rail_mask[rows, cols] = True
    rail_mask = ndimage.binary_dilation(rail_mask, iterations=1)

    # 2. Plateaus and slopes among the rest. A median keeps edges sharp where a blur would smear
    # a ledge's drop across its whole top; the central difference then only touches the one cell
    # either side of an edge.
    smooth = ndimage.median_filter(h, size=5, mode="nearest")
    # Slope over two cells either side: half the noise of neighbouring cells, and an edge still
    # only reaches two cells into a flat top.
    padded = np.pad(smooth, 2, mode="edge")
    gx = (padded[2:-2, 4:] - padded[2:-2, :-4]) / (4 * hm.cell)
    gy = (padded[4:, 2:-2] - padded[:-4, 2:-2]) / (4 * hm.cell)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    rest = raised & ~rail_mask
    flat = rest & (slope < s.flat_degrees)
    sloped = rest & (slope >= s.flat_degrees) & (slope < s.steep_degrees)

    plateaus = []
    labels, _ = ndimage.label(flat)
    plateau_labels = np.zeros_like(labels)
    for index, slices in enumerate(ndimage.find_objects(labels), start=1):
        rows, cols = np.nonzero(labels[slices] == index)
        rows, cols = rows + slices[0].start, cols + slices[1].start
        area = len(rows) * hm.cell ** 2
        if area < s.min_plateau_area:
            continue
        points = hm.cell_centres(rows, cols)
        centre, yaw, along, across = rectangle(points, hm.cell / 2)
        # A flat top's sides are steep and drop out of `flat`; a smoothing cell on each side.
        along += hm.cell * 2
        across += hm.cell * 2
        top = float(np.median(h[rows, cols]))
        plateaus.append(dict(centre=centre, yaw=yaw, along=along, across=across, height=top,
                             cells=len(rows), used=False, feature=None))
        plateau_labels[rows, cols] = len(plateaus)

    # 3. Stairs: narrow plateaus side by side, each a step higher. Whatever else lies on a
    # flight (a tread caught in pieces, the risers' steep edges) belongs to it.
    stairs = _stairs(plateaus)
    features += stairs
    for p in plateaus:
        if not p["used"] and any(_inside(p["centre"], f, 0.2) and p["height"] <= f.height + 0.05 for f in stairs):
            p["used"] = True

    # 4. The other plateaus.
    for p in plateaus:
        if p["used"]:
            continue
        kind = "ledge" if p["across"] < 0.6 else ("manual_pad" if p["height"] <= 0.4 else "box")
        p["feature"] = len(features)
        features.append(Feature(kind, tuple(p["centre"]), p["yaw"], p["along"], p["across"], p["height"],
                                cells=p["cells"]))

    # 5. Slopes.
    labels, _ = ndimage.label(sloped)
    aspect = np.arctan2(gy, gx)  # uphill direction
    for index, slices in enumerate(ndimage.find_objects(labels), start=1):
        rows, cols = np.nonzero(labels[slices] == index)
        rows, cols = rows + slices[0].start, cols + slices[1].start
        if len(rows) * hm.cell ** 2 < s.min_slope_area:
            continue
        for part in _split_by_aspect(rows, cols, aspect):
            feature = _slope(hm, smooth, slope, aspect, *part, s)
            if feature == "edge" or (isinstance(feature, Feature) and any(_inside(feature.centre, f, 0.1) for f in stairs)):
                continue
            if feature is not None:
                features.append(feature)
                # The flat strip along a slope's top is its deck, not a ledge of its own.
                _absorb_deck(hm, smooth, part, feature, plateaus, plateau_labels, features,
                             widest=1.2 if feature.kind == "quarter" else 0.45)
            elif len(part[0]) * hm.cell ** 2 >= 0.1:
                where = hm.cell_centres(part[0][:1], part[1][:1])[0]
                ignored.append(f"slope of {len(part[0]) * hm.cell ** 2:.2f} m2 at ({where[0]:.1f}, {where[1]:.1f})")

    # A quarter pipe's coping (or a deck too narrow for a disc) reads as a rail along its top.
    quarters = [f for f in features if f.kind == "quarter"]
    features = [f for f in features if f.kind != "deck" and not (
        f.kind == "rail" and any(_on_top_edge(f, q) for q in quarters))]

    # Bowls: the Build Kit cannot dig, so these are reported only.
    sunken = hm.observed & (h < -s.bowl_depth)
    labels, _ = ndimage.label(sunken)
    for index, slices in enumerate(ndimage.find_objects(labels), start=1):
        rows, cols = np.nonzero(labels[slices] == index)
        rows, cols = rows + slices[0].start, cols + slices[1].start
        if len(rows) * hm.cell ** 2 < 1.0:
            continue
        points = hm.cell_centres(rows, cols)
        centre, yaw, along, across = rectangle(points, hm.cell / 2)
        depth = float(-h[rows, cols].min())
        features.append(Feature("bowl", tuple(centre), yaw, along, across, 0.0, base=-depth, cells=len(rows),
                                notes=["below the park floor: Build Kit objects cannot cut into the map"]))
    return Detection(features, ignored)


def _rail(hm, h, rows, cols, points, centre, yaw, along, across) -> Feature:
    u = np.array([math.cos(yaw), math.sin(yaw)])
    t = (points - centre) @ u
    z = h[rows, cols]
    # Rails are thin, so the grid often catches them a little low: the top 25 % per span is the bar.
    order = np.argsort(t)
    t, z = t[order], z[order]
    bins = np.clip(((t - t.min()) / max(np.ptp(t), 1e-6) * 10).astype(int), 0, 9)
    ts, zs = [], []
    for b in range(10):
        chosen = bins == b
        if np.any(chosen):
            ts.append(float(np.mean(t[chosen])))
            zs.append(float(np.percentile(z[chosen], 75)))
    ts, zs = np.array(ts), np.array(zs)
    grade, offset = np.polyfit(ts, zs, 1) if len(ts) >= 2 else (0.0, float(np.mean(zs)))
    pitch = math.atan(grade)
    if pitch < 0:  # point yaw uphill
        yaw, pitch = normalise_angle(yaw + math.pi), -pitch
    height = float(offset)  # height at the centre
    notes = []
    if abs(pitch) > math.radians(8):
        notes.append(f"handrail at {math.degrees(pitch):.0f} degrees")
        along = along / math.cos(pitch)
    return Feature("rail", tuple(centre), yaw, along, across, height, pitch=pitch, cells=len(rows), notes=notes)


def _stairs(plateaus: list[dict]) -> list[Feature]:
    treads = [i for i, p in enumerate(plateaus) if 0.2 <= p["across"] <= 0.7 and p["along"] >= 0.7]
    parent = {i: i for i in treads}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a_index, a in enumerate(treads):
        pa = plateaus[a]
        for b in treads[a_index + 1:]:
            pb = plateaus[b]
            if abs(normalise_axis(pa["yaw"] - pb["yaw"])) > math.radians(12):
                continue
            rise = abs(pa["height"] - pb["height"])
            if not 0.08 <= rise <= 0.25:
                continue
            u = np.array([math.cos(pa["yaw"]), math.sin(pa["yaw"])])
            v = np.array([-u[1], u[0]])
            delta = pb["centre"] - pa["centre"]
            if abs(delta @ u) > 0.5 * max(pa["along"], pb["along"]) or not 0.15 <= abs(delta @ v) <= 0.8:
                continue
            parent[find(a)] = find(b)

    groups: dict[int, list[int]] = {}
    for i in treads:
        groups.setdefault(find(i), []).append(i)
    features = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda i: plateaus[i]["height"])
        low, high = plateaus[members[0]], plateaus[members[-1]]
        axis_yaw = low["yaw"]
        u = np.array([math.cos(axis_yaw), math.sin(axis_yaw)])
        v = np.array([-u[1], u[0]])
        up = v if (high["centre"] - low["centre"]) @ v > 0 else -v
        corners = np.vstack([_corners(plateaus[i]) for i in members])
        uphill_yaw = math.atan2(up[1], up[0])
        centre, run, width = extents(corners, uphill_yaw, 0.0)
        steps = len(members) + 1  # the treads, plus the rise onto the top landing
        rise = (high["height"] - low["height"]) / max(len(members) - 1, 1)
        height = high["height"] + rise
        for i in members:
            plateaus[i]["used"] = True
        features.append(Feature("stairs", tuple(centre), uphill_yaw, run, width, float(height), steps=steps,
                                cells=sum(plateaus[i]["cells"] for i in members),
                                notes=[f"{steps} steps of {rise * 100:.0f} cm"]))
    return features


def _corners(p: dict) -> np.ndarray:
    return Feature("box", tuple(p["centre"]), p["yaw"], p["along"], p["across"], p["height"]).footprint()


def _split_by_aspect(rows, cols, aspect) -> list[tuple[np.ndarray, np.ndarray]]:
    """One slope patch per uphill direction: a pyramid's four faces come out as four banks."""
    angles = aspect[rows, cols]
    spread = 1 - math.hypot(np.mean(np.sin(angles)), np.mean(np.cos(angles)))
    if spread < 0.08:
        return [(rows, cols)]
    # Quarters around the commonest direction, not the mean: at a box's corner the mean falls
    # half way between two sides, and each side would straddle two quarters.
    histogram, edges = np.histogram(angles, bins=36, range=(-math.pi, math.pi))
    reference = (edges[np.argmax(histogram)] + edges[np.argmax(histogram) + 1]) / 2
    sector = np.round(((angles - reference + math.pi) % (2 * math.pi) - math.pi) / (math.pi / 2)).astype(int) % 4
    parts = []
    for q in range(4):
        chosen = sector == q
        if np.count_nonzero(chosen) == 0:
            continue
        grid = np.zeros((rows.max() + 1, cols.max() + 1), bool)
        grid[rows[chosen], cols[chosen]] = True
        labels, _ = ndimage.label(grid)
        for index in range(1, labels.max() + 1):
            r, c = np.nonzero(labels == index)
            parts.append((r, c))
    return parts


def _slope(hm, h, slope, aspect, rows, cols, s: Settings) -> Feature | str | None:
    """The ramp a slope patch is; "edge" for the side of a box or a riser, which reads as a strip
    of steep slope only a cell or two deep; None when it is too small or flat to be anything."""
    if len(rows) * hm.cell ** 2 < s.min_slope_area * 0.5:
        return None
    angles = aspect[rows, cols]
    uphill = math.atan2(np.mean(np.sin(angles)), np.mean(np.cos(angles)))
    points = hm.cell_centres(rows, cols)
    centre, run, width = extents(points, uphill, hm.cell / 2)
    # A ramp is deep; the side of a box or a riser is a strip of steep cells as deep as the
    # slope estimate spreads a drop (a few cells), however long.
    depth = len(rows) * hm.cell ** 2 / max(rectangle(points, hm.cell / 2)[2], hm.cell)
    if run < 0.5 or (depth < 0.35 and float(np.median(slope[rows, cols])) > 35):
        return "edge"
    u = np.array([math.cos(uphill), math.sin(uphill)])
    t = (points - centre) @ u
    z = h[rows, cols]
    bottom = float(np.percentile(z, 2))
    # The top of a transition is steeper than a ridable slope and drops out of the patch: the
    # height is where the ground ends up just past the patch's upper edge.
    upper = t > np.percentile(t, 90)
    reach = np.arange(1, int(round(0.6 / hm.cell)) + 1) * hm.cell
    tips = points[upper][:, None, :] + reach[None, :, None] * u[None, None, :]
    tr = np.clip(np.round((tips[..., 1] - hm.origin[1]) / hm.cell).astype(int), 0, h.shape[0] - 1)
    tc = np.clip(np.round((tips[..., 0] - hm.origin[0]) / hm.cell).astype(int), 0, h.shape[1] - 1)
    top = max(float(np.percentile(z, 98)), float(np.median(h[tr, tc].max(axis=1))))
    if top - bottom < 0.1:
        return None
    # Planar or curved: compare the slope over the lowest and highest thirds of the run.
    low, high = t < np.percentile(t, 33), t > np.percentile(t, 67)
    low_slope = float(np.median(slope[rows[low], cols[low]])) if np.any(low) else 0.0
    high_slope = float(np.median(slope[rows[high], cols[high]])) if np.any(high) else 0.0
    notes = [f"slope {low_slope:.0f} to {high_slope:.0f} degrees"]
    if high_slope - low_slope > 12 and high_slope > 35:
        kind = "quarter"
    elif top <= 0.9 and run <= 2.5:
        kind = "kicker"
    else:
        kind = "bank"
    # A ramp's foot reads a few centimetres up (the slope starts within ground tolerance); only a
    # ramp clearly off the ground, on a box or deck, sits higher.
    base = bottom if bottom > 0.15 else 0.0
    return Feature(kind, tuple(centre), uphill, run, width, top, base=base, cells=len(rows), notes=notes)


def _absorb_deck(hm, h, part, feature, plateaus, plateau_labels, features, widest: float) -> None:
    """Narrow plateaus (under `widest` across) level with a slope's top and touching it."""
    rows, cols = part
    grid = np.zeros(h.shape, bool)
    grid[rows, cols] = True
    near = ndimage.binary_dilation(grid, iterations=int(round(0.8 / hm.cell)))
    for label in np.unique(plateau_labels[near]):
        if label == 0:
            continue
        p = plateaus[label - 1]
        if p["across"] < widest and abs(p["height"] - feature.height) < 0.25:
            p["used"] = True
            if p["feature"] is not None:
                features[p["feature"]].kind = "deck"


def _inside(point, feature: Feature, margin: float) -> bool:
    u = np.array([math.cos(feature.yaw), math.sin(feature.yaw)])
    v = np.array([-u[1], u[0]])
    d = np.asarray(point) - np.asarray(feature.centre)
    return abs(d @ u) <= feature.along / 2 + margin and abs(d @ v) <= feature.across / 2 + margin


def _on_top_edge(rail: Feature, quarter: Feature) -> bool:
    """A rail along a quarter's top: parallel to it, level with it, at or just past its upper edge."""
    if abs(normalise_axis(rail.yaw - (quarter.yaw + math.pi / 2))) > math.radians(15):
        return False
    if abs(rail.height - quarter.height) > 0.25:
        return False
    u = np.array([math.cos(quarter.yaw), math.sin(quarter.yaw)])
    v = np.array([-u[1], u[0]])
    d = np.asarray(rail.centre) - np.asarray(quarter.centre)
    return -0.2 <= d @ u - quarter.along / 2 <= 1.0 and abs(d @ v) <= quarter.across / 2 + 0.5


def _join_rail_pieces(pieces: list[dict]) -> list[tuple[np.ndarray, np.ndarray]]:
    """Cells of each rail, joining pieces that are parallel, in line, level, and close end to end."""
    parent = list(range(len(pieces)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(pieces):
        for j in range(i + 1, len(pieces)):
            b = pieces[j]
            if abs(normalise_axis(a["yaw"] - b["yaw"])) > math.radians(8) or abs(a["height"] - b["height"]) > 0.1:
                continue
            u = np.array([math.cos(a["yaw"]), math.sin(a["yaw"])])
            d = b["centre"] - a["centre"]
            offset = abs(d @ np.array([-u[1], u[0]]))
            gap = abs(d @ u) - (a["along"] + b["along"]) / 2
            if offset < 0.12 and gap < 0.8:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(pieces)):
        groups.setdefault(find(i), []).append(i)
    return [(np.concatenate([pieces[i]["rows"] for i in g]), np.concatenate([pieces[i]["cols"] for i in g]))
            for g in groups.values()]
