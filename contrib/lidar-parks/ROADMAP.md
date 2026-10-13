# Real places: roadmap

How `reskate_lidar` turns real places into skate. spots: what works, what is next, and what needs a
human with the game.

## Questions

These need a human.

1. **ReSkate Studio.** **Partly answered by research:** see CUSTOM-MAPS.md, *How ReSkate Studio builds a map*.
   - Studio is in the ReSkate Discord, pinned in `#general`.
   - It builds maps from Blender with its add-on and `reskate_cli compile-map`. Other tools already feed it GLBs this way.
   - **Still needed:** someone with Studio confirms the add-on's collision, surface and grind-curve settings, and how `compile-map` is called.
2. **Where should city parks go on San Vansterdam (`bam`)?** A 160 m square of real plaza needs open, flat ground at least that big.
   - Is there such a spot? `anchor probe` / `anchor check` (README) now measure one in game: they give the share of the footprint that is flat and clear, and which objects would be buried or float.
   - The first Embarcadero try loaded all 33 of 33 objects, but its anchor (627.1, 199.9, 1062.8) is on the NE waterfront promenade or coast road. A probe run there, and at candidates inland, would settle it.
   - Do Build Kit objects 80 m from the anchor still load and collide?
3. **Can this environment reach OpenStreetMap?** This cloud environment's network policy blocks:
   - `overpass-api.de`
   - `nominatim.openstreetmap.org`
   - `opentopography.org`
   - the national LiDAR portals

   So OSM is tested only with saved answers, and the real-place run below used Overture, which carries OSM's data, plus 3DEP. Allowing those hosts in the environment settings would let the OSM path be tried live.
4. ~~**Sharing real places.**~~ **Answered:** "Yes just credit openstreetmap". This is the repository owner's answer, relayed on PR #3.
   - Mods of real places may be published.
   - Each one carries "© OpenStreetMap contributors, ODbL" in its README and manifest. `city` already writes that.
   - They also carry any other credit required by the licence of data they actually use, such as Overture's.

## Try in game

You cannot test this without the game; Claude cannot run it. Generate the plaza:

```
python -m reskate_lidar city --centre 37.7955,-122.3952 --size 160 \
    --anchor "<Skate>/Mods/Marker/parks/bam.park.json" --mods "<Skate>/Mods" \
    --name "Embarcadero Plaza" --preview emb.png --report emb.json
```

The anchor is a measuring stick on open ground, as in README step 1. Before loading the park, check the ground with `anchor probe` and `anchor check` (README, *Is the anchor on open, flat ground?*). Then check:

1. **Load:** **PARK MODS** lists *Embarcadero Plaza*, and it loads without errors. About 50 objects: stairs, handrails, ledges, pads and banks.
2. **Layout:** compare with `emb.png`. North in the plaza should be the stick's left (+90° from its facing), since the stick faces east. Does the layout match? If it is mirrored, tell us: the frame is meant to be right, but nobody has checked it in game yet.
3. **Ledges:** the long benches round the sunken lawn (#4, #6, #8 in the preview) should be grindable at about 0.45 m.
4. **Stairs and rails:** the stairs (#1, 0.8 m) should have no gap under them, and handrails should line up with the flights.
5. **Mesh:** if you have a level tool, open `--mesh emb.glb` and check:
   - its scale against a 1 m Build Kit stick;
   - it is Y up;
   - north is +Z.

   It is about 207k triangles; `emb-collision.glb` is about 52k.

## First real target: Venice Beach Skatepark

Built as a custom map from open data; see [examples/venice-beach-skatepark.md](examples/venice-beach-skatepark.md).
- **The data:**
  - 4 bowls, 0.9-2.6 m deep, at 10-12 points/m²;
  - coping placed from the map's bowl outlines;
  - 90k triangles, 2 collision parts.
- **Next:** someone with ReSkate Studio opens `venice.blend` (from `studio_prep.py`), runs `reskate_cli compile-map`, and checks:
  1. Does it compile? What did Studio ask for that is missing?
  2. In game: do the bowls' depths feel right, and does the coping grind?
  3. Is the map mirrored? The frame should be right since this change, but it is unchecked in game.
- **A phone scan of each bowl** would add the lip and vertical walls (CAPTURE.md). Merging scans into the aerial mesh is the next step after that.

## What works

- **`city`:** a place name (Nominatim), a centre and size, or a bounding box, up to 4 km² per run.
- **Map data:**
  - OpenStreetMap through Overpass;
  - Overture Maps from its GeoParquet on AWS. Only the row groups over the area are read, and the footer index is cached.

  Both become one tag model (`mapdata.MapItem`). Personal tags are dropped.
- **LiDAR:**
  - LAS, LAZ and COPC tiles on disk in any CRS, for surveys outside the US.
  - USGS 3DEP straight from its public EPT copies. The newest survey covering the area is chosen automatically.
  - Any other EPT works with `--lidar`.
  - Big areas read a shallower octree, to `--max-points`.
- **Ground:** bare earth from the ground-class points at 0.5 m. Holes are relaxed smooth. Outside public space it is smoothed to 3 m, for privacy.
- **Buildings:** footprints, with OSM preferred over Overture where both have one, clipped to the area.
  - **Height:** measured in the points first, then the `height` tag, then levels, then a guess of 8 m, which is reported.
  - **Check:** the Embarcadero Center tower comes out at 169 m.
- **Skate spots:**
  - **Steps:** the flight's real extent and rise are taken from the ground, since a mapped line overruns. Width comes from where the ground keeps climbing evenly, so a wall beside the steps does not count. Handrails come from `handrail*` tags.
  - **Benches:** a bench mapped as a point gets its direction from its points.
  - **Walls:** low and retaining walls are measured against their higher side.
  - **Kerbs and planters:** from their tags.
  - **Untagged ledges and pads:** found by the old park detector on a height-above-ground grid, kept only if the top is flat, nothing overhangs it, and its pulses had single returns (leaves give several).
  - **Banks:** found as planes in the ground.
  - **Where:** only in public, hard, car-free space. Grass and gardens are left out.
- **Output:**
  - **Park mod:** Build Kit objects within `--max-objects`. Stairs and rails come first. Each spot stands on the base map's ground, or at its real height with `--keep-elevation`.
  - **Meshes:** GLB in the game frame. The visual one has ground tiles (`--tile`), buildings and spots, with a `surface` in each node's extras. The collision copy has the ground at half resolution and simplified buildings.
  - **Extras:** preview PNG, JSON report, and attribution everywhere.
- **Tests:** 29 offline tests on a synthetic block with known contents, plus the 30 park tests. The checks include:
  - a car, a hedge and a planter on grass give no spots;
  - a pit in a back yard is smoothed away.

## Phases

From a park of real spots to skating anywhere:
1. **Real spots as a park mod.** Done; see *What works*. It needs the in-game check above.
2. **One real block as a custom map:** ground, building massing and spots, built with ReSkate Studio.
   - **Depends on:** Studio importing our mesh.
   - **Next:** make `city --mesh` Studio-ready (`sk8_*` properties, spawn, grind curves; CUSTOM-MAPS.md), then build one block with Studio.
3. **A district of about 1 km².** Measure what it costs in game:
   - **Static bodies:** stock skate. has 8,200. ReSkate raises that to 65,000 (`Extension/World/physics_world_size.h`), and Studio makes one body per collision part.
   - **Memory and load time.**
   - **What it needs:** tiles with level of detail, and few collision parts per tile.
4. **A city in tiles,** each its own map. `reskate-levels.json` allows 64 levels per mod and 256 in total.
5. **Skate anywhere.** A place picker: Leaflet with OSM tiles, in plain JS. Pick a spot, and the tile for it is generated on request.

### Rules for places people live

These apply to every phase:
- **Spots:** only in public, hard, car-free space. Residential parcels get no spots and no detail.
- **Ground:** smoothed outside public space.
- **Buildings:** closed blocks only.
- **Before an area is shared:** its outputs, coordinates and place names stay out of the repository, tests and docs until its owner says otherwise.

## Next

Done since the last list:
- **The Studio-ready mesh,** for phase 2: `sk8_*` extras, a `spawn` node, grind curves, and `studio_prep.py`.
- **Kerbs from the ground.** Where a road's edge runs along a sidewalk or plaza and the ground steps 8-30 cm up from the road, each straight stretch of at least 2 m is a low ledge (a slappy curb in the park). Kerbs come after every other spot, at most 12 (`Settings.kerbs`).
- **LiDAR outside the US.** `--lidar` reads LAS, LAZ and COPC tiles on disk (AHN, the UK Environment Agency, Denmark, Finland), and EPT, in any CRS. Reprojection goes through `pyproj`. `--lidar-credit` carries the survey's credit into every output. It is tested on the synthetic block placed in Amsterdam, written as AHN-style tiles in the Dutch RD grid.
- **Stricter residential handling.** On residential parcels (residential land that is not street, footway or park), no spot is ever placed. Homes (houses, detached, terraces, apartments, and any building on residential land) are their footprint's smallest rectangle, with the height rounded to 2 m.

1. **Downloading national LiDAR.** Reading it works (above). Next is finding and fetching the tiles over an area:
   - AHN's tile index on PDOK;
   - the UK Environment Agency's survey catalogue;
   - Denmark, Finland, and OpenTopography.

   These hosts are blocked from this cloud environment, so it needs a run on the PC to record their answers first.
2. **Neighbourhoods** (phase 3): tiles, level of detail, and per-tile budgets (triangles, collision parts, objects, file size).
3. **Surfaces.** Carry `surface=*` (asphalt, paving_stones, concrete, wood, metal) into the mesh extras and the report.
4. **Better stand-ins for big spots.** A 20 m bench ledge is several boxes end to end. Tune `catalog.json` once the sizes are calibrated in game.
5. **Street-level imagery** (DATA-SOURCES.md). At Venice, Panoramax and KartaView have nothing within 100 m. Mapillary is unknown until a client token is set. Then:
   - colours for skatepark meshes;
   - facade textures for blocks, kept as separate CC BY-SA parts.
6. **Leisure=skatepark.** Report mapped skateparks in the area, and point to `convert` (phone scan) for their real shapes.

## Known limits

- Aerial LiDAR sees tops, at around 5 cm noise. Rails are not found in it: only handrails tagged on steps are placed.
- Surveys that leave vegetation unclassified (San Francisco 2023 does) rely on the flatness, overhang and returns checks. Some real low ledges may be dropped.
- Steps whose ground shows no rise in the DTM, e.g. under a canopy, are reported and left out.
- Build Kit objects scale uniformly, so stand-ins are approximate.
