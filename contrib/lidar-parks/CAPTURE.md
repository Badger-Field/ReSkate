# Scanning a skatepark

A good scan matters more than anything the converter does. These notes are for a phone or tablet
with LiDAR (iPhone Pro or iPad Pro from 2020 on). They also cover what to do with a scan from a
dedicated scanner.

## Phone

**Apps** (any of these exports something the converter reads):

| App | Mode | Export |
|---|---|---|
| Polycam | LiDAR, or Space for big areas | GLB or OBJ (mesh), or PLY/LAS (points) |
| Scaniverse | Area | OBJ, GLB or PLY |
| 3D Scanner App | LiDAR Advanced | OBJ, GLB or LAS |

Prefer a **mesh** export at the app's highest detail. Mesh exports have far less noise than raw
points.

**When**: overcast daylight, or late afternoon with the sun low behind you. Empty is best, and
early mornings help. Dry concrete only: wet patches and puddles scan as holes. Skaters moving
through the scan leave smears; the converter removes small ones, but not a crowd.

**How**:

1. Scan **one area at a time**. Phone LiDAR reaches about 5 m and drifts over long walks.
   A street section, a bowl, or a run of ledges each make a good scan. Scan several and convert
   each one onto its own measuring stick.
2. Walk slowly, about one step a second, holding the phone at chest height and tilted down
   about 45 degrees.
3. Pass **every obstacle from both sides**, and the tops of ledges and boxes from above. The
   converter reads a ledge's height from its top surface, and a quarter pipe's from its deck.
4. Rails need extra care: they are thin. Walk along each rail at about 1 m, and give the tops of
   handrails a slow second pass.
5. Take in a metre or two of flat ground around everything. That ground is how the scan is
   levelled.
6. Finish where you started. Most apps use that to close the loop and cut drift.

**Check before leaving**: in the app's 3D view, look along each ledge and rail. A rail with gaps,
or a ledge top that sags, will convert the same way.

## Dedicated scanners and drones

- **Terrestrial scanners** (Leica, Faro, Matterport Pro3...): export registered points as LAS/LAZ or
  E57. The converter reads LAS/LAZ directly. Convert E57 to LAS in CloudCompare first. Crop to the
  park and subsample to about 1 cm spacing (CloudCompare: **Edit > Subsample**).
- **Drone photogrammetry or drone LiDAR**: fine for bowls, banks and overall layout. Usually too
  coarse for rails and thin ledges. Combine with a phone scan of the street section.
- **Public aerial LiDAR** (USGS 3DEP and similar): at 10 to 30 cm point spacing it shows where a
  park is and its big bowls, but nothing you can grind. Use it for layout only.

## Units and axes

Phone apps export in metres. If the park comes out 3.28 times too big, the export was in feet:
pass `--units 0.3048`. Phone exports are usually Y-up and scanners Z-up. `--up auto` finds the
ground either way. Pass `--up y` or `--up z` if it picks a wall.
