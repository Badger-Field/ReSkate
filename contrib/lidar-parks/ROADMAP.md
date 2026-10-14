# Real places: roadmap

How `reskate_lidar` turns real places into skate. spots: what works, what is next, and what needs a
human with the game.

## Direction

The owner, after Venice as quarter pipes looked wrong in game and a skater fell through it: "I don't want it created in ramps in the world I think we should make the map builder".
- **How real places will ship:** as custom levels written by ReSkate's own map builder, with real ground and collision and no third-party tools.
  - Proven on the PC so far: reading static meshes and the collision format, packing a mod that overrides a resource, and the game accepting our changed collision.
- **This tool's part:** the input data, `city --level` (README): 50 m tiles of triangle lists with materials, visual and collision.
- **Build Kit park mods:** paused. They are not the way real places will ship. What exists keeps working: anchor search, the bowls as quarter pipes, `--no-scales`, `--best-slice`.

## Questions

These need a human.

1. ~~**ReSkate Studio.**~~ **Answered: not pursued.** The owner said: "Lets avoid dependencies on third party tools like the map thing from discord".
   - Real places reach the game as park mods: Build Kit objects that need only this repository and the game.
   - The custom-map research stays in CUSTOM-MAPS.md as reference.
2. **Where should city parks go on San Vansterdam (`bam`)?** A 160 m square of real plaza needs open, flat ground at least that big.
   - Is there such a spot? `anchor probe` / `anchor check` (README) now measure one in game: they give the share of the footprint that is flat and clear, and which objects would be buried or float.
   - The first Embarcadero try loaded all 33 of 33 objects, but its anchor (627.1, 199.9, 1062.8) is on the NE waterfront promenade or coast road.
   - **Measured:** `anchor check` there gave 6 of 78 probes level (8%), 11 higher (a rooftop 145.7 m up), and 61 lower, mostly beach a few metres down. 11 objects would float. Two runs agreed to 0.05 m.
   - **Found:** a settled `anchor search` found a level road at 507.1, 199.5, 1042.8, running along world x (about 130 x 20 m).
   - **Embarcadero Strip there:** loaded 8 of 8 objects; `anchor check`: 68% level, 2 objects float.
   - **Venice there:** loaded only with its `object_scales` stripped (the ReSkate side is being debugged). Then all 76 objects loaded; `anchor check`: 71% level, 10 buried, 1 floating. `--no-scales` now builds it at scale 1, with each bowl's depth error reported:
     - bowl 0: +0.09 m;
     - bowl 1: +0.30 m;
     - bowl 2: +0.11 m;
     - bowl 3: −0.21 m.
   - **The 130 x 20 m slice with the most spots:** `--best-slice 60` finds it 15 m south of the plaza's centre, with 2 flights of steps and 3 long benches (15 objects).
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

Measured from open data; see [examples/venice-beach-skatepark.md](examples/venice-beach-skatepark.md). It was built as a custom-map mesh before the custom-map route was dropped.
- **The data:**
  - 4 bowls, 0.9-2.6 m deep, at 10-12 points/m²;
  - coping placed from the map's bowl outlines;
  - 90k triangles, 2 collision parts.
- **As a park mod:**
  - **The limit:** Build Kit objects stand on the base map and cannot dig, so a bowl cannot be sunk into the ground.
  - **What is built:** each bowl as a ring of quarter pipes rising to its measured depth, with the coping on the map's outlines: 78 quarter pipes (examples/). It is not checked in game yet.
  - **The GLB stays useful as data:** bowl outlines, depths and wall angles.
- **A phone scan of each bowl** would add the lip and vertical walls (CAPTURE.md).

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
2. **Parks that fit their ground:**
   - `anchor probe` / `anchor check` / `anchor search` (done);
   - stand-in sizes calibrated in game;
   - budgets up to the loader's 1,024 objects per map.
3. **Skateparks as park mods:** bowls and transitions approximated from Build Kit ramps, from the measured shapes (`--skatepark` data).
4. **Several places per map:** park mods for different real places on different open ground of a base map, or across base maps.
5. **Skate anywhere.** A place picker: Leaflet with OSM tiles, in plain JS. Pick a spot, and a park mod for it is generated on request.

*Dropped:* custom maps of whole blocks, districts and cities. Those need ReSkate Studio, a third-party tool (CUSTOM-MAPS.md).

### Rules for places people live

These apply to every phase:
- **Spots:** only in public, hard, car-free space. Residential parcels get no spots and no detail.
- **Ground:** smoothed outside public space.
- **Buildings:** closed blocks only.
- **Before an area is shared:** its outputs, coordinates and place names stay out of the repository, tests and docs until its owner says otherwise.

## Next

Done since the last list:
- **Level tiles for the map builder** (`city --level`): 50 m tiles of triangle lists with materials, visual and collision, in the park frame.
  - Collision is split into chunks of at most 256 triangles, with bit-identical shared vertices.
  - Collision parts are welded, and degenerate and repeated triangles dropped.
  - Edges a half-edge shape can't hold are counted in `collision_checks`. The synthetic block has none.
- **Mapped skateparks reported:** `city` lists the skateparks mapped in its area (name, size, source). For each one it prints the `--skatepark --level` command that measures its real shape. The report has them under `skateparks`. Only skateparks keep their name; other names stay dropped.
- **Spot materials from the map:** `material=*` (benches, walls, rails) or `surface=*` (steps) set a spot's material in the mesh, the level tiles and the report. A metal bench is now metal, not the default wood. Overture's value fills in where OpenStreetMap's copy lacks one.
- **Stairs as one solid:** the sawtooth profile extruded across the flight. Before, they were boxes sharing edges, which a half-edge collision shape can't hold. Every spot solid is closed and outward-facing (tested).
- **Skatepark bowls as park mods.** `city --skatepark --mods …` builds each measured bowl as a ring of Build Kit quarter pipes, rising to its depth with the coping on its real coping line. Venice: 78 quarter pipes round 4 bowls (examples/).
- **Anchor check:** `anchor probe` / `anchor check` measure the base map under a park with the trainer's ground probe.
- **Kerbs from the ground.** Where a road's edge runs along a sidewalk or plaza and the ground steps 8-30 cm up from the road, each straight stretch of at least 2 m is a low ledge (a slappy curb in the park). Kerbs come after every other spot, at most 12 (`Settings.kerbs`).
- **LiDAR outside the US.** `--lidar` reads LAS, LAZ and COPC tiles on disk (AHN, the UK Environment Agency, Denmark, Finland), and EPT, in any CRS. Reprojection goes through `pyproj`. `--lidar-credit` carries the survey's credit into every output. It is tested on the synthetic block placed in Amsterdam, written as AHN-style tiles in the Dutch RD grid.
- **Stricter residential handling.** On residential parcels (residential land that is not street, footway or park), no spot is ever placed. Homes (houses, detached, terraces, apartments, and any building on residential land) are their footprint's smallest rectangle, with the height rounded to 2 m.

1. **Whatever the map builder needs next.**
   - **Possible additions:** level of detail for visual tiles, grass split out of the ground, and texture coordinates.
   - **Waiting on the builder's first in-game level**, built from these tiles.
2. **Downloading national LiDAR.** Reading it works (above). Next is finding and fetching the tiles over an area:
   - AHN's tile index on PDOK;
   - the UK Environment Agency's survey catalogue;
   - Denmark, Finland, and OpenTopography.

   These hosts are blocked from this cloud environment, so it needs a run on the PC to record their answers first.
3. **Surfaces of the ground.** The spots' materials now come from the map (done, above). Next is the ground: hard plazas, asphalt and grass from the areas' `surface=*`, when the builder wants grass.
4. **Street-level imagery** (DATA-SOURCES.md). At Venice, Panoramax and KartaView have nothing within 100 m. Mapillary is unknown until a client token is set. Then:
   - colours and textures for level meshes;
   - checking geometry against photos.

*Paused with park mods:* skatepark bowls as quarter pipes in game, and better Build Kit stand-ins.

## Known limits

- Aerial LiDAR sees tops, at around 5 cm noise. Rails are not found in it: only handrails tagged on steps are placed.
- Surveys that leave vegetation unclassified (San Francisco 2023 does) rely on the flatness, overhang and returns checks. Some real low ledges may be dropped.
- Steps whose ground shows no rise in the DTM, e.g. under a canopy, are reported and left out.
- Build Kit objects scale uniformly, so stand-ins are approximate.
