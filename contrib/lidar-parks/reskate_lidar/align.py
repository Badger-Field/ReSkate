"""Levelling a scan: find the ground, turn it so the ground is z = 0 with +z up, and centre it.

Scans arrive Z-up (survey scanners, LAS) or Y-up (phone apps exporting GLB/OBJ), and phone scans
drift a few degrees off level. The ground is the largest plane facing roughly up; everything is
rotated so that plane's normal is +z, then moved so the plane passes through the origin."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

UP_AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


@dataclass
class Alignment:
    rotation: np.ndarray      # 3 x 3, scan -> levelled
    offset: np.ndarray        # added after rotating
    ground_inliers: float     # share of points on the ground plane
    tilt_degrees: float       # how far the ground was from the chosen up axis

    def apply(self, points: np.ndarray) -> np.ndarray:
        return points @ self.rotation.T + self.offset


def rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The smallest rotation taking unit vector a to unit vector b."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if np.linalg.norm(v) < 1e-12:
        if c > 0:
            return np.eye(3)
        # Opposite: half turn about any axis perpendicular to a.
        axis = np.cross(a, [1.0, 0.0, 0.0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0.0, 1.0, 0.0])
        axis /= np.linalg.norm(axis)
        return 2.0 * np.outer(axis, axis) - np.eye(3)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k * (1.0 / (1.0 + c))


def fit_plane(points: np.ndarray) -> tuple[np.ndarray, float]:
    """Least-squares plane through points: unit normal n and d with n.p + d = 0."""
    centre = points.mean(axis=0)
    _, _, vt = np.linalg.svd(points - centre, full_matrices=False)
    normal = vt[-1]
    return normal, -float(normal @ centre)


def ransac_ground(points: np.ndarray, up_hint: np.ndarray | None, threshold: float = 0.04,
                  iterations: int = 400, max_tilt_degrees: float = 35.0, seed: int = 0,
                  sample_size: int = 200_000) -> tuple[np.ndarray, float, np.ndarray]:
    """The largest plane facing within `max_tilt_degrees` of `up_hint` (any direction when None).
    Returns its normal (pointing towards up_hint), d, and the inlier mask over `points`."""
    rng = np.random.default_rng(seed)
    sample = points if len(points) <= sample_size else points[rng.choice(len(points), sample_size, replace=False)]
    cos_limit = np.cos(np.radians(max_tilt_degrees))
    best_count, best = -1, None
    for _ in range(iterations):
        p = sample[rng.choice(len(sample), 3, replace=False)]
        normal = np.cross(p[1] - p[0], p[2] - p[0])
        length = np.linalg.norm(normal)
        if length < 1e-9:
            continue
        normal /= length
        if up_hint is not None:
            alignment = float(normal @ up_hint)
            if abs(alignment) < cos_limit:
                continue
            if alignment < 0:
                normal = -normal
        d = -float(normal @ p[0])
        count = int(np.count_nonzero(np.abs(sample @ normal + d) < threshold))
        if count > best_count:
            best_count, best = count, (normal, d)
    if best is None:
        raise ValueError("No ground plane found: is the up axis right (--up)?")
    normal, d = best
    # Refine on all inliers of the full cloud.
    inliers = np.abs(points @ normal + d) < threshold
    refined, refined_d = fit_plane(points[inliers])
    if refined @ normal < 0:
        refined, refined_d = -refined, -refined_d
    inliers = np.abs(points @ refined + refined_d) < threshold
    return refined, refined_d, inliers


def level(points: np.ndarray, up: str = "auto", seed: int = 0) -> Alignment:
    """Alignment that puts the ground at z = 0, +z up, centred on the scan's middle.

    `up`: "z", "y", "x" (optionally prefixed "-") when the scan's up axis is known, or "auto":
    the largest plane, oriented so most of the scan sits above it."""
    hint = None
    if up != "auto":
        sign = -1.0 if up.startswith("-") else 1.0
        hint = sign * np.array(UP_AXES[up.lstrip("-+")])
    normal, d, inliers = ransac_ground(points, hint, seed=seed)
    if hint is None:
        # Most of a park (ramps, ledges, rails) is above the ground, not below it.
        heights = points @ normal + d
        if np.count_nonzero(heights > 0.05) < np.count_nonzero(heights < -0.05):
            normal, d = -normal, -d
        nearest = max(UP_AXES.values(), key=lambda axis: abs(float(np.dot(axis, normal))))
        tilt_reference = np.sign(np.dot(nearest, normal)) * np.array(nearest)
    else:
        tilt_reference = hint
    rotation = rotation_between(normal, np.array([0.0, 0.0, 1.0]))
    rotated = points @ rotation.T
    ground_height = float(np.median(rotated[inliers][:, 2]))
    lo, hi = np.percentile(rotated[:, :2], [1, 99], axis=0)
    offset = np.array([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -ground_height])
    tilt = float(np.degrees(np.arccos(np.clip(normal @ tilt_reference, -1, 1))))
    return Alignment(rotation, offset, float(np.mean(inliers)), tilt)
