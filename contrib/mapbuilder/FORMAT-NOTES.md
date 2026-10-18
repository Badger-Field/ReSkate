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
| +80 | u32 | flags; 0x40, 0x41, 0x51 seen. Bit 0 is not "don't draw": the drawn skatepark box's LOD 2 (its proxy LOD) has 0x41, LODs 0 and 1 0x40; util_groundplane's one LOD has 0x41. Perhaps a proxy or low-LOD marker |
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
| +0x138 (312) | u64 | shader-block key: an entry key of the ShaderBlockDepot of the bundle holding the MeshSet (below); LODs of one mesh share keys (checked on files, the PC, 2026-10-18) |
| +324, +328 | u32, u32 | a hash-like word and a small number (box section 0: 0x722325fd, 4); not decoded |
| +332 | float | the section's surface area (the PC's diff: 2,500 for our 50 m plane; 36.96 for the box's section 0) |
| +336 | 3 floats, then 3 floats 16 bytes on | the section's box (found on the box's sections by its own bounds) |

Vertex declaration, at section +112:

| Offset in declaration | Meaning |
|---|---|
| 0 | 16 elements of {u8 usage, u8 format, u8 offset, u8 stream} |
| +64 | stream table: {u8 stride, u8 class} per stream |
| +96 | u8 element count |
| +97 | u8 stream count |

**Formats** (the PC, from Skate.exe's reflection names of `VertexElementFormat`, declaration
order): 0 None, 1 Float, 2 Float2, 3 Float3, 4 Float4, 5 Half, 6 Half2, 7 Half3, 8 Half4, 9 UByteN,
10 Byte4, 11 Byte4N, 12 UByte4, 13 UByte4N, 14 Short, 15 Short2, 16 Short3, 17 Short4, 18 ShortN,
19 Short2N, 20 Short3N, 21 Short4N, 22 UShort2, 23 UShort4, 24 UShort2N, 25 UShort4N, 26 Int, ...
39 UInt4N, 40-43 (U)Comp3(N)_10_10_10, 44-47 (U)Comp3(N)_11_11_10, 48-51 (U)Comp4(N)_10_10_10_2.
`meshset.FORMAT_CODES` reads and writes 1-26.

**Usages on the skatepark box** (LOD 0; the PC): 33 (Half2) is TexCoord0, atlas-like (section 0
u 0.277..0.723, v -0.238..0.238; section 1 u -0.217..1.723); 34 (Half2) TexCoord1 at world scale,
about 0.5 per metre; 51 (UByte4) zeros or (1, 0, 0, 0); 52 (UByte4N) (0, 0, 0 or 255, a), a 56 to
253. No normal or tangent element. An earlier guess here (format 12 = two halves, a UV in usage 51;
6 and 13 packed normal frames) was wrong: our planar UVs went into usage 51 and every vertex
carried one copied TexCoord0, which is why our meshes were one flat colour. The builder now writes
TexCoord0 planar per face from the template section's own minimum at its own density (or
`--uv-per-metre`), TexCoord1 planar at the template's density, zeros to 51 and the template
section's most common value to 52. The section's floats at +88 and +92 (0.0732 and 0.4838 on the
box) look like those two densities (texture coordinate ratios); they are kept from the template.

Element usage and format pairs seen (first survey; meanings above supersede the guesses below).

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

### Shader blocks: why a mesh draws (checked on files and in game, the PC, 2026-10-18)

`MeshMaterial.Shader` is null in every mesh's EBX (the lighthouse's too): shaders are not bound
there. Every bundle carries a pair of resources:

| Type | Name | Content |
|---|---|---|
| 0x73312045 ShaderBlockDepot | `<bundle path>_win32_shaderstate/shaderblockdepot_<decimal>` | header u64 0x10, u64 0, then (u64 key, u64 offset) records; the entries are 24-byte-spaced blocks; not decoded further. bam's root bundle's: 86,348 bytes, id 0x7d48e77663665d5b, meta `304801001c09000003001901c4010000` |
| 0x2FEC6526 | `<bundle path>_win32_shaderstateshaderstateassetid` | 32 bytes |

Other depots exist (`terrain.layergraphs_shaderblockdepot`, `decals.shaderblockdepot`, the
compositor's). A section's key at +0x138 must be an entry of the depot of the MeshSet's own
bundle: util_groundplane's two sections (0x01a30ae610851b95, 0x2ac438e15d40e49e) are in bam
root's depot, the lighthouse's (LOD 0 0x4b735b0ec188d4c5, 0x3584aedbc3fc1c7e; LOD 1
0x5918ad80b9252cd6, 0x948964ab2bcc80ea) in the ftue bundle's; the 6 meshes of tile s50_x425z825
use all 16 entries of its depot. In game: a MeshSet in a bundle whose depot lacks its keys is not
drawn (our `--mesh own` in the ground bundle), and the lighthouse copied into the ground bundle
crashed on a null shader block (`[r15+0x20]`). Carrying the ftue depot pair into the ground
bundle hung the game; adding its shader bytecode, expression shader and textures crashed it: a
depot seems tied to its own bundle. `meshset.depot_records` reads the records; `mesh-refs` prints
each section's key and which depot holds it.

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

A version-2 rewrite of a base `staticmodelgroup.physics` (version 0) crashes the game when the
skater reaches it, even with every part record byte-identical to the base (two runs, same crash: a
bad free in physics teardown; the ground there read 192.7, neither the old nor the new top). Keeping
the version-0 container and appending arrays never crashed. Working theory: that loader reads the
file as version 0 regardless of the version word. Base files must be rewritten as version 0.

Rewritten as version 0 (`rebuild`, `write_physics(version=0)` with the source's part tree), the
same three-part file loads and the skater stands 1.00 m higher at all three spots, no crash. A
rebuild with no raise matches the base byte for byte apart from the shape arrays (fanned into
triangles) and the size-dependent tree pointer.

Version-0 layout (read from four base files): header 0x08 = 0, 0x10 = part count, 0x14 = 1,
0x1C = number of 44-byte entries in the 0x2C table, 0x20 = 4, and 0x24 points at the shape table
(at 0x38; there is no mass block). The data start (0x34) holds a 36-byte block: the world box of
the whole resource (min xyz, max xyz), the top-level part tree's node count (P - 1), a pointer to
that tree (relative to its own position) and a zero. The shape arrays follow grouped by slot across
shapes, each at its natural alignment; the part tree (8-byte nodes) comes last, and the file is
padded to 16. `write_physics(version=0)` writes this form.

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

## 6. Level structure seen in the custom map

Checked on files, from the stage 1 inventory (`inventory.md` has the full detail).

- The map's root is a `DetachedSubWorldData` with `UseExtendedStartPoints = true`,
  `StartLocalServer = true`, `WorldSizeXZ` 4096 and `WorldSizeY` 1024.
- Tiles are world-partition sublevels whose `LayerData` holds `ObjectReferenceObjectData`
  placements. A placement has a `BlueprintTransform` (identity in the sample: geometry is baked in
  world space) and a `Blueprint` pointer to an `ObjectBlueprint` in another file.
- `StartPointEntityData` has two fields, `Flags` and `StartName`. The map ships none. Its
  `reskate-levels.json` names a start point `DefaultStartPoint`, which appears in no asset; where
  the player lands was not found.

Nothing in this section has been tested in game; 6.2 and 6.3 say what is now written.

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

### 6.2 Writing a level (written by `new-level`; loads in game)

Tested in game (3c51cd2, ReSkate's own game copy, only this mod in): ReSkate reports the mod
"registers 1 level(s): levels/game/dingolevel_reskate_test/dingolevel_reskate_test" with no merge
problem; the console command `load levels/game/dingolevel_reskate_test/dingolevel_reskate_test`
loads it unattended at DefaultStartPoint ("Level content loaded: … DingoLevel_Root +
…/dingolevel_reskate_test"), and the ground reads exactly as on bam (199.0 at 627, 1063), as a clone
should. No crash.

`python -m reskate_mapbuilder new-level` (`level.py`) builds a mod that adds a level whose content,
for this first test, is a clone of bam. Everything here is tested only on a synthetic game in
`tests/test_level.py`; nothing has been read from the real files or loaded in game.

| Piece | What is written |
|---|---|
| `layout.toc` | the game's, rewritten from its parse (the unchanged parse must rewrite to the same bytes, or the command stops), plus `{name: "Win32/levels/game/<new>/<new>"}` in `superBundles` and the same string in `defaultinstallpackage`'s `superbundles`, each inserted in name order (lower-cased compare), as the C++ merge inserts them |
| `Win32/levels/game/<new>/<new>.toc` | every bundle of `bam_levelroot.toc` except its root, region copied as is (manifest and payloads stay in the base archives), and bam's TOC chunk list |
| root bundle `win32/levels/game/<new>/<new>` | bam's root bundle under the new name: its manifest rewritten into the mod's archive with one more EBX, `levels/game/<new>/<new>`, a clone of bam's root (new file GUID, new GUIDs for exported instances, `Name` renamed, `WorldSizeXZ` 4096, `WorldSizeY` 1024, `StartLocalServer`, `UseExtendedStartPoints`) |
| `levels/game/<new>/<new>/description` | a clone of bam's description, added to the bundle that holds bam's (found by search, globals first); `Name` and `LevelName` renamed; `LevelGuid` set to the new root's file GUID or instance GUID when bam's equals its root's, otherwise a new GUID |
| root description | one `OnDemandBundles` entry copied from bam's own, the one whose `SuperBundle` is `levels/game/BAM_LevelRoot/BAM_LevelRoot` ignoring case (the list also holds others naming bam, such as `Levels/game/BAM_LevelRoot/_DEBUG_/BuildkitPerfTests`, which comes first), renamed to `levels/game/<new>/<new>`, `BundleHash` = `bundle_hash` of it; appended last, as the 333 map's single entry is |
| genprefab | bam's `DetachedSubLevelInfo` (the one ending `BAM_LevelRoot/BAM_LevelRoot`) appended to the same array, renamed, start points cut to `DefaultStartPoint` autoloading the 333 map's pair; bam's `SubWorldReferenceObjectData` copied as a new instance right after the last one of its kind (later instances move up one, as in the 333 map, where it is #4), with a new GUID, appended to every pointer array holding bam's (`Objects`), `BundleName` renamed, the 6.1 values, identity `BlueprintTransform`, null `Blueprint` and `Template`, and `Flags` from its GUID (below); no connections |
| `reskate-levels.json` | `{"schema": 1, "levels": [{"asset": "levels/game/<new>/<new>", "displayName": …, "startPoints": ["DefaultStartPoint"]}]}` (the format `custom_level_manifest.cpp` reads; it also marks the mod as a map for the merge) |

New EBX are stored like resources: raw CAS blocks in the mod's archive of the bundle manifest's
install chunk, listed after the bundle's other EBX, with `originalSize` and `sha1` = SHA-1 of the
document (whether the game checks an EBX's sha1 is unknown; it does not check a resource's). New
GUIDs are MD5s of the level name and the source GUID, so a rebuild gives the same ones.

Guesses, each a constant at the top of `level.py` and each printed by `--dry-run`:

- bam's description is named `levels/game/bam_levelroot/bam_levelroot/description`, and it and the
  root's description are in `globals` (from `mod_merge.cpp` and `live_mods.h`).
- The new description belongs in the same bundle as bam's.
- The field and type names `OnDemandBundles`, `SuperBundle`, `BundleHash`, `DetachedSubLevelInfo`,
  `DetachedSubLevelName`, `StartPoints`, `SubWorldReferenceObjectData`, `BundleName` and the root's
  fields (from the 333 inventory; a missing field is reported, not fatal, except where the step
  needs it).
- `DetachedSubLevelName` and `BundleName` take bam's value with its level segment swapped
  (`Levels/Game/<new>/<new>`).
- `Flags` of a `SubWorldReferenceObjectData` (checked on files, the PC's dumps of bam's and the 333
  map's genprefab): the low 24 bits are the low 24 bits of the instance GUID's first group as
  printed (`6ab69b78` gives 0xB69B78, bam's), sometimes with bit 24 set (`8fd8c2da` gives
  0x1D8C2DA; the 333 map's own `4914e18a` gives 0x114E18A and works). That group is the GUID's
  first four stored bytes read little-endian. The copy gets
  `(template Flags & 0xFF000000) | (first group & 0xFFFFFF)`; the dry run says whether each
  existing entry follows the rule.
- The copy is not wired into the blueprint's event, link or property connections; the 333 map's
  genprefab has none at all.
- Copying all 1,838 bam bundles (rather than the 333 map's 42 plus its own) keeps every sub-world
  bam's root names resolvable; `COPY_BASE_BUNDLES` selects fewer.
- bam's TOC chunk list is carried into the new TOC (`COPY_BASE_CHUNKS`).

**Rewriting unchanged real documents.** On the game's files the writer's output differed from
the input for all four documents `new-level` edits; `layout.toc` rewrote identically. Three causes,
the third a data-loss bug fixed on 2026-10-16 in both the Python port and ReSkate's C++ writer
(`Engine/Resource/ebx_writer.cpp`, which the mod merge uses on real documents):

- Ordering. bam's description, the root description and the genprefab read back to the same
  values at the same size. The 333 map's genprefab rewrites byte for byte, so its tool writes the
  same order this writer does.
- Array hashes (fixed the same day, in both writers). Each array record's hash used to be taken
  from the first record the document read with the same element type, which changed 346 records
  of bam's root. On the game's files (checked on bam's root with 414 records, bam's description,
  the root description and the genprefab):
  - Every (holding type, field) pair carries exactly one hash, and no hash is shared between
    fields. `SubWorldData.Components` 0x4100379e and `ComponentEntityData.Components` 0x7d532392
    share a field name hash but not an array hash.
  - Every record's hash is a key of the RFL2 group table: 12-byte `{hash, count, first}` records
    sorted by hash, with `first[i + 1] = first[i] + count[i]`.
  - The group table is followed by a u32 count and 8-byte `{hash, code}` member pairs (probably
    member name hash and type code); the record's class column is never inside its group's range.

  The table lists the array types a document uses; it does not say how a hash is made.
  djb2, FNV-1, FNV-1a and the TOC hash of field, type and element names match nothing, nor do
  the reflection's own name hashes, signatures or GUIDs. So:
  - An array read from a record keeps that record's hash through edits and copies
    (`FieldValue.array_hash` / `Value::arrayHash`).
  - A new array takes the hash of an array the document read for the same field of the same
    holding type (instances and nested structs; boxed values are not searched).
  - With none, the map builder's writes (`write_document(..., refuse_new_array_hashes=True)`)
    refuse, and refuse too if the hash is not a key of the group table, which would need a new
    group.
  - The C++ default (`write_document(doc)`, the mod merge) still borrows by the old rule rather
    than fail a merge.
- Boxed values (the bug). A boxed value's bytes were copied as they were, pointers included, but
  the arrays and strings those pointers reach were written only when a field reached them too.
  So a rewrite dropped them and left the boxed value's pointers aimed at whatever moved into
  their old place. On bam's root (271,012 bytes, 88 instances) that lost the string
  `Gameflow test running` and about 1,120 bytes of arrays. The writer now carries everything a
  boxed value's pointers reach: arrays, with their own record and hash and with nested pointers
  followed; strings; instances; other boxed values. Each pointer is aimed at the new place. To
  do that a read document keeps its EBXD bytes (`payload`). A pointer whose target is none of
  those keeps its old displacement, as before. Reproduced synthetically
  (`boxed_pointing` in `tests/test_ebx_crosscheck.py`; both writers now agree on it). Not yet
  re-run on bam's root.

`ebx-roundtrip` reports, for any document: both sizes; the first differing byte and its region
(header, instance data with the instance and field, arrays, boxed values, strings, fixup tables,
array records, reflection); each table compared; and whether the rewrite reads back to the same
values. The comparison of values follows boxed values' pointers. `--detail [N]` adds the arrays
and strings found on only one side and the differing array records, each with what reaches it.
`new-level` refuses to edit a document that does not read back to the same values.

Read-only checks: `new-level --dry-run` reads, edits and encodes everything in memory and prints
each source document's rewrite check, the GUIDs, hashes and bundle counts, and writes nothing.
`dump-ebx` prints a document and `ebx-roundtrip` diagnoses its rewrite; with `--mod <folder>` it reads a mod's TOCs and archives over the
game's, which is how the 333 map's own registration can be compared with ours.

### 6.3 A level with its own ground (written by `new-ground-level`; collision tested in game)

**Tested in game** (b1bfca5, `--bam all`, defaults): the level registers and loads, the log says
`Subworld loaded: levels/game/dingolevel_reskate_ground/ground_sublevel`, and our collision holds
with the placement's transform applied: `trainer ground 0 0` and `20 -20` read 300.0 (the skater
landed at 301), `40 0` (outside the 50 m square) reads 191 (sea). No crash. So a copied blueprint
under a new name finds a new `<name>.physics` beside it, and the OROD's transform moves the
collision (`PHYSICS_IN_OBJECT_SPACE` is right). **Nothing is drawn**: the skater stands in the air
over the ocean; util_groundplane's plane does not show. `--bam no-world` and `333` refused: bam's
root #0 holds direct pointer fields to some of the references they remove (`#0 still points at
[6]`; with 333 `[1, 6, 17, 19, 23, 33, 65, 66]`). Since then such references are kept (reported as
`KEPT …`) and only references held by nothing but pointer arrays are removed.


**Tested in game** (d102c07): `--bam no-world` builds (12 references removed, one kept: #6
`Debug_RenderTest`, held by `#0.EventConnections[0/1].Target`) and loads; our collision reads 300,
bam's terrain and water are still there (40, 0 reads 191). `--bam 333` builds (18 removed, 8
kept through event and property connections) and loads with no crash into an **empty world** (sky
and haze only); the skater stands at 301 on our invisible collision, and outside the square the
game says the ground is still loading. So our collision works in a level holding nothing of
bam's world. Not drawn in any run. `--visual template` was inconclusive: the worklight's blueprint
and mesh live in the template tile's own bundle, which the level never loads. util_groundplane's
`StaticModelEntityData` has `Visible` and `Enabled` true; it has no `MeshVTComponentData`, which
the lighthouse `world/plots/iog_island/iog_lighthouse_island_02` (superbundle
`levels/game/dingolevel_ftue_island/dingolevel_ftue_island`) has. The lighthouse as template with
`--bam 333` crashed right after "Level content loaded": access violation reading 0x20 in a loop over
384-byte records (MeshSet sections) with a null pointer, most likely its geometry chunks, which
that superbundle's TOC chunk list holds and ours did not.

**Chunks since then.** For a blueprint from another bundle, every chunk GUID that occurs in the
bytes of the resources listed with it (MeshSet LOD chunks, textures) is listed as it is in the
ground bundle when its bundle has it and in the level's TOC chunk list when the source
superbundle's TOC has it (the source superbundle's TOC is not read when our level loads; the 333
map's level TOC lists 80,006 chunks). `--mesh own` chunks go into both, the TOC entry pointing at
its own copy in the mod's archive. The dry run ends with one line per LOD of each mesh drawn:
where the base game keeps its chunk (bundle entries, TOC chunk list) and what of ours lists it
(the level's TOC, the ground bundle, the level's root bundle, or NOTHING). util_groundplane_mesh's
chunk: the line says; when it is in bam's TOC chunk list it is already in ours (`COPY_BASE_CHUNKS`).

**Tested in game** (3251539, `--bam 333`): util_groundplane with `--mesh own` loads, no crash,
the skater stands at 301 on our collision (three runs); nothing is drawn, not even a shadow. The
lighthouse with every chunk its resources name in the level's TOC crashes as before, after "Level
content loaded": a read at 0x20 through a null pointer (`mov rcx,[r15+0x20]`, r15 = 0) in a loop
that walks 12-byte records and then section structs (index x 384). The PC's reading: a per-section
object (material, shader, VT) resolves to nothing because our level does not load its data. The
lighthouse has a `MeshVTComponentData`, util_groundplane none.

**Looking for that object.** `mesh-refs` (read-only) prints, for one RigidMeshAsset: its document
and imports, each resolved to an EBX name in the bundles scanned (`--scan`); its MeshSet's structs
and every value in the MeshSet's bytes that matches a known GUID (chunk entry, TOC chunk, EBX
file, import, instance), a resource id of a scanned bundle, or a 32-bit hash (djb2 both ways,
FNV-1, FNV-1a, as written and lower-case) of a material name or of a name in the mesh's bundle,
each with the struct and offset it sits at; the bundle's resources by type; the entries named like
the mesh or like shader, block, depot, material, VT or surface data; and with `--mod <ours>
--level <name>`, whether our level lists each of them. Nothing of the section struct beyond
FORMAT-NOTES 1 is decoded yet: this output, on util_groundplane_mesh, the lighthouse's mesh and
one 333 `…_col_N_0`, is what will decode it.

Changes made on the strength of the crash, both guesses:

- Resources named under a listed EBX (`<name>/…`) are now listed with it (per-mesh data such as
  shader blocks, if named that way).
- `--asset-bundle coregameassets` puts the blueprint, its collision and our mesh into the level's
  copy of bam's `bam_coregameassets` (which keeps its name; the copy turns into an edited one),
  leaving only tile and layer in the ground bundle. If shader or material data is found per
  bundle name, our renamed root bundle and our new bundle have none, while bam's own
  `game_coremeshes` layer placing util_groundplane lives in `bam_coregameassets`, and the 333 map
  adds 1,251 of its own files to that bundle.

**Since the shader-block finding** (the subsection "Shader blocks" of section 1):

- A blueprint copied from another bundle is refused when its MeshSet's keys are not in a depot of
  the bundle the copy goes to (the lighthouse case). `--mesh own` warns `NOT DRAWN` when its keys
  are not in that bundle's depots. The dry runs print each mesh's keys and how many its own
  bundle's depots hold.
- `--asset-bundle root` puts the blueprint, its collision and our mesh into the level's root
  bundle, the copy of bam's root bundle, which keeps bam root's depot (util_groundplane, a mesh of
  that bundle, resolves in our level).
- **Stock props** (`--prop BLUEPRINT --prop-at X,Y,Z`, repeatable, also on `new-place-level`): a
  blueprint of bam's root bundle placed unchanged by one more OROD in our layer (identity
  rotation, scale 1, at the given game position), importing its root instance. Its mesh, MeshSet
  and `.physics` stay in the level's root bundle; nothing is copied, so its part count matches its
  `PhysicsPartInfos` and its keys are in the depot beside it. The dry run prints its collision
  (shapes, parts, materials) and its mesh's keys. A PC test of the skatepark box as our own
  blueprint (one part instead of its three) showed neither collision nor mesh.
- **A stock material for our geometry** (`--mesh own --shader-from MESH[:SECTION] --asset-bundle
  root`): every section of our MeshSet takes the donor section's bytes per vertex (+30) and its
  bytes +112..+0x140 (the vertex declaration, the undecoded words after it, and the shader-block
  key), and every vertex is the donor section's first vertex (each stream) with our position. The
  dry run prints the donor's layout: elements (usage, format, offset, stream) and stream strides.
  GUESS: the words between the declaration and the key belong to the shader's vertex layout too;
  the per-section box after the key stays the template's.

**Tested in game** (6239cf7, `--bam 333`): a stock prop is drawn. `--prop
world/props/common/skatepark/cmn_prp_skatepark_01/kit/cmn_prp_skatepark_01_box_100x600x600cm_hxwxd`
(mesh `…_mesh`: res id 0x72f42653b1abadcd, 5,040 bytes, 3 LODs, box [0, 0, -6]..[6, 1, 0], 9 of 9
section keys in bam root's depot; `.physics` version 2, 3 shapes, 3 parts, materials 0x10e0 and
0xee0) at (10, 300, 10): the wooden box is drawn, the first visible object in our own level;
`trainer ground` reads 301 on it (13, 7), the skater lands on it, and 300 beside it (6, 2) and at
(0, 0). Our own plane with `--shader-from` that box's section 0 (key 0xd6314085b7e6bf9a, 24 bytes
per vertex, elements (1,8,0,0), (33,6,0,1), (34,6,0,2), (51,12,0,3), (52,13,0,4), strides 8, 4,
4, 4, 4; section 0 of 4) or section 1 (key 0x097a8959cdcccd55) in the root bundle: collision
fine, not drawn. (PowerShell passes `10,300,10` as three arguments; `--at` and `--prop-at` now take
X Y Z, X,Y,Z or X;Y;Z, in one argument or three.)

**Own mesh on a drawn blueprint** (since then): `--mesh own --blueprint-template <stock blueprint>`
copies that blueprint (MeshVTComponentData, PhysicsPartInfos and all) and starts our MeshSet from
its mesh's own bytes, so every header, LOD and section field is the drawn mesh's except: the
header box; per LOD the index and vertex byte counts and the chunk GUID (a new chunk per LOD, in
the bundle and the level's TOC); per section the triangle count, first index, vertex start and
vertex count; and the boxes found in the LOD and section structs (three floats equal to the
template's own bounds and three equal to their maximum 12 or 16 bytes on; the dry run says where).
`--keep-section N` makes each LOD list only its section N (the LOD's count set to 1 and its table
pointer moved to that struct; the header's total set to the LOD count). Vertices are written in
each section's own layout: positions ours (format 3 float32 x3, or format 8 float16 x4 with the
fourth half kept), UVs ours (usage 51, format 12: float16 x2, x / 6 and z / 6), everything else
(usage 33 and 34, format 6, and usage 52, format 13: 4 bytes each, not decoded) from the section's
most up-facing vertex. `--mesh-shape box` writes a closed box the size of the template mesh's
box; `--mesh-shape donor-copy` writes the template's own geometry and vertex bytes into the new
chunks (if that is not drawn, the plumbing is at fault, not our geometry). `--collision template`
carries the template's `.physics` unchanged under our blueprint's name; `--collision own` (the
default) writes our square split into as many strips as the template has parts, each part copying
its template part's material and words. `meshset-diff` prints every 4-byte word that differs
between two MeshSets' headers, LOD and section structs, with field names where known.

**Tested in game** (cd6d149): our plane cloned from the box with `--keep-section 0` (each LOD
listing one section) crashed 33 s in, right after "Level content loaded": a read through a garbage
index (`mov rdx,[rax+rcx*8]`, Skate+0x506d2c2) in code walking 4 and 3 sections, that is the
template's section counts, which something other than the LOD structs still holds (the
RigidMeshAsset's Materials, the blueprint's tables, the header's +184/+188 words). `meshset-diff`
of the 6239cf7 plane against the box: the plane had 1 LOD of 2 sections (header +180
0x00020001) against the box's 3 LODs of 4, 3 and 2 (0x00090003); header +56/+64 are pointers in
the box and zero in ours; +184/+188 are 0x40000 and 7 in the box; LOD +80 was 0x41 (from
util_groundplane) against 0x40; the section's +88/+92 floats, +324, +328 and +332 differ.

Since then `--keep-section` is refused, and every section is kept: our geometry goes into
`--geometry-section` (default 0) of each LOD, and every other section keeps its struct, material
and layout and draws one zero-area triangle on one vertex of its own, inside our buffers
(`--other-sections geometry` puts our geometry in every section instead). Section +332 gets our
area (0 for those triangles) when every template section's +332 equals its own area. The dry run
lists every LOD (flags, the six floats at +140) and section (triangles, vertices, material name,
+324, +328, +332, key, strides) of the template and of ours.

**Tested in game** (cf7e536, the skatepark box as template, all sections kept, `--bam 333`): our
own geometry is drawn. The box's LOD 0 has 4 sections (Skatepark_Surface_M 44 triangles / 51
vertices, Sides_M 76/99, Trim_Edges_M 48/96, Surface_M_Shadow 120/62 with stride 8), LOD 1 3, LOD 2
2 (Surface_proxy 12/24, Surface_proxy_Shadow 12/8). `--mesh-shape donor-copy` (the box's own data
in our chunks) spawns and is drawn with its wood and shadow, ground 301 at (3, -3), the skater
lands on it; `--mesh-shape box` (our own generated box) spawns and is drawn, flat tan (constant
UVs and frames), lit, with the skater's shadow, ground 301. The 50 m plane (box flat in y, 50 m
across) does not spawn at all: no drawing, no collision ("still loading" everywhere), as an
earlier box blueprint with our own collision did.

Since then: the default `--mesh-shape slab` is a closed `--size W,H,D` box (default 50 x 0.2 x 50)
whose top is at the ground's height; no bounds are written flat (any axis under 0.1 m is padded to
±0.05 m); each vertex takes the normal, tangent and colour bytes of the template vertex (in any
section of the same layout of its LOD) whose face normal is closest to its own, and planar UVs per
face (x, z on top and bottom; z, y facing x; x, y facing z) at the template section's own texel
density (UV length over edge length); the dry run scores reading formats 6 and 13 as 8-bit normals
against the template's face normals (report only), prints the blueprint's and mesh's fields named
like bounds, distances, sizes, and sets any Min/Max box struct to our bounds; our collision strips
cover the same W x D and each part's box is printed. Ladder for the size limit: 6,1,6 (as the
working box), 12,0.2,12, 50,0.2,50.

**Tested in game** (ae03ddd): the 50 x 0.2 x 50 slab is drawn (a flat tan floor to the horizon with
the skater's shadow; at the box's own texel density, 0.1667 per metre, no grain shows). Its
collision was the box's, not ours: `trainer ground` 1, -1 read 301 (the box's top) and 0, 0 "still
loading". So 50 m objects do spawn (cf7e536's plane did too: it was probed outside the box's
footprint). The box blueprint imports its collision EBX, `<blueprint>.physics` (beside the
resource of that name), and the copy kept that import; util_groundplane's copy took our resource,
found by name. Since then, a blueprint's import of its `.physics` EBX is moved to a copy of that EBX
(renamed, its resource references set to ours) with `--collision own`; the dry run lists every
import of our blueprint and where it resolves, and refuses one still naming the template's mesh or
collision. `--uv-per-metre` sets the ground's texture density (default 1 for slab and plane).

Formats 6 and 13 are not plain 8-bit xyz normals: on the box, unorm, snorm and reversed readings
agree with the face normals at -0.16 to 0.26. The dry run now also scores octahedral 16-bit
(snorm and unorm, z or y up) and 8-bit quaternions (each axis), as one line per element; our
vertices copy matched template vertices whatever the format is.

**What the base game does (checked on files, the PC's read-only scans).**

- bam's world-partition tiles place their geometry through merged `StaticModelGroup`s. The first
  template tried (the tile of 2.5) has no `ObjectReferenceObjectData` (OROD) at all, so the first
  version of this command refused on every dry run.
- A scan of all 23 base superbundles (185,301 EBX, no read errors) found the 333 map's kind of
  placement in the base game too, one document further down: 127 of bam's tiles are
  `QuadrantSubWorldData` -> `LayerReferenceObjectData` -> `LayerData` (a separate EBX in the
  tile's bundle) -> OROD -> `ObjectBlueprint`. 1,519 of 1,523 `QuadrantSubWorldData` have exactly
  one `LayerReferenceObjectData`.
- Example, the default template here (all in `levels/game/bam_levelroot/bam_levelroot`): the tile
  `…/wproot/wp/s9600_x0z0/s200_x500z900/s100_x450z850/s50_x425z825/s50_x425z825_sublevel` (own
  bundle, 18,636 bytes: `QuadrantSubWorldData`, one `LayerReferenceObjectData`, a StaticModelGroup
  with its components, audio tags, `InterfaceDescriptorData`; rewrite semantically equal, array
  hashes all in the group table). Its `LayerReferenceObjectData.Blueprint` imports
  `…/s50_x425z825_layer` in the same bundle (7,338 bytes: `LayerData` and one OROD placing
  `world/lighting/common/construction/cmn_lgt_worklight_01/cmn_lgt_worklight_01`; rewrites
  identically).
- Another layer: `levels/game/bam_levelroot/game_coremeshes` in `bam_coregameassets` (7,256 bytes,
  one OROD placing `common/utilities/meshes/util_groundplane`, scale 0.05 with a rotation, at
  355.60, 209.13, 1469.20; rewrites identically).
- `common/utilities/meshes/util_groundplane`, in bam's root bundle (9,690 bytes, 10 instances:
  `ObjectBlueprint`, `PartData`, `StaticModelHealthComponentData`, `FBPhysicsComponentData`,
  `StaticModelEntityData`, `SourcePartMappingComponentData`, `ConsoleCommandTriggerEntityData`,
  `PartState`, `InterfaceDescriptorData`, `BoolEntityData`; rewrite semantically equal, 9,690 to
  9,658 bytes). Its mesh `common/utilities/meshes/util_groundplane_mesh` is a 1,376-byte MeshSet,
  1 LOD, 2 sections, mesh type 0, meta `20050000380000000000000000000000`, box
  [-25, 0, -25]..[25, 0, 25]: a flat 50 x 50 m plane. `PhysicsPartInfos` has one entry.
- **Collision is found by name.** No `ResourceReference` in the blueprint names its physics; the
  resource is `<blueprint name>.physics` in the blueprint's bundle
  (`common/utilities/meshes/util_groundplane.physics`); 9,661 of 10,883 base blueprints have one.

**What is written.** The level is registered as in 6.2, plus one bundle
`win32/levels/game/<new>/ground_sublevel` (load flag 1) holding, in this order:

| Entry | Content |
|---|---|
| `levels/game/<new>/ground_sublevel` | the tile copied (new file GUID, new exported instance GUIDs, `Flags` renewed where they followed the GUID rule), every pointer array on the chain from its root to its `LayerReferenceObjectData` cut to that element, everything the root no longer reaches removed (what boxed values reach is kept), unused imports dropped (kept when boxed values exist); the reference's `Blueprint` imports our layer (the instance the template's import named, mapped to the copy); root `Name` = the EBX name |
| `levels/game/<new>/ground_layer` | the tile's layer (or `--layer-template`) copied the same way and cut to its first OROD, which gets `BlueprintTransform` identity rotation and scale 1 at `--at`, and imports our blueprint's root instance. `--tiles N` makes N x N copies of the OROD `--size` metres apart around `--at` (new GUIDs and Flags), appended to the arrays holding it |
| `levels/game/<new>/ground_bp` | `util_groundplane` copied, `Name` renamed; its mesh import kept, so the base plane is drawn (it lives in bam's root bundle, which the level's root bundle copies in every `--bam` mode) |
| res `levels/game/<new>/ground_bp.physics` | type 0x41759364, meta copied from the template's, id = first 8 bytes of MD5 of the name; version 2, one shape, one kind-5 part: a flat `--size` square (default 50, as the mesh) centred on the origin at local height 0, 5 m cells, normals up, edge data by 2.3, zeroed tree; material, hashes and words +0x30, +0x3C, +0x44 from the template's first kind-5 part, +0x34 = 0 |
| level root | as before: per `SubWorldReferenceObjectData`, its `BundleName` and whether the TOC lists it; one is copied (preferring non-detached with `AutoLoad`), `BundleName` = our sublevel, `AutoLoad` true, `IsDetachedSubLevel` false, identity transform, `Blueprint` an import of our tile's root when the copied one's was an import, `Template` null, `Flags` by the GUID rule |
| `--bam` | `all` (default; only additions to 6.2), `no-world` (bam's `wproot`/`wpvista` bundles left out), `333` (`bam_coregameassets`, `startpoint_sublevels`, `vfx_global`, `vfx_season_04`, `lighting_*`, `tod_*`). Except with `all`, root references to bundles the TOC does not list are removed (refused if anything but a pointer array points at them) |

New documents must read back to themselves and rewrite identically, or nothing is written. The
dry run prints the chain it found (tile, its layer reference and layer, each OROD and what it
places, the blueprint's instances, imports and the MeshSet box, the template collision) and each
source's rewrite check.

Guesses, each a constant at the top of `ground.py` and reported by `--dry-run`:

- **The ground is loaded as a plain sub-world** by the root's new reference, though bam's tiles
  are loaded by the world partition under `wproot`. A `wproot` of our own
  (`QuadrantSubWorldData` + `QuadrantDefinitionAsset`) is not written.
- An import of a blueprint names the blueprint's root instance.
- A new resource id may be any unused 64-bit value.
- `PHYSICS_IN_OBJECT_SPACE`: the OROD's transform moves the collision as it moves the mesh. If
  not, the collision is at height 0 below `--at`'s x, z. (Scaling an OROD to stretch the plane
  is not offered for the same reason.)
- `--at` (0, 300, 0) is above bam's ground (191 to 200 m) when bam's world stays.
- Our three EBX first, then the collision, is an acceptable bundle order.

**Finding why nothing is drawn** (options, all reported by the dry run):

- The dry run prints, per instance of the blueprint, its bool fields and any field named like
  visible, enable, render, debug, hidden, editor, draw, shadow, lod or cull.
- `--visual template` keeps the layer's own placement (of its own blueprint, for the default layer
  the worklight `cmn_lgt_worklight_01`) beside ours at `--at` + (10, 0, 10), rotation and scale as
  they were: whether anything placed by our sublevel is drawn. (Its blueprint lives in another
  bundle, which must be loaded for it to show; the report says so.)
- `--blueprint-template` with `--blueprint-bundle` (and `--blueprint-superbundle`) takes any
  blueprint with a `<name>.physics` beside it, e.g. a visible world object. When its bundle is not
  bam's root bundle (which the level's root bundle copies and loads), what it imports from its
  bundle is listed in the ground bundle as it is: EBX followed through imports, resources named
  like them (MeshSet, textures), chunks whose GUID their bytes hold. A world-partition bundle is
  listed in the level's TOC but loaded only when streamed, so it is copied in every `--bam` mode.

**`--mesh own`: a MeshSet of our own** (written, untested in game; off by default). The template is
the blueprint's mesh (the first import whose EBX has a MeshSet of the same name in the blueprint's
bundle). `meshset.write_mesh`:

- refuses unless the template is the rigid layout (192/176/384), mesh type 0, 16-bit indices, triangle lists, a position element of format 3 or 8, and its chunk rebuilds byte for
  byte from the layout (each section's streams at its vertex start, the index buffer at the LOD's
  vertex bytes, every gap zero);
- writes our plane (the collision's grid, `--size`, 5 m cells) into every section of every LOD,
  one new chunk per LOD (the PC's dry run: util_groundplane_mesh, 2 sections of 9 vertices and 8
  triangles, its 352-byte chunk rebuilds identically; the lighthouse has 2 LODs): per section,
  aligned to 16, each stream's vertices, each vertex a copy of the template section's first
  vertex with the position replaced; then zero padding to 16 (the LOD's vertex bytes), the index
  buffer (each section's indices relative to its first vertex, `first index` cumulative), and zero
  padding of the chunk to 16. Triangles take the template's winding (seen from +y);
- patches only: the header box, each LOD's index and vertex byte counts and chunk GUID, and each
  section's triangle count, first index, vertex start and vertex count. Everything else (names,
  declarations, material references, the undecoded per-section box, unknown words) and the size
  stay the template's, so the template's resource meta is reused as is. The dry run says when our
  box differs from the template's (each section's own box then still says the template's).

With it come a copy of the RigidMeshAsset (`levels/game/<new>/ground_mesh`, `Name` renamed, any
`ResourceReference` equal to the template MeshSet's id set to ours), the MeshSet resource of the
same name (template meta), per LOD a new chunk row (`logicalOffset` 0, `logicalSize` its size, sha1
of it) and the same chunk in the level's TOC, and the blueprint's import of the template mesh moved
to the copy. Not offered: copying the lighthouse's `MeshVTComponentData` into util_groundplane's
copy, since an EBX document carries only the reflection of the types it uses and the writer cannot
add a type; the lighthouse itself with `--mesh own` gives a blueprint that has the component. Guesses: the MeshSet's
inner name strings need not change; the RigidMeshAsset names its MeshSet by id or else by name;
normals and UVs of the template's first vertex suit a flat plane; a chunk in the bundle's
manifest is found as the template's is.

**Next checks on the PC:** `--bam no-world` and `--bam 333` (dry run, then in game); a run with
`--visual template`; a run with a visible blueprint (`--blueprint-template … --blueprint-bundle
…`); the `--mesh own` dry run. In game each time: `load levels/game/<new>/<new>`, `trainer
ground 0 0` twice (300 expected), and what is drawn at (0, 300, 0) and (10, 300, 10).

### 6.4 A real place's collision (written by `new-place-level`; not yet run in game)

`python -m reskate_mapbuilder new-place-level --tiles DIR` (`place.py`) is `new-ground-level`
with the flat square replaced by the collision of a place exported by `contrib/lidar-parks`
(`city --level DIR`, format `reskate-level-tiles` version 2; the format's text is the docstring of
`reskate_lidar/levelexport.py` on the `real-cities` branch). Tested on a synthetic tile directory
in `tests/test_place.py`.

**Axes.** The tiles are in metres, x east, y up, z north, with y = 0 at the origin's elevation and
the origin at the area's centre. That is the frame ReSkate's park mods are placed in
(`park.to_game` maps a placement's (east, north, up) to game (x, z, y), no mirror), so place to
game is a translation: game = place + `--at` (default (0, 300, 0), as the ground's). The
translation is the OROD's `BlueprintTransform`; the collision stays in the place's frame, as the
square did (the transform moving the collision is tested in game, 6.3). Whether the game's z
really is north (and not mirrored) is not tested: `--mirror` flips z and swaps each triangle's
corners so it still faces out. The exporter's winding, (b - a) x (c - a) pointing out of the
surface, is the one physics.py writes; the up-facing square held the skater.

**What is written.** Everything as 6.3, but the collision resource holds one shape and one part
per collision chunk (at most 256 triangles each, welded by the exporter with bit-identical shared
vertices, so edge data finds twins across chunks and tiles), each part a copy of the template
part's material and words, in the place's frame. GUESS: word +0x34 gets the part's index when
there is more than one part (it differs per part in base multi-part files; zeros crashed a
version-0 rewrite; a version-2 file with many parts has not been written before). Every chunk gets
the same material; the tiles' own materials (ground, concrete, wood, metal, grass) are counted in
the summary for a later material map.

**Refused**, with the reason: a missing `level.json`, a format other than `reskate-level-tiles`,
a version other than 2, units other than metres, no tiles, a tile or `.bin` missing, an object's
data past the end of its `.bin`, an index past its vertices, a non-finite vertex, a collision
chunk too large for one shape (21,845 triangles), a triangle repeating a corner, and the
exporter's own report of edges a half-edge shape cannot hold (`edges_same_direction_twice`,
`edges_shared_by_3_or_more` non-zero).

**Printed:** tiles, chunks, triangles and vertices, the bounds in both frames, collision by
material and by part, the origin and spawn, and a list of `trainer ground X Z` spots (the spawn,
the origin, the bounds' centre and quarters, and one outside) with the height the collision
gives there (the highest surface above the point; walls seen from above are skipped). The spawn
itself is game logic (6.1); the skater is put there with `trainer ground`.

**The first real export** (Embarcadero Plaza 130 x 20, the `level-exports` branch, sha256
8fc41c8e…f022b6041; not in this repository) reads without a change: 4 tiles, 39 collision chunks,
5,320 collision triangles, 3,428 vertices (largest chunk 175 triangles), 13 visual objects
(20,920 triangles); collision by part ground 32, bench 4, steps 2, buildings 1; by material ground
32, wood 4, concrete 3; bounds x -65..65, y -0.68..159.06, z -25..-0.47; spawn (0, -0.522, -15);
6 grinds. The one buildings chunk is a clipped tower whose wall rises to 159 m at the west end.
Written with util_groundplane's part as template it is a 239,264-byte version-2 resource of 39
shapes and 39 parts (one per chunk, every part util_groundplane's material and words, +0x34 its
index: still a guess, as no many-part version-2 file has been loaded), 3,252 of 15,960 half-edges
flat across chunk seams; 0.1 s.

`place-info --tiles DIR` prints the summary and the `trainer ground` spots without the game.
`--at` now defaults to putting the export's spawn at the game's x 0, z 0 with the origin at
height 300 (so the skater teleported to `trainer ground 0 0` stands at the spawn); the spots
include each of the first three grinds' midpoints with the grind's top. `--max-height M` drops
collision chunks whose top is above M (whole chunks: a chunk is one welded shape), `--skip-part
P` drops a part's or object's chunks; collision above 20 m is noted.

**Tested in game** (3e0afd2): the Embarcadero level loads without a crash but has no collision at
all ("still loading" everywhere): 38 parts under util_groundplane's copy, whose blueprint declares
one (`PhysicsPartInfos`). The skatepark box declares 3 and our 3-part file worked (4d23295). Note:
before 4d23295 our util_groundplane-based runs used util_groundplane's own collision (its copy
still imported the original `.physics` EBX, or found the original by name), so they never tested
a physics file of ours; the 4d23295 slab (box template, 3 parts, import moved) is the first.

Since then every build checks our physics part count against the template's own `.physics` part
count and refuses a mismatch; the dry run lists every array of the blueprint with "Part" in its
name and `PhysicsPartInfos`' entries. (A first version compared against the length of
`PhysicsPartInfos` and refused the visuals at 89109ff: the real skatepark box declares one entry
over its 3-part file, and our 3-part file under its copy worked, so that length is not the part
count; util_groundplane declares one over a 1-part file, and 38 parts there gave no collision.
What an entry holds is printed to find out what it counts.) `--parts merge` (the default for
new-place-level) merges the chunks into as many parts as the template's `.physics` has, one shape
each (a part names one shape; a shape holds up to 21,845 triangles
in 16-bit counts, shapes of 466 faces with zeroed trees held the skater, 2.5): by part kind when
there are exactly that many kinds (ground, bench, steps for the box's 3), otherwise in strips along
x; vertices equal to the last bit are shared. With util_groundplane: one shape of 5,284 triangles
and 2,803 vertices for the Embarcadero (223,152 bytes). Materials stay per part (the part record's
packed material; no per-triangle material is known). `--parts grow` keeps one part per chunk and
grows `PhysicsPartInfos` and `PartBoundingBoxes` to that many entries (entry 0 copied, each box
set to its part's; untested, and not what the box's file shows); other per-part tables are only
reported.

**Tested in game** (976f955, merged into util_groundplane's one part): the Embarcadero collision
works, every reading within 0.1 m of the printed table (299.4, 299.4, 300.2, 301.0, 300.2, 300.4,
300.1 near grind 2, none 20 m east); the skater lands on the invisible bench at 301.5 (its top plus
the skater's offset; `trainer ground` there reports the surface below).

**Tested in game** (5924574, `--visuals --visuals-only spot_2_ledge`, and the 6 ground objects):
the level load crashes 27 s in (read at 0xffffffffffffffff, Skate+0x3dc1b71) in a walk over a
slot table whose entries carry two tag bits: bit 0 set, skipped; bit 1 set, the value less its tags
used as an object. The value read, 0x14e9899ef8f39c32, is exactly our resource id of
`visual_ground_0_0_tm2_m1_mesh` (an MD5 of the name): an id ending in binary 10 is taken for a
tagged object. The d1b slab's ids ended in 00 (mesh and collision) and Embarcadero's collision in
01; base ids seen end in 01 and 11. Our ids now always end in 00. `--visuals-only` also takes the
names the dry run prints (`ground_0_0_tm2_m1`), and `--visual-slab W,H,D` adds the d1b slab's
shape through the visual path.

**Tested in game** (3bc3aa5): everything loads, no crash. The d1b slab is textured (the skatepark
surface tiling across 50 m at 1 per metre); the Embarcadero ground objects are drawn and textured
with the real terrain's rise, one object or all six; all 13 objects load ("13 visual objects
added") with the readings exact; the skater lands on the spot_2 bench at 301.5. So Embarcadero
Plaza is a visible, textured, skateable level. The ground's TexCoord0 default is 1 per metre and
the dry run says so (it printed "given" before).

**Looking at objects.** The trainer's `tp x y z` and `ground x z` take no heading, and the camera
after a teleport faces wherever it did; `trainer where` prints the heading (0 = +z, north; 90 =
+x, east: `atan2(forward.x, forward.z)` in `Extension/Trainer/trainer.cpp`). `place-info --views`
prints, per visual object, a `trainer ground` spot outside it on the side with ground and the
heading to face. Proposal (not in this branch): an optional heading argument, `trainer ground <x>
<z> [heading]` and `tp <x> <y> <z> [heading]`, setting the skater's yaw (forward = (sin h, 0,
cos h)) after the teleport, so a test can face an object without turning by hand.

**Materials.** `material-donors --game G [--contains TEXT]` lists the MeshSets of bam's root
bundle whose names contain TEXT (default `skatepark`): each LOD 0 section's material name,
triangles, strides, whether its key is in the root depot, and a class (concrete, wood, metal,
ground) guessed from the words of its names; then a proposed `--material-donor CLASS=MESH:N` per
class (the first usable section). A donor of another mesh lends its section's declaration, key,
model vertex, and its own TexCoord0/TexCoord1 densities and colour value.

**Visuals** (`--visuals`, written, not yet run in game): each visual object of each tile (13 in
the Embarcadero export) gets its own blueprint, mesh and placement (at `--at`, like the
collision's), all in the level's root bundle (bam root's copy, whose depot holds the template's
shader blocks), cloned from `--visual-template` (default the skatepark box) the way 6.3's own
mesh is: every section kept, our geometry in one (the default material's section 0, or the
section `--material-donor MATERIAL=MESH[:N]` names; a donor of another mesh lends its section's
layout, key and vertex), others drawing one zero-area triangle; per-vertex planar UVs at
`--uv-per-metre` (default 1) and the closest-normal template vertex for the undecoded 4-byte
elements; header, LOD and section boxes from the object's own vertices, padded; the blueprint's
mesh and collision EBX imports moved to its own copies. Its collision is harmless: one 0.1 m
triangle per template part (3 for the box, as its own `.physics` has), 500 m below the object,
in a `.physics` of its own; a template's own collision (`--collision template`) would put a box's
collision at every placement, and a blueprint without physics has not been tried. `--visuals-only
OBJECT` (name, part or name prefix) adds objects one at a time. Sizes in the Embarcadero export:
ground objects 1,271 to 2,911 vertices and 2,400 to 5,600 triangles, the rest 4 to 20 vertices;
all far under 65,535 vertices. `--skip-part` and `--max-height` filter collision only: the clipped
tower's visual (`buildings`, tile -2, -1) is drawn unless left out with `--visuals-only`. `--bam`
defaults to `333` (the empty world of 6.3).

## 7. Practical notes

- Ground probing with `trainer ground x z` reports the height about 5 ms after the teleport, before
  distant collision has loaded; an unloaded area reads 191.0, which is also sea level on the base
  map. Use the settled reading (a second probe some seconds later, or the `Trainer: sank … under
  the surface at H` line).
- The local park persists between game sessions. A park load that changes nothing logs no
  `Park edit applied` line.
- Smart App Control on the test PC intermittently blocks freshly built unsigned executables. Do
  not work around it; this is the reason for the Python port.
