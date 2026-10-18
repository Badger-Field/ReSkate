"""Places on the Earth as a flat local frame in metres.

A city area is small (a plaza, a few blocks), so a tangent plane at its centre is accurate to a
few centimetres over a kilometre: x east, y north, z up, the same frame a levelled scan uses.
park.py and meshing.py carry it on into the game's frame (x, y up, z), so north in the real place
is the game's +z (forward) and east its +x, before the anchor turns it."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

WGS84_A = 6378137.0
WGS84_E2 = 6.69437999014e-3
MERCATOR_R = 6378137.0  # EPSG:3857 sphere


@dataclass(frozen=True)
class BBox:
    """Longitude and latitude bounds, degrees (WGS 84)."""
    west: float
    south: float
    east: float
    north: float

    def __post_init__(self):
        if not (-180 <= self.west < self.east <= 180 and -85 <= self.south < self.north <= 85):
            raise ValueError(f"Bounding box {self.west},{self.south},{self.east},{self.north}: "
                             "give west,south,east,north in degrees, west < east and south < north")

    @property
    def centre(self) -> tuple[float, float]:
        return (self.south + self.north) / 2, (self.west + self.east) / 2

    def key(self) -> str:
        """A file-name-safe key for caching, to about 10 cm."""
        return "_".join(f"{v:.6f}" for v in (self.west, self.south, self.east, self.north))


def parse_bbox(text: str) -> BBox:
    parts = [float(p) for p in re.split(r"[,\s]+", text.strip()) if p]
    if len(parts) != 4:
        raise ValueError(f"--bbox {text!r}: give four numbers, west,south,east,north")
    return BBox(*parts)


def parse_latlon(text: str) -> tuple[float, float]:
    parts = [float(p) for p in re.split(r"[,\s]+", text.strip()) if p]
    if len(parts) != 2 or not (-85 <= parts[0] <= 85 and -180 <= parts[1] <= 180):
        raise ValueError(f"--centre {text!r}: give latitude,longitude in degrees")
    return parts[0], parts[1]


def bbox_around(lat: float, lon: float, width: float, height: float | None = None) -> BBox:
    """A box `width` x `height` metres centred on a point."""
    frame = LocalFrame(lat, lon)
    height = width if height is None else height
    (w, s), (e, n) = frame.to_lonlat(np.array([[-width / 2, -height / 2], [width / 2, height / 2]]))
    return BBox(float(w), float(s), float(e), float(n))


@dataclass(frozen=True)
class LocalFrame:
    """A tangent plane at (lat, lon): x metres east, y metres north."""
    lat: float
    lon: float

    @property
    def _radii(self) -> tuple[float, float]:
        s = math.sin(math.radians(self.lat))
        w = 1 - WGS84_E2 * s * s
        meridian = WGS84_A * (1 - WGS84_E2) / w ** 1.5
        normal = WGS84_A / math.sqrt(w)
        return meridian, normal

    def from_lonlat(self, lonlat: np.ndarray) -> np.ndarray:
        lonlat = np.asarray(lonlat, float)
        meridian, normal = self._radii
        x = np.radians(lonlat[..., 0] - self.lon) * normal * math.cos(math.radians(self.lat))
        y = np.radians(lonlat[..., 1] - self.lat) * meridian
        return np.stack([x, y], axis=-1)

    def to_lonlat(self, xy: np.ndarray) -> np.ndarray:
        xy = np.asarray(xy, float)
        meridian, normal = self._radii
        lon = self.lon + np.degrees(xy[..., 0] / (normal * math.cos(math.radians(self.lat))))
        lat = self.lat + np.degrees(xy[..., 1] / meridian)
        return np.stack([lon, lat], axis=-1)

    def bounds(self, bbox: BBox) -> tuple[float, float, float, float]:
        """The box's corners in this frame: xmin, ymin, xmax, ymax."""
        (x0, y0), (x1, y1) = self.from_lonlat(np.array([[bbox.west, bbox.south], [bbox.east, bbox.north]]))
        return float(x0), float(y0), float(x1), float(y1)


def mercator_to_lonlat(xy: np.ndarray) -> np.ndarray:
    """EPSG:3857 (web mercator) metres to degrees."""
    xy = np.asarray(xy, float)
    lon = np.degrees(xy[..., 0] / MERCATOR_R)
    lat = np.degrees(np.arctan(np.sinh(xy[..., 1] / MERCATOR_R)))
    return np.stack([lon, lat], axis=-1)


def lonlat_to_mercator(lonlat: np.ndarray) -> np.ndarray:
    lonlat = np.asarray(lonlat, float)
    x = np.radians(lonlat[..., 0]) * MERCATOR_R
    y = np.log(np.tan(math.pi / 4 + np.radians(lonlat[..., 1]) / 2)) * MERCATOR_R
    return np.stack([x, y], axis=-1)


def transform_geometry(geometry, function):
    """A shapely geometry with every coordinate passed through function((n, 2) array) -> (n, 2)."""
    import shapely
    return shapely.transform(geometry, function)
