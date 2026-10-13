"""Street-level imagery coverage (imagery.py), against answers shaped like each service's live
API, and the ways a query can fail: a zero must be a real zero."""
from __future__ import annotations

import json

import pytest

from reskate_lidar import geo, imagery, net
from reskate_lidar.cli import main

LAT, LON = 33.98709, -118.47547
BBOX = geo.bbox_around(LAT, LON, 200)

MAPILLARY = {"data": [
    {"id": "1", "captured_at": 1_600_000_000_000, "sequence": "a", "sfm_cluster": {"id": "c1"}},
    {"id": "2", "captured_at": 1_700_000_000_000, "sequence": "a"},
    {"id": "3", "captured_at": 1_650_000_000_000, "sequence": "b", "sfm_cluster": {"id": "c2"}}]}
# As the meta-catalogue answers: 'features' and 'links', no "type".
PANORAMAX = {"features": [
    {"collection": "s1", "properties": {"datetime": "2024-05-02T10:00:00Z", "license": "CC-BY-SA-4.0"}},
    {"collection": "s1", "properties": {"datetime": "2023-01-09T10:00:00Z", "license": "etalab-2.0"}}],
    "links": [{"rel": "self", "href": "https://api.panoramax.xyz/api/search"}]}
# KartaView's answer to itemsPerPage over its maximum, word for word.
KARTAVIEW_400 = b"Invalid itemsPerPage value `1000`. Maximum is 150. Use multiple smaller pages instead."
KARTAVIEW = {"status": {"apiCode": 600, "apiMessage": "Request has been processed without errors"},
             "result": {"data": [{"sequenceId": "9", "shotDate": "2019-07-01 12:00:00"},
                                 {"sequenceId": "9", "shotDate": "2019-07-01 12:00:05"}]}}


@pytest.fixture
def serve(monkeypatch):
    """Answer each service with (status, JSON) from `routes`; record what was asked."""
    routes, asked = {}, []

    def fake(url, data=None, headers=None, timeout=60.0):
        asked.append({"url": url, "data": data, "headers": headers or {}})
        for prefix, route in routes.items():
            if url.startswith(prefix):
                status, body = route(url) if callable(route) else route
                return status, url, body if isinstance(body, bytes) else json.dumps(body).encode()
        raise OSError("no route")

    monkeypatch.setattr(net, "request", fake)
    routes.update({imagery.MAPILLARY: (200, MAPILLARY), imagery.PANORAMAX: (200, PANORAMAX),
                   imagery.KARTAVIEW: (200, KARTAVIEW)})
    return routes, asked


def test_mapillary_without_a_token_is_skipped(monkeypatch, serve):
    monkeypatch.delenv("MAPILLARY_TOKEN", raising=False)
    found = imagery.mapillary(BBOX)
    assert found.outcome == "skipped" and "no token" in found.note
    assert found.summary().startswith("Mapillary: skipped")
    assert serve[1] == []  # nothing was asked without one


def test_mapillary_counts_and_keeps_the_token_out(serve):
    found = imagery.mapillary(BBOX, token="secret-token")
    assert found.outcome == "found"
    assert (found.images, found.sequences, found.with_point_cloud) == (3, 2, 2)
    assert (found.first, found.last) == ("2020-09-13", "2023-11-14")
    (asked,) = serve[1]
    assert "secret-token" not in asked["url"] and asked["headers"]["Authorization"] == "OAuth secret-token"
    # bbox is west,south,east,north
    assert f"bbox={BBOX.west}%2C{BBOX.south}%2C{BBOX.east}%2C{BBOX.north}" in asked["url"]
    assert "secret-token" not in json.dumps(found.__dict__)


def test_panoramax_reads_each_pictures_licence(serve):
    found = imagery.panoramax(BBOX)
    assert found.outcome == "found" and found.images == 2 and found.sequences == 1
    assert found.licences == {"CC-BY-SA-4.0": 1, "etalab-2.0": 1}
    assert found.count_field == "features: 2 items over 1 page(s)"


def test_panoramax_follows_next_links(serve):
    page_two = "https://api.panoramax.xyz/api/search?page=2"
    def route(url):
        if url == page_two:
            return 200, {"features": [{"collection": "s2", "properties": {"datetime": "2025-01-01T00:00:00Z"}}],
                         "links": []}
        return 200, {**PANORAMAX, "links": PANORAMAX["links"] + [{"rel": "next", "href": page_two}]}
    serve[0][imagery.PANORAMAX] = route
    found = imagery.panoramax(BBOX)
    assert found.images == 3 and found.sequences == 2 and found.last == "2025-01-01"
    assert found.count_field == "features: 3 items over 2 page(s)"


def test_kartaview_v2_query_and_answer(serve):
    found = imagery.kartaview(LAT, LON, 100)
    assert found.outcome == "found" and found.images == 2 and found.sequences == 1
    assert (found.first, found.last) == ("2019-07-01", "2019-07-01")
    asked = serve[1][-1]["url"]
    assert asked.startswith("https://api.openstreetcam.org/2.0/photo/?") and "radius=100" in asked
    assert f"lat={LAT}" in asked and f"lng={LON}" in asked
    assert "itemsPerPage=150" in asked and "page=1" in asked


def test_kartaview_pages_until_a_short_page(serve):
    def route(url):
        page = int(url.split("page=")[-1])
        count = 150 if page < 3 else 7
        return 200, {"status": {"apiCode": 600},
                     "result": {"data": [{"sequenceId": str(page), "shotDate": f"2020-0{page}-01"}] * count}}
    serve[0][imagery.KARTAVIEW] = route
    found = imagery.kartaview(LAT, LON, 100)
    assert found.images == 307 and found.sequences == 3
    assert found.count_field.startswith("result.data: 307 items over 3 page(s)")
    assert [a["url"].split("page=")[-1] for a in serve[1]] == ["1", "2", "3"]
    assert imagery.kartaview(LAT, LON, 2000).note == "radius capped at 500 m"


def test_a_true_zero_is_empty_not_failed(serve):
    routes, _ = serve
    routes[imagery.PANORAMAX] = (200, {"type": "FeatureCollection", "features": []})
    routes[imagery.KARTAVIEW] = (200, {"status": {"apiCode": 600}, "result": {"data": []}})
    for found in (imagery.panoramax(BBOX), imagery.kartaview(LAT, LON, 100)):
        assert found.outcome == "empty" and found.images == 0
        assert "licence n/a" in found.summary()


@pytest.mark.parametrize("answer, expected", [
    ((400, KARTAVIEW_400), "HTTP 400: Invalid itemsPerPage value"),
    ((404, b"<html>Not Found</html>"), "HTTP 404"),
    ((200, {"status": {"apiCode": 401, "apiMessage": "You have to be authenticated"}}), "API error 401"),
    ((200, {"currentPageItems": [], "totalFilteredItems": [0]}), "unexpected answer"),  # the old 1.0 shape
    ((200, b"not json"), "not JSON"),
])
def test_kartaview_failures_are_not_zeros(serve, answer, expected):
    serve[0][imagery.KARTAVIEW] = answer
    found = imagery.kartaview(LAT, LON, 100)
    assert found.outcome == "failed" and expected in found.note
    assert "FAILED" in found.summary()


def test_panoramax_without_type_is_read(serve):
    assert "type" not in PANORAMAX
    assert imagery.panoramax(BBOX).outcome == "found"


def test_panoramax_misshapen_answer_fails(serve):
    serve[0][imagery.PANORAMAX] = (200, {"features": "nope"})
    assert imagery.panoramax(BBOX).outcome == "failed"


def test_imagery_command_verbose(tmp_path, monkeypatch, serve, capsys):
    monkeypatch.setenv("MAPILLARY_TOKEN", "t")
    assert main(["imagery", "--centre", f"{LAT},{LON}", "--verbose", "--report", str(tmp_path / "imagery.json")]) == 0
    out = capsys.readouterr().out
    assert "Mapillary: 3 images in 2 sequences" in out and "KartaView: 2 images" in out
    assert "HTTP status: 200" in out and "count read from: result.data: 2 items over 1 page(s)" in out
    assert "request: GET https://api.panoramax.xyz/api/search?bbox=" in out
    report = json.loads((tmp_path / "imagery.json").read_text())
    assert {r["source"]: r["outcome"] for r in report} == {"Mapillary": "found", "Panoramax": "found", "KartaView": "found"}


def test_imagery_command_exit_code_says_a_source_failed(monkeypatch, serve):
    monkeypatch.setenv("MAPILLARY_TOKEN", "t")
    serve[0][imagery.KARTAVIEW] = (500, b"oops")
    assert main(["imagery", "--centre", f"{LAT},{LON}"]) == 1


def test_unreachable_still_says_what_was_asked(serve):
    serve[0].pop(imagery.PANORAMAX)
    found = imagery.panoramax(BBOX)
    assert found.outcome == "failed" and "not reachable" in found.note
    assert found.request.startswith("GET https://api.panoramax.xyz/api/search?bbox=")


def test_no_mapillary_token_is_not_a_failure(monkeypatch, serve):
    monkeypatch.delenv("MAPILLARY_TOKEN", raising=False)
    assert main(["imagery", "--centre", f"{LAT},{LON}"]) == 0
