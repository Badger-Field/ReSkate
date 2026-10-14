"""LiDAR from tiles on disk: LAS, LAZ and COPC files in any projected CRS.

Most national surveys outside the US hand out tiles rather than EPT:
- the Netherlands' AHN, in EPSG:28992;
- the UK Environment Agency, in EPSG:27700;
- Denmark and Finland, in UTM zones (EPSG:25832, 3067).

Download the tiles over the area, then pass the files or their folder with `--lidar`. Only tiles
whose header bounds reach the area are read. A COPC file (`.copc.laz`) is queried for just the
area. Any other tile is read in chunks and cropped.

Coordinates are reprojected with pyproj (`pip install pyproj`). The tiles' CRS comes from their
header, or from `--lidar-crs EPSG:<code>` where the header has none. EPSG:3857 and EPSG:4326 need
no pyproj."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import geo
from .ept import NOISE, Points

SUFFIXES = (".las", ".laz")
CHUNK = 2_000_000


class Projection:
    """Between longitude/latitude and one CRS's x, y."""

    def __init__(self, crs: str):
        self.crs = crs.upper() if crs.upper().startswith("EPSG:") else crs
        self._to = self._from = None
        if self.crs not in ("EPSG:3857", "EPSG:4326"):
            try:
                import pyproj
            except ImportError:
                raise ValueError(f"the LiDAR is in {crs}: reprojecting it needs pyproj (pip install pyproj)") from None
            target = pyproj.CRS.from_user_input(crs)
            self._from = pyproj.Transformer.from_crs("EPSG:4326", target, always_xy=True)
            self._to = pyproj.Transformer.from_crs(target, "EPSG:4326", always_xy=True)

    def to_lonlat(self, xy: np.ndarray) -> np.ndarray:
        if self.crs == "EPSG:3857":
            return geo.mercator_to_lonlat(xy)
        if self.crs == "EPSG:4326":
            return xy
        return np.column_stack(self._to.transform(xy[:, 0], xy[:, 1]))

    def from_lonlat(self, lonlat: np.ndarray) -> np.ndarray:
        if self.crs == "EPSG:3857":
            return geo.lonlat_to_mercator(lonlat)
        if self.crs == "EPSG:4326":
            return lonlat
        return np.column_stack(self._from.transform(lonlat[:, 0], lonlat[:, 1]))

    def area(self, bbox: geo.BBox) -> tuple[float, float, float, float]:
        """The box's extent in this CRS. The edges are sampled, since they bend when reprojected."""
        t = np.linspace(0, 1, 9)
        lon = bbox.west + t * (bbox.east - bbox.west)
        lat = bbox.south + t * (bbox.north - bbox.south)
        edge = np.vstack([np.column_stack([lon, np.full(9, bbox.south)]), np.column_stack([lon, np.full(9, bbox.north)]),
                          np.column_stack([np.full(9, bbox.west), lat]), np.column_stack([np.full(9, bbox.east), lat])])
        xy = self.from_lonlat(edge)
        return (*xy.min(axis=0), *xy.max(axis=0))


def is_tiles(source: str) -> bool:
    """Whether `--lidar` names tiles on disk rather than an EPT dataset."""
    path = Path(source)
    if path.is_file():
        return path.suffix.lower() in SUFFIXES
    return path.is_dir() and not (path / "ept.json").exists() and any(_tiles_in(path))


def _tiles_in(folder: Path):
    return (p for p in sorted(folder.rglob("*")) if p.is_file() and p.suffix.lower() in SUFFIXES)


def header_crs(header) -> str | None:
    try:
        crs = header.parse_crs()
    except Exception:  # no pyproj, or a VLR it can't read
        return None
    if crs is None:
        return None
    crs = getattr(crs, "sub_crs_list", None) and crs.sub_crs_list[0] or crs  # compound: horizontal part
    code = crs.to_epsg()
    return f"EPSG:{code}" if code else crs.to_wkt()


@dataclass
class TileReader:
    paths: list[Path]
    crs: str | None = None       # overrides the headers'

    @classmethod
    def open(cls, sources: list[str], crs: str | None = None) -> "TileReader":
        paths = []
        for source in sources:
            path = Path(source)
            if path.is_dir():
                paths += list(_tiles_in(path))
            elif path.is_file():
                paths.append(path)
            else:
                raise FileNotFoundError(f"--lidar: {source} not found")
        if not paths:
            raise ValueError(f"--lidar: no .las or .laz tiles in {', '.join(sources)}")
        return cls(paths, crs)

    @property
    def name(self) -> str:
        return self.paths[0].name if len(self.paths) == 1 else f"{len(self.paths)} tiles from {self.paths[0].parent}"

    def _crs_of(self, path: Path, header) -> str:
        crs = self.crs or header_crs(header)
        if not crs:
            raise ValueError(f"{path.name} has no CRS in its header: pass --lidar-crs EPSG:<code>")
        return crs

    def read(self, bbox: geo.BBox, frame: geo.LocalFrame, max_points: int | None = None, progress=None) -> Points:
        import laspy
        parts = []
        projections: dict[str, Projection] = {}
        used = 0
        for path in self.paths:
            with laspy.open(path) as reader:
                header = reader.header
                crs = self._crs_of(path, header)
                projection = projections.setdefault(crs, Projection(crs))
                area = projection.area(bbox)
                lo, hi = header.mins, header.maxs
                if lo[0] > area[2] or hi[0] < area[0] or lo[1] > area[3] or hi[1] < area[1]:
                    continue
                used += 1
                if path.name.lower().endswith(".copc.laz"):
                    chunks = [_copc_query(path, area)]
                else:
                    chunks = reader.chunk_iterator(CHUNK)
                for points in chunks:
                    xyz, classes, layered = _columns(points)
                    inside = ((xyz[:, 0] >= area[0]) & (xyz[:, 0] <= area[2]) &
                              (xyz[:, 1] >= area[1]) & (xyz[:, 1] <= area[3]) & ~np.isin(classes, NOISE))
                    if inside.any():
                        xyz = xyz[inside]
                        local = frame.from_lonlat(projection.to_lonlat(xyz[:, :2]))
                        parts.append((np.column_stack([local, xyz[:, 2]]), classes[inside], layered[inside]))
        if progress:
            progress(f"  {used} of {len(self.paths)} LiDAR tiles over the area")
        if not parts:
            return Points(np.zeros((0, 3)), np.zeros(0, np.uint8), self.name, np.zeros(0, bool))
        xyz = np.concatenate([p[0] for p in parts])
        classes = np.concatenate([p[1] for p in parts])
        layered = np.concatenate([p[2] for p in parts])
        # The projected box is a little bigger than the area: crop to it in the local frame.
        x0, y0, x1, y1 = frame.bounds(bbox)
        inside = (xyz[:, 0] >= x0) & (xyz[:, 0] <= x1) & (xyz[:, 1] >= y0) & (xyz[:, 1] <= y1)
        xyz, classes, layered = xyz[inside], classes[inside], layered[inside]
        if max_points and len(xyz) > max_points:
            if progress:
                progress(f"  {len(xyz):,} points: keeping an even {max_points:,}")
            keep = np.random.default_rng(0).choice(len(xyz), max_points, replace=False)
            keep.sort()
            xyz, classes, layered = xyz[keep], classes[keep], layered[keep]
        return Points(xyz, classes, self.name, layered)


def _copc_query(path: Path, area):
    from laspy import Bounds, CopcReader
    with CopcReader.open(str(path)) as reader:
        return reader.query(Bounds(mins=np.array(area[:2]), maxs=np.array(area[2:])))


def _columns(points):
    xyz = np.column_stack([points.x, points.y, points.z]).astype(np.float64)
    classes = np.asarray(points.classification, dtype=np.uint8)
    layered = np.asarray(points.number_of_returns) > 1
    try:
        withheld = np.asarray(points.withheld, bool)
    except (AttributeError, ValueError):
        withheld = np.zeros(len(classes), bool)
    return xyz[~withheld], classes[~withheld], layered[~withheld]
