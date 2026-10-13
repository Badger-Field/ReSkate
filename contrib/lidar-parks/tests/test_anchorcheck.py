"""The anchor check: probes over a park's footprint, and the game's ground heights read back."""
import json

import numpy as np
import pytest

from reskate_lidar import anchorcheck, cli


def write_park(path, positions, map_name="bam"):
    objects = [{"id": i + 1, "item": "own_bk_box", "position": list(p), "rotation": [0, 0, 0, 1]}
               for i, p in enumerate(positions)]
    path.write_text(json.dumps({"schema": 1, "maps": {map_name: objects}}), encoding="utf-8")
    return path


def landed(x, ground, z):
    return f"[info] Trainer: landing on the surface at {x:.1f}, {ground + anchorcheck.STAND_HEIGHT:.1f}, {z:.1f}."


@pytest.fixture
def park(tmp_path):
    # Objects over 40 x 20 m around (600, 1000), standing at 200.
    return write_park(tmp_path / "bam.park.json", [(580, 200, 990), (620, 200, 990), (600, 200, 1010), (600, 201, 1000)])


def test_read_park(park):
    footprint = anchorcheck.read_park(park)
    assert footprint.map == "bam" and len(footprint.items) == 4
    assert footprint.base == pytest.approx(200.0)
    assert footprint.xz[0] == pytest.approx([580, 990])


def test_probes_cover_the_footprint_within_the_limit(park):
    footprint = anchorcheck.read_park(park)
    points = anchorcheck.probe_points(footprint, spacing=10, margin=3)
    assert points[0] == pytest.approx([600, 1000])               # the centre first
    assert points[:, 0].min() == pytest.approx(577) and points[:, 0].max() <= 623
    assert points[:, 1].min() == pytest.approx(987) and points[:, 1].max() <= 1013
    few = anchorcheck.probe_points(footprint, spacing=1, limit=20)
    assert len(few) <= 20
    commands = anchorcheck.startup_commands(points)
    assert commands.startswith("trainer ground 600.0 1000.0;wait 8;trainer ground 600.0 1000.0;wait 1;trainer ground")
    assert len(commands) <= anchorcheck.STARTUP_LIMIT
    with pytest.raises(ValueError, match="spacing"):
        anchorcheck.startup_commands(np.zeros((200, 2)))


def test_heights_from_the_log():
    log = "\n".join([
        "[info] Trainer: no collision at 10.0, 20.0 yet; going to height 202.0 and waiting for the ground.",
        landed(30, 199.5, -40),
        "[info] Trainer: no collision at 50.0, 60.0 yet; going to height 202.0 and waiting for the ground.",
        landed(10, 200.2, 20),                                     # probed again, once loaded
        "[info] unrelated line",
    ])
    heights, missing = anchorcheck.read_heights(log)
    assert heights == {(30.0, -40.0): pytest.approx(199.5), (10.0, 20.0): pytest.approx(200.2)}
    assert missing == [(50.0, 60.0)]


def test_flat_ground_is_a_good_anchor(park):
    footprint = anchorcheck.read_park(park)
    points = anchorcheck.probe_points(footprint)
    heights = {(round(x, 1), round(z, 1)): 200.1 for x, z in points}
    verdict = anchorcheck.check(footprint, heights)
    assert verdict.good and verdict.level_share == 1.0
    assert "a good anchor" in verdict.summary(0.3)


def test_a_wall_and_a_drop_are_reported(park):
    footprint = anchorcheck.read_park(park)
    points = anchorcheck.probe_points(footprint)
    # A sea wall: the west third drops 2 m to a beach, a building stands over the east edge.
    def ground(x):
        return 198.0 if x < 588 else 206.0 if x > 615 else 200.0
    heights = {(round(x, 1), round(z, 1)): ground(x) for x, z in points}
    verdict = anchorcheck.check(footprint, heights, missing=[(1.0, 2.0)])
    assert not verdict.good
    assert verdict.floating == [0] and verdict.buried == [1]     # the objects at x 580 and 620
    assert max(d for _, _, d in verdict.low) == pytest.approx(2.0)
    assert max(d for _, _, d in verdict.high) == pytest.approx(6.0)
    text = verdict.summary(0.3)
    assert "1 would be buried, 1 would float" in text and "move the anchor" in text and "not streamed in" in text


def test_no_readings_is_an_error(park):
    with pytest.raises(ValueError, match="run the probes"):
        anchorcheck.check(anchorcheck.read_park(park), {})


def test_cli_probe_then_check(park, tmp_path, capsys):
    lines = tmp_path / "probes.txt"
    assert cli.main(["anchor", "probe", "--park", str(park), "--out", str(lines)]) == 0
    out = capsys.readouterr().out
    assert "RESKATE_STARTUP_COMMANDS=trainer ground 600.0 1000.0;wait 8;trainer ground 600.0 1000.0;wait 1" in out
    probes = lines.read_text().splitlines()
    log = tmp_path / "ReSkate.log"
    log.write_text("\n".join(landed(float(x), 200.0, float(z)) for _, _, x, z in (p.split() for p in probes)))
    report = tmp_path / "verdict.json"
    assert cli.main(["anchor", "check", "--park", str(park), "--log", str(log), "--report", str(report)]) == 0
    assert "a good anchor" in capsys.readouterr().out
    assert json.loads(report.read_text())["level_share"] == 1.0


def test_search_grid_and_batches():
    points = anchorcheck.search_points(600, 1000, 200, 20)
    assert len(points) == 21 * 21
    hops = np.hypot(*np.diff(points, axis=0).T)
    assert hops.max() == pytest.approx(20)                       # a serpentine: every hop is one step
    batches = anchorcheck.batches(points)
    assert all(len(b) <= anchorcheck.STARTUP_LIMIT for b in batches)
    assert sum(b.count("trainer ground") for b in batches) == 2 * len(points)
    assert all(not b.startswith(";") and not b.endswith(";") for b in batches)
    assert batches[0].startswith("trainer ground 400.0 800.0;wait 8;trainer ground 400.0 800.0;wait 1;"
                                 "trainer ground 420.0 800.0;wait 8")
    assert all(b.count("trainer ground") <= 2 * 30 for b in anchorcheck.batches(points, per_batch=30))
    quick = anchorcheck.batches(points, wait=1, settle=False)
    assert sum(b.count("trainer ground") for b in quick) == len(points)


def watch(x, y, z, ended=False):
    what = "landing watch ended" if ended else "landed"
    return f"[info] Trainer: {what} at {x:.1f}, {y + anchorcheck.STAND_HEIGHT:.1f}, {z:.1f}" + \
        (" after 0 correction(s)." if ended else ".")


def test_settled_readings():
    start = (627.1, 1062.8)
    log = "\n".join([
        # Not loaded yet: the first ray reads the sea; the settling probe reads the street.
        landed(100, 191.0, 200), watch(100.2, 200.0, 199.5), landed(100, 200.3, 200),
        # No second probe logged, but the watch saw it land.
        landed(120, 191.0, 200), watch(120.1, 206.8, 200.4),
        # Water: the game puts the skater back at the start.
        landed(140, 191.0, 200), watch(start[0], 199.9, start[1], ended=True), landed(140, 191.0, 200),
        # Only the early reading.
        landed(160, 199.6, 200),
        # Nothing at all.
        "[info] Trainer: no collision at 180.0, 200.0 yet; going to height 202.0 and waiting for the ground.",
    ])
    readings = anchorcheck.read_probes(log)
    assert readings.heights[(100.0, 200.0)] == pytest.approx(200.3)       # the second probe wins
    assert readings.heights[(120.0, 200.0)] == pytest.approx(206.8)       # then the watch
    assert readings.reset == {(140.0, 200.0)}
    assert readings.unsettled == {(160.0, 200.0)}
    assert readings.missing == [(180.0, 200.0)]
    heights, missing = anchorcheck.read_heights(log)
    assert heights == readings.heights and missing == readings.missing


def test_a_later_run_of_a_spot_wins():
    log = "\n".join([landed(100, 191.0, 200), landed(100, 191.0, 200),
                     landed(100, 191.0, 200), watch(100, 205.0, 200), landed(100, 205.0, 200)])
    assert anchorcheck.read_probes(log).heights == {(100.0, 200.0): pytest.approx(205.0)}


def test_reset_spots_count_as_water():
    heights = {(float(x), float(z)): 200.0 + 0.001 * x for x in range(0, 401, 20) for z in range(0, 401, 20)}
    reset = {k for k in heights if k[0] <= 160}                  # the west part reset the skater
    best = anchorcheck.rank(heights, size=160, top=3, wet_spots=reset)
    assert best[0].x - 80 > 160 and best[0].level_share == 1.0
    assert best[0].water == 0


def waterfront(x, z):
    """A shore along x = 600: the sea (exactly 191.0) west of x = 500, a beach rising to it, a
    200 m-wide plaza east of it from z 900 to 1100 with a gentle fall, a hillside beyond, and a
    tower at (880, 1000)."""
    if x < 500:
        return 191.0
    if x < 600:
        return 192.0 + (x - 500) * 0.03
    if abs(x - 880) < 15 and abs(z - 1000) < 15:
        return 345.0
    if 900 <= z <= 1100:
        return 200.0 + 0.001 * (x - 600)
    return 200.0 + 0.1 * (abs(z - 1000) - 100)


def test_search_ranks_the_open_ground():
    heights = {(round(x, 1), round(z, 1)): round(waterfront(x, z), 1)
               for x, z in anchorcheck.search_points(600, 1000, 300, 20)}
    water = anchorcheck.water_level(heights)
    assert water == pytest.approx(191.0)
    best = anchorcheck.rank(heights, size=160, top=3, water=water, reference=200.0)
    top = best[0]
    assert top.level_share == 1.0 and top.ground == pytest.approx(200.1, abs=0.15) and not top.suspicious
    assert 600 <= top.x - 80 and top.x + 80 < 865                # on the plaza: off the beach, clear of the tower
    assert 900 <= top.z - 80 and top.z + 80 <= 1100
    assert all(c.ground > 195 for c in best)                     # never the sea
    for a in best:                                               # no two overlap by more than half
        for b in best:
            if a is not b:
                assert max(abs(a.x - b.x), abs(a.z - b.z)) >= 80
    with pytest.raises(ValueError, match="smaller --size"):
        anchorcheck.rank(heights, size=2000)


def test_without_the_water_filter_the_sea_is_flagged():
    heights = {(round(x, 1), round(z, 1)): round(waterfront(x, z), 1)   # enough sea for whole squares of it
               for x, z in anchorcheck.search_points(500, 1000, 300, 20)}
    sea = anchorcheck.rank(heights, size=160, top=40)
    flat = [c for c in sea if c.spread == 0.0]
    assert flat and all(c.suspicious and c.ground == pytest.approx(191.0) for c in flat)
    assert "SUSPICIOUS" in flat[0].line()
    flags = [c.suspicious for c in sea]
    assert flags == sorted(flags)                                # flagged ones are ranked last


def test_no_water_on_dry_ground():
    heights = {(float(x), float(z)): 200.0 + 0.01 * x for x in range(0, 400, 20) for z in range(0, 400, 20)}
    heights[(0.0, 0.0)] = 150.0                                  # one pit is not a sea
    assert anchorcheck.water_level(heights) is None


def test_reference_height_comes_first():
    # Two level squares: a roof deck 30 m up and the street. The street wins by height, though both are flat.
    heights = {}
    for x in range(0, 401, 20):
        for z in range(0, 201, 20):
            heights[(float(x), float(z))] = (230.0 if x <= 160 else 200.0) + 0.001 * z
    best = anchorcheck.rank(heights, size=160, reference=200.0, top=2)
    assert best[0].ground == pytest.approx(200.1, abs=0.15)
    assert "-30" in best[1].line() or best[1].ground == pytest.approx(200.1, abs=0.15)


def test_skip_radius_widens_without_repeating():
    inner = anchorcheck.search_points(0, 0, 200, 20)
    ring = anchorcheck.search_points(0, 0, 400, 20, skip_radius=200)
    assert len(inner) + len(ring) == len(anchorcheck.search_points(0, 0, 400, 20))
    assert not {tuple(p) for p in inner} & {tuple(p) for p in ring}


def test_cli_search_round_trip(park, tmp_path, capsys):
    assert cli.main(["anchor", "search", "--around", "600", "1000", "--radius", "300", "--spacing", "20",
                     "--out-dir", str(tmp_path / "batches")]) == 0
    capsys.readouterr()
    logs = []
    for number, batch in enumerate(sorted((tmp_path / "batches").glob("batch-*.txt")), 1):
        probes = [c.split() for c in batch.read_text().strip().split(";") if c.startswith("trainer ground")]
        log = tmp_path / f"batch-{number:02d}.log"
        log.write_text("\n".join(landed(float(x), round(waterfront(float(x), float(z)), 1), float(z))
                                 for _, _, x, z in probes))
        logs += ["--log", str(log)]
    anchor = tmp_path / "best.park.json"
    assert cli.main(["anchor", "search", *logs, "--size", "160", "--anchor-out", str(anchor),
                     "--anchor-template", str(park)]) == 0
    out = capsys.readouterr().out
    assert "961 spots read from" in out
    assert "1. " in out and "100% level" in out and "--around" in out
    assert "Water at 191.0" in out and "Reference height 200.0" in out
    document = json.loads(anchor.read_text())
    (stick,) = document["maps"]["bam"]
    assert stick["position"][1] == pytest.approx(200.1, abs=0.15) and stick["item"] == "own_bk_box"


def test_lower_and_higher_say_relative_to_the_park(park):
    footprint = anchorcheck.read_park(park)
    heights = {(580.0, 990.0): 191.1, (620.0, 990.0): 205.0, (600.0, 1010.0): 200.0}
    text = anchorcheck.check(footprint, heights).summary(0.3)
    assert "up to 8.9 m below the park's ground" in text and "up to 5.0 m above the park's ground" in text


def test_probe_prints_the_bare_value(park, capsys):
    assert cli.main(["anchor", "probe", "--park", str(park)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("trainer ground 600.0 1000.0;wait 8;") for line in lines)


def test_reference_height_skips_water_at_the_centre():
    # The search's centre is on the sea; the land round it is at street level.
    heights = {(round(x, 1), round(z, 1)): round(waterfront(x, z), 1)
               for x, z in anchorcheck.search_points(440, 1000, 300, 20)}
    assert next(iter(heights.values())) == 191.0
    water = anchorcheck.water_level(heights)
    height, how = anchorcheck.reference_height(heights, water)
    assert height > water + anchorcheck.WATER_BAND and "nearest" in how      # the beach, not the sea
    heights_on_land = {(round(x, 1), round(z, 1)): round(waterfront(x, z), 1)
                       for x, z in anchorcheck.search_points(700, 1000, 300, 20)}
    height, how = anchorcheck.reference_height(heights_on_land, anchorcheck.water_level(heights_on_land))
    assert height == pytest.approx(200.1, abs=0.15) and "within 30 m" in how


def test_small_squares_and_mixed_grids_have_enough_probes():
    # Size 100 at 20 m spacing: 25 probes a square (5 x 5), not (100/20 + 1)^2.
    heights = {(round(x, 1), round(z, 1)): round(waterfront(x, z), 1)
               for x, z in anchorcheck.search_points(700, 1000, 200, 20)}
    best = anchorcheck.rank(heights, size=100, water=anchorcheck.water_level(heights), reference=200.0)
    assert best[0].probes == 25 and best[0].level_share == 1.0
    # A 30 m ring round a 20 m middle: squares out in the ring still count.
    ring = {(round(x, 1), round(z, 1)): round(waterfront(x, z), 1)
            for x, z in anchorcheck.search_points(700, 1000, 500, 30, skip_radius=200)}
    best = anchorcheck.rank(heights | ring, size=160, top=20, reference=200.0)
    assert any(max(abs(c.x - 700), abs(c.z - 1000)) > 250 for c in best)
