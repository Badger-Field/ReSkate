"""Choosing Build Kit objects for detected features.

Objects scale uniformly only (the game keeps one scale per object), so an object is scaled to the
feature's height and then repeated to cover its length: a 9 m ledge becomes three 3 m grind
boxes end to end, a 6 m wide quarter pipe two 3 m ones side by side."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .features import Feature

# Kinds whose objects repeat along their long axis; the rest repeat across (side by side).
LINEAR = {"rail", "ledge", "manual_pad", "box"}


@dataclass
class CatalogItem:
    item: str
    kinds: list[str]
    along: float
    across: float
    height: float
    estimated: bool = True
    yaw_offset_degrees: float = 0.0
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    min_scale: float = 0.6
    max_scale: float = 1.6
    has_rail: bool = False


@dataclass
class Catalog:
    items: list[CatalogItem]
    marker: str

    def for_kind(self, kind: str) -> list[CatalogItem]:
        return [item for item in self.items if kind in item.kinds]


def load_catalog(path: str | Path) -> Catalog:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise ValueError(f"{path}: unknown catalog schema {data.get('schema')!r}")
    items = []
    for entry in data["items"]:
        item = CatalogItem(
            item=entry["item"], kinds=list(entry["kinds"]), along=float(entry["along"]),
            across=float(entry["across"]), height=float(entry["height"]),
            estimated=bool(entry.get("estimated", True)),
            yaw_offset_degrees=float(entry.get("yaw_offset_degrees", 0.0)),
            origin=tuple(float(v) for v in entry.get("origin", (0.0, 0.0, 0.0))),
            min_scale=float(entry.get("min_scale", 0.6)), max_scale=float(entry.get("max_scale", 1.6)),
            has_rail=bool(entry.get("has_rail", False)))
        if not item.item.startswith("own_bk"):
            raise ValueError(f"{item.item}: the game only places Build Kit items (own_bk...)")
        if min(item.along, item.across, item.height) <= 0:
            raise ValueError(f"{item.item}: sizes must be positive")
        items.append(item)
    return Catalog(items, data.get("marker", "own_bkprops_generic_measuringstick_00001"))


@dataclass
class Placement:
    item: CatalogItem
    x: float           # base centre, levelled scan frame (metres)
    y: float
    z: float
    yaw: float         # radians from +x towards +y
    pitch: float       # radians, rising along yaw
    scale: float
    feature: int       # index into the detected features
    notes: list[str] = field(default_factory=list)


@dataclass
class Choice:
    item: CatalogItem
    scale: float
    copies: int
    score: float


def choose(feature: Feature, catalog: Catalog, fixed_scale: bool = False) -> Choice | None:
    """The catalog object, scale and number of copies that best fit a feature. With
    `fixed_scale`, every object stays at scale 1 (the nearest size in the catalog), for parks
    whose loader must not see per-object scales."""
    candidates = catalog.for_kind(feature.kind)
    if feature.kind == "stairs":
        # Handrails found beside stairs are placed on their own; plain stairs are the default.
        candidates = [c for c in candidates if not c.has_rail] or candidates
    best = None
    linear = feature.kind in LINEAR
    fill = feature.along if linear else feature.across      # covered by repeating copies
    other = feature.across if linear else feature.along     # matched by the object's own size
    for item in candidates:
        target_height = feature.height - (feature.base if feature.kind != "rail" else 0.0)
        scale = float(np.clip(max(target_height, 0.05) / item.height, item.min_scale, item.max_scale))
        if feature.kind == "rail" or fixed_scale:
            scale = 1.0  # a rail's height is where it is placed, not how big it is
        item_fill = (item.along if linear else item.across) * scale
        item_other = (item.across if linear else item.along) * scale
        copies = max(1, int(round(fill / item_fill)))
        e_fill = abs(copies * item_fill - fill) / max(fill, 0.1)
        e_other = abs(item_other - other) / max(other, 0.1)
        e_height = abs(item.height * scale - max(target_height, 0.05)) / max(target_height, 0.05)
        score = e_fill + 0.5 * e_other + e_height + 0.3 * abs(math.log(scale)) + 0.05 * copies
        if fixed_scale:
            # Unscaled, the height can only be matched by picking the object: it decides, so that
            # one feature's neighbours (a bowl's quarters) come out the same height.
            score += 10 * e_height
        if feature.kind == "rail":
            score = e_fill + 0.05 * copies + abs(item.height - max(feature.height, 0.05))
        if best is None or score < best.score:
            best = Choice(item, scale, copies, score)
    return best


def place(features: list[Feature], catalog: Catalog, fixed_scale: bool = False) -> tuple[list[Placement], list[str]]:
    placements: list[Placement] = []
    skipped: list[str] = []
    for index, feature in enumerate(features):
        if feature.kind == "bowl":
            skipped.append(f"#{index} bowl {feature.along:.1f} x {feature.across:.1f} m, "
                           f"{-feature.base:.1f} m deep: not converted (Build Kit cannot dig)")
            continue
        choice = choose(feature, catalog, fixed_scale)
        if choice is None:
            skipped.append(f"#{index} {feature.kind}: no catalog object for it")
            continue
        placements += _copies(index, feature, choice)
    return placements, skipped


def _copies(index: int, feature: Feature, choice: Choice) -> list[Placement]:
    item, scale, n = choice.item, choice.scale, choice.copies
    u = np.array([math.cos(feature.yaw), math.sin(feature.yaw)])
    v = np.array([-u[1], u[0]])
    linear = feature.kind in LINEAR
    axis = u if linear else v
    fill = feature.along if linear else feature.across
    step = fill / n
    result = []
    for k in range(n):
        offset = (k - (n - 1) / 2) * step
        if feature.kind == "rail" and feature.pitch:
            horizontal = offset * math.cos(feature.pitch)
            x, y = np.array(feature.centre) + axis * horizontal
            z = feature.height + offset * math.sin(feature.pitch) - item.height
        else:
            x, y = np.array(feature.centre) + axis * offset
            z = feature.base if feature.kind != "rail" else feature.height - item.height
        result.append(Placement(item, float(x), float(y), float(z), feature.yaw,
                                feature.pitch if feature.kind == "rail" else 0.0, scale, index))
    return result
