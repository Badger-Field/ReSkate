# Level exports

`reskate-level-tiles` v2 exports from `contrib/lidar-parks` (`city --level`), for the map builder.
Only exports of public places go here. These are derived outputs, not raw survey data. This branch
has no shared history with the code branches and is never merged.

## embarcadero-plaza-130x20.zip

Embarcadero Plaza, San Francisco (a public plaza). Built from `real-cities` at 243eec6:

    python -m reskate_lidar city --centre 37.7955,-122.3952 --size 130x20 --best-slice 60 --level embarcadero-plaza-130x20

- **Contents:** 4 tiles of 50 m, with 20,920 visual and 5,320 collision triangles. There are 39 collision chunks and 13 visual objects, and 6 grinds.
- **Collision checks:** all zero.
- **The slice:** 15 m south of the origin, z −25 to −5 m. The origin is 37.7955, −122.3952 at 2.64 m elevation.
- **Spawn:** 0, −0.522, −15.
- **Ground:** y −0.71 to 1.62, or 1.93 to 4.26 m above sea level.
- **sha256:** 8fc41c8eefd0b0eb0d1a2e84581e2baf8619def89ded2e141bbae61f022b6041

Attribution (also in the zip's ATTRIBUTION.txt and level.json):
- Map data © OpenStreetMap contributors, ODbL 1.0 (openstreetmap.org/copyright).
- Map data from Overture Maps Foundation (overturemaps.org), including data © OpenStreetMap contributors (ODbL).
- Elevation: U.S. Geological Survey 3D Elevation Program (3DEP) LiDAR, survey CA_SanFrancisco_1_B23; public domain.

## freedom-plaza-150x60.zip

Freedom Plaza, Washington DC (a public plaza). Built from `real-cities` at ffe0b21:

    python -m reskate_lidar city --centre 38.8958,-77.0310 --size 150x60 --level freedom-plaza-150x60

- **Contents:** 8 tiles of 50 m, with 73,016 visual and 18,416 collision triangles. There are 113 collision chunks and 47 visual objects, and 14 grinds.
- **Collision checks:** all zero.
- **Spots:** 8 flights of steps, 9 banks and 7 kerbs.
- **Origin:** 38.8958, −77.0310 at 5.37 m elevation. The level isn't sliced, and covers x −75..75, z −30..30.
- **Spawn:** 0, 0.1, 0.
- **Ground:** y −1.85 to 0.55, or 3.52 to 5.92 m above sea level.
- **Buildings:** one corner of a building at the west edge, assumed 8 m, which tops out at y 7.2.
- **sha256:** 3a5110a1ddcfb2cc05cdd5d2a169d7c72d76b6817f2b47ef32175770375f2011

OpenStreetMap was not reachable when this was built. Its map data comes from Overture, which includes OpenStreetMap data.

Attribution (also in the zip's ATTRIBUTION.txt and level.json):
- Map data © OpenStreetMap contributors, ODbL 1.0 (openstreetmap.org/copyright).
- Map data from Overture Maps Foundation (overturemaps.org), including data © OpenStreetMap contributors (ODbL).
- Elevation: U.S. Geological Survey 3D Elevation Program (3DEP) LiDAR, survey USGS_LPC_MD_VA_Sandy_NCR_2014_LAS_2015; public domain.
