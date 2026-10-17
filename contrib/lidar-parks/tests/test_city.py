"""The city pipeline on a synthetic block with known contents (synthcity.py), offline: the LiDAR
comes from a small EPT written here, the map data from an Overpass answer and an Overture
GeoParquet written here.

    cd contrib/lidar-parks && python -m pytest tests"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
import shapely

from reskate_lidar import catalog, city, citymesh, ept, features, geo, mapdata, osm, overture, park, synthcity
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
    (bank,) = [s for s in model.spots if s.source == "ground" and s.what != "kerb"]
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


def glb_document(path) -> dict:
    import struct
    data = Path(path).read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + length])


def test_mesh_is_in_the_gltf_frame(model, tmp_path):
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
    # glTF is right-handed, +Y up, -Z north. The building is west (-x) and south (+z) of the
    # origin, and 12 m tall.
    building = visual.geometry["buildings"]
    lo, hi = building.bounds
    assert hi[0] < -30 and lo[2] > 0 and hi[2] > 15
    assert hi[1] - (model.buildings[0].base - model.origin_elevation) == pytest.approx(12.0, abs=0.6)
    assert building.is_volume


def test_mesh_carries_what_studio_reads(model, tmp_path):
    info = citymesh.write(model, tmp_path / "block.glb", tile=32)
    document = glb_document(tmp_path / "block.glb")
    nodes = {n["name"]: n for n in document["nodes"]}
    assert nodes["ground_0_0"]["extras"]["sk8_collision_mode"] == "triangle_mesh"
    assert nodes["buildings"]["extras"]["sk8_collision_mode"] == "triangle_mesh"
    # Grind curves: the wall's and the bench's two top edges, each rail's bar.
    grinds = [n for n in nodes.values() if (n.get("extras") or {}).get("sk8_grind_curve")]
    assert len(grinds) == info["grinds"] >= 2 + 2 + 2 + 2
    assert all(n["extras"]["sk8_collision_mode"] == "none" for n in grinds)
    assert {"metal", "wood", "concrete"} <= {n["extras"]["reskate_surface"] for n in grinds}
    # A spawn empty on the ground at the origin, in the scene.
    spawn = nodes["spawn"]
    assert "mesh" not in spawn and spawn["translation"][1] == pytest.approx(0.1, abs=0.05)
    assert document["nodes"].index(spawn) in document["scenes"][0]["nodes"]


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


def with_a_home():
    """The synthetic block plus a residential parcel along the square's east edge (x 30-40): a
    house, and a bench just inside the parcel (0.6 m from the square, which once let a spot in) and
    a low wall in its yard, all visible in the points."""
    items = synthcity.map_items() + [
        mapdata.MapItem(shapely.box(30, -30, 40, 28), {"landuse": "residential"}, "osm", "way/20"),
        # An L-shaped house, 8.6 m tall in the points.
        mapdata.MapItem(shapely.Polygon([(33, -25), (39, -25), (39, -15), (36, -15), (36, -19), (33, -19)]),
                        {"building": "house"}, "osm", "way/21"),
        mapdata.MapItem(shapely.Point(30.6, 5.0), {"amenity": "bench", "direction": "90"}, "osm", "node/22"),
        mapdata.MapItem(shapely.LineString([(33, 15), (39, 15)]), {"barrier": "wall", "height": "0.5"}, "osm", "way/23"),
    ]
    points = synthcity.survey()
    xy = points.xyz[:, :2]
    house = shapely.contains_xy(items[-3].geometry, xy[:, 0], xy[:, 1])
    points.xyz[house, 2] += 8.6
    points.classes[house] = ept.BUILDING
    bench = shapely.contains_xy(synthcity._rect((30.6, 5.0), math.pi / 2, 1.8, 0.5), xy[:, 0], xy[:, 1])
    points.xyz[bench, 2] += 0.45
    points.classes[bench] = 1
    return items, points


def test_no_spots_on_residential_parcels():
    items, points = with_a_home()
    model = city.build(synthcity.BOUNDS, items, points)
    for spot in model.spots:
        assert spot.feature.centre[0] < 30, (spot.what, spot.feature.centre)
    assert "1 spots on residential parcels left out" in model.notes
    # Without the parcel, the same bench is a spot: the rule is what keeps it out.
    open_items = [i for i in items if i.tags.get("landuse") != "residential"]
    assert any(s.what == "bench" and s.feature.centre[0] > 30 for s in city.build(synthcity.BOUNDS, open_items, points).spots)
    # The square's own spots are all still there.
    assert {s.what for s in model.spots} >= {"steps", "handrail", "bench", "wall"}


def test_homes_are_plain_blocks():
    items, points = with_a_home()
    model = city.build(synthcity.BOUNDS, items, points)
    (house,) = [b for b in model.buildings if b.home]
    # The L becomes its smallest rectangle, and 8.6 m becomes 8 m.
    assert house.footprint.area == pytest.approx(6 * 10, rel=0.01)
    assert len(house.footprint.exterior.coords) == 5
    assert house.height == 8.0 and house.height_from == "lidar"
    (office,) = [b for b in model.buildings if not b.home]
    assert office.height == pytest.approx(12.0, abs=0.5)  # not a home: measured as it is


def test_a_house_off_residential_land_is_still_a_home():
    item = mapdata.MapItem(shapely.box(0, 0, 5, 5), {"building": "detached"}, "osm")
    assert city.is_home(item, None)
    assert not city.is_home(mapdata.MapItem(shapely.box(0, 0, 5, 5), {"building": "office"}, "osm"), None)


def test_a_spot_mapped_by_both_sources_is_placed_once():
    """Overture repeats OpenStreetMap: the same steps, bench and wall from both are one spot each."""
    items = synthcity.map_items()
    repeated = [mapdata.MapItem(i.geometry, dict(i.tags), "overture", "o-" + i.id) for i in items
                if i.tags.get("highway") == "steps" or i.tags.get("amenity") == "bench" or i.tags.get("barrier")]
    model = city.build(synthcity.BOUNDS, items + repeated, synthcity.survey())
    for what in ("steps", "bench", "wall"):
        (spot,) = spots(model, what)
        assert spot.source == "osm"  # the repeat is dropped before measuring: decided once, from OSM
    assert len(spots(model, "handrail")) == 2
    # Spots that both sources found separately (slightly apart) are still merged.
    a = city.Spot(city.Feature("ledge", (0.0, 0.0), 0.0, 5.0, 0.4, 0.5), 10.0, 10.0, "osm", "wall")
    b = city.Spot(city.Feature("ledge", (0.3, 0.0), math.pi, 5.2, 0.4, 0.5), 10.0, 10.0, "overture", "wall")
    (merged,) = city.merge_sources([b, a])
    assert merged.source == "osm+overture"


def test_a_feature_both_sources_map_is_noted_once():
    line = shapely.LineString([(-20, 20), (-12, 20)])
    items = synthcity.map_items() + [mapdata.MapItem(line, {"highway": "steps"}, "osm", "way/30"),
                                     mapdata.MapItem(line, {"highway": "steps"}, "overture", "ovt-30")]
    model = city.build(synthcity.BOUNDS, items, synthcity.survey())
    flight_notes = [n for n in model.notes if "does not climb like a flight" in n]
    assert len(flight_notes) == 1 and "(osm)" in flight_notes[0]


def test_the_writer_never_writes_the_same_object_twice():
    cat = catalog.load_catalog(DEFAULT_CATALOG)
    one = catalog.Placement(cat.items[6], 1.0, 2.0, 0.0, 0.3, 0.0, 1.2, 0)
    twin = catalog.Placement(cat.items[6], 1.0, 2.0, 0.0, 0.3, 0.0, 1.2, 1)
    other = catalog.Placement(cat.items[6], 4.0, 2.0, 0.0, 0.3, 0.0, 1.2, 2)
    document = park.build_park([one, twin, other], "bam", park.Anchor())
    assert len(document["maps"]["bam"]) == 2


def test_a_slope_is_not_a_flight_of_steps():
    """Steps mapped where the ground only climbs a plaza's 2 %: left out, unless the map counts them."""
    line = shapely.LineString([(-20, 20), (-12, 20)])
    plain = mapdata.MapItem(line, {"highway": "steps"}, "osm", "way/30")
    model = city.build(synthcity.BOUNDS, synthcity.map_items() + [plain], synthcity.survey())
    assert not [s for s in spots(model, "steps") if s.feature.centre[1] > 10]
    assert any("does not climb like a flight" in n for n in model.notes)
    counted = mapdata.MapItem(line, {"highway": "steps", "step_count": "5"}, "osm", "way/31")
    model = city.build(synthcity.BOUNDS, synthcity.map_items() + [counted], synthcity.survey())
    (stairs,) = [s for s in spots(model, "steps") if s.feature.centre[1] > 10]
    assert stairs.feature.height == pytest.approx(0.8) and stairs.feature.along == pytest.approx(1.5)
    assert stairs.feature.steps == 5


def test_kerb_along_the_sidewalk(model):
    """The street is 15 cm below the sidewalk: its edge there is a kerb, standing on the road. (It
    breaks where the terrace's 1.2 m edge crosses the street.)"""
    kerbs = spots(model, "kerb")
    assert kerbs and sum(k.feature.along for k in kerbs) > 60  # most of the 80 m street
    for kerb in kerbs:
        f = kerb.feature
        assert f.kind == "ledge" and kerb.source == "ground"
        assert f.height == pytest.approx(synthcity.KERB, abs=0.04)
        assert f.centre[1] == pytest.approx(29.0, abs=0.5)  # the road's edge
        assert axis_error(f.yaw, 0.0) < 3
        assert kerb.ground == pytest.approx(
            float(synthcity.ground(np.array([f.centre[0]]), np.array([30.0]))[0]), abs=0.05)
    # Kerbs come after every other spot.
    assert all(s.what == "kerb" for s in model.spots[-len(kerbs):])


def test_no_kerb_where_the_road_meets_no_public_space():
    items = [i for i in synthcity.map_items() if i.tags.get("footway") != "sidewalk"]
    model = city.build(synthcity.BOUNDS, items, synthcity.survey())
    assert spots(model, "kerb") == []


def test_kerbs_are_capped():
    model = city.build(synthcity.BOUNDS, synthcity.map_items(), synthcity.survey(), city.Settings(kerbs=0))
    assert spots(model, "kerb") == []


def test_best_slice_finds_the_busy_strip():
    feature = lambda x, y: city.Spot(features.Feature("ledge", (x, y), 0.0, 2.0, 0.5, 0.4), 10.0, 10.0, "osm", "bench")
    kerb = lambda x, y: city.Spot(features.Feature("ledge", (x, y), 0.0, 3.0, 0.4, 0.15), 10.0, 10.0, "ground", "kerb")
    model = city.CityModel.__new__(city.CityModel)
    model.spots = [feature(x, 30.0) for x in (-40, 0, 40)] + [kerb(x, -30.0) for x in (-40, 0, 40)] + [feature(0, 0)]
    offset, counts = city.best_slice(model, 130, 20, 40)
    assert offset == pytest.approx(30.0) and counts[30.0] == 3
    assert counts[-30.0] == 3                                    # as many, but kerbs: the benches win
    assert [s.what for s in model.spots if city.in_slice(s, 130, 20, offset)] == ["bench"] * 3


def test_crop_to_a_slice(model, tmp_path):
    from reskate_lidar import levelexport
    rect = (-35.0, -15.0, 40.0, 0.0)                              # through the west building (x -38..-32)
    cut = city.crop(model, rect)
    assert cut is not model and len(cut.buildings) == 1 and model.buildings[0].footprint.area == pytest.approx(90)
    assert all(rect[0] <= s.feature.centre[0] <= rect[2] and rect[1] <= s.feature.centre[1] <= rect[3]
               for s in cut.spots)
    assert len(cut.spots) < len(model.spots)
    west = [b for b in cut.buildings if b.footprint.bounds[0] < -30]
    assert west and west[0].footprint.bounds == pytest.approx((-35, -15, -32, -5), abs=0.6)
    x, y = cut.ground.centres()
    assert x.min() <= rect[0] < x.min() + cut.ground.cell and x.max() - cut.ground.cell < rect[2] <= x.max()
    assert y.min() <= rect[1] and rect[3] <= y.max()
    assert np.array_equal(cut.ground.sample([(10.0, -7.0)]), model.ground.sample([(10.0, -7.0)]))
    manifest = levelexport.export(cut, tmp_path / "level", 50.0)
    assert not any(manifest["collision_checks"][k] for k in ("edges_same_direction_twice", "edges_shared_by_3_or_more"))
    edge = cut.ground.cell + 0.01                                  # the ground reaches the grid point past each side
    parts = set()
    for listed in manifest["tiles"]:
        tile = json.loads((tmp_path / "level" / listed["json"]).read_text())
        for obj in tile["objects"]:
            parts.add(obj["part"])
            if obj["part"] in ("ground", "buildings"):           # spots stay whole, if their middle is in
                v, _ = levelexport.read_object(tmp_path / "level", tile, obj)
                assert v[:, 0].min() >= rect[0] - edge and v[:, 0].max() <= rect[2] + edge   # x east
                assert v[:, 2].min() >= rect[1] - edge and v[:, 2].max() <= rect[3] + edge   # z north
    assert {"ground", "buildings"} <= parts
    spawn = manifest["spawn"]
    assert rect[0] <= spawn[0] <= rect[2] and rect[1] <= spawn[2] <= rect[3]


def test_level_tiles_for_the_map_builder(model, tmp_path):
    from reskate_lidar import levelexport
    manifest = levelexport.export(model, tmp_path / "level", 50.0, ["credit"], {"lat": FRAME.lat, "lon": FRAME.lon})
    assert manifest["format"] == "reskate-level-tiles" and manifest["tile_size"] == 50.0
    assert json.loads((tmp_path / "level" / "level.json").read_text())["tiles"] == manifest["tiles"]
    # Every triangle of the GLB's scenes is in exactly one tile.
    for role, collision in (("visual", False), ("collision", True)):
        scene, _ = citymesh.scene(model, collision=collision)
        expected = sum(len(g.faces) for g in scene.geometry.values() if hasattr(g, "faces") and len(g.faces))
        assert sum(t["triangles"][role] for t in manifest["tiles"]) == expected
    materials, ground_up = set(), []
    for listed in manifest["tiles"]:
        tile = json.loads((tmp_path / "level" / listed["json"]).read_text())
        size = (tmp_path / "level" / tile["bin"]).stat().st_size
        i, j = tile["tile"]
        for obj in tile["objects"]:
            assert obj["material"] in levelexport.MATERIALS
            materials.add((obj["part"], obj["material"]))
            v, f = levelexport.read_object(tmp_path / "level", tile, obj)
            assert obj["indices"]["offset"] + f.nbytes <= size and f.max() < len(v)
            centre = v[f].mean(axis=1)
            assert np.all(np.floor(centre[:, 0] / 50) == i) and np.all(np.floor(centre[:, 2] / 50) == j)
            if obj["part"] == "ground":
                normals = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
                ground_up.append(np.mean(normals[:, 1] > 0))
            if obj["part"] == "buildings" and obj["role"] == "collision":
                # The building west of the square: x -38..-32, z (north) -20..-5, 12 m tall.
                west = v[v[:, 0] < -30]
                assert west[:, 2].min() == pytest.approx(-20, abs=0.6) and west[:, 2].max() == pytest.approx(-5, abs=0.6)
                assert np.ptp(west[:, 1]) == pytest.approx(12, abs=1.5)
    assert min(ground_up) > 0.99                                  # wound for this frame: ground faces up
    # Collision: chunks of at most 256 triangles; where they meet, the vertices are the same bits.
    collision = [(listed, obj) for listed in manifest["tiles"]
                 for obj in json.loads((tmp_path / "level" / listed["json"]).read_text())["objects"]
                 if obj["role"] == "collision"]
    assert collision and max(o["indices"]["count"] // 3 for _, o in collision) <= levelexport.CHUNK_TRIANGLES
    assert any(o["chunks"] > 1 for _, o in collision)
    for listed, obj in collision:
        assert 0 <= obj["chunk"] < obj["chunks"] and "crease_degrees" not in obj
    every = []
    for listed, obj in collision:
        tile = json.loads((tmp_path / "level" / listed["json"]).read_text())
        every.append(levelexport.read_object(tmp_path / "level", tile, obj)[0])
    every = np.vstack(every)
    exact = len(np.unique(every.view(np.uint32).reshape(-1, 3), axis=0))
    near = len(np.unique(np.round(every.astype(np.float64), 3), axis=0))
    assert exact == near                                          # no shared corner differs in the last bits
    assert ("ground", "ground") in materials and ("bench", "wood") in materials
    visual = [o for listed in manifest["tiles"]
              for o in json.loads((tmp_path / "level" / listed["json"]).read_text())["objects"] if o["role"] == "visual"]
    assert {o["crease_degrees"] for o in visual if o["part"] == "ground"} == {45.0}
    assert all("chunk" not in o for o in visual)
    assert manifest["grinds"] and all(len(g["points"]) >= 2 for g in manifest["grinds"])
    assert manifest["spawn"] is not None and len(manifest["spawn"]) == 3


def test_city_writes_level_tiles(tmp_path):
    points = synthcity.survey(density=20)
    write_ept(tmp_path / "ept", points, FRAME)
    answer = tmp_path / "overpass.json"
    answer.write_text(json.dumps(overpass_answer(synthcity.map_items(), FRAME)))
    corners = FRAME.to_lonlat(np.array([synthcity.BOUNDS[:2], synthcity.BOUNDS[2:]]))
    bbox = ",".join(f"{v:.7f}" for v in corners.ravel())
    assert main(["city", f"--bbox={bbox}", "--lidar", str(tmp_path / "ept"), "--sources", "osm",
                 "--overpass", str(answer), "--cache", str(tmp_path / "cache"), "--level", str(tmp_path / "level")]) == 0
    manifest = json.loads((tmp_path / "level" / "level.json").read_text())
    assert len(manifest["tiles"]) == 4                            # 80 m round the origin: 2 x 2 tiles of 50 m
    assert "OpenStreetMap" in (tmp_path / "level" / "ATTRIBUTION.txt").read_text()


def test_collision_meshes_suit_half_edges(model, tmp_path):
    from reskate_lidar import levelexport
    # Every spot is closed and outward-facing; stairs are one solid, not boxes sharing edges.
    for spot in model.spots:
        if spot.feature.kind == "rail":
            continue                                              # a bar on posts: separate closed boxes
        mesh = citymesh.spot_mesh(spot, model.origin_elevation)
        assert mesh.is_watertight and mesh.volume > 0, spot.what
    checks = levelexport.export(model, tmp_path / "level")["collision_checks"]
    assert checks == {"merged_vertices": 0, "dropped_degenerate": 0, "dropped_repeated": 0,
                      "edges_same_direction_twice": 0, "edges_shared_by_3_or_more": 0}


def test_weld_merges_exact_duplicates_only():
    from reskate_lidar import levelexport
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1], [1, 0, 0], [1, 0, 1], [1 + 1e-6, 0, 0]], float)
    f = np.array([[0, 2, 1], [3, 2, 4], [0, 2, 1], [1, 3, 2]])
    welded_v, welded_f, fixed = levelexport.weld(v, f)
    assert fixed == {"merged_vertices": 1, "dropped_degenerate": 1, "dropped_repeated": 1}
    assert len(welded_v) == 5 and len(welded_f) == 2              # the 1e-6 neighbour stays apart


def test_surfaces_from_the_map(tmp_path):
    from reskate_lidar import levelexport
    assert city.surface_of({"material": "Granite"}, "bench") == "stone"
    assert city.surface_of({"surface": "paving_stones", "material": "wood"}, "steps") == "concrete"
    assert city.surface_of({"surface": "paving_stones", "material": "wood"}, "bench") == "wood"
    assert city.surface_of({"material": "recycled dreams"}, "bench") is None
    items = synthcity.map_items()
    for item in items:
        if item.tags.get("amenity") == "bench":
            item.tags["material"] = "metal"
    model = city.build(synthcity.BOUNDS, items, synthcity.survey())
    (bench,) = [s for s in model.spots if s.what == "bench"]
    assert bench.surface == "metal"
    manifest = levelexport.export(model, tmp_path / "level")
    parts = {(o["part"], o["material"]) for t in manifest["tiles"]
             for o in json.loads((tmp_path / "level" / t["json"]).read_text())["objects"]}
    assert ("bench", "metal") in parts and ("bench", "wood") not in parts


def test_city_reports_mapped_skateparks(tmp_path, capsys):
    write_ept(tmp_path / "ept", synthcity.survey(density=10), FRAME)
    items = synthcity.map_items() + [mapdata.MapItem(shapely.box(20, -35, 35, -20), {"leisure": "skatepark",
                                                                                     "name": "Corner Bowl"},
                                                     "osm", "way/99")]
    answer = tmp_path / "overpass.json"
    answer.write_text(json.dumps(overpass_answer(items, FRAME)))
    corners = FRAME.to_lonlat(np.array([synthcity.BOUNDS[:2], synthcity.BOUNDS[2:]]))
    bbox = ",".join(f"{v:.7f}" for v in corners.ravel())
    assert main(["city", f"--bbox={bbox}", "--lidar", str(tmp_path / "ept"), "--sources", "osm",
                 "--overpass", str(answer), "--cache", str(tmp_path / "cache"),
                 "--report", str(tmp_path / "report.json")]) == 0
    out = capsys.readouterr().out
    assert "1 skatepark mapped here" in out and "Corner Bowl" in out and "--skatepark --level" in out
    (park_found,) = json.loads((tmp_path / "report.json").read_text())["skateparks"]
    assert park_found["name"] == "Corner Bowl" and park_found["area"] == pytest.approx(225, rel=0.02)
    assert park_found["size"] == 40                              # 15 m across, 10 m round it
    lon, lat = FRAME.to_lonlat(np.array([[27.5, -27.5]]))[0]
    assert park_found["lat"] == pytest.approx(lat, abs=1e-6) and park_found["lon"] == pytest.approx(lon, abs=1e-6)
