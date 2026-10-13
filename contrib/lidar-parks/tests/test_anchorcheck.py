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
    assert commands.startswith("trainer ground 600.0 1000.0;wait 1;trainer ground")
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
    assert "RESKATE_STARTUP_COMMANDS=trainer ground 600.0 1000.0;wait 1" in out
    probes = lines.read_text().splitlines()
    log = tmp_path / "ReSkate.log"
    log.write_text("\n".join(landed(float(x), 200.0, float(z)) for _, _, x, z in (p.split() for p in probes)))
    report = tmp_path / "verdict.json"
    assert cli.main(["anchor", "check", "--park", str(park), "--log", str(log), "--report", str(report)]) == 0
    assert "a good anchor" in capsys.readouterr().out
    assert json.loads(report.read_text())["level_share"] == 1.0
