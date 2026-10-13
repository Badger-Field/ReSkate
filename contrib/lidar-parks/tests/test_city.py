"""The city pipeline on a synthetic block with known contents (synthcity.py), offline: the LiDAR
comes from a small EPT written here, the map data from an Overpass answer and an Overture
GeoParquet written here.

    cd contrib/lidar-parks && python -m pytest tests"""
from __future__ import annotations

import json
import math

import numpy as np
import pytest
import shapely

from reskate_lidar import catalog, city, citymesh, ept, geo, mapdata, osm, overture, park, synthcity
from reskate_lidar.cli import DEFAULT_CATALOG, main

FRAME = geo.LocalFrame(37.7955, -122.3952)


def axis_error(a: float, b: float) -> float:
    d = (a - b) % math.pi
    return math.degrees(min(d, math.pi - d))


def direction_error(a: float, b: float) -> float:
    d = (a - b) % (2 * math.pi)
    return math.degrees(min(d, 2 * math.pi - d))


@pytest.fixture(scope="module")
def model():
    return city.build(synthcity.BOUNDS, synthcity.map_items(), synthcity.survey())


def spots(model, what):
    return [s for s in model.spots if s.what == what]


# ---------------------------------------------------------------- frames

def test_local_frame_round_trip():
    points = np.array([[0.0, 0.0], [120.0, -45.0], [-800.0, 950.0]])
    back = FRAME.from_lonlat(FRAME.to_lonlat(points))
    assert np.allclose(back, points, atol=1e-6)
    # A degree of latitude is about 111 km; of longitude, that times cos(latitude).
    one_north, one_east = FRAME.from_lonlat(np.array([[FRAME.lon, FRAME.lat + 0.01], [FRAME.lon + 0.01, FRAME.lat]]))
    assert one_north[1] == pytest.approx(1110.0, rel=0.01)
    assert one_east[0] == pytest.approx(1110.0 * math.cos(math.radians(FRAME.lat)), rel=0.01)


def test_mercator_round_trip():
    lonlat = np.array([[-122.3952, 37.7955], [4.9, 52.37]])
    assert np.allclose(geo.mercator_to_lonlat(geo.lonlat_to_mercator(lonlat)), lonlat, atol=1e-9)


def test_bbox_around_has_the_asked_size():
    box = geo.bbox_around(FRAME.lat, FRAME.lon, 160)
    xmin, ymin, xmax, ymax = geo.LocalFrame(*box.centre).bounds(box)
    assert xmax - xmin == pytest.approx(160, abs=0.1)
    assert ymax - ymin == pytest.approx(160, abs=0.1)


@pytest.mark.parametrize("text", ["1,2,3", "10,5,9,6", "-122.4,37.8,-122.5,37.9"])
def test_bad_bbox_is_a_clear_error(text):
    with pytest.raises(ValueError, match="west"):
        geo.parse_bbox(text)


def test_length_tags():
    assert mapdata.length_tag("12") == 12
    assert mapdata.length_tag("12.5 m") == 12.5
    assert mapdata.length_tag("45 cm") == pytest.approx(0.45)
    assert mapdata.length_tag("3'6\"") == pytest.approx(1.0668)
    assert mapdata.length_tag("tall") is None


def test_personal_tags_are_dropped():
    items = mapdata.localise(synthcity.map_items(), FRAME)
    building = next(i for i in items if "building" in i.tags)
    assert set(building.tags) == {"building"}


# ---------------------------------------------------------------- the model

def test_building_height_comes_from_the_points(model):
    (building,) = model.buildings
    assert building.height_from == "lidar"
    assert building.height == pytest.approx(12.0, abs=0.5)
    assert building.footprint.area == pytest.approx(6 * 15, rel=0.01)


def test_steps_are_found_uphill_with_their_rails(model):
    (stairs,) = spots(model, "steps")
    t = synthcity.TRUTH["stairs"]
    f = stairs.feature
    assert f.kind == "stairs"
    # The line was drawn top to bottom; stairs point uphill (+x).
    assert direction_error(f.yaw, t["yaw"]) < 5
    assert np.hypot(*(np.array(f.centre) - t["centre"])) < 0.5
    assert f.along == pytest.approx(t["run"], abs=0.6)
    assert f.across == pytest.approx(t["width"], abs=1.0)
    assert f.height == pytest.approx(t["height"], abs=0.15)
    rails = spots(model, "handrail")
    assert len(rails) == 2
    for r in rails:
        assert math.degrees(r.feature.pitch) == pytest.approx(math.degrees(math.atan2(1.2, 3.0)), abs=5)
        assert abs(abs(r.feature.centre[1]) - (f.across / 2 - 0.15)) < 0.05


def test_bench_is_oriented_from_the_points(model):
    (bench,) = spots(model, "bench")
    t = synthcity.TRUTH["bench"]
    assert bench.feature.kind == "ledge"
    assert axis_error(bench.feature.yaw, t["yaw"]) < 8
    assert bench.feature.along == pytest.approx(t["along"], abs=0.3)
    assert bench.feature.height == pytest.approx(t["height"], abs=0.07)


def test_tagged_wall_is_measured(model):
    (wall,) = spots(model, "wall")
    assert wall.feature.kind == "ledge"
    assert wall.feature.height == pytest.approx(synthcity.TRUTH["wall"]["height"], abs=0.07)
    assert wall.feature.along == pytest.approx(10.0, abs=0.1)


def test_untagged_ledge_is_found_in_the_points(model):
    found = [s for s in model.spots if s.source == "lidar"]
    t = synthcity.TRUTH["ledge"]
    assert len(found) == 1, [(s.what, s.feature.centre) for s in found]
    f = found[0].feature
    assert np.hypot(*(np.array(f.centre) - t["centre"])) < 0.5
    assert f.height == pytest.approx(t["height"], abs=0.07)
    assert f.along == pytest.approx(t["along"], abs=0.6)


def test_bank_is_found_in_the_ground(model):
    (bank,) = [s for s in model.spots if s.source == "ground"]
    t = synthcity.TRUTH["bank"]
    assert np.hypot(*(np.array(bank.feature.centre) - t["centre"])) < 1.0
    assert direction_error(bank.feature.yaw, t["yaw"]) < 10
    assert bank.feature.height == pytest.approx(t["height"], abs=0.15)


@pytest.mark.parametrize("thing", ["car", "hedge", "planter"])
def test_nothing_from_cars_hedges_or_grass(model, thing):
    where = np.array(synthcity.TRUTH[thing])
    near = [s for s in model.spots if np.hypot(*(np.array(s.feature.centre) - where)) < 3]
    assert not near, [(s.what, s.feature.centre) for s in near]


def test_no_spots_outside_public_space(model):
    for s in model.spots:
        assert model.public.buffer(1.0).contains(shapely.Point(s.feature.centre)), s.what


def test_private_ground_is_smoothed():
    """Outside public space, roads and buildings, the ground keeps only its broad shape."""
    points = synthcity.survey()
    rng = np.random.default_rng(1)
    # A 1 m deep, 2 m wide pit in a yard (outside the square, x > 30).
    pit = (np.abs(points.xyz[:, 0] - 35) < 1) & (np.abs(points.xyz[:, 1]) < 1) & (points.classes == ept.GROUND)
    points.xyz[pit, 2] -= 1.0 + rng.normal(0, 0.01, pit.sum())
    sharp = city.build(synthcity.BOUNDS, synthcity.map_items(), points, city.Settings(private_blur=0))
    smooth = city.build(synthcity.BOUNDS, synthcity.map_items(), points)
    at = np.array([[35.0, 0.0]])
    assert sharp.ground.sample(at)[0] < smooth.ground.sample(at)[0] - 0.5


def test_no_lidar_still_builds():
    model = city.build(synthcity.BOUNDS, synthcity.map_items(), None)
    assert not model.ground_measured
    assert model.buildings[0].height_from == "default"
    assert spots(model, "wall") == []  # an untagged wall height is not guessed
    assert spots(model, "bench") == []  # nor a bench's direction


# ---------------------------------------------------------------- outputs

def test_park_keeps_to_the_budget(model):
    cat = catalog.load_catalog(DEFAULT_CATALOG)
    chosen, placements, skipped = city.park_features(model, 256, cat)
    assert placements and len(chosen) == len(model.spots)
    small_chosen, small, small_skipped = city.park_features(model, 6, cat)
    assert 0 < len(small) <= 6 and small_skipped
    # Stairs and their rails come first.
    assert {small_chosen[0].kind} <= {"stairs", "rail"}
    document = park.build_park(placements, "bam", park.Anchor())
    park.validate_park(document)


def test_keep_elevation_lifts_the_terrace(model):
    cat = catalog.load_catalog(DEFAULT_CATALOG)
    _, flat, _ = city.park_features(model, 256, cat)
    _, real, _ = city.park_features(model, 256, cat, keep_elevation=True)
    lifts = {round(b.z - a.z, 3) for a, b in zip(flat, real)}
    assert len(lifts) > 1  # spots on different ground stand at different heights


def test_mesh_is_in_the_game_frame(model, tmp_path):
    import trimesh
    info = citymesh.write(model, tmp_path / "block.glb", tile=32)
    assert (tmp_path / "block.glb").exists() and (tmp_path / "block-collision.glb").exists()
    assert info["collision_triangles"] < info["visual_triangles"]
    visual = trimesh.load(tmp_path / "block.glb")
    names = set(visual.geometry)
    assert {"ground_0_0", "buildings"} <= names
    assert len([n for n in names if n.startswith("ground_")]) == 9  # 80 m in 32 m tiles: 3 x 3
    ground = visual.geometry["ground_1_1"]
    assert np.mean(ground.face_normals[:, 1]) > 0.9  # faces up (+y)
    # The building is west (game -x) and south (game -z) of the origin, and 12 m tall.
    building = visual.geometry["buildings"]
    lo, hi = building.bounds
    assert hi[0] < -30 and lo[2] < -15 and hi[2] < 0
    assert hi[1] - (model.buildings[0].base - model.origin_elevation) == pytest.approx(12.0, abs=0.6)
    assert building.is_volume


def test_spot_meshes_sit_on_their_ground(model):
    for spot in model.spots:
        mesh = citymesh.spot_mesh(spot, model.origin_elevation)
        top = mesh.bounds[1][1] + model.origin_elevation
        assert top == pytest.approx(spot.ground + spot.feature.height + (
            0.0 if spot.feature.kind != "rail" else spot.feature.along / 2 * math.sin(spot.feature.pitch)), abs=0.1), spot.what


# ---------------------------------------------------------------- sources

def write_ept(folder, points: ept.Points, frame: geo.LocalFrame) -> None:
    """A small EPT in web mercator: a root node, four children, and one child's hierarchy in its
    own file, as large surveys split theirs."""
    import laspy
    merc = geo.lonlat_to_mercator(frame.to_lonlat(points.xyz[:, :2]))
    xyz = np.column_stack([merc, points.xyz[:, 2]])
    lo = xyz.min(axis=0) - 1
    size = float(np.ptp(xyz, axis=0).max() + 2)
    bounds = [*lo, *(lo + size)]
    (folder / "ept-data").mkdir(parents=True)
    (folder / "ept-hierarchy").mkdir()
    rng = np.random.default_rng(0)
    root = rng.random(len(xyz)) < 0.1
    quadrant = ((xyz[:, 0] - lo[0]) >= size / 2).astype(int), ((xyz[:, 1] - lo[1]) >= size / 2).astype(int)
    nodes = {"0-0-0-0": root}
    for ix in (0, 1):
        for iy in (0, 1):
            nodes[f"1-{ix}-{iy}-0"] = ~root & (quadrant[0] == ix) & (quadrant[1] == iy)
    hierarchy = {}
    for key, chosen in nodes.items():
        header = laspy.LasHeader(point_format=6, version="1.4")
        header.scales = np.array([0.01, 0.01, 0.01])
        header.offsets = lo
        las = laspy.LasData(header)
        las.x, las.y, las.z = xyz[chosen, 0], xyz[chosen, 1], xyz[chosen, 2]
        las.classification = points.classes[chosen]
        las.return_number = np.ones(chosen.sum(), np.uint8)
        las.number_of_returns = np.where(points.layered[chosen], 2, 1).astype(np.uint8)
        las.write(folder / "ept-data" / f"{key}.laz")
        hierarchy[key] = int(chosen.sum())
    split = "1-1-1-0"
    (folder / "ept-hierarchy" / f"{split}.json").write_text(json.dumps({split: hierarchy[split]}))
    hierarchy[split] = -1
    (folder / "ept-hierarchy" / "0-0-0-0.json").write_text(json.dumps(hierarchy))
    (folder / "ept.json").write_text(json.dumps({
        "bounds": bounds, "boundsConforming": bounds, "dataType": "laszip", "hierarchyType": "json",
        "points": len(xyz), "span": 128, "srs": {"authority": "EPSG", "horizontal": "3857"}, "version": "1.0.0",
        "schema": []}))


def overpass_answer(items: list[mapdata.MapItem], frame: geo.LocalFrame) -> dict:
    elements = []
    for n, item in enumerate(items, start=1):
        g = geo.transform_geometry(item.geometry, frame.to_lonlat)
        if g.geom_type == "Point":
            elements.append({"type": "node", "id": n, "lat": g.y, "lon": g.x, "tags": item.tags})
        else:
            coords = g.exterior.coords if g.geom_type == "Polygon" else g.coords
            elements.append({"type": "way", "id": n, "tags": item.tags,
                             "geometry": [{"lat": y, "lon": x} for x, y in coords]})
    return {"elements": elements}


def test_ept_reader_reads_the_area(tmp_path):
    points = synthcity.survey(density=20)
    write_ept(tmp_path / "ept", points, FRAME)
    reader = ept.EptReader(str(tmp_path / "ept" / "ept.json"), tmp_path / "cache")
    box = geo.BBox(*FRAME.to_lonlat(np.array([-20.0, -20.0])), *FRAME.to_lonlat(np.array([20.0, 20.0])))
    read = reader.read(box, FRAME)
    inside = np.all(np.abs(points.xyz[:, :2]) <= 20, axis=1)
    assert len(read.xyz) == pytest.approx(inside.sum(), rel=0.01)
    assert np.abs(read.xyz[:, :2]).max() <= 20.05
    assert read.layered.sum() == pytest.approx((points.layered & inside).sum(), rel=0.05)
    # Positions survive mercator and back to a centimetre.
    tree = __import__("scipy.spatial", fromlist=["cKDTree"]).cKDTree(points.xyz)
    distance, _ = tree.query(read.xyz[:500])
    assert distance.max() < 0.02


def test_overpass_answer_is_parsed():
    items = osm.parse(overpass_answer(synthcity.map_items(), FRAME))
    kinds = {i.geometry.geom_type for i in items}
    assert kinds == {"Point", "LineString", "Polygon"}
    steps = next(i for i in items if i.tags.get("highway") == "steps")
    assert steps.geometry.geom_type == "LineString"
    square = next(i for i in items if i.tags.get("place") == "square")
    assert square.geometry.geom_type == "Polygon"


def test_overture_rows_become_tags(tmp_path):
    import pyarrow as pa
    import pyarrow.fs as fs
    import pyarrow.parquet as pq
    root = tmp_path / "release"
    lonlat = lambda g: shapely.to_wkb(geo.transform_geometry(g, FRAME.to_lonlat))
    near, far = shapely.LineString([(0, 0), (4, 0)]), shapely.LineString([(5000, 0), (5004, 0)])
    rows = {"id": ["a", "b"], "geometry": [lonlat(near), lonlat(far)], "subtype": ["road", "road"],
            "class": ["steps", "footway"], "subclass": [None, "sidewalk"]}
    boxes = [geo.transform_geometry(g, FRAME.to_lonlat).bounds for g in (near, far)]
    rows["bbox"] = [{"xmin": b[0], "ymin": b[1], "xmax": b[2], "ymax": b[3]} for b in boxes]
    folder = root / "theme=transportation" / "type=segment"
    folder.mkdir(parents=True)
    pq.write_table(pa.Table.from_pydict(rows), folder / "part-0.parquet")
    box = geo.bbox_around(FRAME.lat, FRAME.lon, 100)
    found = overture.read_layer("segment", box, tmp_path / "cache", filesystem=fs.LocalFileSystem(), root=str(root))
    assert [r["id"] for r in found] == ["a"]
    (item,) = overture.to_items("segment", found)
    assert item.tags["highway"] == "steps" and item.source == "overture"


def test_city_command_end_to_end(tmp_path):
    points = synthcity.survey()
    write_ept(tmp_path / "ept", points, FRAME)
    answer = tmp_path / "overpass.json"
    answer.write_text(json.dumps(overpass_answer(synthcity.map_items(), FRAME)))
    corners = FRAME.to_lonlat(np.array([synthcity.BOUNDS[:2], synthcity.BOUNDS[2:]]))
    bbox = ",".join(f"{v:.7f}" for v in corners.ravel())
    code = main(["city", f"--bbox={bbox}", "--lidar", str(tmp_path / "ept"), "--sources", "osm",
                 "--overpass", str(answer), "--cache", str(tmp_path / "cache"), "--mods", str(tmp_path / "Mods"),
                 "--name", "Synthetic Square", "--author", "tests", "--mesh", str(tmp_path / "square.glb"),
                 "--preview", str(tmp_path / "square.png"), "--report", str(tmp_path / "square.json")])
    assert code == 0
    mod = tmp_path / "Mods" / "Synthetic Square"
    document = json.loads((mod / "parks" / "bam.park.json").read_text())
    park.validate_park(document)
    readme = (mod / "README.md").read_text()
    assert "OpenStreetMap contributors" in readme and "ODbL" in readme
    manifest = json.loads((mod / "manifest.json").read_text())
    assert "OpenStreetMap" in manifest["description"]
    report = json.loads((tmp_path / "square.json").read_text())
    assert {s["what"] for s in report["spots"]} >= {"steps", "handrail", "bench", "wall"}
    assert (tmp_path / "square-collision.glb").exists() and (tmp_path / "square.png").exists()
    assert "OpenStreetMap" in (tmp_path / "square.txt").read_text()


def test_city_refuses_huge_areas(tmp_path, capsys):
    code = main(["city", "--centre", "37.79,-122.39", "--size", "5000", "--lidar", "none", "--cache", str(tmp_path)])
    assert code == 2
    assert "km²" in capsys.readouterr().err
