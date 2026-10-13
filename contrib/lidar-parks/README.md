# LiDAR parks

Turn a LiDAR scan of a real skatepark into a ReSkate park you can skate in game.

`reskate_lidar` reads the scan, levels it, and finds the park's obstacles: rails, ledges, manual
pads, boxes, stairs, kickers, banks and quarter pipes. It then stands a Build Kit object in for
each one and writes a **park mod**: `Mods/<name>/parks/<map>.park.json`, the same format the Park
Editor saves. It loads like any park mod, from the overlay's **PARK MODS** tab, and a dedicated
server can spawn it for everyone (`park-mod add`).

This is the first of two routes. The other route keeps the park's real shape, bowls included,
as a custom map. See [CUSTOM-MAPS.md](CUSTOM-MAPS.md); `mesh` below produces its starting point.

No scan yet? See [CAPTURE.md](CAPTURE.md) for scanning a park with a phone.

## Install

Python 3.10 or newer:

```
cd contrib/lidar-parks
python -m pip install -r requirements.txt
```

## Use

### 1. Mark where the park goes

In game, open the Park Editor on the base map you want (San Vansterdam is `bam`). Place one
**measuring stick** (`own_bkprops_generic_measuringstick_00001`) on flat ground where the middle
of the scanned park should be. Turn it to face the direction the scan's **+x** should run. Save it
as a park mod (**PARK MODS** tab, or `editor mod-create`). Its file,
`Mods/<that mod>/parks/bam.park.json`, is the anchor.

### 2. Convert

```
python -m reskate_lidar convert my-park.glb --anchor "<Skate>/Mods/Marker/parks/bam.park.json" \
    --mods "<Skate>/Mods" --name "Burnside" --author you --preview burnside.png
```

It prints what it found and placed, and writes `<Skate>/Mods/Burnside/`. Open `burnside.png` before
going in game. It shows the scan from above (**right is +x, up is +y**), every feature outlined in
its kind's colour, and each placed object as a white outline.

| Option | |
|---|---|
| `--up z` / `--up y` | the scan's up axis when `auto` guesses wrong |
| `--units 0.3048` | the scan is not in metres (feet here; `0.01` for centimetres) |
| `--turn 90` | turn the whole park (degrees) before placing it |
| `--mirror` | the park comes out mirrored in game |
| `--merge` | keep the anchor park's other objects in the output |
| `--park out.park.json` | write just the park file, no mod folder |
| `--report found.json` | every feature and placement, for checking or scripting |
| `--cell 0.05` | grid size in metres; smaller sees thinner rails, but runs slower |

### 3. Skate it

Start the game through ReSkate, open **PARK MODS** and load the new park. If objects go missing,
check the object limits. The Object Dropper allows 256 objects by default
(`config/object-dropper.json`), and a dedicated server spawns 512 across all its parks.

### Check the catalog sizes once

`catalog.json` lists the Build Kit objects the converter may use and how big each one is. The
item ids come from the game's own content cache, but **the sizes are estimates** until someone
measures them in game. Build a row of every catalog object, each between two measuring sticks
set at its expected length:

```
python -m reskate_lidar calibrate --anchor <anchor.park.json> --mods "<Skate>/Mods"
```

Load **LiDAR Calibration**. Where an object doesn't fill the gap between its sticks, correct its
`along`, `across` and `height` in `catalog.json`. If an object faces the wrong way, set its
`yaw_offset_degrees`. If it sits off-centre, set its `origin`. Then set `"estimated": false`.

## What it finds, and what it can't do

| Found as | Placed as |
|---|---|
| rail (flat or down a set of stairs) | flat rails, set at the bar's height and pitch |
| ledge (flat top, under 0.6 m wide) | grind boxes, slappy curbs, or a long grind pad, end to end |
| manual pad, box | manual pads, end to end |
| stairs | the Build Kit stairs, side by side |
| kicker, bank (planar slope) | kickers, or a wall ramp for tall banks |
| quarter pipe (slope that curves up) | quarter pipes, side by side |
| bowl (below the park floor) | **nothing**: the Build Kit can't dig into a map. Reported only. |

- Objects scale **uniformly**, because the game keeps one scale per object. An object is scaled to
  match the feature's height, then repeated to cover its length. So a narrow box can come out
  wider than the real one.
- Banks, hips, pyramids and funboxes come out as their parts: a box for the flat top and a kicker
  or bank for each face.
- Curved ledges, hubbas and pools fit poorly. For those, use the custom-map route.
- The scan's ground is taken as flat. A park on a hillside levels to its average slope.
- Detection is tested on synthetic parks with up to 1.5 cm of scan noise. Real phone scans
  exported as meshes are usually smoother than that. Noisier scans start to lose thin rails
  first: use a smaller `--cell`, or a denser export.

## Tests

```
python -m pytest tests
```

The tests build a synthetic park with a known ledge, manual pad, rail, kicker, quarter pipe and
stairs. They scan it Y-up and Z-up, tilted, turned and noisy, and check that every obstacle comes
back in place and nothing extra appears. They also check the park file, frames and limits.

With `RESKATE_PARK_CHECK` set, the written mod is also read by the dedicated server's own park
loader (`Server/server_parks.cpp`), which applies the checks players' games apply. On Linux:

```
RESKATE_PARK_CHECK="$(tests/build_park_check.sh)" python -m pytest tests
```

`python -m reskate_lidar synth park.ply` writes the synthetic scan, if you want to try `convert`
without a scan of your own.
