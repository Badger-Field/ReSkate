# Route 2: the real park as a custom map

The Build Kit route places stand-in objects. A custom map keeps the scanned shape itself: bowls,
pools, odd transitions, curved ledges, the actual ground. ReSkate can already load custom maps
made with other tools. A Call of Duty map (*BO2 TranZit*) and original parks are on the
ReSkate Thunderstore community. So the open work is getting a scan into that pipeline, not
teaching the game something new.

## What ReSkate expects of a custom map

From the loader in this repository (`Engine/Vfs/mod_list.h`, `Extension/World/level_loading.cpp`,
`Extension/Assets/mod_layers.cpp`):

- A custom map is a mod folder with native Frostbite data: a `layout.toc` and its bundles, plus a
  `reskate-levels.json` that registers the level (`dingolevel_reskate_<map>`). The game lists it
  by its short name.
- Those files are built by **ReSkate Studio**, whose stamp is `.reskate-studio-patch` (it records
  the `Skate.exe` SHA-256 it built against). Studio is not in this repository.
- The game rides the level's **collision**, not its visible mesh, so a map needs both. Collision
  has to come out as the game's own physics data.

## The plan

1. **Clean surface: done.** `python -m reskate_lidar mesh scan.glb park.glb` writes the levelled
   scan as one surface mesh, in the game's frame: metres, Y up, ground at y = 0, centred. It is
   2.5-D (one height per spot): coping lips and the space under rails close down. For a bowl that
   is exactly the ridable surface.
2. **Tidy it in Blender** (manual for now): trim the scan's ragged border, flatten the ground,
   add proper coping and rails where the scan was thin, and decimate. Hundreds of thousands of
   triangles is fine for drawing, too many for collision. A simplified copy should be the
   collision mesh.
3. **Build the level with ReSkate Studio**: visible mesh, collision mesh, spawn points, a level
   name. This needs Studio, or its documentation, from the ReSkate maintainers or map authors.
   That is the next thing to get.
4. **Automate** steps 2 and 3 once the manual route works: decimation, a separate collision mesh,
   and the parts (deck, coping, rails) as Build Kit objects on top, using `convert --merge`.

## Unknowns

- Whether Studio takes a mesh from outside (GLB/FBX/OBJ) for both drawing and collision, or only
  rebuilds meshes it exported.
- How Studio sets surface types (concrete, wood, metal coping), which change how the board sounds
  and grinds.
- Triangle limits for a level's collision.
