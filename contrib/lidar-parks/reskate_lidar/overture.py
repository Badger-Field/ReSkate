"""Overture Maps: building footprints, streets and paths, street furniture, land use.

Overture publishes GeoParquet on AWS (s3://overturemaps-us-west-2/release/<date>/theme=*/type=*).
The files are sorted spatially and each row group records its bounding box, so a small area needs
only the row groups over it: the footers are read once (and kept in the cache), then the few
matching row groups. Licences per theme are in DATA-SOURCES.md: buildings, transportation and
base are ODbL (they carry OpenStreetMap data), so the output needs OpenStreetMap's attribution.

Rows come out as MapItems with OSM-style tags, so the rest of the pipeline treats Overture and
OpenStreetMap the same: a segment of class `steps` becomes highway=steps, a bench in `base`
keeps its source_tags (amenity=bench), a building gets building=* and height."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import geo, net
from .mapdata import MapItem

BUCKET = "overturemaps-us-west-2"
REGION = "us-west-2"
# (theme, type, columns read)
LAYERS = {
    "building": ("buildings", "building", ["id", "geometry", "bbox", "height", "num_floors", "min_height", "class", "subtype", "is_underground"]),
    "segment": ("transportation", "segment", ["id", "geometry", "bbox", "subtype", "class", "subclass"]),
    "infrastructure": ("base", "infrastructure", ["id", "geometry", "bbox", "subtype", "class", "height", "source_tags"]),
    "land_use": ("base", "land_use", ["id", "geometry", "bbox", "subtype", "class", "source_tags"]),
}
ATTRIBUTION = "Overture Maps Foundation (overturemaps.org), including data © OpenStreetMap contributors (ODbL)"


def _filesystem():
    import pyarrow.fs as fs
    return fs.S3FileSystem(anonymous=True, region=REGION)


def latest_release(filesystem=None) -> str:
    import pyarrow.fs as fs
    filesystem = filesystem or _filesystem()
    infos = filesystem.get_file_info(fs.FileSelector(f"{BUCKET}/release/"))
    releases = sorted(i.base_name for i in infos if i.type == fs.FileType.Directory)
    if not releases:
        raise ValueError("No Overture releases found")
    return releases[-1]


def _row_group_index(filesystem, folder: str, cache: Path) -> list[list]:
    """[path, row group, xmin, xmax, ymin, ymax] for every row group of a theme/type, from the footers."""
    import pyarrow.fs as fs
    import pyarrow.parquet as pq

    stored = net.cache_path(cache, "overture", folder, ".index.json")
    if stored.exists():
        return json.loads(stored.read_text())
    paths = sorted(i.path for i in filesystem.get_file_info(fs.FileSelector(folder)) if i.path.endswith(".parquet"))

    def groups(path):
        metadata = pq.ParquetFile(path, filesystem=filesystem).metadata
        names = [metadata.schema.column(i).path for i in range(metadata.num_columns)]
        columns = [names.index(f"bbox.{n}") for n in ("xmin", "xmax", "ymin", "ymax")]
        rows = []
        for g in range(metadata.num_row_groups):
            stats = [metadata.row_group(g).column(c).statistics for c in columns]
            rows.append([path, g, stats[0].min, stats[1].max, stats[2].min, stats[3].max])
        return rows

    with ThreadPoolExecutor(16) as pool:
        index = [row for rows in pool.map(groups, paths) for row in rows]
    stored.write_text(json.dumps(index))
    return index


def read_layer(layer: str, bbox: geo.BBox, cache: Path, release: str | None = None, filesystem=None,
               root: str | None = None) -> list[dict]:
    """Rows of one layer whose bounding box meets `bbox`, as dicts with a shapely `geometry` (lon, lat).
    `root` replaces s3://<bucket>/release/<release> (a local copy, or test fixtures)."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    import shapely

    theme, kind, columns = LAYERS[layer]
    filesystem = filesystem or _filesystem()
    if root is None:
        release = release or latest_release(filesystem)
        root = f"{BUCKET}/release/{release}"
    stored = net.cache_path(cache, "overture", f"{root}/{layer}/{bbox.key()}", ".parquet")
    if stored.exists():
        table = pq.read_table(stored)
    else:
        folder = f"{root}/theme={theme}/type={kind}"
        index = _row_group_index(filesystem, folder, cache)
        hits: dict[str, list[int]] = {}
        for path, group, xmin, xmax, ymin, ymax in index:
            if xmin <= bbox.east and xmax >= bbox.west and ymin <= bbox.north and ymax >= bbox.south:
                hits.setdefault(path, []).append(group)
        parts = []
        for path, row_groups in hits.items():
            file = pq.ParquetFile(path, filesystem=filesystem)
            present = [c for c in columns if c in file.schema_arrow.names]
            table = file.read_row_groups(row_groups, columns=present)
            box = table.column("bbox").combine_chunks()
            inside = pc.and_(pc.and_(pc.less_equal(box.field("xmin"), bbox.east), pc.greater_equal(box.field("xmax"), bbox.west)),
                             pc.and_(pc.less_equal(box.field("ymin"), bbox.north), pc.greater_equal(box.field("ymax"), bbox.south)))
            parts.append(table.filter(inside).drop_columns(["bbox"]))
        table = pa.concat_tables(parts, promote_options="default") if parts else pa.table({"geometry": pa.array([], pa.binary())})
        pq.write_table(table, stored)
    rows = table.to_pylist()
    for row in rows:
        row["geometry"] = shapely.from_wkb(row["geometry"])
    return rows


def _number(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def to_items(layer: str, rows: list[dict]) -> list[MapItem]:
    """Overture rows as OSM-style tagged items."""
    items = []
    for row in rows:
        tags: dict[str, str] = {}
        if row.get("source_tags"):
            source = row["source_tags"]
            tags.update(dict(source.items() if isinstance(source, dict) else source))
        if layer == "building":
            if row.get("is_underground"):
                continue
            tags.setdefault("building", row.get("class") or row.get("subtype") or "yes")
            for key, tag in (("height", "height"), ("min_height", "min_height"), ("num_floors", "building:levels")):
                if row.get(key) is not None:
                    tags[tag] = str(row[key])
        elif layer == "segment":
            if row.get("subtype") != "road":
                continue  # rail and water lines are not ridden
            tags["highway"] = row.get("class") or "road"
            if row.get("subclass") in ("sidewalk", "crosswalk"):
                tags["footway"] = row["subclass"]
        elif layer == "infrastructure":
            if row.get("height") is not None:
                tags.setdefault("height", str(row["height"]))
            tags.setdefault("overture:class", row.get("class") or "")
        elif layer == "land_use":
            tags.setdefault("overture:class", row.get("class") or "")
            tags.setdefault("overture:subtype", row.get("subtype") or "")
        items.append(MapItem(row["geometry"], tags, "overture", str(row.get("id", ""))))
    return items


def fetch(bbox: geo.BBox, cache: Path, release: str | None = None, progress=None, **kwargs) -> list[MapItem]:
    items: list[MapItem] = []
    filesystem = kwargs.pop("filesystem", None) or (None if kwargs.get("root") else _filesystem())
    if release is None and not kwargs.get("root"):
        release = latest_release(filesystem)
        if progress:
            progress(f"  Overture release {release}")
    for layer in LAYERS:
        rows = read_layer(layer, bbox, cache, release, filesystem, **kwargs)
        items += to_items(layer, rows)
        if progress:
            progress(f"  Overture {layer}: {len(rows)}")
    return items
