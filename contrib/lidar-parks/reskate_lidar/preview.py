"""A top-down picture of what was found and what will be placed, to check before going in game:
the scan as shaded heights, each feature outlined in its kind's colour, each placed object as a
white outline."""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

from .catalog import Placement
from .features import Feature
from .heightmap import HeightMap

COLOURS = {
    "rail": (255, 80, 80), "ledge": (255, 170, 0), "manual_pad": (240, 230, 60), "box": (180, 120, 255),
    "stairs": (60, 200, 255), "kicker": (80, 230, 120), "bank": (40, 160, 90), "quarter": (255, 100, 220),
    "bowl": (120, 120, 255),
}


def render(hm: HeightMap, features: list[Feature], placements: list[Placement], path, max_side: int = 1600) -> None:
    rows, cols = hm.shape
    scale = min(1.0, max_side / max(rows, cols))
    h = hm.heights.astype(np.float64)
    gy, gx = np.gradient(h, hm.cell)
    shade = np.clip(0.6 + 0.4 * (-gx - gy) / np.sqrt(1 + gx * gx + gy * gy), 0, 1)
    tone = np.clip((h + 1.0) / 4.0, 0, 1)
    grey = (np.clip(0.25 + 0.5 * tone, 0, 1) * shade * 255).astype(np.uint8)
    grey[~hm.observed] = 20
    image = Image.fromarray(np.flipud(grey)).convert("RGB")
    if scale < 1.0:
        image = image.resize((max(1, int(cols * scale)), max(1, int(rows * scale))), Image.BILINEAR)
    draw = ImageDraw.Draw(image)

    def pixel(x, y):
        c = (x - hm.origin[0]) / hm.cell
        r = (y - hm.origin[1]) / hm.cell
        return (c * scale, (rows - 1 - r) * scale)

    for index, f in enumerate(features):
        colour = COLOURS.get(f.kind, (200, 200, 200))
        corners = [pixel(*p) for p in f.footprint()]
        draw.polygon(corners, outline=colour, width=3)
        cx, cy = pixel(*f.centre)
        if f.kind in ("stairs", "kicker", "bank", "quarter"):
            tip = pixel(f.centre[0] + math.cos(f.yaw) * f.along / 2, f.centre[1] + math.sin(f.yaw) * f.along / 2)
            draw.line([(cx, cy), tip], fill=colour, width=3)
        draw.text((cx + 4, cy + 4), f"#{index} {f.kind} {f.height:.2f}m", fill=colour)
    for p in placements:
        item = p.item
        along = item.along * p.scale * (math.cos(p.pitch) if p.pitch else 1.0)
        across = item.across * p.scale
        f = Feature("box", (p.x, p.y), p.yaw, along, across, 0.0)
        draw.polygon([pixel(*q) for q in f.footprint()], outline=(255, 255, 255), width=1)
    image.save(path)


def render_city(model, placements: list[Placement], path, max_side: int = 1800) -> None:
    """A city model from above: shaded ground, public space tinted, buildings in grey, each spot
    outlined in its kind's colour with its number, placed objects in white. North is up."""
    import shapely
    from .city import rasterise

    grid = model.ground
    rows, cols = grid.shape
    scale = min(4.0, max_side / max(rows, cols))
    h = grid.heights
    gy, gx = np.gradient(h, grid.cell)
    shade = np.clip(0.6 + 0.4 * (-gx - gy) / np.sqrt(1 + gx * gx + gy * gy), 0, 1)
    tone = np.clip((h - np.percentile(h, 2)) / max(np.ptp(h), 1.0), 0, 1)
    grey = np.clip(0.3 + 0.4 * tone, 0, 1) * shade
    rgb = np.stack([grey, grey, grey], axis=-1)
    public = rasterise(model.car_free, grid)
    rgb[public] = rgb[public] * [0.75, 0.95, 0.8]
    image = Image.fromarray((np.flipud(rgb) * 255).astype(np.uint8))
    image = image.resize((max(1, int(cols * scale)), max(1, int(rows * scale))), Image.BILINEAR)
    draw = ImageDraw.Draw(image)

    def pixel(x, y):
        return ((x - grid.origin[0]) / grid.cell * scale, (rows - 1 - (y - grid.origin[1]) / grid.cell) * scale)

    for b in model.buildings:
        for polygon in (b.footprint.geoms if b.footprint.geom_type == "MultiPolygon" else [b.footprint]):
            draw.polygon([pixel(x, y) for x, y in polygon.exterior.coords], fill=(90, 90, 100), outline=(200, 200, 210))
            c = polygon.representative_point()
            draw.text(pixel(c.x, c.y), f"{b.height:.0f}m", fill=(230, 230, 240))
    for index, spot in enumerate(model.spots):
        f = spot.feature
        colour = COLOURS.get(f.kind, (200, 200, 200))
        draw.polygon([pixel(*p) for p in f.footprint()], outline=colour, width=3)
        cx, cy = pixel(*f.centre)
        if f.kind in ("stairs", "kicker", "bank", "quarter"):
            tip = pixel(f.centre[0] + math.cos(f.yaw) * f.along / 2, f.centre[1] + math.sin(f.yaw) * f.along / 2)
            draw.line([(cx, cy), tip], fill=colour, width=3)
        draw.text((cx + 4, cy + 4), f"#{index}", fill=colour)
    for p in placements:
        along = p.item.along * p.scale * (math.cos(p.pitch) if p.pitch else 1.0)
        f = Feature("box", (p.x, p.y), p.yaw, along, p.item.across * p.scale, 0.0)
        draw.polygon([pixel(*q) for q in f.footprint()], outline=(255, 255, 255), width=1)
    for grind in getattr(model, "grinds", []):
        line = [pixel(x, y) for x, y in grind.points[:, :2]]
        if grind.closed:
            line.append(line[0])
        draw.line(line, fill=(255, 60, 60), width=3)
    if getattr(model, "spawn", None) is not None:
        sx, sy = pixel(*model.spawn)
        draw.ellipse([sx - 7, sy - 7, sx + 7, sy + 7], outline=(255, 255, 255), width=3)
        draw.text((sx + 9, sy - 6), "spawn", fill=(255, 255, 255))
    image.save(path)
