# Data sources

`reskate_lidar city` builds real places only from open data whose licences allow it. This file
lists every source the tool reads, under what terms, and the credit each one needs. The tool writes
that credit into every mod and mesh it makes:
- the mod's `README.md` and `manifest.json` description;
- `<mesh>.txt` beside a mesh;
- `attribution` in `--report`.

Keep the credit when you share what it made.

## Sharing what it makes

Mods of real places may be published, for example on Thunderstore. This is the repository owner's
decision, recorded on PR #3: "Yes just credit openstreetmap".
- Every mod keeps the OpenStreetMap credit, "© OpenStreetMap contributors, ODbL", in its README and manifest.
- It also keeps any credit its other data requires (the table below).
- A park is a Produced Work under ODbL: credit is required, share-alike is not.

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

## Street-level imagery: assessed, not used yet

**Google Street View is not allowed.** Google's terms forbid extracting its imagery or deriving 3D
content from it. Openly licensed street-level imagery exists:

| Source | Licence | What it could add | Catches |
|---|---|---|---|
| [Mapillary](https://www.mapillary.com) (API v4, free access token) | images CC BY-SA 4.0. Faces and number plates are blurred by Mapillary. | <ul><li>facade colour and texture for buildings</li><li>what a ledge or bench is made of</li><li>handrails and kerbs the map lacks</li><li>photos to check a spot by hand</li></ul>Its structure-from-motion reconstructions (per-sequence point clouds) could also give ledge and stair heights at street level, at a few centimetres where coverage is dense. | <ul><li>**Share-alike:** a texture or mesh made from these images is an adaptation and must be shared under CC BY-SA. That is fine for a free mod, but its README must say so.</li><li>**Attribution** names the photographer and links the image (Mapillary's [licence page](https://help.mapillary.com/hc/en-us/articles/115001770409)). The API does not always return the author, so that needs care.</li><li>**Coverage** varies by street, and many images are from cars, so walls and backs of plazas are thin.</li><li>Developer terms and rate limits apply.</li></ul> |
| [KartaView](https://kartaview.org) | images CC BY-SA 4.0 ([OSM wiki](https://wiki.openstreetmap.org/wiki/KartaView)) | the same, mostly from cars | Bulk download has been limited. The same share-alike and attribution rules apply. |

**Verdict:** worth adding later for facade textures and for checking spots, not for geometry
first: the LiDAR already gives shape.
- **If added,** it is an optional source with:
  - its own licence line in each mod: "Imagery © Mapillary contributors, CC BY-SA 4.0";
  - a per-image credit list;
  - a privacy filter. Use only images of public space; never textures of private homes; never anything that shows a person, even blurred.

## Not allowed

Never use these, not even for checking:
- Google Maps, Google Earth, Google Street View or Google Photorealistic 3D Tiles;
- Apple Maps;
- Bing imagery or Bing Maps;
- anything scraped from a website.

Their terms forbid extracting data from them. Street-level photos of people or private property
are also out.
