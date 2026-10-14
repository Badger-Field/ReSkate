"""A synthetic skatepark scan with known obstacles, for testing the converter without a real
scan and for checking a change to the detector against the truth."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class Truth:
    kind: str
    centre: tuple[float, float]
    yaw: float      # long axis, or uphill
    along: float
    across: float
    height: float


def _box(centre, yaw, along, across, height, base=0.0):
    import trimesh
    mesh = trimesh.creation.box(extents=(along, across, height - base))
    mesh.apply_translation((0, 0, base + (height - base) / 2))
    mesh.apply_transform(trimesh.transformations.rotation_matrix(yaw, (0, 0, 1)))
    mesh.apply_translation((centre[0], centre[1], 0))
    return mesh


def _profile(centre, yaw, run, width, profile):
    """A ramp from a 2-D (distance uphill, height) profile, extruded across `width`."""
    import trimesh
    from shapely.geometry import Polygon
    polygon = Polygon(profile + [(run, 0.0), (0.0, 0.0)])
    mesh = trimesh.creation.extrude_polygon(polygon, width)
    # Polygon in (x=uphill, y=height), extruded along z: turn to (x uphill, y across, z up).
    mesh.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, (1, 0, 0)))
    mesh.apply_translation((-run / 2, width / 2, 0))
    mesh.apply_transform(trimesh.transformations.rotation_matrix(yaw, (0, 0, 1)))
    mesh.apply_translation((centre[0], centre[1], 0))
    return mesh


def scene(seed: int = 0):
    """Meshes of a small park and what is in it."""
    import trimesh
    meshes, truth = [], []
    # The ground is one sheet: a scanner never sees the underside of the park.
    ground = trimesh.Trimesh(vertices=[(-17, -17, 0), (17, -17, 0), (17, 17, 0), (-17, 17, 0)],
                             faces=[(0, 1, 2), (0, 2, 3)])
    meshes.append(ground)

    # Ledge: 6 m x 0.45 m x 0.45 m, turned 20 degrees.
    meshes.append(_box((-8, 8), math.radians(20), 6.0, 0.45, 0.45))
    truth.append(Truth("ledge", (-8, 8), math.radians(20), 6.0, 0.45, 0.45))
    # Manual pad: 4 x 2 x 0.3.
    meshes.append(_box((6, 9), math.radians(-10), 4.0, 2.0, 0.3))
    truth.append(Truth("manual_pad", (6, 9), math.radians(-10), 4.0, 2.0, 0.3))
    # Flat rail: 5 cm bar at 0.4 m on two posts.
    rail_yaw = math.radians(75)
    meshes.append(_box((-9, -6), rail_yaw, 4.0, 0.05, 0.4, base=0.35))
    for t in (-1.6, 1.6):
        meshes.append(_box((-9 + t * math.cos(rail_yaw), -6 + t * math.sin(rail_yaw)), rail_yaw, 0.05, 0.05, 0.36))
    truth.append(Truth("rail", (-9, -6), rail_yaw, 4.0, 0.05, 0.4))
    # Kicker: 1.8 m run, 1.5 m wide, 0.6 m tall, facing +y (uphill is +y).
    meshes.append(_profile((0, -9), math.radians(90), 1.8, 1.5, [(1.8, 0.6)]))
    truth.append(Truth("kicker", (0, -9), math.radians(90), 1.8, 1.5, 0.6))
    # Quarter pipe: 1.6 m radius transition, 1.5 m tall, 4 m wide, uphill towards +x.
    radius, height = 1.6, 1.5
    angles = np.linspace(0, math.acos(1 - height / radius), 16)
    curve = [(radius * math.sin(a), radius * (1 - math.cos(a))) for a in angles]
    run = curve[-1][0] + 0.3
    profile = curve + [(run, height)]
    meshes.append(_profile((12, -2), 0.0, run, 4.0, profile))
    truth.append(Truth("quarter", (12, -2), 0.0, run, 4.0, height))
    # Stairs: 4 steps of 0.16 m, 0.35 m deep, 3 m wide, climbing towards -x, then a landing.
    stairs_yaw = math.pi
    u = np.array([math.cos(stairs_yaw), math.sin(stairs_yaw)])
    start = np.array([2.0, 2.0])
    for k in range(4):
        c = start + u * (0.35 * k + 0.175)
        meshes.append(_box(tuple(c), stairs_yaw, 0.35, 3.0, 0.16 * (k + 1)))
    landing = start + u * (0.35 * 4 + 1.0)
    meshes.append(_box(tuple(landing), stairs_yaw, 2.0, 3.0, 0.8))
    stairs_centre = start + u * (0.35 * 4 / 2)
    truth.append(Truth("stairs", tuple(stairs_centre), stairs_yaw, 1.4, 3.0, 0.8))
    return meshes, truth


def scan(seed: int = 0, density: float = 900.0, noise: float = 0.006, yaw_degrees: float = 23.0,
         tilt_degrees: float = 2.0, y_up: bool = True):
    """Points of the synthetic park as a phone would deliver them: sampled surfaces with noise,
    turned and tilted a little, Y-up like a GLB export. Returns (points, truth, transform) where
    transform is the 4 x 4 applied to the clean scene."""
    import trimesh
    meshes, truth = scene(seed)
    mesh = trimesh.util.concatenate(meshes)
    # Faces pointing down sit on the ground, where no scanner can see them.
    mesh.update_faces(mesh.face_normals[:, 2] > -0.9)
    count = int(mesh.area * density)
    points, _ = trimesh.sample.sample_surface(mesh, count, seed=seed)
    # Nor can it see the ground under a solid obstacle (a rail's bar is not solid: the ground
    # shows from either side).
    import shapely
    hidden = np.zeros(len(points), bool)
    on_ground = np.abs(points[:, 2]) < 1e-6
    for solid in meshes[1:]:
        if solid.bounds[0][2] > 1e-6:
            continue
        footprint = shapely.MultiPoint(solid.vertices[:, :2]).convex_hull.buffer(-1e-3)
        if footprint.area < 0.05:
            continue
        hidden |= on_ground & shapely.contains_xy(footprint, points[:, 0], points[:, 1])
    points = points[~hidden]
    rng = np.random.default_rng(seed)
    points = points + rng.normal(0, noise, points.shape)
    transform = trimesh.transformations.rotation_matrix(math.radians(yaw_degrees), (0, 0, 1))
    transform = trimesh.transformations.rotation_matrix(math.radians(tilt_degrees), (1, 0, 0)) @ transform
    if y_up:
        transform = trimesh.transformations.rotation_matrix(-math.pi / 2, (1, 0, 0)) @ transform
    transform[:3, 3] = (3.0, 1.5, -7.0)
    points = trimesh.transform_points(points, transform)
    return points, truth, transform


def write_ply(path, points: np.ndarray) -> None:
    import trimesh
    trimesh.PointCloud(points).export(path)
