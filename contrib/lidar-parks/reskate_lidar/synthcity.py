"""A synthetic city block with known contents, as an aerial LiDAR survey and map data would show
it, for testing the city pipeline without the network.

80 x 80 m, on ground rising 2 % to the east, with:
- a square (place=square) with a terrace 1.2 m up on its east side, reached by a tagged flight
  of 8 steps with handrails, and a plain retaining wall elsewhere;
- a tagged bench (a point: its direction comes from the points), a tagged low wall, an untagged
  ledge only the LiDAR shows, and a bank in the ground;
- things that must not become skate spots: a hedge (rough, multiple returns), a planter on grass,
  and a car on the street north of the square;
- a sidewalk along that street, 15 cm above it: a kerb;
- a building west of the square, 12 m tall in the points but untagged.
Points are what a laser sees from above: the top surface at each spot, a little noise."""
from __future__ import annotations

import math

import numpy as np

from .ept import BUILDING, GROUND, Points
from .mapdata import MapItem

BOUNDS = (-40.0, -40.0, 40.0, 40.0)
BASE = 10.0
STEP_RISE, STEP_TREAD, STEPS = 0.15, 0.375, 8
KERB = 0.15

TRUTH = {
    "stairs": dict(centre=(11.5, 0.0), yaw=0.0, run=3.0, width=5.0, height=1.2, steps=8),
    "bench": dict(centre=(-10.0, -10.0), yaw=math.radians(30), along=1.8, height=0.45),
    "wall": dict(centre=(0.0, -15.0), yaw=0.0, along=10.0, height=0.5),
    "ledge": dict(centre=(0.0, 15.0), yaw=0.0, along=6.0, across=0.5, height=0.5),
    "bank": dict(centre=(-23.5, -21.5), yaw=0.0, run=3.0, height=0.8),
    "building": dict(centre=(-35.0, -12.5), height=12.0),
    "car": (0.0, 35.0), "hedge": (-15.0, 15.0), "planter": (24.0, 20.0),
}


def ground(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    z = BASE + 0.02 * x
    on_steps = np.abs(y) <= 2.5
    k = np.clip(np.floor((x - 10.0) / STEP_TREAD) + 1, 0, STEPS)
    rise = np.where(on_steps, np.where(x < 10.0, 0.0, k * STEP_RISE), np.where(x >= 11.5, STEPS * STEP_RISE, 0.0))
    bank = (x >= -25) & (x < -22) & (y >= -25) & (y <= -18)
    road = (y >= 29) & (y <= 41)  # the street, a kerb's height below the sidewalk
    return z + rise + np.where(bank, 0.8 * (x + 25) / 3, 0.0) - np.where(road, KERB, 0.0)


def _rect(centre, yaw, along, across):
    import shapely
    u = np.array([math.cos(yaw), math.sin(yaw)])
    v = np.array([-u[1], u[0]])
    c = np.array(centre)
    a, b = along / 2, across / 2
    return shapely.Polygon([c + a * u + b * v, c - a * u + b * v, c - a * u - b * v, c + a * u - b * v])


def survey(seed: int = 0, density: float = 100.0, noise: float = 0.02) -> Points:
    import shapely
    rng = np.random.default_rng(seed)
    x0, y0, x1, y1 = BOUNDS
    n = int((x1 - x0) * (y1 - y0) * density)
    x, y = rng.uniform(x0, x1, n), rng.uniform(y0, y1, n)
    z = ground(x, y)
    classes = np.full(n, GROUND, np.uint8)
    layered = np.zeros(n, bool)
    t = TRUTH
    # (shape, top above the ground there, class); later ones win where they overlap.
    solids = [
        (_rect(t["bench"]["centre"], t["bench"]["yaw"], 1.8, 0.5), 0.45, 1),
        (_rect(t["wall"]["centre"], 0.0, 10.0, 0.3), 0.5, 1),
        (_rect(t["ledge"]["centre"], 0.0, 6.0, 0.5), 0.5, 1),
        (_rect(t["car"], 0.0, 4.5, 1.8), 1.5, 1),
        (_rect(t["planter"], 0.0, 2.0, 2.0), 0.4, 1),
        (shapely.box(-38, -20, -32, -5), 12.0, BUILDING),
    ]
    for shape, top, cls in solids:
        inside = shapely.contains_xy(shape, x, y)
        z[inside] += top
        classes[inside] = cls
    hedge = shapely.contains_xy(_rect(t["hedge"], 0.0, 3.0, 1.2), x, y)
    z[hedge] += rng.uniform(0.0, 0.7, hedge.sum())
    classes[hedge] = 1
    layered[hedge] = True
    z += rng.normal(0, noise, n)
    return Points(np.column_stack([x, y, z]), classes, "synthetic", layered)


def map_items() -> list[MapItem]:
    """The block's map data, in the local frame, tagged as OpenStreetMap would tag it."""
    import shapely
    return [
        MapItem(shapely.box(-30, -30, 30, 28), {"place": "square", "name": "Synthetic Square"}, "osm", "way/1"),
        MapItem(shapely.LineString([(-40, 35), (40, 35)]), {"highway": "primary", "name": "Main Street"}, "osm", "way/2"),
        # The sidewalk along the square's side of the street, 2 m wide, its edge at the kerb.
        MapItem(shapely.LineString([(-40, 28), (40, 28)]), {"highway": "footway", "footway": "sidewalk", "width": "2"},
                "osm", "way/8"),
        # Drawn top to bottom: the pipeline finds uphill from the ground.
        MapItem(shapely.LineString([(13.5, 0), (9.5, 0)]), {"highway": "steps", "handrail": "yes"}, "osm", "way/3"),
        MapItem(shapely.Point(*TRUTH["bench"]["centre"]), {"amenity": "bench", "backrest": "no"}, "osm", "node/4"),
        MapItem(shapely.LineString([(-5, -15), (5, -15)]), {"barrier": "wall"}, "osm", "way/5"),
        MapItem(shapely.box(-38, -20, -32, -5), {"building": "yes", "name": "Someone's office",
                                                "addr:street": "Main Street", "phone": "555"}, "osm", "way/6"),
        MapItem(shapely.box(20, 15, 28, 25), {"landuse": "grass"}, "osm", "way/7"),
    ]
