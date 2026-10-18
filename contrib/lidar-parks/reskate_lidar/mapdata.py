"""Map data from any source as one list of tagged shapes, in OpenStreetMap's tag vocabulary.

OpenStreetMap and Overture both end up here as MapItems: a shapely geometry and a dict of OSM-
style tags. Their geometry starts in longitude/latitude and is carried into the local frame
(metres) once, by `localise`. Only public, physical things are kept: ground, structures and
street furniture. Names, addresses, phone numbers and the like are dropped on the way in."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import geo

# Tags kept from the sources. Anything else (names, addresses, contact details, opening hours,
# operators) is left behind: the city needs shapes, not who lives or works there.
KEEP_TAGS = re.compile(
    r"^(building|building:levels|building:min_level|height|min_height|roof:.*|highway|footway|area:highway|"
    r"step_count|incline|handrail.*|width|est_width|surface|smoothness|amenity|leisure|landuse|place|barrier|"
    r"kerb|material|backrest|direction|man_made|natural|sport|layer|level|bridge|tunnel|location|access|"
    r"tactile_paving|ramp.*|overture:.*)$")


@dataclass
class MapItem:
    geometry: object             # shapely geometry: lon/lat from a source, metres after localise()
    tags: dict[str, str]
    source: str                  # "osm" or "overture"
    id: str = ""
    extra: dict = field(default_factory=dict)

    def tag(self, key: str, default: str = "") -> str:
        return self.tags.get(key, default)


def clean_tags(tags: dict) -> dict[str, str]:
    return {str(k): str(v) for k, v in tags.items() if KEEP_TAGS.match(str(k)) and v is not None}


def localise(items: list[MapItem], frame: geo.LocalFrame) -> list[MapItem]:
    """The items with geometry in metres in `frame`, and only the tags that are kept."""
    return [MapItem(geo.transform_geometry(i.geometry, frame.from_lonlat), clean_tags(i.tags), i.source, i.id, i.extra)
            for i in items if i.geometry is not None and not i.geometry.is_empty]


def length_tag(value: str | None) -> float | None:
    """A length tag in metres: '12', '12 m', '12.5m', "3'6\"" (feet and inches), '40 ft'."""
    if not value:
        return None
    text = value.strip().lower().replace(",", ".")
    feet = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:'|ft)\s*(?:(\d+(?:\.\d+)?)\s*(?:\"|in))?", text)
    if feet:
        return float(feet.group(1)) * 0.3048 + float(feet.group(2) or 0) * 0.0254
    number = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(m|metres|meters|cm)?", text)
    if not number:
        return None
    return float(number.group(1)) * (0.01 if number.group(2) == "cm" else 1.0)


def count_tag(value: str | None) -> int | None:
    try:
        return int(float(value)) if value else None
    except ValueError:
        return None
