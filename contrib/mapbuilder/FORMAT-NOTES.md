# skate. map formats: what has been verified

Reference for the Python port of the map builder. It records what was measured on the supported
game build (Skate.exe SHA-256 `fbce74d5…ae88b8f9`) and the Thunderstore map `333-Skate_3_Improved`
on 2026-10-13. Structure only; no asset bytes.

Each claim carries one of three labels:

- **Tested in game**: a mod built from this was loaded and the effect was measured.
- **Checked on files**: read from many files and cross-checked (counts given).
- **From source**: taken from reading ReSkate's C++; not independently tested.

All integers are little-endian unless a section says otherwise. "Pointer" means two different things
in the two resource types; each section defines its own.

The throwaway C++ tools that produced these results are in `C:\LocalCode\reskate-mapformat\tool\`
(`meshx.cpp`, `meshrt.cpp`, `phys.cpp`, `edgebits.cpp`, `edgebits2.cpp`, `colltest.cpp`,
`inventory.cpp`). They link ReSkate's static libraries and are not meant to be kept.

## 1. Rigid MeshSet resource (res type 0x49B156D4)

Checked on files: 300 MeshSets read without failure (150 from the mod, 150 base game). Positions
and triangles are verified; everything else about vertex data is unverified.

ReSkate's own reader, `Engine/Resource/mesh_set.cpp`, handles only the skinned layout. The rigid
layout differs as shown.

### Header

Pointers here are u64 and count from byte 16 of the resource (file offset = value + 16).

| Offset | Type | Meaning | Rigid | Skinned |
|---|---|---|---|---|
| 0 | u32 | header size | 192 | 208 |
| 4 | u32 | LOD struct size | 176 | 188 |
| 8 | u32 | section struct size | 384 | 384 |
| 0x10 | 4 floats | bounding box minimum | | |
| 0x20 | 4 floats | bounding box maximum | | |
| 0x30 | u64 each | LOD pointer table | | |
| 0x68, 0x70 | u64 | name pointers | | |
| 124 | u8 | mesh type | 0 in 297 of 300; 2 in 3 (not examined) | skinned flag |
| 180 | u16 | LOD count (1 to 6 seen) | | |
| 182 | u16 | total sections over all LODs | | |

The first LOD struct is at 208; later ones follow every 176 bytes. The header box is at least as
large as LOD 0's geometry; in 11 of 300 base-game meshes it is looser by 3 to 38 cm.

### LOD struct (176 bytes)

| Offset | Type | Meaning |
|---|---|---|
| +0 | u32 | 0 (the skinned reader requires 1 here and so rejects rigid meshes) |
| +8 | u32 | section count |
| +12 | u64 | pointer to the section table |
| +80 | u32 | flags; 0x40, 0x41, 0x51 seen; meaning unknown |
| +84 | u32 | index format; 33 (16-bit) in all 639 LODs seen |
| +88 | u32 | index bytes |
| +92 | u32 | vertex bytes |
| +116 | GUID | geometry chunk; one chunk per LOD |

### Section struct (384 bytes)

| Offset | Type | Meaning |
|---|---|---|
| +8 | u64 | pointer to the material name (zero-terminated) |
| +16 | u64 | bone palette pointer (unused for rigid) |
| +24 | u16 | bone palette count (0 for rigid) |
| +30 | u8 | bytes per vertex over all streams |
| +31 | u8 | primitive type; 3 (triangle list) in all 1,082 sections |
| +32 | u32 | triangle count |
| +36 | u32 | first index |
| +40 | u32 | vertex start: a byte offset into the chunk's vertex data |
| +44 | u32 | vertex count |
| +112 | | vertex declaration, below |
| about +332 to +360 | floats | a per-section box; exact offsets not pinned down |

Vertex declaration, at section +112:

| Offset in declaration | Meaning |
|---|---|
| 0 | 16 elements of {u8 usage, u8 format, u8 offset, u8 stream} |
| +64 | stream table: {u8 stride, u8 class} per stream |
| +96 | u8 element count |
| +97 | u8 stream count |

Element usage and format pairs seen. Only usage 1 is decoded and verified.

| Usage | Format | Sections | Reading |
|---|---|---|---|
| 1 | 3 | 520 | position, 3 floats, stream stride 12 (verified) |
| 1 | 8 | 562 | position, 4 half floats, stride 8; the first three are x, y, z; no scale or offset (verified) |
| 33 | 6 | 896 | 4 bytes; probably a packed normal and tangent frame |
| 34 | 6 | 39 | 4 bytes; a second such set |
| 51 | 12 | 283 | 4 bytes; probably UV as two half floats |
| 52 | 13 | 766 | 4 bytes; UV or colour |
| 30, 31 | 13 | 150, 6 | 4 bytes |
| 42 | 3 | 62 | 3 floats |
| 6, 9 | 8 | 17, 10 | 4 half floats |
| 2 | 22 | 14 | unknown |

Sections named `…_Shadow` carry positions only. The mod's own meshes use both position forms.

### Geometry chunk

Checked on files: 771 LODs (430 mod, 341 base) decoded and written back from the decoded data;
every written byte was identical to the original (`meshrt.cpp`).

1. For each section, in order, starting at the section's vertex start: all of stream 0 for its
   vertices, then all of stream 1, and so on. Streams are not interleaved.
2. Zero padding up to the LOD's "vertex bytes". Gaps of 4, 8, 10 and 12 bytes were seen, all zero.
3. The index buffer, starting exactly at "vertex bytes". Indices are relative to their section's
   first vertex. Each section's indices start at `first index`.
4. Zero padding at the end of the chunk: 0, 4, 8 or 12 bytes (619, 3, 147 and 2 LODs).

Float to half conversion with round-to-nearest-even reproduces the original halves exactly. The
fourth half of a format 8 position was carried through unchanged; its meaning is unknown.

In the bundle manifest the chunk entry has `logicalOffset` 0 and `logicalSize` equal to the decoded
chunk size (771 of 771). In all printed cases the chunk sat in the same bundle as the MeshSet; 519
other LODs keep their chunk elsewhere and were not tested.

### Not done

- Writing the MeshSet resource itself (header, LOD and section structs, names).
- Decoding normals, tangents, UVs and colours.
- Mesh type 2.
- Any in-game test of a changed mesh.

## 2. Physics resource (res type 0x41759364)

This is a native Frostbite `PhysicsResource`, not Havok: no packfile or tagfile magic and no class
name strings. `Engine/Resource/material_grid.cpp` already reads its header, part table and packed
material.

Checked on files: 600 resources (300 mod, 300 base), 2,640 shapes, 8,486 parts, all read. The edge
rules were scored on all 11,452 of the mod's version-2 files.

Pointers here are u32 holding the distance from the pointer's own position to its target
(target = position + value). Zero means none.

### Header

| Offset | Type | Meaning |
|---|---|---|
| 0x00 | u32 | version: 2 in the mod's files and in base props; 0 in the base map's `staticmodelgroup.physics` |
| 0x04 | u32 | magic 0x69AF7015 |
| 0x08 | u32 | 1 with version 2; 0 with version 0 |
| 0x0C | u32 | part count |
| 0x10, 0x14, 0x20 | u32 | 0 in version 2; nonzero in version 0, meaning unknown |
| 0x18 | u32 | shape count |
| 0x1C | u32 | 0 in 583 files, 1 in 16 |
| 0x24 | pointer | always to 0x38 |
| 0x28 | pointer | shape table |
| 0x2C | pointer | equals the part table in small files; not understood |
| 0x30 | pointer | part table |
| 0x34 | pointer | start of the array data |

Version 2 has a fixed 76-byte block at 0x38 to 0x84, identical in every small file printed:
0, 0, 0, 1.0; six zeros; 1.0; u32 1; 0; -1.0, -1.0, -1.0; 0; 0; u32 256. Its shape table then
starts at 0x84. Version 0 has no such block and its shape table starts at 0x38. An empty version-2
resource is 132 bytes.

File order: header, shape table, part table, array data. In the base file the arrays are grouped
by kind across shapes. Appending new arrays at the end of the file and repointing a shape's slots
works (tested in game, section 2.5).

### Shape record (84 bytes)

| Offset | Type | Meaning |
|---|---|---|
| +0 | 3 floats | box minimum, shape-local |
| +12 | 3 floats | box maximum |
| +24 | u16 | V, vertex count |
| +26 | u16 | H, half-edge count |
| +28 | u16 | F, face count |
| +30 | u16 | N, tree node count: F - 1, or 0 |
| +32 | u32 | 0 |
| +36 | 12 pointers | slots 0 to 11 |

| Slot | Content | Evidence |
|---|---|---|
| 0 | V vertices, 3 floats each, shape-local | 2,640 of 2,640 sizes fit; 98.5% of vertices inside the box |
| 1 | V + 1 u16, rising from 0 to H: start of each vertex's run in slot 2 | 2,640 of 2,640 |
| 2 | H u16, a permutation of 0..H-1: half-edge ids grouped by start vertex, ascending within a vertex | 2,640 of 2,640 |
| 3 | H u16: start vertex of each half-edge; half-edges are stored face by face in loop order | 2,640 of 2,640 |
| 4 | H floats: edge value, section 2.3 | |
| 5 | 1 bit per half-edge, least significant bit first, rounded up to 4 bytes | 2,132 of 2,132 sizes |
| 6 | 1 bit per half-edge, same packing | 2,132 of 2,132 sizes |
| 7 | F + 1 u16, rising from 0 to H: start of each face's half-edges | 2,640 of 2,640 |
| 8, 9 | never used | |
| 10 | hulls only; not decoded | |
| 11 | N tree nodes, 8 bytes each | section 2.4 |

There is no twin array. The twin of half-edge a→b is the half-edge b→a.

Two families of shape exist:

- **Mesh**: slots 0 to 7 and 11. Every mesh with two or more faces has a tree (N = F - 1); only
  one-face shapes have none. All 942 of the mod's shapes examined are triangle meshes (H = 3F).
  Base-game shapes also use quads and larger polygons.
- **Hull**: slots 0 to 3, 7 and 10; no tree; up to a few hundred faces; presumably convex.

### Part record (72 bytes)

| Offset | Type | Meaning |
|---|---|---|
| +0x00 | 4 floats | rotation quaternion x, y, z, w |
| +0x10 | 3 floats | translation (world position for map collision) |
| +0x1C | float | uniform scale; 1.0 in 6,848 of 8,486 |
| +0x20 | | 0 when the part has a shape; a float for kind 1 |
| +0x24 | u32 | kind: 5 (6,876 parts), 3 (755), 1 (488), 2 (248), 6 (119) |
| +0x28, +0x2C | u32, u32 | hash-like; 0x859F4B83 / 0xE6686C9C on almost everything, 0xA7324F6E / 0xE6686C9C on the mod's water areas |
| +0x30 | float | -0.073 on every part printed |
| +0x34 | u32 | distinct per part in multi-part files (2, 0, 1 in a three-part base file; 2904, 3315 in another); 0 in single-part mod files. Writing 0 for every part of a three-part file crashed the game (a bad free in physics teardown), so rewrites copy it through |
| +0x38 | u32 | shape index; 0xFFFFFFFF for kind 1 |
| +0x3C | u32 | 0x7FFFFFFF or 0 |
| +0x40 | u32 | packed material: bits 0-5 flags, 6-18 material slot, 19-31 property slot |
| +0x44 | u32 | 2048 on all 8,486 |

Kind 5 is the triangle mesh used by all the mod's `collision_area_*` and `water_area_*` parts. Kind
1 has no shape (an analytic primitive). Kinds 2, 3 and 6 were not told apart.

World vertex = rotate(q, local × scale) + translation.

The material slot indexes the live material grid without a bounds check: a slot the grid lacks
crashes the game on contact (stated in `material_grid.h`; not tested here).

### 2.3 Edge data (slots 4, 5, 6)

Checked on files: all of the mod's version-2 collision, 12.9 million half-edges with a twin inside
their shape and 1.3 million matched across shapes.

Definitions, per half-edge from a to b:

- n1 is the normal of its own face, taken as (p1 - p0) × (p2 - p0) with the face's vertices in
  stored order; n2 is the same for the twin's face. Both in world space.
- angle is the angle between n1 and n2. Compute it with atan2(|n1 × n2|, n1 · n2) so it stays
  accurate near zero.
- side is the sign of (n1 × n2) · (b - a).

The twin must be searched by position over the whole area, across shapes and files, not only
inside the shape: of 1,057,003 border edges that carry a flag or a value under 1, 99.8% have their
twin in another shape or another file (matched to 1 mm, opposite direction).

| Case | Slot 4 | Slot 5 bit | Slot 6 bit |
|---|---|---|---|
| no twin anywhere | 1.0 | 0 | 0 |
| angle ≤ 0.1° | 1.0 | 0 | 1 |
| side positive | cos(angle / 2) | 0 | 0 |
| side negative, angle < 90° | 1.0 | 1 | 0 |
| side negative, angle ≥ 90° | 1.0 | 0 | 0 |

Match rates:

| Rule | Inside a shape | Across shapes |
|---|---|---|
| slot 6 | 99.94% | 98.60% |
| slot 5 | 99.67% | 99.80% |
| slot 4 | 96.6% under an earlier, slightly wrong rule; not re-scored | 99.69% |

Open points:

- Whether exactly 90° counts as under the limit. About 200,000 edges sit at 90°.
- About 263,000 border edges read 1.0 with no bits although a neighbour exists.
- About 5,000 border edges read about 0.70 with no neighbour found.
- On the base map's version-0 polygon file the older 1.81° threshold mismatched 14 to 39% of bits;
  the 0.1° rule was not re-scored there.

### 2.4 Tree nodes (slot 11)

Partly decoded, on one-node trees only (98 shapes). A node read as one 64-bit value:

| Bits | Field |
|---|---|
| 0-9, 10-19, 20-29 | lower inset on x, y, z: (child minimum - box minimum) / box size × 1023 |
| 30-31 | unknown |
| 32-40 | upper inset on x (matched on 78 of 98; the field is not fully understood) |
| 41-50, 51-60 | upper inset on y, z: (box maximum - child maximum) / box size × 1023 |
| 61-63 | unknown; related to which child each inset belongs to |

The lower insets equal the larger of the two faces' insets on 286 of 286 axes; the upper y and z
insets on 188 of 188. Which child an inset applies to, and how nodes link to children and faces,
are not decoded. Twelve implicit layouts (three split rules, pre-order or level-order, two reference
boxes) were scored and none fits trees of 64 or more nodes.

**A writer does not need this.** Tested in game: shapes of 52, 466 and 321 faces with every node
set to zero still gave correct ground heights and held the skater. Any speed cost was not measured.
Real files almost never contain zero nodes.

### 2.5 In-game tests

All on the base map, resource
`levels/game/bam_levelroot/wproot/wp/s9600_x0z0/s200_x700z1100_sublevel/staticmodelgroup.physics`
(version 0, 3,059,120 bytes, 439 parts, 407 shapes), overridden by a one-resource mod.

| Test | Change | Result |
|---|---|---|
| Round trip | none | heights identical to the unmodded game |
| Manifest sha1 | original value kept, or SHA-1 of the decoded bytes | no difference; the field is not checked |
| Raise | all vertex y and box y of three shapes + 1.00 m | ground and skater height + 1.00 m at three spots |
| Raise, zero tree | same, with 836 tree nodes zeroed | identical to "raise" |
| Ramp from scratch | one shape replaced by a new 12-triangle shape: 10 vertices, 36 half-edges, slots 0-7 written by the tool, edge data by the rule above, 11 zero nodes, arrays appended at the end of the file | ground matched the designed slope at four points to 0.1 m; the skater stood on the slope |

Open from the ramp test: on its 3 m deep flat top the probe read the right height but the skater
settled 1.6 m lower. It may have slid off the back; not established. Rolling, ollies and grinds
were not tested. No version-2 file has been written from scratch.

## 3. Mod folder

From source (`Engine/Vfs/mod_list.cpp`, `mod_catalog.cpp`, `mod_merge*.cpp`) and tested in game by
the collision mods above.

A folder `<game>\Mods\<Name>\`. The name may use ASCII letters, digits, space, `_`, `-`, `.`; at
most 64 characters; not starting with a dot.

| File | Required | Content |
|---|---|---|
| `layout.toc` | yes | a byte copy of `<game>\Data\layout.toc` is enough when no superbundle is added |
| `.reskate-studio-patch` | yes, with `layout.toc` | text; must contain a line `skate_sha256=<64 hex>` equal to the supported build's hash. The tests use `ReSkate Studio native Patch v1\nskate_sha256=…\n` |
| `Win32\<same relative path as the base>.toc` | yes | a patch TOC listing only the bundles the mod changes |
| `Win32\<package directory>\cas_01.cas` | yes | the mod's payloads, concatenated; no header and no catalogue |
| `manifest.json` | no | `name`, `author`, `version_number`, `description` |
| `reskate-levels.json` | only for added levels | level asset, display name, start point names |

There are no hashes or signatures. The package directory is the lower-cased install chunk name from
the layout; for the tile above it was `configurations/layout/defaultinstallpackage`.

Merge behaviour that shapes the packer:

- When one mod ships a bundle, its copy replaces the base bundle wholesale. The mod's bundle must
  therefore list the manifest and every ebx, res and chunk. Unchanged entries keep their base
  location.
- The patch TOC needs only the changed bundles and no chunks.
- A location is {patch flag, install chunk, archive number}. Patch flag false reads from
  `Data\Win32\…`; true reads from the mod's own folder. Use the same install chunk as the entry
  being replaced, and archive 1.
- The merge renumbers physics materials only for mods that ship a material grid.
- A mod with any merge problem is left out whole. `Mods\.reskate-excluded.json` remembers a failed
  mod until one of its files changes.

A successful load logs: `<mod>: <dir>/cas_1 placed as cas_N`, `Merged N mod(s) into N
superbundle(s) and N archive(s)`, and `Mod <mod>: … ships a layout; no merge problem recorded`.
Failures log `Leaving out <mod>:` or `Mod <mod> was not loaded`.

A park-only mod (`parks\<map>.park.json`, no `layout.toc`) logs `ships no layout` and is not part
of the merge.

## 4. What the packer writes

From source, and exercised by `colltest.cpp` (which reads its own output back before writing).

Sequence, following `Engine/Vfs/Test/item_list_merge_tests.cpp` (`write_mod`):

1. Read the base TOC and find the bundle. Read its region and, from the region's first file, its
   manifest.
2. Decode the target resource, change it, encode it, append it to the mod's archive, and point the
   region file at {patch true, same install chunk, archive 1, offset, size}.
3. Update the manifest entry: `originalSize` = decoded size; `sha1` = SHA-1 of the decoded bytes.
4. Write the manifest, append it to the archive unencoded, and point region file 0 at it.
5. Write the region, then a patch TOC holding that one bundle with the base's load flag.

### Bundle region (`Engine/Resource/binary_bundle.h`, `write_bundle_region`)

Big-endian. A 0x24-byte header, then per file an optional u64 location id followed by u32 offset
and u32 size, then one flag byte per file (0x80: a new id follows; 0x00: same as the previous
file; the first byte also has 0x04). The location id is `(patch ? 1 : 0) << 48 | installChunk <<
16 | archive`. `size` is the stored size.

Region file order: the manifest, then one file per ebx, per resource, per chunk, in manifest order.
Resource i is region file 1 + (ebx count) + i.

Checked on files: all 1,838 bundles of `bam_levelroot.toc` have load flag 1 and no inline manifest.

### Bundle manifest (`write_binary_bundle`)

| Kind | Fields |
|---|---|
| ebx | name, sha1, originalSize |
| resource | name, sha1, originalSize, resourceType, resourceMeta (exactly 16 bytes), resourceId |
| chunk | sha1, guid, logicalOffset, logicalSize |

Plus `chunkMetadata`, an opaque blob to copy through. The physics resource's meta is 16 zero bytes.
The base manifest's sha1 for a resource is not the SHA-1 of its decoded bytes; what it hashes is
unknown and, for a physics resource, does not matter.

### Patch TOC (`Engine/Resource/toc.h`, `write_patch_toc`)

A 0x22C-byte envelope (magic `00 D1 CE 01`, rest zero), then bundles and chunks with rebuilt
perfect hashes; flags 3. Each bundle carries its name, its region bytes and a load flag. A
one-bundle TOC for the tile above is 20,176 bytes. `read_toc` then `verify_toc` checks the result.

### CAS entry (`Engine/Resource/cas_codec.h`, `encode_cas`)

Blocks of at most 0x10000 decoded bytes. Each block has an 8-byte big-endian header:
`decodedSize (24 bits) << 32 | compression << 24 | 0x7 << 20 | payloadSize (20 bits)`, then the
payload. Compression codes: 0x00 raw, 0x11 Kraken, 0x15 Selkie, 0x19 Leviathan. The tests used raw
(code 0x00), which needs no Oodle library, and the game accepted it. The manifest is stored without
any block header.

## 5. Source map

| Piece | ReSkate source |
|---|---|
| MeshSet (skinned reader only) | `Engine/Resource/mesh_set.h`, `.cpp` |
| Physics header, part table, material | `Engine/Resource/material_grid.h`, `.cpp` |
| Bundle region and manifest | `Engine/Resource/binary_bundle.h`, `.cpp` |
| TOC | `Engine/Resource/toc.h`, `.cpp` |
| CAS codec | `Engine/Resource/cas_codec.h`, `.cpp` |
| EBX reader | `Engine/Resource/ebx_document.h` |
| Layout and archive reads | `Engine/Vfs/game_archives.h`, `.cpp`; `game_bundles.h` |
| Mod listing and stamps | `Engine/Vfs/mod_list.cpp`, `mod_catalog.cpp` |
| Merge | `Engine/Vfs/mod_merge.cpp`, `mod_merge_combine.cpp`, `mod_merge_grid.cpp`, `mod_merge_overrides.cpp` |
| Worked example of writing a mod | `Engine/Vfs/Test/item_list_merge_tests.cpp`, `mod_merge_added_assets_tests.cpp` |
| Level list and start point names | `Engine/Game/World/custom_level_manifest.cpp`, `world_model.cpp` |
| Park mod loading | `Extension/Objects/ParkEditor/` |

## 6. Level structure seen in the custom map (not yet writable)

Checked on files, from the stage 1 inventory (`inventory.md` has the full detail).

- The map's root is a `DetachedSubWorldData` with `UseExtendedStartPoints = true`,
  `StartLocalServer = true`, `WorldSizeXZ` 4096 and `WorldSizeY` 1024.
- Tiles are world-partition sublevels whose `LayerData` holds `ObjectReferenceObjectData`
  placements. A placement has a `BlueprintTransform` (identity in the sample: geometry is baked in
  world space) and a `Blueprint` pointer to an `ObjectBlueprint` in another file.
- `StartPointEntityData` has two fields, `Flags` and `StartName`. The map ships none. Its
  `reskate-levels.json` names a start point `DefaultStartPoint`, which appears in no asset; where
  the player lands was not found.

Nothing in this section has been written or tested.

### 6.1 Registering a level (read from the base game; not yet written)

A level is reachable when four things exist:

1. **Its own superbundle.** In `layout.toc`, an entry `{name: "Win32/levels/game/<new>/<new>"}` in
   the top-level `superBundles` list, and the same name in the `superbundles` list of the install
   chunk `configurations/layout/defaultinstallpackage` (persistentIndex 0x7837152C, the chunk mods
   already use). The matching TOC goes under `Win32/`. The base level's TOC holds 1,838 bundles,
   most of them world-partition tiles; the 333 map ships far fewer and is the better minimum.
2. **A level description** at `<level>/description`: a `LevelDescriptionAsset` cloned from bam's
   with a new `LevelName` and `LevelGuid`.
3. **An `OnDemandBundles` entry** `{BundleHash, SuperBundle}` in
   `levels/game/dingolevel_root/dingolevel_root/description`. `BundleHash` is djb2 with xor
   (`h = 5381; h = h * 33 ^ c`, 32-bit) over the lower-cased superbundle path:
   `toc.bundle_hash()`, checked against all five entries in the base game.
4. **A detached sublevel** in `levels/game/dingolevel_root/dingolevel_root_dsublevelmanager_genprefab`
   (a `DetachedSetBlueprint`): a `DetachedSubLevelInfo` {DetachedSubLevelName, StartPoints} and a
   `SubWorldReferenceObjectData` {BundleName, IsDetachedSubLevel true, AutoLoad false,
   StreamRealm 3, ObjectSubVariation 0xFFFFFFFF}.

**Start points.** `LevelStartPoint` has three fields: `Name`, `AutoloadSublevels` (an array of
sub-world paths) and `IsDefault`. Bam's `DefaultStartPoint` autoloads
`Levels/Game/BAM_LevelRoot/StartPoint_Sublevels` and `Levels/game/BAM_LevelRoot/TOD_2_Noon`; the
333 map copies exactly that pair, borrowing bam's start-point logic and time of day.
`StartPointEntityData` (in bam's `startpoint_schematic`) carries only `Flags` and `StartName`, and
`StartPoint_Sublevels` is logic (console-command triggers, sub-world references), not placement.

**Spawn position.** No start-point or spawn asset found carries a transform; the position comes
from game logic. The plan is to reuse bam's `DefaultStartPoint` entry as the 333 map does and have
ReSkate place the skater after load.

**What the 333 map ships.** Four TOCs: `layout.toc`, `Win32/globals.toc`,
`Win32/levels/game/dingolevel_root/dingolevel_root.toc` (the root-level edits) and its own level
TOC. That level TOC holds 4,729 bundles (all load flag 1):

- 4,432 world-partition tiles `wproot/wp/dN_xXXX_zZZZ_sublevel` (N looks like a quadtree depth:
  d1 x1, d2 x402, d3 x1,154, d4 x2,875) and 249 vista tiles under `wpvista/wp/` (all d2);
- six named bundles: the root `<map>/<map>`, `wproot`, `wpvista`, `wpvista_root`, `busstops`,
  `behaviors/effects_0`;
- 42 of bam's bundles listed in its own TOC, all under `bam_levelroot/`: `bam_coregameassets`,
  `startpoint_sublevels`, `vfx_global`, `vfx_season_04`, the `lighting_*` set and the seven
  `tod_*` bundles with their low/medium/high variants.

A first level could be: root, `wproot`, one tile, plus those 42 base bundles. The vista set,
`busstops` and `behaviors/effects_0` look optional. Not yet checked: whether the 42 point at the
base game's own data or are rebuilt copies.

Checked since: they are copies in the level's own TOC (the base superbundle's TOC is not used
when the level loads). `startpoint_sublevels` and `tod_2_noon` keep every payload pointing at the
base archives and only re-store a same-size manifest; `bam_coregameassets` is extended with 1,251
of the map's own files. The map's root bundle is the level's whole shared asset pool (46,948 EBX,
of which about 41,800 are base-game animation, audio, UI and narrative assets duplicated in;
5,132 under `world/custom/<map>/`). `wproot` holds just a `QuadrantSubWorldData` and a
`QuadrantDefinitionAsset`. Open question: whether the base pool must be duplicated or resolves
from bam's bundles.

## 7. Practical notes

- Ground probing with `trainer ground x z` reports the height about 5 ms after the teleport, before
  distant collision has loaded; an unloaded area reads 191.0, which is also sea level on the base
  map. Use the settled reading (a second probe some seconds later, or the `Trainer: sank … under
  the surface at H` line).
- The local park persists between game sessions. A park load that changes nothing logs no
  `Park edit applied` line.
- Smart App Control on the test PC intermittently blocks freshly built unsigned executables. Do
  not work around it; this is the reason for the Python port.
