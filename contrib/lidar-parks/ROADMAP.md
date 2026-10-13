# Real places: roadmap

How `reskate_lidar` turns real places into skate. spots: what works, what is next, and what needs a
human with the game.

## Questions

These need a human.

1. **ReSkate Studio.**
   - **The question:** can Studio take an outside mesh (GLB/OBJ/FBX) for both drawing and collision? Where can we get Studio, or its docs?
   - **Why it matters:** a true custom map (route 2) needs it. Nothing in this repository builds Frostbite meshes or Havok collision. The details are in [CUSTOM-MAPS.md](CUSTOM-MAPS.md).
   - **What exists now:** `city --mesh` writes ready meshes: tiled ground, buildings and skate features, plus a lighter collision copy.
2. **Where should city parks go on San Vansterdam (`bam`)?** A 160 m square of real plaza needs open, flat ground at least that big.
   - Is there such a spot?
   - Do Build Kit objects 80 m from the anchor still load and collide?
3. **Can this environment reach OpenStreetMap?** This cloud environment's network policy blocks:
   - `overpass-api.de`
   - `nominatim.openstreetmap.org`
   - `opentopography.org`
   - the national LiDAR portals

   So OSM is tested only with saved answers, and the real-place run below used Overture, which carries OSM's data, plus 3DEP. Allowing those hosts in the environment settings would let the OSM path be tried live.
4. **Sharing real places.**
   - **The question:** is it fine to publish mods of real places (e.g. on Thunderstore), with the ODbL credit in their README and manifest?
   - **Why it needs a decision:** a park is a Produced Work under ODbL. That needs credit, not share-alike, but someone should decide the project's policy.

## Try in game

You cannot test this without the game; Claude cannot run it. Generate the plaza:

```
python -m reskate_lidar city --centre 37.7955,-122.3952 --size 160 \
    --anchor "<Skate>/Mods/Marker/parks/bam.park.json" --mods "<Skate>/Mods" \
    --name "Embarcadero Plaza" --preview emb.png --report emb.json
```

The anchor is a measuring stick on open ground, as in README step 1. Then check:

1. **Load:** **PARK MODS** lists *Embarcadero Plaza*, and it loads without errors. About 50 objects: stairs, handrails, ledges, pads and banks.
2. **Layout:** compare with `emb.png`. North in the plaza should be the stick's left (+90° from its facing), since the stick faces east. Does the layout match? If it is mirrored, tell us: the frame is meant to be right, but nobody has checked it in game yet.
3. **Ledges:** the long benches round the sunken lawn (#4, #6, #8 in the preview) should be grindable at about 0.45 m.
4. **Stairs and rails:** the stairs (#1, 0.8 m) should have no gap under them, and handrails should line up with the flights.
5. **Mesh:** if you have a level tool, open `--mesh emb.glb` and check:
   - its scale against a 1 m Build Kit stick;
   - it is Y up;
   - north is +Z.

   It is about 207k triangles; `emb-collision.glb` is about 52k.

## What works

- **`city`:** a place name (Nominatim), a centre and size, or a bounding box, up to 4 km² per run.
- **Map data:**
  - OpenStreetMap through Overpass;
  - Overture Maps from its GeoParquet on AWS. Only the row groups over the area are read, and the footer index is cached.

  Both become one tag model (`mapdata.MapItem`). Personal tags are dropped.
- **LiDAR:**
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

## Next

1. **Kerbs from the ground.** Road edges are 15 cm steps in the DTM: slappy curbs along plazas. Map them only where a footway meets a road, and cap the count.
2. **Other open LiDAR.**
   - **Readers:** COPC/LAZ tile lists, for the Netherlands' AHN (CC0), the UK Environment Agency (OGL), Denmark and Finland, and OpenTopography datasets.
   - **Reprojection:** EPSG codes other than 3857 and 4326, through `pyproj` (optional).
3. **Neighbourhoods.** Split an area into tiles, each its own park mod (1024 objects per map, 3 parks per server by default) or mesh tile.
   - Thin the ground by level of detail: a coarser grid away from spots, with decimation that keeps edges.
   - Set budgets per tile: triangles, objects and file size.
4. **Surfaces.** Carry `surface=*` (asphalt, paving_stones, concrete, wood, metal) into the mesh extras and the report.
5. **Better stand-ins for big spots.** A 20 m bench ledge is several boxes end to end. Long banks and wide stairs are sets side by side.
   - Use `--keep-elevation` with real terrain only once custom maps exist.
   - Tune `catalog.json` once the sizes are calibrated in game (`calibrate`).
6. **Leisure=skatepark.** Report mapped skateparks in the area, and point to `convert` (phone scan) for their real shapes.

## Known limits

- Aerial LiDAR sees tops, at around 5 cm noise. Rails are not found in it: only handrails tagged on steps are placed.
- Surveys that leave vegetation unclassified (San Francisco 2023 does) rely on the flatness, overhang and returns checks. Some real low ledges may be dropped.
- Steps whose ground shows no rise in the DTM, e.g. under a canopy, are reported and left out.
- Build Kit objects scale uniformly, so stand-ins are approximate.
