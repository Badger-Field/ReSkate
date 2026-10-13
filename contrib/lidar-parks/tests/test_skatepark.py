"""A mapped skatepark as its real shape (skatepark.py, `city --skatepark`), on a synthetic park:
a 30 x 20 m deck with a round bowl 2 m deep, mapped as a hole in the park like OpenStreetMap
maps them, two skaters standing in it and a palm over the deck.

With RESKATE_BPY_PYTHON set to a Python that has Blender's `bpy` module, the Studio converter
(studio_prep.py) is run on the result too."""
from __future__ import annotations

import json
import math
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest
import shapely

from reskate_lidar import citymesh, ept, geo, skatepark
from reskate_lidar.cli import main
from reskate_lidar.mapdata import MapItem
from test_city import FRAME, glb_document, overpass_answer, write_ept

DECK, DEPTH, RADIUS = 10.0, 2.0, 4.0
BOWL_CENTRE = np.array([-6.0, 0.0])
SKATERS = [(-6.0, 1.0), (-4.0, -1.5)]
PALM = (8.0, 5.0)
HERE = Path(__file__).resolve().parent.parent


def surface(x, y):
    r = np.hypot(x - BOWL_CENTRE[0], y - BOWL_CENTRE[1])
    # A bowl: flat bottom, then a transition steepening to the coping.
    inside = r < RADIUS
    t = np.clip(r / RADIUS, 0, 1)
    return np.where(inside, DECK - DEPTH * (1 - t ** 4), DECK)


def survey(seed: int = 0, density: float = 15.0, noise: float = 0.02) -> ept.Points:
    rng = np.random.default_rng(seed)
    n = int(50 * 40 * density)
    x, y = rng.uniform(-25, 25, n), rng.uniform(-20, 20, n)
    z = surface(x, y) + rng.normal(0, noise, n)
    classes = np.full(n, ept.GROUND, np.uint8)
    # The steep top of a bowl's wall is often left unclassified by the survey's ground filter.
    r = np.hypot(x - BOWL_CENTRE[0], y - BOWL_CENTRE[1])
    classes[(r > 0.8 * RADIUS) & (r < RADIUS)] = 1
    layered = np.zeros(n, bool)
    extra = []
    for sx, sy in SKATERS:  # a person: 0.5 m across, 1.7 m tall
        k = 40
        extra.append(np.column_stack([sx + rng.normal(0, 0.15, k), sy + rng.normal(0, 0.15, k),
                                      surface(np.full(k, sx), np.full(k, sy)) + rng.uniform(0.3, 1.7, k),
                                      np.ones(k), np.zeros(k)]))
    k = 300  # a palm's crown: leaves, several returns per pulse
    extra.append(np.column_stack([PALM[0] + rng.normal(0, 1.5, k), PALM[1] + rng.normal(0, 1.5, k),
                                  DECK + rng.uniform(8, 12, k), np.ones(k), np.ones(k)]))
    extra = np.vstack(extra)
    return ept.Points(np.vstack([np.column_stack([x, y, z]), extra[:, :3]]),
                      np.concatenate([classes, extra[:, 3].astype(np.uint8)]), "synthetic",
                      np.concatenate([layered, extra[:, 4] > 0]))


def park_item(with_hole: bool = True) -> MapItem:
    outline = shapely.box(-15, -10, 15, 10)
    holes = [shapely.Point(*BOWL_CENTRE).buffer(RADIUS, quad_segs=16).exterior.coords] if with_hole else []
    return MapItem(shapely.Polygon(outline.exterior.coords, holes),
                   {"leisure": "pitch", "sport": "skateboard", "surface": "concrete"}, "osm", "relation/1")


@pytest.fixture(scope="module")
def park():
    return skatepark.build([park_item()], survey())


def test_the_park_is_found_by_its_tags():
    plaza = MapItem(shapely.box(-40, -40, 40, 40), {"place": "square"}, "osm")
    found = skatepark.find_park([plaza, park_item()])
    assert found is not None and found.area == pytest.approx(30 * 20 - math.pi * RADIUS ** 2, rel=0.02)
    assert skatepark.find_park([plaza]) is None


def test_bowl_depth_and_coping(park):
    (bowl,) = park.bowls
    assert bowl.from_map
    assert park.deck == pytest.approx(DECK, abs=0.05)
    assert bowl.depth == pytest.approx(DEPTH, abs=0.2)
    assert bowl.density == pytest.approx(15.0, rel=0.25)
    (coping,) = park.model.grinds
    assert coping.closed and coping.what == "coping"
    assert np.allclose(coping.points[:, 2], DECK, atol=0.08)
    radii = np.hypot(*(coping.points[:, :2] - BOWL_CENTRE).T)
    assert np.allclose(radii, RADIUS, atol=0.1)


def test_people_and_trees_leave_no_trace(park):
    grid = park.model.ground
    for spot in SKATERS:
        assert grid.sample(np.array(spot))[0] == pytest.approx(surface(*spot), abs=0.1)
    assert grid.sample(np.array(PALM))[0] == pytest.approx(DECK, abs=0.1)
    assert park.removed_above >= 2 * 40


def test_the_wall_is_kept_where_the_survey_left_it_unclassified(park):
    near_top = BOWL_CENTRE + [0.9 * RADIUS, 0.0]
    assert park.model.ground.sample(near_top)[0] == pytest.approx(float(surface(*near_top)), abs=0.12)


def test_bowls_are_found_without_map_holes():
    found = skatepark.build([park_item(with_hole=False)], survey())
    (bowl,) = found.bowls
    assert not bowl.from_map
    # Grown out to where it is 0.25 m deep: 2(1 - (r/4)^4) = 0.25 at r = 3.87 m, by the coping.
    assert bowl.outline.area == pytest.approx(math.pi * 3.87 ** 2, rel=0.15)
    assert bowl.depth == pytest.approx(DEPTH, abs=0.25)
    radii = np.hypot(*(found.model.grinds[0].points[:, :2] - BOWL_CENTRE).T)
    assert np.median(radii) == pytest.approx(RADIUS, abs=0.3)


def test_a_hillside_is_not_a_bowl():
    """A park on a slope, without a bowl: falling ground towards one side is no depression."""
    points = survey()
    keep = np.hypot(points.xyz[:, 0] - BOWL_CENTRE[0], points.xyz[:, 1] - BOWL_CENTRE[1]) > RADIUS + 0.5
    xyz = points.xyz[keep].copy()
    xyz[:, 2] += -0.12 * xyz[:, 0]  # 12 % down to the east: 6 m across the area
    hill = ept.Points(xyz, points.classes[keep], "synthetic", points.layered[keep])
    found = skatepark.build([park_item(with_hole=False)], hill)
    assert found.bowls == []


def test_spawn_is_on_the_deck(park):
    spawn = shapely.Point(park.model.spawn)
    assert park.outline.contains(spawn)
    assert spawn.distance(park.bowls[0].outline) > 1.0


def test_mesh_for_studio(park, tmp_path):
    info = citymesh.write(park.model, tmp_path / "park.glb", tile=16)
    assert info["grinds"] == 1
    document = glb_document(tmp_path / "park.glb")
    nodes = {n["name"]: n for n in document["nodes"]}
    assert nodes["grind_0"]["extras"]["reskate_part"] == "coping"
    assert nodes["spawn"]["translation"][1] == pytest.approx(0.1, abs=0.05)  # the deck is y = 0
    import trimesh
    scene = trimesh.load(tmp_path / "park.glb")
    low = min(g.bounds[0][1] for g in scene.geometry.values() if hasattr(g, "faces"))
    assert low == pytest.approx(-DEPTH, abs=0.2)


def test_skatepark_command(tmp_path):
    write_ept(tmp_path / "ept", survey(), FRAME)
    answer = tmp_path / "overpass.json"
    # As Overpass returns a park with a bowl: a multipolygon relation, outer and inner.
    item = park_item()
    lonlat = geo.transform_geometry(item.geometry, FRAME.to_lonlat)
    ring = lambda coords: [{"lat": y, "lon": x} for x, y in coords]
    answer.write_text(json.dumps({"elements": [{
        "type": "relation", "id": 1, "tags": {**item.tags, "type": "multipolygon"},
        "members": [{"type": "way", "role": "outer", "geometry": ring(lonlat.exterior.coords)},
                    {"type": "way", "role": "inner", "geometry": ring(lonlat.interiors[0].coords)}]}]}))
    code = main(["city", f"--centre={FRAME.lat},{FRAME.lon}", "--size", "60", "--skatepark",
                 "--lidar", str(tmp_path / "ept"), "--sources", "osm", "--overpass", str(answer),
                 "--cache", str(tmp_path / "cache"), "--mesh", str(tmp_path / "park.glb"),
                 "--preview", str(tmp_path / "park.png"), "--report", str(tmp_path / "park.json")])
    assert code == 0
    report = json.loads((tmp_path / "park.json").read_text())
    (bowl,) = report["bowls"]
    assert bowl["from_map"] and bowl["depth"] == pytest.approx(DEPTH, abs=0.2)
    assert "OpenStreetMap" in " ".join(report["attribution"])
    assert (tmp_path / "park-collision.glb").exists() and (tmp_path / "park.png").exists()


@pytest.mark.skipif(not os.environ.get("RESKATE_BPY_PYTHON"), reason="set RESKATE_BPY_PYTHON to a Python with bpy")
def test_studio_prep_lays_out_the_blend(park, tmp_path):
    citymesh.write(park.model, tmp_path / "park.glb", tile=16)
    python = os.environ["RESKATE_BPY_PYTHON"]
    result = subprocess.run([python, str(HERE / "studio_prep.py"), str(tmp_path / "park.glb"),
                             str(tmp_path / "park.blend")], capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    check = tmp_path / "check.py"
    check.write_text(
        "import bpy, sys, json\n"
        "bpy.ops.wm.open_mainfile(filepath=sys.argv[1])\n"
        "c = {k.name: sorted((o.name, o.type) for o in k.objects) for k in bpy.data.collections}\n"
        "spawn = bpy.data.objects['spawn'].location\n"
        "grind = bpy.data.objects['grind_0']\n"
        "print('RESULT', json.dumps({'collections': c, 'spawn': list(spawn), 'grind': grind.get('sk8_grind_curve')}))\n")
    out = subprocess.run([python, str(check), str(tmp_path / "park.blend")], capture_output=True, text=True, timeout=600)
    line = next(l for l in out.stdout.splitlines() if l.startswith("RESULT "))
    found = json.loads(line[len("RESULT "):])
    assert ["grind_0", "CURVE"] in found["collections"]["Grind curves"]
    assert ["spawn", "EMPTY"] in found["collections"]["Markers"]
    assert all(kind == "MESH" for _, kind in found["collections"]["Map"])
    assert found["grind"]
    # Blender is Z up with Y north: the spawn keeps its east/north place.
    assert found["spawn"][0] == pytest.approx(park.model.spawn[0], abs=0.01)
    assert found["spawn"][1] == pytest.approx(park.model.spawn[1], abs=0.01)
