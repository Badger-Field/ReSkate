"""USGS 3DEP aerial LiDAR, read straight from its public Entwine Point Tile (EPT) copies.

3DEP is public domain. Its point clouds sit on AWS as EPT (s3://usgs-lidar-public, one folder
per survey): an octree of LAZ files, each node a few thousand points, so a city block needs only
the handful of nodes over it, not the survey's gigabytes. The index of surveys and their outlines
(resources.geojson) is published by Hobu, Inc., who host the EPT copies.

Other EPT datasets work the same way (pass --lidar <url or folder of ept.json>), as long as their
horizontal SRS is EPSG:3857 or EPSG:4326, or they are already in metres in the local frame."""
from __future__ import annotations

import io
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import geo, net

USGS_BUCKET = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public"
USGS_INDEX = "https://raw.githubusercontent.com/hobuinc/usgs-lidar/master/boundaries/resources.geojson"

# ASPRS LAS classes used here.
GROUND, LOW_VEG, MED_VEG, HIGH_VEG, BUILDING, LOW_NOISE, WATER, BRIDGE, HIGH_NOISE = 2, 3, 4, 5, 6, 7, 9, 17, 18
UNCLASSIFIED = (0, 1)
NOISE = (LOW_NOISE, HIGH_NOISE)


@dataclass
class Points:
    """A survey's points over an area: x east, y north (metres in the local frame), z elevation
    (metres, the survey's vertical datum), and the ASPRS class of each."""
    xyz: np.ndarray
    classes: np.ndarray
    source: str
    layered: np.ndarray | None = None  # pulse had several returns: seen through something (leaves)

    def __post_init__(self):
        if self.layered is None:
            self.layered = np.zeros(len(self.classes), bool)

    def of(self, *classes: int) -> np.ndarray:
        return self.xyz[np.isin(self.classes, classes)]


def _year(name: str) -> int:
    years = [int(y) for y in re.findall(r"(?<!\d)(20\d\d|19\d\d)(?!\d)", name)]
    if years:
        return max(years)
    short = re.search(r"_[A-Z](\d\d)$", name)  # CA_SanFrancisco_1_B23: delivered 2023
    return 2000 + int(short.group(1)) if short else 0


def find_surveys(bbox: geo.BBox, cache: Path, index_url: str = USGS_INDEX) -> list[dict]:
    """3DEP surveys whose outline covers the box's centre, newest first."""
    import shapely
    index = json.loads(net.fetch(index_url, cache, "3dep-index", suffix=".geojson"))
    lat, lon = bbox.centre
    box = shapely.box(bbox.west, bbox.south, bbox.east, bbox.north)
    found = []
    for feature in index["features"]:
        shape = shapely.geometry.shape(feature["geometry"])
        if not shape.intersects(box):
            continue
        p = feature["properties"]
        found.append({"name": p["name"], "url": p["url"], "points": p.get("count", 0), "year": _year(p["name"]),
                      "covers": shape.contains(box), "has_centre": shape.contains(shapely.Point(lon, lat))})
    found.sort(key=lambda s: (s["covers"], s["has_centre"], s["year"], s["points"]), reverse=True)
    return found


class EptReader:
    def __init__(self, url: str, cache: Path, workers: int = 8):
        self.base = url[:-len("/ept.json")] if url.endswith("ept.json") else url.rstrip("/")
        self.cache = cache
        self.workers = workers
        self.info = json.loads(net.fetch(self.base + "/ept.json", cache, "ept", suffix=".json"))
        srs = self.info.get("srs") or {}
        self.srs = f"{srs.get('authority', '')}:{srs.get('horizontal', '')}"
        if self.info.get("dataType") != "laszip":
            raise ValueError(f"{self.base}: EPT data type {self.info.get('dataType')!r}; only laszip is read")

    # ------------------------------------------------------------ the octree

    def _node_bounds(self, key: str) -> tuple[float, float, float, float]:
        d, x, y, _ = (int(v) for v in key.split("-"))
        x0, y0, _, x1, y1, _ = self.info["bounds"]
        size = (x1 - x0) / 2 ** d
        return x0 + x * size, y0 + y * size, x0 + (x + 1) * size, y0 + (y + 1) * size

    def _hierarchy(self, key: str) -> dict[str, int]:
        return json.loads(net.fetch(f"{self.base}/ept-hierarchy/{key}.json", self.cache, "ept", suffix=".json"))

    def nodes(self, area: tuple[float, float, float, float], max_depth: int | None = None) -> list[str]:
        """Keys of the data nodes over `area` (xmin, ymin, xmax, ymax in the EPT's SRS)."""
        def overlaps(key):
            a = self._node_bounds(key)
            return a[0] <= area[2] and a[2] >= area[0] and a[1] <= area[3] and a[3] >= area[1]

        found = []
        pending = [self._hierarchy("0-0-0-0")]
        while pending:
            hierarchy = pending.pop()
            for key, count in hierarchy.items():
                depth = int(key.split("-")[0])
                if (max_depth is not None and depth > max_depth) or not overlaps(key):
                    continue
                if count == -1:  # this subtree's hierarchy is in its own file
                    pending.append(self._hierarchy(key))
                elif count > 0:
                    found.append(key)
        return sorted(set(found))

    def _read_node(self, key: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        import laspy
        data = net.fetch(f"{self.base}/ept-data/{key}.laz", self.cache, "ept", suffix=".laz")
        las = laspy.read(io.BytesIO(data))
        xyz = np.column_stack([las.x, las.y, las.z]).astype(np.float64)
        classes = np.asarray(las.classification, dtype=np.uint8)
        layered = np.asarray(las.number_of_returns) > 1
        keep = np.ones(len(classes), bool)
        try:
            keep &= ~np.asarray(las.withheld, bool)
        except (AttributeError, ValueError):
            pass
        return xyz[keep], classes[keep], layered[keep]

    # ------------------------------------------------------------ reading an area

    def _to_lonlat(self, xy: np.ndarray) -> np.ndarray:
        if self.srs == "EPSG:3857":
            return geo.mercator_to_lonlat(xy)
        if self.srs == "EPSG:4326":
            return xy
        raise ValueError(f"{self.base}: horizontal SRS {self.srs}; only EPSG:3857 and EPSG:4326 are read")

    def _from_lonlat(self, lonlat: np.ndarray) -> np.ndarray:
        return geo.lonlat_to_mercator(lonlat) if self.srs == "EPSG:3857" else lonlat

    def depth_for(self, spacing: float, lat: float) -> int:
        """The octree depth whose points are about `spacing` metres apart; deeper adds no detail
        a model at that spacing would use, and a big area would not fit in memory."""
        x0, _, _, x1, _, _ = self.info["bounds"]
        root = (x1 - x0) / self.info.get("span", 128)
        if self.srs == "EPSG:3857":
            root *= math.cos(math.radians(lat))  # mercator metres are stretched by 1 / cos(lat)
        elif self.srs == "EPSG:4326":
            root *= 111_320 * math.cos(math.radians(lat))
        return max(0, int(math.ceil(math.log2(max(root / spacing, 1.0)))))

    def read(self, bbox: geo.BBox, frame: geo.LocalFrame, max_depth: int | None = None,
             progress=None) -> Points:
        corners = self._from_lonlat(np.array([[bbox.west, bbox.south], [bbox.east, bbox.north]]))
        area = (*corners.min(axis=0), *corners.max(axis=0))
        keys = self.nodes(area, max_depth)
        if progress:
            progress(f"  {len(keys)} LiDAR tiles over the area")
        with ThreadPoolExecutor(self.workers) as pool:
            parts = list(pool.map(self._read_node, keys))
        if not parts:
            return Points(np.zeros((0, 3)), np.zeros(0, np.uint8), self.base, np.zeros(0, bool))
        xyz = np.concatenate([p[0] for p in parts])
        classes = np.concatenate([p[1] for p in parts])
        layered = np.concatenate([p[2] for p in parts])
        inside = ((xyz[:, 0] >= area[0]) & (xyz[:, 0] <= area[2]) & (xyz[:, 1] >= area[1]) & (xyz[:, 1] <= area[3]))
        xyz, classes, layered = xyz[inside], classes[inside], layered[inside]
        local = frame.from_lonlat(self._to_lonlat(xyz[:, :2]))
        xyz = np.column_stack([local, xyz[:, 2]])
        keep = ~np.isin(classes, NOISE)
        return Points(xyz[keep], classes[keep], self.base, layered[keep])
