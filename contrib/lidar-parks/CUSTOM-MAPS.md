# Route 2: the real park as a custom map (not pursued)

> **Not pursued by this project.** The owner decided: "Lets avoid dependencies on third party tools
> like the map thing from discord". Building a custom map needs ReSkate Studio, which is a
> third-party tool from the ReSkate Discord: its Blender add-on and `reskate_cli compile-map`.
> So real places reach the game as **park mods** (Build Kit objects on a base map), which need
> only this repository and the game.
>
> Kept here as reference:
> - what the loader expects;
> - what was learned about Studio.
>
> `city --mesh`, `--skatepark` and `studio_prep.py` still work. Their GLBs are useful as data, such as previews and measured shapes, but nothing more is built on this route.

The Build Kit route places stand-in objects. A custom map keeps the scanned shape itself: bowls,
pools, odd transitions, curved ledges, the actual ground. ReSkate can already load custom maps
made with other tools. A Call of Duty map (*BO2 TranZit*) and original parks are on the
ReSkate Thunderstore community. So the open work is getting a scan into that pipeline, not
teaching the game something new.

## What ReSkate expects of a custom map

Checked against the loader in this repository:
- the manifest: `Engine/Game/World/custom_level_manifest.{h,cpp}`;
- mods: `Engine/Vfs/mod_list.cpp`, `mod_catalog.cpp`, `mod_merge*.cpp`;
- mounting: `Extension/Assets/mod_layers.cpp`;
- level requests: `Runtime/level_requests.cpp`.

**A map is native Frostbite data in a mod folder.** It holds:
- `layout.toc`;
- superbundles under `Win32/`: the level's own `Win32/levels/game/<level>/<level>.toc`, edits to `globals.toc` and to `dingolevel_root`, and `cas_NN.cas` archives;
- `reskate-levels.json`.

At launch every enabled mod is merged into one patch (`Mods/.reskate`). Each mod's cas archives are
re-indexed so they don't collide, and the merged patch replaces the stock `Patch/` layer.

**`reskate-levels.json` (strict, fails closed):**

```json
{"schema": 1, "levels": [{"asset": "levels/game/dingolevel_reskate_<map>/dingolevel_reskate_<map>",
  "displayName": "My Map", "startPoints": ["Spawn"]}]}
```

- **Fields:** exactly these; any other field rejects the file.
- **Size:** at most 64 KiB, 64 levels per file and 256 across all mods.
- **`asset`:** must start with `levels/game/`.
- **`startPoints`:** names of spawn points that must exist inside the built level data.
- **How it loads:** the root `DingoLevel_Root` level, with the asset as its sublevel. The short name the game shows drops `dingolevel_` and `reskate_`.

**A ReSkate Studio stamp is required.** Any mod with a `layout.toc` needs `.reskate-studio-patch`:
- its first line is `ReSkate Studio native Patch v1`;
- then a `skate_sha256=` line, which must match this game build (`Engine/Game/Build/supported_build.h`).

Without the stamp, the mod is listed as outdated and never loads. Its `pipelineCodeChangelists` must
also match the game's `Data/layout.toc`.

**Studio is not in this repository, and nothing here builds meshes or collision.** No code writes
MeshSet, Havok or material data from GLB, OBJ or FBX. The game rides the level's collision, not its
visible mesh, so a map needs both.

**ReSkate already makes room for big maps.** `Extension/World/physics_world_size.h` raises static
collision bodies per world from 8,200 to 65,000, because "a large custom map gives every collision
part its own body".

**Units are metres, Y up.** The server's `teleport x y z (y is up)` and every speed in m/s agree.
Base map keys are `bam`, `grom`, `ftue`, `stadium_1`, `stadium_2` and `mpr`. Placements and server
park mods on a custom map use its base map's key (`Server/server_config.h`).

So a real place becomes a map in two steps: meshes from this tool, then Studio to build the level.

## The plan

1. **Clean surface: done.** `python -m reskate_lidar mesh scan.glb park.glb` writes the levelled
   scan as one surface mesh in glTF's own frame (metres, Y up, ground at y = 0, centred, -Z along
   the scan's y), which Blender and Studio read the right way round. It is
   2.5-D (one height per spot): coping lips and the space under rails close down. For a bowl that
   is exactly the ridable surface.
2. **Tidy it in Blender** (manual for now): trim the scan's ragged border, flatten the ground,
   add proper coping and rails where the scan was thin, and decimate. Hundreds of thousands of
   triangles is fine for drawing, too many for collision. A simplified copy should be the
   collision mesh.
3. *(Not pursued: needs Studio.)* **Build the level with ReSkate Studio**: visible mesh, collision mesh, spawn points, a level
   name. This needs Studio, or its documentation, from the ReSkate maintainers or map authors.
   That is the next thing to get.
4. **Real places: done up to Studio.** `python -m reskate_lidar city ... --mesh place.glb` writes:
   - the area's ground from aerial LiDAR, in tiles;
   - building blocks;
   - skate features as solids.

   Its collision copy, `place-collision.glb`, has a coarser ground and simplified buildings.
   Both are in the game frame, and node extras name each part's surface. See README, *Real places*.
5. *(Not pursued.)* **Automate** steps 2 and 3 once the manual route works: decimation, a separate collision mesh,
   and the parts (deck, coping, rails) as Build Kit objects on top, using `convert --merge`.

## How ReSkate Studio builds a map (researched; reference only)

Nobody here has run Studio yet. What follows comes from public sources, not from Studio's own
documentation, so check it against Studio once someone has it.
- **Where to get it.** Studio is not on GitHub. It is posted and pinned in the `#general` channel of
  the ReSkate Discord ([upstream issue #1](https://github.com/Dingo-Shenanigans/ReSkate/issues/1)).
  It has its own developer; bug reports are forwarded to them ([issue #8](https://github.com/Dingo-Shenanigans/ReSkate/issues/8)).
  Version 1.0.0 is the one mentioned. Its settings file is `%LOCALAPPDATA%\ReSkateStudio\settings.json`.
- **It works through Blender.** Studio has a Blender add-on (the "Skate Map add-on") and a compiler,
  `reskate_cli compile-map`, which writes the mod. The mod has `layout.toc`, superbundles,
  `reskate-levels.json` and the Studio stamp.
- **A GLB is a valid starting point.** [Spotbuilder](https://github.com/zeex64/BundleRipper) ports
  other games' maps by:
  1. writing a `.glb`;
  2. turning it into a `.blend` with headless Blender;
  3. running `reskate_cli compile-map` on that.

  Our `city --mesh` output can take the same road.
- **What the add-on reads**, as Spotbuilder writes it (object custom properties):

  | Property | Meaning |
  |---|---|
  | `sk8_collision_mode` (flat key; `sk8_object.collision_mode` 0-4 in the add-on) | collision per object: triangle mesh (`triangle_mesh`), convex parts, convex hull (`hull`), none (render only, `none`), water |
  | `sk8_collision_material_packed` (`collision_material`) | the surface material: the board's sound and grind |
  | `sk8_object.round_rail` | a round rail |
  | `sk8_hide_from_pause_map` | left off the pause map |
  | `sk8_material.invisible` | an invisible material (collision only) |
  | an empty named `spawn` | the spawn point, facing its local −Z |
  | curves with Studio's grind settings (radius, surface preset) | grind splines. Grinds are curves, not inferred from mesh edges. |
  | `sk8_light_range`, `sk8_light_tod`; `sk8_audio_*`; `sk8_npc_*` | lights, audio volumes, NPC routes |

  - **Blender import:** it maps a glTF node's `extras` to the object's custom properties. So a GLB can carry `sk8_*` keys straight into the `.blend`.
  - **Units:** Z up, in metres, the add-on's frame. The [ReSkate Map Creator](https://github.com/jonigiuro/ReSkate-Map-Creator) README uses collections named `Map`, `Markers` and `Grind curves`.
- **Collision cost.** Studio gives every collision part its own static body ([upstream PR #38](https://github.com/Dingo-Shenanigans/ReSkate/pull/38)).
  - **The limit:** a 41k-part export crashed against the stock limit of 8,200 bodies. ReSkate now raises it to 65,000.
  - **What that means here:** a city map should merge its collision into few parts (ground tiles, building blocks, spots), not one part per object.
- **Known Studio map sizes.** Small parks are tens of MB. The Skate 3 map is 690 MB, and a whole-city GTA port is 5.1 GB. A 3 × 3 km mountain (Sledding Game) is 650 MB.

### What `city --mesh` and `studio_prep.py` do with this

1. **The GLB is Studio-ready.**
   - Every node's extras carry `sk8_collision_mode` (`triangle_mesh` for ground, buildings and spots; `none` for grind curves).
   - Surface hints sit in `reskate_surface`.
   - There is an empty `spawn` node.
   - It is standard right-handed glTF: +Y up, -Z north. Earlier versions mirrored it.
2. **Grind curves** are line nodes with `sk8_grind_curve`:
   - ledge and bench top edges;
   - rail bars;
   - with `--skatepark`, each bowl's coping, from the map's bowl outlines at deck height.
3. **`studio_prep.py`** (headless Blender, tested with `bpy` 4.2):
   - imports the GLB;
   - puts meshes in `Map`, the spawn in `Markers` and the grind lines, as curves, in `Grind curves`;
   - sets the add-on's own properties when it is loaded;
   - saves a `.blend` for Studio.
4. **Collision parts stay few:** one per ground tile, one for the buildings, one per spot. The
   Venice Beach Skatepark example has 2 collision parts (examples/).
5. **Still to check with Studio itself:**
   - the collision mode and surface preset names;
   - the grind-curve settings;
   - the spawn's facing;
   - how `reskate_cli compile-map` is called.

## Unknowns

- The exact grind-curve properties and surface-preset names the add-on reads. Studio has them; the public sources do not.
- How `reskate_cli compile-map` is called: its arguments, and the base level a map sits on.
- Triangle limits for a level's collision. A 160 m plaza at 0.5 m is about 52k collision triangles.
  A neighbourhood needs level of detail or tiles (ROADMAP.md).
