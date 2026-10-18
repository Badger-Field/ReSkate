"""LiDAR tiles on disk in a national CRS: the synthetic block, placed in Amsterdam and written as
LAZ tiles in the Dutch RD grid (EPSG:28992), as AHN hands them out."""
import json

import numpy as np
import pytest

pyproj = pytest.importorskip("pyproj")

from reskate_lidar import geo, synthcity, tiles  # noqa: E402
from reskate_lidar.cli import main  # noqa: E402

FRAME = geo.LocalFrame(52.3573, 4.8818)   # Museumplein
RD = "EPSG:28992"


def write_tiles(folder, points, crs=RD, header_crs=True, split=0.0):
    """The points as two LAZ tiles split at local x = `split`, in `crs`."""
    import laspy
    folder.mkdir(parents=True, exist_ok=True)
    projection = tiles.Projection(crs)
    xy = projection.from_lonlat(FRAME.to_lonlat(points.xyz[:, :2]))
    paths = []
    for name, part in (("west", points.xyz[:, 0] < split), ("east", points.xyz[:, 0] >= split)):
        header = laspy.LasHeader(point_format=6, version="1.4")
        header.offsets = xy[part].min(axis=0).tolist() + [0.0]
        header.scales = [0.001, 0.001, 0.001]
        if header_crs:
            header.add_crs(pyproj.CRS.from_user_input(crs))
        las = laspy.LasData(header)
        las.x, las.y, las.z = xy[part, 0], xy[part, 1], points.xyz[part, 2]
        las.classification = points.classes[part]
        las.number_of_returns = np.where(points.layered[part], 2, 1)
        las.return_number = np.ones(part.sum(), np.uint8)
        path = folder / f"{name}.laz"
        las.write(path)
        paths.append(path)
    return paths


def box(half):
    return geo.BBox(*FRAME.to_lonlat(np.array([-half, -half])), *FRAME.to_lonlat(np.array([half, half])))


def test_rd_projection():
    # Amersfoort's tower, the RD grid's origin, is at 155000, 463000.
    xy = tiles.Projection(RD).from_lonlat(np.array([[5.38720621, 52.15517440]]))
    assert xy[0] == pytest.approx([155000, 463000], abs=0.5)
    back = tiles.Projection(RD).to_lonlat(xy)
    assert back[0] == pytest.approx([5.38720621, 52.15517440], abs=1e-8)


def test_tiles_are_read_reprojected_and_cropped(tmp_path):
    points = synthcity.survey(density=20)
    write_tiles(tmp_path / "ahn", points, split=25.0)          # the east tile is outside the box
    reader = tiles.TileReader.open([str(tmp_path / "ahn")])
    assert tiles.is_tiles(str(tmp_path / "ahn")) and len(reader.paths) == 2
    notes = []
    read = reader.read(box(20), FRAME, progress=notes.append)
    assert "1 of 2 LiDAR tiles" in notes[0]
    inside = np.all(np.abs(points.xyz[:, :2]) <= 20, axis=1)
    assert len(read.xyz) == pytest.approx(inside.sum(), rel=0.01)
    assert np.abs(read.xyz[:, :2]).max() <= 20.0
    assert read.layered.sum() == pytest.approx((points.layered & inside).sum(), rel=0.05)
    tree = __import__("scipy.spatial", fromlist=["cKDTree"]).cKDTree(points.xyz)
    distance, _ = tree.query(read.xyz[:500])
    assert distance.max() < 0.01                                # RD and back, to a centimetre


def test_point_budget(tmp_path):
    write_tiles(tmp_path / "ahn", synthcity.survey(density=20))
    read = tiles.TileReader.open([str(tmp_path / "ahn")]).read(box(20), FRAME, max_points=5000)
    assert len(read.xyz) == 5000


def test_a_tile_without_a_crs_needs_one(tmp_path):
    (path, _) = write_tiles(tmp_path / "bare", synthcity.survey(density=5), header_crs=False)
    with pytest.raises(ValueError, match="--lidar-crs"):
        tiles.TileReader.open([str(path)]).read(box(20), FRAME)
    read = tiles.TileReader.open([str(path)], crs=RD).read(box(20), FRAME)
    assert len(read.xyz) > 0 and read.xyz[:, 0].max() < 0       # the west tile only


def test_ept_folder_is_not_tiles(tmp_path):
    (tmp_path / "ept").mkdir()
    (tmp_path / "ept" / "ept.json").write_text("{}")
    (tmp_path / "ept" / "x.laz").write_bytes(b"")
    assert not tiles.is_tiles(str(tmp_path / "ept"))
    assert not tiles.is_tiles("https://example.org/ept.json")


def test_city_from_tiles(tmp_path, capsys):
    from test_city import overpass_answer
    write_tiles(tmp_path / "ahn", synthcity.survey())
    answer = tmp_path / "overpass.json"
    answer.write_text(json.dumps(overpass_answer(synthcity.map_items(), FRAME)))
    corners = FRAME.to_lonlat(np.array([synthcity.BOUNDS[:2], synthcity.BOUNDS[2:]]))
    bbox = ",".join(f"{v:.7f}" for v in corners.ravel())
    code = main(["city", f"--bbox={bbox}", "--lidar", str(tmp_path / "ahn"), "--lidar-credit", "AHN, CC0",
                 "--sources", "osm", "--overpass", str(answer), "--cache", str(tmp_path / "cache"),
                 "--mods", str(tmp_path / "Mods"), "--name", "Tiles Square", "--author", "tests",
                 "--report", str(tmp_path / "square.json")])
    assert code == 0
    report = json.loads((tmp_path / "square.json").read_text())
    assert {s["what"] for s in report["spots"]} >= {"steps", "bench", "wall"}
    readme = (tmp_path / "Mods" / "Tiles Square" / "README.md").read_text()
    assert "AHN, CC0" in readme and "3DEP" not in readme
