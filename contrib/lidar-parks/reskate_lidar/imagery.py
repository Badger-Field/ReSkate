"""Openly licensed street-level imagery around a place: what exists, how recent, under what
licence, and whether it carries 3-D (structure-from-motion) data. Metadata only: no picture is
downloaded, and nothing about the people in them is kept.

Every answer is checked, not trusted: a source reports `found` (images counted from the field
named in `count_field`), `empty` (a well-formed answer with none), `skipped` (Mapillary with no
token), or `failed` (an HTTP error, an
API error inside a 200, or an answer whose shape is not the one expected). So a zero is a real
zero. `request` and `http_status` say exactly what was asked (never with a token in it).

Sources (DATA-SOURCES.md has their licences and the credit each needs):
- Mapillary (API v4, graph.mapillary.com/images): every request needs a client token, which only
  the person running this can get. Pass it in MAPILLARY_TOKEN; it is sent in a header, never in
  the URL, the cache or the report. bbox is west,south,east,north (lon/lat). Images may carry
  `sfm_cluster`, the URL of Mapillary's point cloud.
- Panoramax (api.panoramax.xyz/api/search, the federation's meta-catalogue): STAC search, no
  token; bbox is west,south,east,north (lon/lat). Answers are {"features": [...], "links": [...]}
  (no "type"), paged by a links entry rel=next. Pictures carry their own licence.
- KartaView (api.openstreetcam.org/2.0/photo/): lat, lng and radius in metres (1-500), at most
  150 photos per page (itemsPerPage, page). The old
  1.0 nearby-photos call has answered 301/404 since 2023. Errors come as `status.apiCode` inside
  an HTTP 200; KartaView may also ask for authentication, which is reported, not worked around.

Google Street View and other closed imagery are never used: their terms forbid it."""
from __future__ import annotations

import json
import os
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import geo, net

MAPILLARY = "https://graph.mapillary.com/images"
PANORAMAX = "https://api.panoramax.xyz/api/search"
KARTAVIEW = "https://api.openstreetcam.org/2.0/photo/"
KARTAVIEW_OK = {600, 200}
KARTAVIEW_EMPTY = 601      # "The request has an empty response": KartaView's no-results code
KARTAVIEW_PAGE = 150       # the most photos KartaView returns per page  # KartaView's apiCode for success (600), and plain 200 in case it changes


@dataclass
class Coverage:
    source: str
    outcome: str = "failed"               # found, empty, skipped or failed
    images: int = 0
    first: str | None = None              # earliest capture date (YYYY-MM-DD)
    last: str | None = None
    sequences: int = 0
    with_point_cloud: int = 0             # Mapillary images with an sfm_cluster
    licences: dict[str, int] = field(default_factory=dict)
    note: str = ""
    request: str = ""                     # method and URL asked, without any token
    http_status: int | None = None
    count_field: str = ""                 # where the count was read, and its raw value

    def summary(self) -> str:
        if self.outcome == "failed":
            return f"{self.source}: FAILED, {self.note}"
        if self.outcome == "skipped":
            return f"{self.source}: skipped, {self.note}"
        if self.outcome == "empty":
            return f"{self.source}: 0 images (a well-formed answer with none); licence n/a"
        span = f"{self.first} to {self.last}" if self.first else "dates unknown"
        extra = f", {self.with_point_cloud} with a point cloud" if self.source == "Mapillary" else ""
        licences = ", ".join(f"{name} ({n})" for name, n in sorted(self.licences.items())) or "licence not stated"
        return (f"{self.source}: {self.images} images in {self.sequences} sequences, {span}{extra}; {licences}"
                + (f" ({self.note})" if self.note else ""))

    def details(self) -> str:
        return (f"    request: {self.request or '(none)'}\n    HTTP status: {self.http_status}\n"
                f"    count read from: {self.count_field or '(nothing usable)'}")


def _dates(values) -> tuple[str | None, str | None]:
    days = sorted(str(v)[:10] for v in values if v)
    return (days[0], days[-1]) if days else (None, None)


def _ask(coverage: Coverage, url: str, data: bytes | None = None, headers: dict | None = None):
    """The JSON answer, or None with the coverage marked failed and why."""
    coverage.request = ("POST " if data else "GET ") + url
    try:
        status, final, body = net.request(url, data, headers)
    except OSError as error:  # no connection at all
        coverage.note = f"not reachable ({error})"
        return None
    coverage.http_status = status
    if final != url:
        coverage.request += f" (redirected to {final})"
    if status != 200:
        coverage.note = f"HTTP {status}: {body[:200].decode('utf-8', 'replace').strip()}"
        return None
    try:
        return json.loads(body)
    except ValueError:
        coverage.note = f"not JSON: {body[:200].decode('utf-8', 'replace').strip()}"
        return None


def _misshapen(coverage: Coverage, answer, expected: str) -> None:
    keys = sorted(answer) if isinstance(answer, dict) else type(answer).__name__
    coverage.note = f"unexpected answer: no {expected} (top-level keys: {keys})"


def mapillary(bbox: geo.BBox, token: str | None = None, url: str = MAPILLARY) -> Coverage:
    coverage = Coverage("Mapillary")
    token = token or os.environ.get("MAPILLARY_TOKEN")
    if not token:
        coverage.outcome = "skipped"
        coverage.note = "no token: it needs a client token (a free Mapillary developer app) in MAPILLARY_TOKEN"
        return coverage
    query = urllib.parse.urlencode({
        "bbox": f"{bbox.west},{bbox.south},{bbox.east},{bbox.north}",
        "fields": "id,captured_at,sequence,sfm_cluster", "limit": 2000})
    answer = _ask(coverage, f"{url}?{query}", headers={"Authorization": f"OAuth {token}"})
    if answer is None:
        return coverage
    if isinstance(answer, dict) and "error" in answer:
        coverage.note = f"API error: {answer['error'].get('message', answer['error'])}"
        return coverage
    if not isinstance(answer, dict) or not isinstance(answer.get("data"), list):
        _misshapen(coverage, answer, "'data' list")
        return coverage
    images = answer["data"]
    coverage.count_field = f"data: {len(images)} items"
    coverage.outcome = "found" if images else "empty"
    coverage.images = len(images)
    coverage.first, coverage.last = _dates(
        datetime.fromtimestamp(i["captured_at"] / 1000, timezone.utc).isoformat() for i in images if i.get("captured_at"))
    coverage.sequences = len({i.get("sequence") for i in images if i.get("sequence")})
    coverage.with_point_cloud = sum(1 for i in images if i.get("sfm_cluster"))
    coverage.licences = {"CC BY-SA 4.0": len(images)} if images else {}
    if len(images) >= 2000:
        coverage.note = "capped at 2000 per request: there are more"
    return coverage


def panoramax(bbox: geo.BBox, url: str = PANORAMAX, max_pages: int = 10) -> Coverage:
    """STAC search pages: each answer has a 'features' list, and a links entry rel=next while
    there are more (the meta-catalogue leaves out "type")."""
    coverage = Coverage("Panoramax")
    query = urllib.parse.urlencode({"bbox": f"{bbox.west},{bbox.south},{bbox.east},{bbox.north}", "limit": 1000})
    features, pages, next_url = [], 0, f"{url}?{query}"
    first_request = None
    while next_url and pages < max_pages:
        answer = _ask(coverage, next_url)
        first_request = first_request or coverage.request
        if answer is None:
            coverage.request = first_request
            return coverage
        if not isinstance(answer, dict) or not isinstance(answer.get("features"), list):
            coverage.request = first_request
            _misshapen(coverage, answer, "'features' list")
            return coverage
        features += answer["features"]
        pages += 1
        next_url = next((link.get("href") for link in answer.get("links") or []
                         if isinstance(link, dict) and link.get("rel") == "next"), None)
    coverage.request = first_request
    coverage.count_field = f"features: {len(features)} items over {pages} page(s)"
    if next_url:
        coverage.note = f"stopped after {max_pages} pages: there are more"
    coverage.outcome = "found" if features else "empty"
    coverage.images = len(features)
    properties = [(f.get("properties") or {}) for f in features]
    coverage.first, coverage.last = _dates(p.get("datetime") for p in properties)
    coverage.sequences = len({f.get("collection") for f in features if f.get("collection")})
    coverage.licences = dict(Counter(p.get("license") or "see the instance's /api/configuration" for p in properties))
    return coverage


def kartaview(lat: float, lon: float, radius: float, url: str = KARTAVIEW, max_pages: int = 20) -> Coverage:
    """Pages of at most KARTAVIEW_PAGE photos (the API's maximum), until one comes back short."""
    coverage = Coverage("KartaView")
    radius = int(min(max(round(radius), 1), 500))  # the API takes 1-500 m
    photos, status, first_request = [], None, None
    for page in range(1, max_pages + 1):
        query = urllib.parse.urlencode({"lat": lat, "lng": lon, "radius": radius,
                                        "itemsPerPage": KARTAVIEW_PAGE, "page": page})
        answer = _ask(coverage, f"{url}?{query}")
        first_request = first_request or coverage.request
        if answer is None:
            coverage.request = first_request if page == 1 else coverage.request
            return coverage
        status = answer.get("status") if isinstance(answer, dict) else None
        if isinstance(status, dict) and status.get("apiCode") == KARTAVIEW_EMPTY:
            break  # no (more) photos: a real zero, not a failure
        if isinstance(status, dict) and status.get("apiCode") not in KARTAVIEW_OK:
            coverage.note = f"API error {status.get('apiCode')}: {status.get('apiMessage', '')}"
            return coverage
        result = answer.get("result") if isinstance(answer, dict) else None
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list):
            _misshapen(coverage, answer, "'result.data' list")
            return coverage
        photos += data
        if len(data) < KARTAVIEW_PAGE:
            break
    else:
        coverage.note = f"stopped after {max_pages} pages: there are more"
    coverage.request = first_request
    coverage.count_field = f"result.data: {len(photos)} items over {page} page(s)" + (
        f"; status.apiCode {status.get('apiCode')}" if isinstance(status, dict) else "")
    coverage.outcome = "found" if photos else "empty"
    coverage.images = len(photos)
    coverage.first, coverage.last = _dates(p.get("shotDate") or p.get("dateAdded") for p in photos)
    coverage.sequences = len({p.get("sequenceId") for p in photos if p.get("sequenceId")})
    coverage.licences = {"CC BY-SA 4.0": len(photos)} if photos else {}
    if radius == 500 and not coverage.note:
        coverage.note = "radius capped at 500 m"
    return coverage


def survey(lat: float, lon: float, radius: float, token: str | None = None) -> list[Coverage]:
    """Coverage within `radius` metres from every source; one that fails says why."""
    bbox = geo.bbox_around(lat, lon, 2 * radius)
    found = []
    for name, call in (("Mapillary", lambda: mapillary(bbox, token)),
                       ("Panoramax", lambda: panoramax(bbox)),
                       ("KartaView", lambda: kartaview(lat, lon, radius))):
        try:
            found.append(call())
        except OSError as error:  # no connection at all
            found.append(Coverage(name, note=f"not reachable ({error})"))
    return found
