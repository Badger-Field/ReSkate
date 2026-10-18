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
