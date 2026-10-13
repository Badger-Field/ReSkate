"""The levelled scan as a clean surface mesh: the starting point for a true custom map (see
CUSTOM-MAPS.md), where the park's real shape is kept instead of Build Kit stand-ins.

The mesh is the height grid triangulated, so it is 2.5-D: one height per spot, as a skater
rides it. Overhangs (a coping's lip, the gap under a rail) close down to the ground. It is in
metres, Y up, ground at y = 0, the park's centre at the origin, in glTF's own right-handed frame
(+X along the scan's x, -Z along its y), which Blender and ReSkate Studio read the right way round
(CUSTOM-MAPS.md)."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .heightmap import HeightMap


def surface(hm: HeightMap, rails_from_peaks: bool = True):
    import trimesh

    rows, cols = hm.shape
    heights = hm.heights.astype(np.float64)
    if rails_from_peaks:
        # Thin raised lines (rails) are where the peaks stand well above the surface.
        heights = np.where(hm.peaks - heights > 0.1, hm.peaks, heights)
    index = np.arange(rows * cols).reshape(rows, cols)
    r, c = np.mgrid[0:rows, 0:cols]
    x = hm.origin[0] + c * hm.cell
    y = hm.origin[1] + r * hm.cell
    # Scan (x, y, z up) to glTF (x, y up, -y): a rotation, as citymesh.to_gltf.
    vertices = np.column_stack([x.ravel(), heights.ravel(), -y.ravel()])
    quad = hm.observed[:-1, :-1] & hm.observed[1:, :-1] & hm.observed[:-1, 1:] & hm.observed[1:, 1:]
    a, b = index[:-1, :-1][quad], index[:-1, 1:][quad]
    d, e = index[1:, :-1][quad], index[1:, 1:][quad]
    # Wound so normals face up (+y).
    faces = np.concatenate([np.column_stack([a, b, d]), np.column_stack([b, e, d])])
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    mesh.remove_unreferenced_vertices()
    return mesh


def write(hm: HeightMap, path: str | Path) -> tuple[int, int]:
    mesh = surface(hm)
    mesh.export(path)
    return len(mesh.vertices), len(mesh.faces)
