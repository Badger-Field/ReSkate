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
- **Homes:** houses, apartments, and anything on residential land are plain rectangular blocks, with heights rounded to 2 m.
- **Residential parcels:** they never get a skate spot.

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

## Street-level imagery (a track in progress)

Street-level photos can add what aerial LiDAR can't see from above. Only openly licensed sources
are used; **Google Street View is not**, because its terms forbid extracting imagery or deriving
3-D content from it.

`python -m reskate_lidar imagery --centre lat,lon --radius 100 [--verbose]` reports, for each source, how many
images lie around a place, their capture dates, their licences, and (for Mapillary) how many carry
a point cloud. It reads metadata only and downloads no picture.
- **Each source's answer is checked.** It reports *found*, *empty* (a well-formed answer with no images) or *failed* (an HTTP error, an API error, or an answer of the wrong shape), so a zero is a real zero.
- **`--verbose`** prints each request (never with a token), its HTTP status, and the field the count was read from.

| Source | Licence | Credit to show | Access |
|---|---|---|---|
| [Mapillary](https://www.mapillary.com) | CC BY-SA 4.0 ([licence page](https://help.mapillary.com/hc/en-us/articles/115001770409-CC-BY-SA-license-for-open-data)) | the photographer and a link to each image used, plus "CC BY-SA 4.0". The API does not always return the author, so keep the image ids. | API v4 at `graph.mapillary.com`. **Every request needs a client token** from a Mapillary developer app, which only the person running the tool can make. Put it in `MAPILLARY_TOKEN`: it is sent in a header and never stored. Searches return at most 2,000 images per box. The `sfm_cluster` field is the URL of Mapillary's reconstructed point cloud. |
| [Panoramax](https://panoramax.fr) (federated; `api.panoramax.xyz`) | per instance: the OpenStreetMap France instance uses CC BY-SA 4.0, the IGN instance its open licence. Each picture's licence is read at run time (`/api/configuration`). | as the picture's licence asks; the tool records it per picture | open STAC search, no token |
| [KartaView](https://kartaview.org) | CC BY-SA 4.0 ([OSM wiki](https://wiki.openstreetmap.org/wiki/KartaView)) | "© KartaView contributors, CC BY-SA 4.0" and a link to the images | API v2: `GET /2.0/photo/?lat=&lng=&radius=` (radius 1-500 m). The old 1.0 nearby-photos call has answered 301/404 since 2023. Errors come as `status.apiCode` inside an HTTP 200, and some reads may need authentication. |

**Share-alike.** A texture, colour or mesh made from CC BY-SA photos is an adaptation, and must be
shared under CC BY-SA 4.0 with the credit above. For a free mod that is fine, but its README must
say which parts are CC BY-SA. Keep those parts separable from the ODbL and public-domain parts.

**Privacy.**
- Use photos of public space only.
- Never make textures of private homes or back yards.
- Never keep anything that shows a person or a number plate, even blurred.
- Coverage reports keep counts and dates, not pictures.

### What it could add on top of 3DEP

| | Realistic? | How |
|---|---|---|
| **Facades and textures** for building blocks | **yes, where coverage is good** | Project photos onto each block's walls from their camera positions (Mapillary's reconstruction gives them). This takes a texturing step that is not built yet, and the textures are CC BY-SA. |
| **Colours** (concrete tone, painted coping and curbs, wood benches, graffiti) | **yes** | Sample photo colours onto the mesh's vertices or as a few flat materials. That is cheap, and it is enough for a skatepark. |
| **Coping and rail edges** at centimetre accuracy | **mostly no** | The point clouds are sparse feature points placed by phone GPS. Skateparks are seldom photographed from inside, and street sequences see them from the road. A dense reconstruction (COLMAP/OpenSfM) needs many overlapping photos inside the park. That is what a ground scan gives directly (CAPTURE.md), and someone who walks a park with a phone could also upload the photos to Mapillary or Panoramax under an open licence. |
| **Things the map lacks** (handrails, kerbs, benches) | **yes, by a person** | Look at the photos to tag OpenStreetMap, which the city pipeline then reads. |

### Status

- **Built:** the coverage command. It is tested with answers in the shapes the live services gave:
  - Panoramax's `{features, links}` pages;
  - KartaView's 150-per-page limit, and its `apiCode 601` for no results.
- **Venice Beach Skatepark, within 100 m** (run on 2026-10-13 from a machine that can reach the services):
  - **Panoramax:** 0 images (a well-formed empty answer).
  - **KartaView:** 0 images (`apiCode 601`, "The request has an empty response").
  - **Mapillary:** unknown until a client token is set (`MAPILLARY_TOKEN`).

  So there is no open street-level imagery there from Panoramax or KartaView. Any texture or colour work for Venice depends on Mapillary, or on new photos someone takes and uploads under an open licence.

## Not allowed

Never use these, not even for checking:
- Google Maps, Google Earth, Google Street View or Google Photorealistic 3D Tiles;
- Apple Maps;
- Bing imagery or Bing Maps;
- anything scraped from a website.

Their terms forbid extracting data from them. Street-level photos of people or private property
are also out.
