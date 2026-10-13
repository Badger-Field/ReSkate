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


def probe_points(footprint: Footprint, spacing: float = 10.0, margin: float = 3.0, limit: int = 100) -> np.ndarray:
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


def startup_commands(points: np.ndarray, wait: int = 1) -> str:
    text = ";".join(f"trainer ground {x:.1f} {z:.1f};wait {wait}" for x, z in points)
    if len(text) > STARTUP_LIMIT:
        raise ValueError(f"{len(points)} probes make {len(text)} characters of startup commands, over the "
                         f"launcher's {STARTUP_LIMIT}: use a wider --spacing")
    return text


def read_heights(log: str) -> tuple[dict[tuple[float, float], float], list[tuple[float, float]]]:
    """Surface heights by (x, z) from a ReSkate.log, the last reading of each point winning; and the
    points whose collision had not loaded."""
    heights: dict[tuple[float, float], float] = {}
    missing = []
    for line in log.splitlines():
        if m := LANDED.search(line):
            x, y, z = (float(v) for v in m.groups())
            heights[(round(x, 1), round(z, 1))] = y - STAND_HEIGHT
        elif m := NOT_LOADED.search(line):
            missing.append((round(float(m.group(1)), 1), round(float(m.group(2)), 1)))
    missing = [p for p in missing if p not in heights]
    return heights, missing


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
            lines.append(f"{len(self.high)} probes are higher (up to {worst[2]:.1f} m, at {worst[0]:.0f}, {worst[1]:.0f}): "
                         "walls, buildings or rising ground")
        if self.low:
            worst = max(self.low, key=lambda p: p[2])
            lines.append(f"{len(self.low)} probes are lower (down to {worst[2]:.1f} m, at {worst[0]:.0f}, {worst[1]:.0f}): "
                         "drops, beach or falling ground")
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
