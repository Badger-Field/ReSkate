"""A levelled scan as a top-down height grid: the highest surface in each cell, which is what a
skater rides. Overhangs (trees, roofs, lamp heads) above `ceiling` are dropped first."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage


@dataclass
class HeightMap:
    heights: np.ndarray    # [row, col] metres above ground: the surface (upper quartile of the cell's points)
    observed: np.ndarray   # cells with scan data near them
    origin: np.ndarray     # (x, y) of cell [0, 0]'s centre
    cell: float            # cell size, metres
    peaks: np.ndarray | None = None  # the highest point per cell: where a thin rail shows
    noise: float = 0.0     # spread of the ground's cell heights (metres, one sigma)

    def __post_init__(self):
        if self.peaks is None:
            self.peaks = self.heights

    def cell_centres(self, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
        return np.column_stack([self.origin[0] + cols * self.cell, self.origin[1] + rows * self.cell])

    @property
    def shape(self) -> tuple[int, int]:
        return self.heights.shape


def build(points: np.ndarray, cell: float = 0.05, ceiling: float = 4.5, floor: float = -4.0,
          max_cells: int = 16_000_000, quantile: float = 0.75) -> HeightMap:
    keep = (points[:, 2] < ceiling) & (points[:, 2] > floor)
    points = points[keep]
    lo = points[:, :2].min(axis=0)
    hi = points[:, :2].max(axis=0)
    cols, rows = (np.floor((hi - lo) / cell).astype(int) + 1)
    if rows * cols > max_cells:
        raise ValueError(f"The scan covers {(hi - lo)[0]:.0f} x {(hi - lo)[1]:.0f} m: too large at {cell} m cells. "
                         "Crop it to the park, or use a larger --cell.")
    c = np.floor((points[:, 0] - lo[0]) / cell).astype(int)
    r = np.floor((points[:, 1] - lo[1]) / cell).astype(int)
    flat = r * cols + c
    z = points[:, 2]

    # Per cell: the highest point, and the upper quartile. The quartile is the surface: the top
    # of a box without the noise the maximum picks up, and above a cell's share of side-wall
    # points. The maximum is kept for rails, which may fill only part of a cell.
    order = np.lexsort((z, flat))
    flat_sorted, z_sorted = flat[order], z[order]
    counts = np.bincount(flat, minlength=rows * cols)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    has_flat = counts > 0
    pick = starts[has_flat] + np.floor(quantile * (counts[has_flat] - 1)).astype(int)
    top = np.full(rows * cols, np.nan)
    peak = np.full(rows * cols, np.nan)
    top[has_flat] = z_sorted[pick]
    peak[has_flat] = z_sorted[starts[has_flat] + counts[has_flat] - 1]
    top, peak = top.reshape(rows, cols), peak.reshape(rows, cols)
    has = has_flat.reshape(rows, cols)
    del flat_sorted

    # Holes: take the nearest observed height, but only close to real data.
    distance, (near_r, near_c) = ndimage.distance_transform_edt(~has, return_indices=True)
    heights, peaks = top[near_r, near_c], peak[near_r, near_c]
    observed = distance * cell <= 0.3

    # A cell standing above all eight neighbours is a stray point (bird, scanner noise), not a
    # rail: even a one-cell-wide rail has neighbours along its length at its own height.
    ring = np.ones((3, 3), bool)
    ring[1, 1] = False
    for grid in (heights, peaks):
        neighbours = ndimage.maximum_filter(grid, footprint=ring, mode="nearest")
        np.copyto(grid, neighbours, where=grid - neighbours > 0.1)

    # Ground noise: how much cells near zero height wander, from their median absolute deviation.
    near_ground = heights[observed & (np.abs(heights) < 0.1)]
    noise = float(1.4826 * np.median(np.abs(near_ground - np.median(near_ground)))) if near_ground.size else 0.0
    origin = lo + cell / 2
    return HeightMap(heights.astype(np.float32), observed, origin, cell, peaks.astype(np.float32), noise)
