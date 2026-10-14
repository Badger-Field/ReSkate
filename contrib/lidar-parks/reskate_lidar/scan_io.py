"""Reading a scan: a mesh (PLY, OBJ, GLB/GLTF, STL) from a phone LiDAR app, or a point cloud
(PLY, LAS/LAZ, XYZ/TXT/CSV) from a scanner. Everything comes out as one N x 3 array of points in
metres; meshes are sampled evenly over their surface so a big flat triangle counts as much as a
detailed corner."""
from __future__ import annotations

from pathlib import Path

import numpy as np

MESH_SUFFIXES = {".ply", ".obj", ".glb", ".gltf", ".stl", ".off"}
LAS_SUFFIXES = {".las", ".laz"}
TEXT_SUFFIXES = {".xyz", ".txt", ".csv", ".pts"}


def load_points(path: str | Path, density: float = 400.0, max_points: int = 6_000_000,
                seed: int = 0) -> np.ndarray:
    """Points of the scan at `path`. `density`: points per square metre sampled from a mesh."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in LAS_SUFFIXES:
        points = _load_las(path)
    elif suffix in TEXT_SUFFIXES:
        points = _load_text(path)
    elif suffix in MESH_SUFFIXES:
        points = _load_mesh_or_cloud(path, density, max_points, seed)
    elif suffix == ".e57":
        raise ValueError("E57 is not read directly: export it as LAS or PLY first "
                         "(CloudCompare: File > Save, choose LAS or PLY).")
    elif suffix == ".usdz":
        raise ValueError("USDZ is not read directly: export the scan as GLB, OBJ or PLY from the app.")
    else:
        raise ValueError(f"Unknown scan format {suffix!r}.")
    points = np.asarray(points, dtype=np.float64)
    points = points[np.isfinite(points).all(axis=1)]
    if len(points) < 1000:
        raise ValueError(f"{path.name} has only {len(points)} usable points.")
    if len(points) > max_points:
        keep = np.random.default_rng(seed).choice(len(points), max_points, replace=False)
        points = points[keep]
    return points


def _load_las(path: Path) -> np.ndarray:
    try:
        import laspy
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise ValueError("Reading LAS/LAZ needs laspy: pip install 'laspy[lazrs]'") from error
    las = laspy.read(path)
    return np.column_stack([np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)])


def _load_text(path: Path) -> np.ndarray:
    delimiter = "," if path.suffix.lower() == ".csv" else None
    data = np.loadtxt(path, delimiter=delimiter, usecols=(0, 1, 2), comments=("#", "//"), ndmin=2)
    return data[:, :3]


def _load_mesh_or_cloud(path: Path, density: float, max_points: int, seed: int) -> np.ndarray:
    import trimesh

    loaded = trimesh.load(path, process=False)
    if isinstance(loaded, trimesh.Scene):
        meshes = [geometry for geometry in loaded.dump() if isinstance(geometry, trimesh.Trimesh)]
        clouds = [geometry for geometry in loaded.dump() if isinstance(geometry, trimesh.PointCloud)]
        if meshes:
            loaded = trimesh.util.concatenate(meshes)
        elif clouds:
            return np.vstack([np.asarray(cloud.vertices) for cloud in clouds])
        else:
            raise ValueError(f"{path.name} holds no mesh or points.")
    if isinstance(loaded, trimesh.PointCloud):
        return np.asarray(loaded.vertices)
    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
        return np.asarray(getattr(loaded, "vertices", np.empty((0, 3))))
    count = int(min(max_points, max(50_000, loaded.area * density)))
    points, _ = trimesh.sample.sample_surface_even(loaded, count, seed=seed) \
        if hasattr(trimesh.sample, "sample_surface_even") else trimesh.sample.sample_surface(loaded, count)
    # Even sampling can come back short; top up with plain area-weighted samples.
    if len(points) < count * 0.8:
        extra, _ = trimesh.sample.sample_surface(loaded, count - len(points), seed=seed)
        points = np.vstack([points, extra])
    return np.asarray(points)
