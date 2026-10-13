# Data sources

`reskate_lidar city` builds real places only from open data whose licences allow it. This file
lists every source the tool reads, under what terms, and the credit each one needs. The tool writes
that credit into every mod and mesh it makes:
- the mod's `README.md` and `manifest.json` description;
- `<mesh>.txt` beside a mesh;
- `attribution` in `--report`.

Keep the credit when you share what it made.

## Used now

| Source | What it gives | Licence | Credit to show |
|---|---|---|---|
| [OpenStreetMap](https://www.openstreetmap.org), via the [Overpass API](https://overpass-api.de) | building footprints, streets, footways, steps (`step_count`, `handrail`), benches, walls, kerbs, squares, parks, `leisure=skatepark` | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/) | "© OpenStreetMap contributors" with a link to openstreetmap.org/copyright |
| [Nominatim](https://nominatim.openstreetmap.org) (OSM's geocoder) | the place for `--place` | ODbL (it is OSM data) | as OpenStreetMap |
| [Overture Maps](https://overturemaps.org), GeoParquet on AWS (`s3://overturemaps-us-west-2/release/<date>/`) | `buildings` (footprints, heights), `transportation` (segments: footways, steps, roads), `base` (infrastructure: benches, walls, kerbs; land use) | **ODbL 1.0** for buildings, transportation and base. Places (CDLA-Permissive 2.0) is not read. | "© OpenStreetMap contributors, Overture Maps Foundation" |
| Microsoft Building Footprints and Esri Community Maps | footprints inside Overture's buildings theme | ODbL, as part of Overture | covered by the Overture credit |
| [USGS 3D Elevation Program (3DEP)](https://www.usgs.gov/3d-elevation-program) LiDAR, the public EPT copies on AWS (`s3://usgs-lidar-public`) | ground, building heights, benches' and walls' heights, untagged ledges, banks | public domain (US federal government work) | not required. The tool still credits "U.S. Geological Survey 3D Elevation Program" and the survey name. |
| [usgs-lidar boundaries](https://github.com/hobuinc/usgs-lidar) (`resources.geojson`, Hobu, Inc.) | which 3DEP survey covers an area | published openly with the EPT copies | none needed |

### Each server's rules

- **Nominatim** ([usage policy](https://operations.osmfoundation.org/policies/nominatim/)):
  - at most one request a second, with an identifying User-Agent;
  - no bulk geocoding;
  - results are cached.

  The tool asks once, for the one name you give.
- **Overpass** ([fair use](https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances)):
  - one query per area, cached, with a server-side timeout;
  - areas over 4 km² are refused.

  For anything big, run your own Overpass server or pass `--overpass` a saved answer.
- **Overture and 3DEP on AWS:**
  - the tool reads only the row groups and octree nodes over the area: a 160 m square is about 150 LAZ tiles and a few Parquet row groups;
  - everything is cached;
  - nothing is fetched twice.

### What is kept, and what is not

Only ground, structures and street furniture are kept.
- **Map tags:**
  - names, addresses, phone numbers, operators and every other tag not about shape or use are dropped as the data is read (`mapdata.KEEP_TAGS`);
  - LiDAR noise is dropped.
- **Spot search:** spots are looked for only in public, car-free space, so cars, people and back yards are not read.
- **Ground outside public space:** away from streets and buildings, the ground is smoothed to a few metres.
- **Buildings:** closed blocks, with no interiors.

### Caching and git

- **The cache:** downloads go to the cache (`~/.cache/reskate-lidar`, `RESKATE_LIDAR_CACHE` or `--cache`), outside the repository. `contrib/lidar-parks/.gitignore` keeps LAZ, Parquet, GLB and cache folders out of git. Never commit downloaded data.
- **Tests:** they build their fixtures (a small EPT, an Overpass answer, a Parquet file) from synthetic data at run time.

## Allowed, not wired up yet

These are open, and they are next on the list in [ROADMAP.md](ROADMAP.md). Check each dataset's own
page before use: licences differ by tile and year.

| Source | Licence | Credit |
|---|---|---|
| [OpenTopography](https://opentopography.org) open datasets | per dataset; many CC BY 4.0 or public domain | as the dataset asks |
| Netherlands [AHN](https://www.ahn.nl) | CC0 | none required ("AHN" appreciated) |
| UK [Environment Agency LiDAR](https://environment.data.gov.uk/survey) | Open Government Licence v3 | "© Environment Agency copyright and/or database right" |
| Denmark [Danmarks Højdemodel](https://dataforsyningen.dk) | free data, CC BY 4.0 terms | "Klimadatastyrelsen" |
| Finland [NLS laser scanning](https://www.maanmittauslaitos.fi/en) | CC BY 4.0 | "National Land Survey of Finland" |
| Other US state and county LiDAR portals | usually public domain; check each | as asked |

Any EPT dataset already works with `--lidar <url or folder>` if its horizontal SRS is EPSG:3857 or
EPSG:4326. Credit it yourself: the tool only names the URL.

## Not allowed

Never use these, not even for checking:
- Google Maps, Google Earth or Google Photorealistic 3D Tiles;
- Apple Maps;
- Bing imagery or Bing Maps;
- anything scraped from a website.

Their terms forbid extracting data from them. Street-level photos of people or private property
are also out.
