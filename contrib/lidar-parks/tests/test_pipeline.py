"""End-to-end checks on a synthetic park with known obstacles, and of the park file against the
rules ReSkate applies when it loads one.

    cd contrib/lidar-parks && python -m pytest tests

With RESKATE_PARK_CHECK set to a park_check binary (tests/build_park_check.sh), the written mod is
also read by the dedicated server's own park loader."""
from __future__ import annotations

import json
import math
import os
import subprocess

import numpy as np
import pytest
import trimesh

from reskate_lidar import align, catalog, features, heightmap, park, synth
from reskate_lidar.cli import DEFAULT_CATALOG, main

LINEAR = {"rail", "ledge", "manual_pad", "box"}


def run_detection(y_up: bool, yaw_degrees: float = 23.0, seed: int = 0):
    points, truth, transform = synth.scan(seed=seed, yaw_degrees=yaw_degrees, y_up=y_up)
    alignment = align.level(points, "auto", seed=seed)
    levelled = alignment.apply(points)
    hm = heightmap.build(levelled)
    detection = features.detect(hm)
    return detection, truth, transform, alignment


def truth_in_levelled(t: synth.Truth, transform, alignment) -> tuple[np.ndarray, float]:
    """A truth's centre and yaw carried through the scan's transform and the levelling."""
    centre = np.array([[t.centre[0], t.centre[1], 0.0]])
    ahead = centre + [[math.cos(t.yaw), math.sin(t.yaw), 0.0]]
    a, b = alignment.apply(trimesh.transform_points(np.vstack([centre, ahead]), transform))
    return a[:2], math.atan2(b[1] - a[1], b[0] - a[0])


def angle_error(a: float, b: float, undirected: bool) -> float:
    period = math.pi if undirected else 2 * math.pi
    d = (a - b) % period
    return math.degrees(min(d, period - d))


@pytest.fixture(scope="module", params=[True, False], ids=["y-up", "z-up"])
def detected(request):
    return run_detection(request.param)


def test_every_obstacle_is_found_in_place(detected):
    detection, truth, transform, alignment = detected
    for t in truth:
        centre, yaw = truth_in_levelled(t, transform, alignment)
        matches = [f for f in detection.features if f.kind == t.kind
                   and np.hypot(*(np.array(f.centre) - centre)) < 0.5]
        assert matches, f"{t.kind} at {centre} not found in {[(f.kind, f.centre) for f in detection.features]}"
        f = matches[0]
        assert angle_error(f.yaw, yaw, t.kind in LINEAR) < 5, f"{t.kind} yaw"
        assert abs(f.height - t.height) < 0.12, f"{t.kind} height {f.height} vs {t.height}"
        if t.kind in LINEAR:
            assert abs(f.along - t.along) < 0.35, f"{t.kind} length {f.along} vs {t.along}"


def test_nothing_invented(detected):
    detection, truth, _, _ = detected
    # The truth plus the stairs' top landing, which is a box of its own.
    assert len(detection.features) == len(truth) + 1
    assert sorted(f.kind for f in detection.features).count("box") == 1


def test_ground_is_levelled(detected):
    _, _, _, alignment = detected
    assert alignment.ground_inliers > 0.8
    assert alignment.tilt_degrees == pytest.approx(2.0, abs=0.3)


@pytest.mark.parametrize("seed", [0, 1])
def test_phone_grade_noise(seed):
    # About what a phone LiDAR app's point export carries: 1.5 cm noise, 400 points per m2.
    points, truth, transform = synth.scan(seed=seed, noise=0.015, density=400, yaw_degrees=17 + 40 * seed)
    alignment = align.level(points, "auto", seed=seed)
    detection = features.detect(heightmap.build(alignment.apply(points)))
    for t in truth:
        centre, _ = truth_in_levelled(t, transform, alignment)
        assert any(f.kind == t.kind and np.hypot(*(np.array(f.centre) - centre)) < 0.5 for f in detection.features), t.kind
    assert len(detection.features) == len(truth) + 1


def test_stairs_are_counted():
    detection, *_ = run_detection(True)
    (stairs,) = [f for f in detection.features if f.kind == "stairs"]
    assert stairs.steps == 5


def test_bowl_is_reported_not_placed():
    hm = heightmap.HeightMap(np.zeros((200, 200), np.float32), np.ones((200, 200), bool), np.zeros(2), 0.05)
    hm.heights[50:150, 60:140] = -1.2
    detection = features.detect(hm)
    assert [f.kind for f in detection.features] == ["bowl"]
    placements, skipped = catalog.place(detection.features, catalog.load_catalog(DEFAULT_CATALOG))
    assert placements == [] and "bowl" in skipped[0]


# ---------------------------------------------------------------- placing and the park file

def test_long_ledge_is_tiled():
    cat = catalog.load_catalog(DEFAULT_CATALOG)
    ledge = features.Feature("ledge", (0.0, 0.0), 0.3, 13.5, 0.5, 0.4)
    placements, _ = catalog.place([ledge], cat)
    assert len(placements) >= 3
    xs = [p.x for p in placements]
    span = max(xs) - min(xs) + placements[0].item.along * placements[0].scale * math.cos(0.3)
    assert span == pytest.approx(13.5 * math.cos(0.3), rel=0.15)


def test_game_frame():
    item = catalog.CatalogItem("own_bk_test", ["ledge"], 1.0, 1.0, 1.0)
    anchor = park.Anchor(np.array([100.0, 50.0, -20.0]), math.radians(90))
    # One metre along the scan's +x, facing +y.
    p = catalog.Placement(item, 1.0, 0.0, 0.0, math.radians(90), 0.0, 1.0, 0)
    position, rotation = park.to_game(p, anchor)
    # The anchor's facing (+x turned 90 degrees about +y) is where the scan's +x goes.
    facing = park.q_rotate(park.q_axis((0, 1, 0), anchor.yaw), (1.0, 0.0, 0.0))
    assert np.allclose(position, anchor.position + facing, atol=1e-3)
    # The object's long axis follows the scan's +y, which is the game's +z before the anchor turn.
    scan_y = park.q_rotate(park.q_axis((0, 1, 0), anchor.yaw), (0.0, 0.0, 1.0))
    assert np.allclose(park.q_rotate(rotation, (1.0, 0.0, 0.0)), scan_y, atol=1e-4)
    # Height is the game's +y.
    up = catalog.Placement(item, 0.0, 0.0, 2.0, 0.0, 0.0, 1.0, 0)
    assert park.to_game(up, anchor)[0][1] == pytest.approx(52.0)


def test_mirror_flips_across():
    item = catalog.CatalogItem("own_bk_test", ["ledge"], 1.0, 1.0, 1.0)
    p = catalog.Placement(item, 0.0, 3.0, 0.0, 0.0, 0.0, 1.0, 0)
    assert park.to_game(p, park.Anchor())[0][2] == pytest.approx(3.0)
    assert park.to_game(p, park.Anchor(), mirror=True)[0][2] == pytest.approx(-3.0)


def test_pitched_rail_rises_along_its_length():
    item = catalog.CatalogItem("own_bk_test", ["rail"], 1.0, 0.1, 0.3)
    p = catalog.Placement(item, 0.0, 0.0, 0.0, 0.0, math.radians(30), 1.0, 0)
    _, rotation = park.to_game(p, park.Anchor())
    forward = park.q_rotate(rotation, (1.0, 0.0, 0.0))
    assert forward[1] == pytest.approx(0.5, abs=1e-4)


def test_anchor_and_merge(tmp_path):
    anchor_file = tmp_path / "anchor.park.json"
    anchor_file.write_text(json.dumps({
        "format": "reskate-park", "schema_version": 1, "revision": 3,
        "maps": {"bam": [
            {"id": 7, "item": "own_bkprops_generic_measuringstick_00001", "position": [10, 20, 30],
             "rotation": [0, math.sin(math.radians(45) / 2), 0, math.cos(math.radians(45) / 2)]},
            {"id": 9, "item": "own_bkpads_generic_manualpadsmall_00001", "position": [0, 20, 0], "rotation": [0, 0, 0, 1]},
        ]},
        "object_scales": {"bam": {"9": 1.5, "7": 2.0}},
    }))
    cat = catalog.load_catalog(DEFAULT_CATALOG)
    anchor = park.read_anchor(anchor_file, cat.marker)
    assert anchor.map == "bam" and anchor.yaw == pytest.approx(math.radians(45))
    ledge = catalog.Placement(cat.items[6], 0.0, 0.0, 0.0, 0.0, 0.0, 1.2, 0)
    document = park.build_park([ledge], "bam", anchor, merge=True)
    objects = document["maps"]["bam"]
    assert [o["id"] for o in objects] == [9, 10]
    assert objects[1]["position"] == pytest.approx([10, 20, 30])
    assert document["object_scales"]["bam"] == {"9": 1.5, "10": 1.2}


def test_park_rules():
    item = catalog.CatalogItem("own_bk_test", ["ledge"], 1.0, 1.0, 1.0)
    good = park.build_park([catalog.Placement(item, 0, 0, 0, 0.2, 0, 1.0, 0)], "bam", park.Anchor())
    park.validate_park(good)
    with pytest.raises(ValueError):
        park.build_park([], "San Vansterdam", park.Anchor())
    bad = json.loads(park.encode(good))
    bad["maps"]["bam"][0]["rotation"] = [0, 0, 0, 2]
    with pytest.raises(ValueError):
        park.validate_park(bad)
    too_many = [catalog.Placement(item, i * 0.01, 0, 0, 0, 0, 1.0, 0) for i in range(park.MAX_OBJECTS_PER_MAP + 1)]
    with pytest.raises(ValueError):
        park.build_park(too_many, "bam", park.Anchor())


def test_catalog_items_are_build_kit():
    cat = catalog.load_catalog(DEFAULT_CATALOG)
    assert cat.items and all(i.item.startswith("own_bk") for i in cat.items)
    for kind in ("rail", "ledge", "manual_pad", "box", "stairs", "kicker", "bank", "quarter"):
        assert cat.for_kind(kind), kind


# ---------------------------------------------------------------- command line

def test_convert_writes_a_mod(tmp_path):
    scan = tmp_path / "park.ply"
    assert main(["synth", str(scan)]) == 0
    mods = tmp_path / "Mods"
    preview = tmp_path / "preview.png"
    assert main(["convert", str(scan), "--mods", str(mods), "--name", "Test Park", "--author", "me",
                 "--preview", str(preview), "--report", str(tmp_path / "report.json")]) == 0
    manifest = json.loads((mods / "Test Park" / "manifest.json").read_text())
    assert manifest == {"name": "Test Park", "author": "me", "version_number": "1.0.0",
                        "description": manifest["description"]}
    document = json.loads((mods / "Test Park" / "parks" / "bam.park.json").read_text())
    park.validate_park(document)
    assert len(document["maps"]["bam"]) >= 7
    assert preview.stat().st_size > 1000

    checker = os.environ.get("RESKATE_PARK_CHECK")
    if checker:
        result = subprocess.run([checker, str(mods), "Test Park"], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"objects {len(document['maps']['bam'])} skipped 0" in result.stdout


def test_calibrate_writes_a_row(tmp_path):
    anchor = tmp_path / "anchor.park.json"
    anchor.write_text(json.dumps({"format": "reskate-park", "schema_version": 1, "revision": 0, "maps": {"bam": [
        {"id": 1, "item": "own_bkprops_generic_measuringstick_00001", "position": [0, 0, 0], "rotation": [0, 0, 0, 1]}]}}))
    assert main(["calibrate", "--anchor", str(anchor), "--mods", str(tmp_path / "Mods")]) == 0
    document = json.loads((tmp_path / "Mods" / "LiDAR Calibration" / "parks" / "bam.park.json").read_text())
    cat = catalog.load_catalog(DEFAULT_CATALOG)
    assert len(document["maps"]["bam"]) == 3 * len(cat.items)


def test_mesh_export(tmp_path):
    scan = tmp_path / "park.ply"
    assert main(["synth", str(scan)]) == 0
    out = tmp_path / "park.glb"
    assert main(["mesh", str(scan), str(out)]) == 0
    mesh = trimesh.load(out, force="mesh")
    low, high = mesh.bounds
    assert low[1] == pytest.approx(0.0, abs=0.05)          # ground at y = 0
    assert high[1] == pytest.approx(1.5, abs=0.1)          # the quarter pipe is the tallest
    assert np.ptp(mesh.bounds[:, [0, 2]], axis=0).min() > 30
    assert np.mean(mesh.face_normals[:, 1]) > 0.8          # facing up


@pytest.mark.parametrize("folder", ["Park ", " Park", ".hidden", "Park\n", "a/b", ""])
def test_mod_folder_names_the_game_refuses(tmp_path, folder):
    item = catalog.CatalogItem("own_bk_test", ["ledge"], 1.0, 1.0, 1.0)
    document = park.build_park([catalog.Placement(item, 0, 0, 0, 0, 0, 1.0, 0)], "bam", park.Anchor())
    with pytest.raises(ValueError):
        park.write_mod(tmp_path, folder, document, "x", "", "1", "")


@pytest.mark.parametrize("up", ["w", "--z", "zz", "+-y"])
def test_bad_up_axis_is_a_clear_error(tmp_path, up):
    with pytest.raises(ValueError):
        align.level(np.zeros((10, 3)), up)
    scan = tmp_path / "flat.xyz"
    grid = np.stack(np.meshgrid(np.arange(50.0), np.arange(50.0)), -1).reshape(-1, 2) * 0.1
    np.savetxt(scan, np.column_stack([grid, np.zeros(len(grid))]))
    assert main(["mesh", str(scan), str(tmp_path / "out.glb"), f"--up={up}"]) == 2


def test_catalog_check_against_the_games_names(tmp_path, capsys):
    from reskate_lidar import catalog as catalog_module
    cat = catalog_module.load_catalog(DEFAULT_CATALOG)
    names = tmp_path / "names.txt"
    names.write_text("\n".join([i.item for i in cat.items] + [cat.marker]) + "\n", encoding="utf-8")
    assert main(["catalog-check", str(names)]) == 0
    names.write_text("\n".join(i.item for i in cat.items[1:]) + "\n", encoding="utf-8")
    assert main(["catalog-check", str(names)]) == 1
    out = capsys.readouterr().out
    assert f"not in the game: {cat.items[0].item}" in out and f"not in the game: {cat.marker}" in out
