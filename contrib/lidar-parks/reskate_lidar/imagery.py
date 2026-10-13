"""Openly licensed street-level imagery around a place: what exists, how recent, under what
licence, and whether it carries 3-D (structure-from-motion) data. Metadata only: no picture is
downloaded, and nothing about the people in them is kept.

Sources (DATA-SOURCES.md has their licences and the credit each needs):
- Mapillary (API v4, graph.mapillary.com): every request needs a client token, which only the
  person running this can get (a Mapillary developer app). Pass it in MAPILLARY_TOKEN; it is sent
  in a header and never written to the cache or the report. Images may carry `sfm_cluster`, the
  URL of the point cloud Mapillary reconstructed from them.
- Panoramax (api.panoramax.xyz, the federation's meta-catalogue): open STAC search, no token. Each
  picture's licence is read from it, or from its instance's /api/configuration.
- KartaView (api.openstreetcam.org): open, but its API is little documented; the nearby-photos
  call used here is the long-standing v1 one.

Google Street View and other closed imagery are never used: their terms forbid it."""
from __future__ import annotations

import json
import os
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import geo, net

MAPILLARY = "https://graph.mapillary.com/images"
PANORAMAX = "https://api.panoramax.xyz/api/search"
KARTAVIEW = "https://api.openstreetcam.org/1.0/list/nearby-photos/"


@dataclass
class Coverage:
    source: str
    images: int = 0
    first: str | None = None              # earliest capture date (YYYY-MM-DD)
    last: str | None = None
    sequences: int = 0
    with_point_cloud: int = 0             # Mapillary images with an sfm_cluster
    licences: dict[str, int] = field(default_factory=dict)
    note: str = ""

    def summary(self) -> str:
        if self.note and not self.images:
            return f"{self.source}: {self.note}"
        span = f"{self.first} to {self.last}" if self.first else "dates unknown"
        extra = f", {self.with_point_cloud} with a point cloud" if self.source == "Mapillary" else ""
        licences = ", ".join(f"{name} ({n})" for name, n in sorted(self.licences.items())) or "licence not stated"
        return (f"{self.source}: {self.images} images in {self.sequences} sequences, {span}{extra}; {licences}"
                + (f" ({self.note})" if self.note else ""))


def _dates(values) -> tuple[str | None, str | None]:
    days = sorted(v[:10] for v in values if v)
    return (days[0], days[-1]) if days else (None, None)


def mapillary(bbox: geo.BBox, cache: Path, token: str | None = None, url: str = MAPILLARY) -> Coverage:
    token = token or os.environ.get("MAPILLARY_TOKEN")
    if not token:
        return Coverage("Mapillary", note="needs a client token (a free Mapillary developer app); set MAPILLARY_TOKEN")
    query = urllib.parse.urlencode({
        "bbox": f"{bbox.west},{bbox.south},{bbox.east},{bbox.north}",  # south-west, then north-east
        "fields": "id,captured_at,sequence,sfm_cluster", "limit": 2000})
    answer = json.loads(net.fetch(f"{url}?{query}", cache, "mapillary", suffix=".json",
                                  headers={"Authorization": f"OAuth {token}"}))
    images = answer.get("data", [])
    from datetime import datetime, timezone
    first, last = _dates(datetime.fromtimestamp(i["captured_at"] / 1000, timezone.utc).isoformat()
                         for i in images if i.get("captured_at"))
    return Coverage("Mapillary", len(images), first, last, len({i.get("sequence") for i in images if i.get("sequence")}),
                    sum(1 for i in images if i.get("sfm_cluster")), {"CC BY-SA 4.0": len(images)} if images else {},
                    "capped at 2000 per request" if len(images) >= 2000 else "")


def panoramax(bbox: geo.BBox, cache: Path, url: str = PANORAMAX) -> Coverage:
    query = urllib.parse.urlencode({"bbox": f"{bbox.west},{bbox.south},{bbox.east},{bbox.north}", "limit": 1000})
    answer = json.loads(net.fetch(f"{url}?{query}", cache, "panoramax", suffix=".json"))
    features = answer.get("features", [])
    licences = Counter((f.get("properties") or {}).get("license") or "see the instance's /api/configuration"
                       for f in features)
    first, last = _dates((f.get("properties") or {}).get("datetime") for f in features)
    return Coverage("Panoramax", len(features), first, last, len({f.get("collection") for f in features}), 0,
                    dict(licences))


def kartaview(lat: float, lon: float, radius: float, cache: Path, url: str = KARTAVIEW) -> Coverage:
    form = urllib.parse.urlencode({"lat": lat, "lng": lon, "radius": radius}).encode()
    answer = json.loads(net.fetch(url, cache, "kartaview", data=form, suffix=".json"))
    photos = answer.get("currentPageItems") or answer.get("osv", {}).get("photos") or []
    first, last = _dates(p.get("shot_date") or p.get("date_added") for p in photos)
    return Coverage("KartaView", len(photos), first, last, len({p.get("sequence_id") for p in photos}), 0,
                    {"CC BY-SA 4.0": len(photos)} if photos else {})


def survey(lat: float, lon: float, radius: float, cache: Path, token: str | None = None) -> list[Coverage]:
    """Coverage within `radius` metres from every source; a source that fails says why."""
    bbox = geo.bbox_around(lat, lon, 2 * radius)
    found = []
    for name, call in (("Mapillary", lambda: mapillary(bbox, cache, token)),
                       ("Panoramax", lambda: panoramax(bbox, cache)),
                       ("KartaView", lambda: kartaview(lat, lon, radius, cache))):
        try:
            found.append(call())
        except (OSError, ValueError, KeyError) as error:
            found.append(Coverage(name, note=f"not reachable or unexpected answer ({error})"))
    return found
