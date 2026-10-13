"""Is a park's anchor on open, flat ground? Measured in the game itself.

A park of real spots spreads tens of metres round its anchor, and a base map's ground there may
be a road, a beach, a slope or a building. The tool cannot read the game's terrain, but the game
can: the trainer's `trainer ground <x> <z>` finds the topmost collision surface at a map position
(a downward ray) and writes it to ReSkate.log as

    Trainer: landing on the surface at X, Y, Z.            (Y is the surface + 0.5 m standing height)
    Trainer: no collision at X, Z yet; going to height ...  (not streamed in yet: probe again)

So:
1. `anchor probe --park <file>.park.json` writes the probes: a grid over the park's footprint,
   as RESKATE_STARTUP_COMMANDS (`trainer ground x z;wait 1;...`) and as plain lines;
2. run them in game (the launcher passes the startup commands on, or paste the lines into the
   console), standing on the base map;
3. `anchor check --park <file>.park.json --log ReSkate.log` reads the heights back and says how
   much of the footprint is level with the park (flat and clear), and which objects would be
   buried or float."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

STAND_HEIGHT = 0.5         # Extension/Trainer/trainer_landing.h: the log's height is the surface + this
STARTUP_LIMIT = 4000       # characters of RESKATE_STARTUP_COMMANDS the launcher passes on
LANDED = re.compile(r"Trainer: landing on the surface at (-?[\d.]+), (-?[\d.]+), (-?[\d.]+)\.")
NOT_LOADED = re.compile(r"Trainer: no collision at (-?[\d.]+), (-?[\d.]+) yet")
# The landing watch after each teleport (trainer.cpp update_landing): where the skater ended up.
NUMBER = r"(-?\d+(?:\.\d+)?)"   # not [\d.]+: "landed at X, Y, Z." ends in a full stop
SETTLED = re.compile(rf"Trainer: (?:landed|landing watch ended) at {NUMBER}, {NUMBER}, {NUMBER}")
SETTLE_WAIT = 8           # seconds for a far spot's collision to stream in (measured on bam)
ARRIVED = 30.0            # metres: the watch's own arrival radius; further away means a reset


@dataclass
class Footprint:
    map: str
    xz: np.ndarray          # (n, 2) object positions on the map (game x, z)
    base: float             # the park's ground height (median of the objects' heights)
    items: list[str]


def read_park(path: str | Path) -> Footprint:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    (map_name, objects), = data["maps"].items()
    if not objects:
        raise ValueError(f"{path}: the park has no objects")
    positions = np.array([o["position"] for o in objects], float)
    return Footprint(map_name, positions[:, [0, 2]], float(np.median(positions[:, 1])),
                     [o["item"] for o in objects])


def probe_points(footprint: Footprint, spacing: float = 10.0, margin: float = 3.0, limit: int = 50) -> np.ndarray:
    """A grid over the footprint (its objects' bounding box plus `margin`), widened until it has at
    most `limit` points, plus the box's centre."""
    lo = footprint.xz.min(axis=0) - margin
    hi = footprint.xz.max(axis=0) + margin
    while True:
        xs = np.arange(lo[0], hi[0] + 1e-6, spacing)
        zs = np.arange(lo[1], hi[1] + 1e-6, spacing)
        if len(xs) * len(zs) + 1 <= limit:
            break
        spacing *= 1.25
    grid = np.array([(x, z) for z in zs for x in xs])
    return np.vstack([(lo + hi) / 2, grid])


def _probe(x: float, z: float, wait: int, settle: bool = True) -> str:
    """One spot's commands. A first `trainer ground` sends the skater there; its reading can come
    before the spot's collision has streamed in (an unloaded spot on bam reads as the sea). With
    `settle`, a second one after `wait` seconds reads the loaded surface under the skater."""
    first = f"trainer ground {x:.1f} {z:.1f};wait {wait}"
    return f"{first};trainer ground {x:.1f} {z:.1f};wait 1" if settle else first


def startup_commands(points: np.ndarray, wait: int = SETTLE_WAIT, settle: bool = True) -> str:
    text = ";".join(_probe(x, z, wait, settle) for x, z in points)
    if len(text) > STARTUP_LIMIT:
        raise ValueError(f"{len(points)} probes make {len(text)} characters of startup commands, over the "
                         f"launcher's {STARTUP_LIMIT}: use a wider --spacing")
    return text


@dataclass
class Readings:
    heights: dict[tuple[float, float], float]   # settled surface height by spot
    missing: list[tuple[float, float]]          # spots with no reading at all
    reset: set[tuple[float, float]]             # spots the game sent the skater away from (water, out of bounds)
    unsettled: set[tuple[float, float]]         # spots read only by the first, early ray


def read_probes(log: str) -> Readings:
    """Each probed spot's settled height from a ReSkate.log.

    A spot starts at its first `trainer ground` line. Its height is, in order of trust:
    1. a second `trainer ground` reading at the same spot (the settled run's own ray, taken
       after the collision streamed in);
    2. the last landing-watch line ("landed at" / "landing watch ended at") within 30 m of it,
       less the standing height;
    3. the first, early reading, marked unsettled.
    A watch line further than 30 m away means the game put the skater back at the start: the
    spot is water or out of bounds. The last run of a spot wins."""
    readings = Readings({}, [], set(), set())
    spot = None

    def close(spot):
        if spot is None:
            return
        key = spot["key"]
        for found in (readings.heights, readings.reset, readings.unsettled):
            if isinstance(found, dict):
                found.pop(key, None)
            else:
                found.discard(key)
        if spot["reset"]:
            readings.reset.add(key)
        if spot["confirm"] is not None:
            readings.heights[key] = spot["confirm"]
        elif spot["watched"] is not None:
            readings.heights[key] = spot["watched"]
        elif spot["first"] is not None:
            readings.heights[key] = spot["first"]
            readings.unsettled.add(key)
        else:
            readings.missing.append(key)

    for line in log.splitlines():
        if m := LANDED.search(line) or NOT_LOADED.search(line):
            landed = m.re is LANDED
            x = round(float(m.group(1)), 1)
            z = round(float(m.group(3 if landed else 2)), 1)
            ground = float(m.group(2)) - STAND_HEIGHT if landed else None
            if spot is not None and spot["key"] == (x, z) and spot["teleports"] == 1:
                spot["teleports"] = 2               # the settling second look at the same spot
                if ground is not None:
                    spot["confirm"] = ground
                continue
            close(spot)
            spot = {"key": (x, z), "first": ground, "confirm": None, "watched": None, "reset": False, "teleports": 1}
        elif (m := SETTLED.search(line)) and spot is not None:
            x, y, z = (float(v) for v in m.groups())
            if math.hypot(x - spot["key"][0], z - spot["key"][1]) <= ARRIVED:
                spot["watched"] = y - STAND_HEIGHT
            else:
                spot["reset"] = True
    close(spot)
    readings.missing = [p for p in dict.fromkeys(readings.missing) if p not in readings.heights]
    return readings


def read_heights(log: str) -> tuple[dict[tuple[float, float], float], list[tuple[float, float]]]:
    """Settled surface heights by (x, z) from a ReSkate.log (read_probes), and the spots with no
    reading."""
    readings = read_probes(log)
    return readings.heights, readings.missing


@dataclass
class Verdict:
    probes: int
    level: int                       # probes within tolerance of the park's ground
    high: list[tuple[float, float, float]]   # (x, z, how far above): walls, buildings, hills
    low: list[tuple[float, float, float]]    # (x, z, how far below): drops, beaches, water
    missing: list[tuple[float, float]]
    buried: list[int]                # objects (by index) where the ground rises over the park's
    floating: list[int]              # objects where the ground falls away under the park's

    @property
    def level_share(self) -> float:
        return self.level / max(self.probes, 1)

    @property
    def good(self) -> bool:
        return self.level_share >= 0.9 and not self.buried and not self.floating

    def summary(self, tolerance: float) -> str:
        lines = [f"{self.level} of {self.probes} probes ({self.level_share:.0%}) are within {tolerance:.2f} m of the "
                 f"park's ground: flat and clear"]
        if self.high:
            worst = max(self.high, key=lambda p: p[2])
            lines.append(f"{len(self.high)} probes are higher (up to {worst[2]:.1f} m above the park's ground, at "
                         f"{worst[0]:.0f}, {worst[1]:.0f}): walls, buildings or rising ground")
        if self.low:
            worst = max(self.low, key=lambda p: p[2])
            lines.append(f"{len(self.low)} probes are lower (up to {worst[2]:.1f} m below the park's ground, at "
                         f"{worst[0]:.0f}, {worst[1]:.0f}): drops, beach or falling ground")
        if self.missing:
            lines.append(f"{len(self.missing)} probes found no collision yet (not streamed in): probe them again from closer")
        if self.buried or self.floating:
            lines.append(f"objects: {len(self.buried)} would be buried, {len(self.floating)} would float")
        verdict = ("a good anchor" if self.good else "move the anchor, or shrink the park (--size)")
        lines.append(f"verdict: {verdict}")
        return "\n".join(lines)


def check(footprint: Footprint, heights: dict[tuple[float, float], float], missing=(), tolerance: float = 0.3,
          object_reach: float = 6.0) -> Verdict:
    if not heights:
        raise ValueError("no 'Trainer: landing on the surface' lines in the log: run the probes first")
    points = np.array(list(heights.keys()))
    ground = np.array(list(heights.values()))
    offset = ground - footprint.base
    level = int((np.abs(offset) <= tolerance).sum())
    high = [(float(x), float(z), float(d)) for (x, z), d in zip(points, offset) if d > tolerance]
    low = [(float(x), float(z), float(-d)) for (x, z), d in zip(points, offset) if d < -tolerance]
    buried, floating = [], []
    for index, xz in enumerate(footprint.xz):
        distance = np.hypot(*(points - xz).T)
        nearest = int(distance.argmin())
        if distance[nearest] > object_reach:
            continue
        # Against the park's ground, not the object's own height: a rail's origin sits above it.
        if offset[nearest] > tolerance:
            buried.append(index)
        elif offset[nearest] < -tolerance:
            floating.append(index)
    return Verdict(len(points), level, high, low, list(missing), buried, floating)


# ---------------------------------------------------------------- searching for open ground

def search_points(x: float, z: float, radius: float, spacing: float, skip_radius: float = 0.0) -> np.ndarray:
    """A square grid `radius` each way round x, z, as a serpentine: row by row, each row the other
    way, so each hop is short and lands next to ground that has already streamed in. With
    `skip_radius`, the square already probed that far each way is left out, for widening a
    search without probing it again."""
    steps = np.arange(-radius, radius + 1e-6, spacing)
    rows = []
    for i, dz in enumerate(steps):
        row = [(x + dx, z + dz) for dx in (steps if i % 2 == 0 else steps[::-1])]
        if skip_radius > 0:
            row = [p for p in row if max(abs(p[0] - x), abs(p[1] - z)) > skip_radius + 1e-6]
        rows += row
    return np.array(rows)


def batches(points: np.ndarray, wait: int = SETTLE_WAIT, limit: int = STARTUP_LIMIT, settle: bool = True,
            per_batch: int | None = None) -> list[str]:
    """The probes as RESKATE_STARTUP_COMMANDS values, each within the launcher's limit (and at
    most `per_batch` spots), in the points' order."""
    out, current, count = [], "", 0
    for x, z in points:
        probe = _probe(x, z, wait, settle)
        if current and (len(current) + 1 + len(probe) > limit or (per_batch and count >= per_batch)):
            out.append(current)
            current, count = "", 0
        current = f"{current};{probe}" if current else probe
        count += 1
    if current:
        out.append(current)
    return out


WATER_BAND = 0.5          # metres above the lowest probe still counted as that surface
WATER_FLAT = 0.05         # a water surface reads the same height everywhere, to the log's 0.1 m
WATER_MIN = 20            # probes: a few low points are a dip, not a sea


def water_level(heights: dict[tuple[float, float], float]) -> float | None:
    """The height of water, if the probes show it: many probes at exactly the lowest height seen.
    Probes up to WATER_BAND above it then count as water too (wet sand, the shoreline). The sea and lakes read like that; ground never does. None if there is none.

    The game's ground probe gives a height, not what the surface is, so this is a guess from the
    heights alone. The game also sends the skater back to the start from water and out-of-bounds
    places; the height logged before that is still the surface."""
    if not heights:
        return None
    ground = np.array(list(heights.values()))
    low = ground.min()
    flat = ground <= low + WATER_FLAT + 1e-6       # beach just above it may be in the band; the sea itself is exact
    if flat.sum() >= max(WATER_MIN, 0.05 * len(ground)):
        return float(low)
    return None


@dataclass
class Candidate:
    x: float
    z: float
    ground: float          # the square's median land height: where the anchor stands
    level_share: float     # probes level with that, out of all its probes (water counts against)
    probes: int
    spread: float          # 10th to 90th percentile of the land heights (m)
    water: int = 0         # probes in the square at the water level
    from_reference: float | None = None  # ground minus the reference (street) height

    @property
    def suspicious(self) -> bool:
        """Perfectly flat over many probes: real ground has kerbs, cambers and slopes."""
        return self.probes >= WATER_MIN and self.spread <= 0.0 + 1e-6

    def line(self) -> str:
        notes = []
        if self.from_reference is not None:
            notes.append(f"{self.from_reference:+.1f} m from the reference height")
        if self.water:
            notes.append(f"{self.water} probes on water")
        if self.suspicious:
            notes.append("SUSPICIOUS: exactly flat, so maybe water or out of bounds; teleport there and look")
        return (f"{self.x:.1f}, {self.ground:.1f}, {self.z:.1f}: {self.level_share:.0%} level "
                f"({self.probes} probes, heights spread {self.spread:.1f} m"
                + "".join(f"; {n}" for n in notes) + ")")


def rank(heights: dict[tuple[float, float], float], size: float, tolerance: float = 0.3, top: int = 5,
         coverage: float = 0.85, water: float | None = None, reference: float | None = None,
         window: float = 10.0, band: float = WATER_FLAT,
         wet_spots: set[tuple[float, float]] = frozenset()) -> list[Candidate]:
    """Squares `size` across, centred on probed points, ranked by how much of each is level land
    at its own median height. Probes at the `water` height (within `band`), and `wet_spots` the
    game reset the skater from, are not land: they count against a square. Squares within `window` metres of the `reference` height (the street level round the
    start) come first. Exactly flat squares (see Candidate.suspicious) come after the rest.
    Squares with under `coverage` of their probes read are left out, and the ones returned don't
    overlap by more than half."""
    if not heights:
        raise ValueError("no 'Trainer: landing on the surface' lines in the logs: run the probes first")
    points = np.array(list(heights.keys()))
    ground = np.array(list(heights.values()))
    wet = ground <= water + band + 1e-6 if water is not None else np.zeros(len(ground), bool)
    wet |= np.array([tuple(p) in wet_spots for p in heights.keys()], bool)
    if len(points) > 1:
        from scipy.spatial import cKDTree
        gaps = cKDTree(points).query(points, k=2)[0][:, 1]   # each probe's spacing: grids may be mixed
    else:
        gaps = np.full(1, size)
    half = size / 2
    found = []
    for cx, cz in points:
        inside = (np.abs(points[:, 0] - cx) <= half + 1e-6) & (np.abs(points[:, 1] - cz) <= half + 1e-6)
        # A square centred on a probe of a grid this fine holds this many; water probes count as read.
        spacing = max(float(np.median(gaps[inside])), 1e-6)
        expected = (2 * math.floor(half / spacing + 1e-6) + 1) ** 2
        if inside.sum() < coverage * expected:
            continue
        land = ground[inside & ~wet]
        if len(land) == 0:
            continue
        median = float(np.median(land))
        share = float((np.abs(land - median) <= tolerance).sum() / inside.sum())
        found.append(Candidate(float(cx), float(cz), median, share, int(inside.sum()),
                               float(np.percentile(land, 90) - np.percentile(land, 10)),
                               int((inside & wet).sum()),
                               None if reference is None else median - reference))
    if not found:
        raise ValueError(f"no {size:.0f} m square of land has {coverage:.0%} of its probes read: probe a wider "
                         "area, or pass a smaller --size")

    def order(c: Candidate):
        far = c.from_reference is not None and abs(c.from_reference) > window
        return (c.suspicious, far, -round(c.level_share, 2), abs(c.from_reference or 0.0), c.spread)

    found.sort(key=order)
    best: list[Candidate] = []
    for c in found:
        if all(max(abs(c.x - b.x), abs(c.z - b.z)) >= half for b in best):
            best.append(c)
        if len(best) == top:
            break
    return best


def anchor_park(map_name: str, x: float, y: float, z: float, item: str, template: str | Path | None = None) -> dict:
    """A park file holding one measuring stick at x, y, z, for `city --anchor`. With a template
    (the old anchor), its stick keeps its item and facing and only moves."""
    from .park import validate_park
    stick = {"id": 1, "item": item, "rotation": [0.0, 0.0, 0.0, 1.0]}
    if template:
        data = json.loads(Path(template).read_text(encoding="utf-8"))
        (template_map, objects), = data["maps"].items()
        if template_map != map_name:
            raise ValueError(f"{template} is on {template_map}, not {map_name}")
        old = ([o for o in objects if o.get("item") == item] or objects[:1])[0]
        stick.update(item=old["item"], rotation=old["rotation"])
    stick["position"] = [round(x, 3), round(y, 3), round(z, 3)]
    park = {"format": "reskate-park", "schema_version": 1, "revision": 0, "maps": {map_name: [stick]}}
    validate_park(park)
    return park


def reference_height(heights: dict[tuple[float, float], float], water: float | None,
                     reach: float = 30.0, wet_spots: set[tuple[float, float]] = frozenset(),
                     centre: tuple[float, float] | None = None) -> tuple[float, str]:
    """The street height round the search's centre (by default the middle of the probed area): the
    median of the land probes within `reach` of it, else the nearest land probe. Returns
    the height and how it was found."""
    points = np.array(list(heights.keys()))
    ground = np.array(list(heights.values()))
    land = ground > water + WATER_FLAT + 1e-6 if water is not None else np.ones(len(ground), bool)
    land &= np.array([tuple(p) not in wet_spots for p in heights.keys()], bool)
    if not land.any():
        raise ValueError("every probe is water: probe somewhere else, or pass --near-height")
    centre = np.array(centre) if centre is not None else (points.min(axis=0) + points.max(axis=0)) / 2
    distance = np.hypot(*(points - centre).T)
    near = land & (distance <= reach + 1e-6)
    if near.any():
        return float(np.median(ground[near])), (f"the median of {int(near.sum())} land probes within {reach:g} m of "
                                                 f"the search's centre ({centre[0]:.1f}, {centre[1]:.1f})")
    nearest = int(np.where(land, distance, np.inf).argmin())
    return float(ground[nearest]), (f"the land probe nearest the search's centre, at {points[nearest][0]:.1f}, "
                                    f"{points[nearest][1]:.1f}, {distance[nearest]:.0f} m away")
