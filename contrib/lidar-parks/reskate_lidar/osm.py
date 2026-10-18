"""OpenStreetMap through the Overpass API, and place names through Nominatim.

Both are run by volunteers, so the tool keeps to their usage policies:
- one query per area, cached; nothing is fetched twice;
- an identifying User-Agent (net.USER_AGENT);
- Nominatim at most once per second, and only for the one name you give (no bulk geocoding);
- Overpass with a server-side timeout and a bounded area (cli.py refuses areas over 4 km²).
OpenStreetMap data is ODbL: the output carries "© OpenStreetMap contributors".

Use another Overpass server with --overpass (or RESKATE_OVERPASS), or a saved Overpass JSON file."""
from __future__ import annotations

import json
import os
import urllib.parse
from pathlib import Path

from . import geo, net
from .mapdata import MapItem

OVERPASS = "https://overpass-api.de/api/interpreter"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0 (openstreetmap.org/copyright)"

# What the city needs: built shapes, ways people walk and ride, and street furniture.
QUERY = """[out:json][timeout:90];
(
  way["building"]({b});
  relation["building"]({b});
  way["highway"]({b});
  way["area:highway"]({b});
  way["place"="square"]({b});
  relation["place"="square"]({b});
  way["leisure"~"^(park|skatepark|playground|pitch)$"]({b});
  relation["leisure"~"^(park|skatepark)$"]({b});
  way["landuse"]({b});
  way["barrier"~"^(wall|retaining_wall|kerb|handrail|planter|bollard)$"]({b});
  node["amenity"="bench"]({b});
  way["amenity"="bench"]({b});
  node["leisure"="picnic_table"]({b});
  way["man_made"~"^(planter|bridge)$"]({b});
);
out tags geom;
"""


def geocode(name: str, cache: Path, url: str = NOMINATIM) -> dict:
    """One place name to {lat, lon, bbox: BBox, display_name}."""
    query = urllib.parse.urlencode({"q": name, "format": "jsonv2", "limit": 1})
    results = json.loads(net.fetch(f"{url}?{query}", cache, "nominatim", suffix=".json"))
    if not results:
        raise ValueError(f"Nominatim found nothing for {name!r}; give --centre or --bbox instead")
    r = results[0]
    south, north, west, east = (float(v) for v in r["boundingbox"])
    return {"lat": float(r["lat"]), "lon": float(r["lon"]), "bbox": geo.BBox(west, south, east, north),
            "display_name": r.get("display_name", name)}


def fetch(bbox: geo.BBox, cache: Path, url: str | None = None) -> list[MapItem]:
    url = url or os.environ.get("RESKATE_OVERPASS") or OVERPASS
    if "://" not in url and Path(url).exists():  # a saved Overpass answer
        return parse(json.loads(Path(url).read_text(encoding="utf-8")))
    query = QUERY.format(b=f"{bbox.south},{bbox.west},{bbox.north},{bbox.east}")
    body = net.fetch(url, cache, "overpass", data=urllib.parse.urlencode({"data": query}).encode(), suffix=".json")
    return parse(json.loads(body))


def _ring(geometry: list[dict]) -> list[tuple[float, float]]:
    return [(p["lon"], p["lat"]) for p in geometry if p]


def parse(answer: dict) -> list[MapItem]:
    """Overpass JSON (out tags geom) to MapItems in lon/lat."""
    import shapely
    from shapely.ops import polygonize, unary_union

    items = []
    for element in answer.get("elements", []):
        tags = element.get("tags") or {}
        kind = element.get("type")
        geometry = None
        if kind == "node":
            geometry = shapely.Point(element["lon"], element["lat"])
        elif kind == "way":
            coords = _ring(element.get("geometry") or [])
            if len(coords) < 2:
                continue
            closed = len(coords) >= 4 and coords[0] == coords[-1]
            is_area = closed and (tags.get("area") == "yes" or any(
                k in tags for k in ("building", "area:highway", "place", "leisure", "landuse", "amenity"))
                or tags.get("highway") == "pedestrian" and tags.get("area") == "yes")
            if closed and tags.get("highway") and tags.get("area") != "yes":
                is_area = False
            geometry = shapely.Polygon(coords) if is_area else shapely.LineString(coords)
        elif kind == "relation":
            outers = [shapely.LineString(_ring(m.get("geometry") or [])) for m in element.get("members", [])
                      if m.get("role") == "outer" and len(m.get("geometry") or []) >= 2]
            inners = [shapely.LineString(_ring(m.get("geometry") or [])) for m in element.get("members", [])
                      if m.get("role") == "inner" and len(m.get("geometry") or []) >= 2]
            outer = unary_union(list(polygonize(unary_union(outers)))) if outers else None
            if outer is None or outer.is_empty:
                continue
            if inners:
                outer = outer.difference(unary_union(list(polygonize(unary_union(inners)))))
            geometry = outer
        if geometry is None or geometry.is_empty:
            continue
        if not geometry.is_valid:
            geometry = shapely.make_valid(geometry)
        items.append(MapItem(geometry, dict(tags), "osm", f"{kind}/{element.get('id')}"))
    return items
