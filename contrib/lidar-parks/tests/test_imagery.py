"""Street-level imagery coverage (imagery.py), against answers shaped like each service's."""
from __future__ import annotations

import json

import pytest

from reskate_lidar import geo, imagery, net
from reskate_lidar.cli import main

BBOX = geo.bbox_around(33.98709, -118.47547, 200)


@pytest.fixture
def answers(monkeypatch):
    sent = []

    def fake_fetch(url, cache, kind, data=None, suffix="", headers=None, **_):
        sent.append({"url": url, "kind": kind, "headers": headers or {}, "data": data})
        if kind == "mapillary":
            return json.dumps({"data": [
                {"id": "1", "captured_at": 1_600_000_000_000, "sequence": "a", "sfm_cluster": {"id": "c1"}},
                {"id": "2", "captured_at": 1_700_000_000_000, "sequence": "a"},
                {"id": "3", "captured_at": 1_650_000_000_000, "sequence": "b", "sfm_cluster": {"id": "c2"}}]}).encode()
        if kind == "panoramax":
            return json.dumps({"type": "FeatureCollection", "features": [
                {"collection": "s1", "properties": {"datetime": "2024-05-02T10:00:00Z", "license": "CC-BY-SA-4.0"}},
                {"collection": "s1", "properties": {"datetime": "2023-01-09T10:00:00Z", "license": "etalab-2.0"}}]}).encode()
        if kind == "kartaview":
            return json.dumps({"currentPageItems": [
                {"sequence_id": "9", "shot_date": "2019-07-01 12:00:00"}]}).encode()
        raise AssertionError(kind)

    monkeypatch.setattr(net, "fetch", fake_fetch)
    return sent


def test_mapillary_needs_a_token(tmp_path, monkeypatch, answers):
    monkeypatch.delenv("MAPILLARY_TOKEN", raising=False)
    found = imagery.mapillary(BBOX, tmp_path)
    assert found.images == 0 and "token" in found.note
    assert answers == []  # nothing was asked without one


def test_mapillary_counts_and_keeps_the_token_out_of_the_url(tmp_path, answers):
    found = imagery.mapillary(BBOX, tmp_path, token="secret-token")
    assert (found.images, found.sequences, found.with_point_cloud) == (3, 2, 2)
    assert (found.first, found.last) == ("2020-09-13", "2023-11-14")
    (request,) = answers
    assert "secret-token" not in request["url"]
    assert request["headers"]["Authorization"] == "OAuth secret-token"
    assert "sfm_cluster" in request["url"]
    assert "secret-token" not in json.dumps(found.__dict__)


def test_panoramax_reads_each_pictures_licence(tmp_path, answers):
    found = imagery.panoramax(BBOX, tmp_path)
    assert found.images == 2 and found.sequences == 1
    assert found.licences == {"CC-BY-SA-4.0": 1, "etalab-2.0": 1}
    assert (found.first, found.last) == ("2023-01-09", "2024-05-02")


def test_a_failing_source_says_why(tmp_path, monkeypatch, answers):
    def broken(*args, **kwargs):
        raise OSError("blocked")
    monkeypatch.setattr(imagery, "kartaview", broken)
    found = {c.source: c for c in imagery.survey(33.98709, -118.47547, 100, tmp_path, token="t")}
    assert "blocked" in found["KartaView"].note
    assert found["Panoramax"].images == 2


def test_imagery_command(tmp_path, monkeypatch, answers, capsys):
    monkeypatch.setenv("MAPILLARY_TOKEN", "t")
    assert main(["imagery", "--centre", "33.98709,-118.47547", "--cache", str(tmp_path),
                 "--report", str(tmp_path / "imagery.json")]) == 0
    out = capsys.readouterr().out
    assert "Mapillary: 3 images in 2 sequences" in out and "KartaView: 1 images" in out
    report = json.loads((tmp_path / "imagery.json").read_text())
    assert {r["source"] for r in report} == {"Mapillary", "Panoramax", "KartaView"}
