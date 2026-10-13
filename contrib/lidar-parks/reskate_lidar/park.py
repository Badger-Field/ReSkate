"""Writing placements as a ReSkate park: the park.json format of Extension/Objects/ParkEditor
(park_document.cpp, placements_store.cpp), packaged as a park mod (park_mods.h):
Mods/<folder>/manifest.json and Mods/<folder>/parks/<map>.park.json.

Frames. The levelled scan is right-handed, z up. The game (Frostbite) is y up and left-handed:
x right, y up, z forward. Scan (x, y, z) becomes game (x, z, y), which is that handedness change.
A yaw of t in the scan (from +x towards +y) is a turn of -t about the game's +y. `mirror` flips
the scan's y first, for when a park comes out mirrored in game.

Anchor. Where the park goes on a base map comes from a marker object (the measuring stick by
default) placed in the Park Editor and saved: the marker's position is the scan's origin at
ground level and its facing is the scan's +x."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .catalog import Placement

MAX_OBJECTS_PER_MAP = 1024   # placements_store.cpp
DEFAULT_DROPPER_LIMIT = 256  # config/defaults/object-dropper.json limits.max_objects
MAX_PARK_BYTES = 2 * 1024 * 1024
MAP_PATTERN = re.compile(r"^[a-z0-9_]{1,32}$")
MOD_NAME_PATTERN = re.compile(r"^[A-Za-z0-9 _\-.]{1,64}$")


@dataclass
class Anchor:
    position: np.ndarray = field(default_factory=lambda: np.zeros(3))  # game frame
    yaw: float = 0.0                                                   # about game +y, radians
    map: str | None = None
    others: list[dict] = field(default_factory=list)  # the park's other objects, for --merge
    scales: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------- quaternions (x, y, z, w)

def q_multiply(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def q_axis(axis, angle):
    s = math.sin(angle / 2)
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(angle / 2))


def q_rotate(q, v):
    qv = (v[0], v[1], v[2], 0.0)
    conj = (-q[0], -q[1], -q[2], q[3])
    x, y, z, _ = q_multiply(q_multiply(q, qv), conj)
    return np.array([x, y, z])


def yaw_of(q) -> float:
    """The turn about +y of a quaternion that turns about +y only (a Park Editor object)."""
    x, y, z, w = q
    return 2 * math.atan2(y, w)


# ---------------------------------------------------------------- anchor

def read_anchor(path: str | Path, marker: str) -> Anchor:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    maps = data.get("maps") or {}
    if len(maps) != 1:
        raise ValueError(f"{path}: expected a park with one map, found {len(maps)}")
    (map_name, objects), = maps.items()
    markers = [o for o in objects if o.get("item") == marker]
    if not markers:
        if len(objects) == 1:
            markers = objects
        else:
            raise ValueError(f"{path}: place one {marker} where the park should go (found none)")
    if len(markers) > 1:
        raise ValueError(f"{path}: {len(markers)} markers ({marker}); keep exactly one")
    m = markers[0]
    others = [o for o in objects if o is not m]
    scales = (data.get("object_scales") or {}).get(map_name, {})
    return Anchor(np.array(m["position"], float), yaw_of(m["rotation"]), map_name, others,
                  {k: v for k, v in scales.items() if k != str(m["id"])})


# ---------------------------------------------------------------- conversion

def to_game(placement: Placement, anchor: Anchor, mirror: bool = False) -> tuple[list[float], list[float]]:
    x, y, z = placement.x, placement.y, placement.z
    yaw = placement.yaw
    if mirror:
        y, yaw = -y, -yaw
    item = placement.item
    # Object rotation in the game frame: yaw about +y, then pitch (nose up) about the object's
    # own across axis, then the catalog's correction for a model built facing another way.
    q = q_axis((0, 1, 0), -yaw)
    q = q_multiply(q, q_axis((0, 0, 1), placement.pitch))
    q = q_multiply(q, q_axis((0, 1, 0), -math.radians(item.yaw_offset_degrees)))
    base = np.array([x, z, y])
    origin = q_rotate(q, np.array(item.origin) * placement.scale)
    local = base + origin
    q_anchor = q_axis((0, 1, 0), anchor.yaw)
    position = anchor.position + q_rotate(q_anchor, local)
    rotation = q_multiply(q_anchor, q)
    norm = math.sqrt(sum(c * c for c in rotation))
    return [round(float(c), 4) for c in position], [round(c / norm, 6) for c in rotation]


def build_park(placements: list[Placement], map_name: str, anchor: Anchor, mirror: bool = False,
               merge: bool = False) -> dict:
    if not MAP_PATTERN.match(map_name):
        raise ValueError(f"Map key {map_name!r}: use the game's key, e.g. bam (San Vansterdam)")
    objects: list[dict] = []
    scales: dict[str, float] = {}
    if merge:
        objects += [dict(o) for o in anchor.others]
        scales.update(anchor.scales)
    next_id = max([int(o["id"]) for o in objects], default=0) + 1
    for placement in placements:
        position, rotation = to_game(placement, anchor, mirror)
        objects.append({"id": next_id, "item": placement.item.item, "position": position, "rotation": rotation})
        if abs(placement.scale - 1.0) > 1e-4:
            scales[str(next_id)] = round(float(placement.scale), 4)
        next_id += 1
    if len(objects) > MAX_OBJECTS_PER_MAP:
        raise ValueError(f"{len(objects)} objects: a park holds at most {MAX_OBJECTS_PER_MAP} per map")
    park = {"format": "reskate-park", "schema_version": 1, "revision": 0, "maps": {map_name: objects}}
    if scales:
        park["object_scales"] = {map_name: scales}
    validate_park(park)
    return park


def validate_park(park: dict) -> None:
    """The checks ReSkate makes on load (valid_placed_object, decode_park), so a bad file fails here."""
    if park.get("format") != "reskate-park" or park.get("schema_version") != 1:
        raise ValueError("not a reskate-park document")
    maps = park["maps"]
    if len(maps) != 1:
        raise ValueError("a park holds exactly one base map")
    ids = set()
    for map_name, objects in maps.items():
        if not MAP_PATTERN.match(map_name):
            raise ValueError(f"bad map key {map_name!r}")
        for o in objects:
            if set(o) != {"id", "item", "position", "rotation"}:
                raise ValueError(f"object fields {sorted(o)}")
            if not isinstance(o["id"], int) or o["id"] <= 0 or o["id"] in ids:
                raise ValueError(f"bad or duplicate id {o['id']!r}")
            ids.add(o["id"])
            if not re.fullmatch(r"own_bk[A-Za-z0-9_]*", o["item"]) or len(o["item"]) > 256:
                raise ValueError(f"not a Build Kit item: {o['item']!r}")
            if len(o["position"]) != 3 or any(not math.isfinite(v) or abs(v) > 100000 for v in o["position"]):
                raise ValueError(f"object {o['id']}: position out of range")
            norm = sum(v * v for v in o["rotation"])
            if len(o["rotation"]) != 4 or not 0.98 < norm < 1.02:
                raise ValueError(f"object {o['id']}: rotation is not a unit quaternion")
    for map_name, scales in (park.get("object_scales") or {}).items():
        if map_name not in maps:
            raise ValueError("scale for an unknown map")
        for key, value in scales.items():
            if int(key) not in ids:
                raise ValueError(f"scale for unknown object {key}")
            if not 0.01 <= value <= 100:
                raise ValueError(f"object {key}: scale {value} out of range")
    if len(encode(park)) > MAX_PARK_BYTES:
        raise ValueError("park file over 2 MB")


def encode(park: dict) -> str:
    return json.dumps(park, indent=2) + "\n"


def write_mod(mods_root: str | Path, folder: str, park: dict, title: str, author: str, version: str,
              description: str) -> Path:
    """Mods/<folder>/manifest.json + parks/<map>.park.json; returns the park file."""
    if not MOD_NAME_PATTERN.match(folder) or folder.startswith("."):
        raise ValueError(f"Mod folder name {folder!r}: letters, digits, spaces, _ - . only")
    for text, limit, what in ((title, 64, "name"), (author, 64, "author"), (version, 32, "version"),
                              (description, 512, "description")):
        if len(text) > limit:
            raise ValueError(f"The {what} is longer than {limit} characters")
    directory = Path(mods_root) / folder
    (directory / "parks").mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({"name": title, "author": author, "version_number": version, "description": description})
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (map_name,) = park["maps"].keys()
    park_path = directory / "parks" / f"{map_name}.park.json"
    park_path.write_text(encode(park), encoding="utf-8")
    return park_path
