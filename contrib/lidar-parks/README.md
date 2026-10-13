# LiDAR parks

Turn a LiDAR scan of a real skatepark into a ReSkate park you can skate in game.

`reskate_lidar` reads the scan, levels it, and finds the park's obstacles: rails, ledges, manual
pads, boxes, stairs, kickers, banks and quarter pipes. It then stands a Build Kit object in for
each one and writes a **park mod**: `Mods/<name>/parks/<map>.park.json`, the same format the Park
Editor saves. It loads like any park mod, from the overlay's **PARK MODS** tab, and a dedicated
server can spawn it for everyone (`park-mod add`).

This is the first of two routes. The other route keeps the park's real shape, bowls included,
as a custom map. See [CUSTOM-MAPS.md](CUSTOM-MAPS.md); `mesh` below produces its starting point.

No scan yet? See [CAPTURE.md](CAPTURE.md) for scanning a park with a phone. Or skip scanning, and
build a real plaza or city block from open map data and aerial LiDAR: see
[Real places](#real-places-city).

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

## Real places (`city`)

`city` builds a real public place from open data:
- OpenStreetMap and Overture Maps for footprints, steps, benches, walls and public space;
- USGS 3DEP aerial LiDAR for the ground and heights.

It writes the place as a park mod, as meshes for a custom map, or both:

```
python -m reskate_lidar city --place "Embarcadero Plaza, San Francisco" --size 160 \
    --anchor "<Skate>/Mods/Marker/parks/bam.park.json" --mods "<Skate>/Mods" \
    --preview plaza.png --mesh plaza.glb --report plaza.json
```

Give the area one of three ways:
- `--place` (looked up with Nominatim);
- `--centre lat,lon` and `--size` metres;
- `--bbox=west,south,east,north`. Write it with `=`, because the bounds start with a minus sign west of Greenwich.

What it finds:

| Spot | From |
|---|---|
| stairs, handrails | `highway=steps` (+ `handrail*`); length, rise and width measured in the ground |
| ledges | benches, low walls, retaining walls, kerbs, planters (tags), measured in the points |
| ledges, pads, boxes | the point cloud alone, if flat-topped, not overhung and not vegetation |
| banks | planar slopes in the ground |
| buildings | footprints, heights measured in the points (or tagged) |

- **Where spots come from:** only public, hard, car-free space: squares, plazas, parks and footways. Grass is left out.
- **Park objects:** they follow the anchor, as with `convert`. Real north is 90° to the left of the stick's facing (its facing is east). Each spot sits on the base map's ground; `--keep-elevation` keeps real heights.
- **Budget:** `--max-objects` (default 256) keeps the mod within the Object Dropper's default. Stairs and rails go first.
- **Meshes:** `--mesh` writes the visual GLB and a `-collision.glb`, for a custom map ([CUSTOM-MAPS.md](CUSTOM-MAPS.md)).
- **Attribution:** every output carries the data's credit. Keep it when you share. See [DATA-SOURCES.md](DATA-SOURCES.md) for licences and server rules.
- **Limits:** areas are capped at 4 km². Downloads are cached in `~/.cache/reskate-lidar`, which `--cache` changes.
- **Plans:** what's next, and what to check in game, is in [ROADMAP.md](ROADMAP.md).

### A skatepark's real shape (`--skatepark`)

For a park of bowls and transitions, which the Build Kit can't make:

```
python -m reskate_lidar city --centre 33.9870851,-118.4754707 --size 120 --skatepark --mesh venice.glb
python studio_prep.py venice.glb venice.blend      # Blender's bpy, or: blender -b -P studio_prep.py -- ...
```

- **The park:** it finds the mapped skatepark at the centre (`leisure=skatepark`, or `sport=skateboard`).
- **The surface:** builds it from the LiDAR's ground at 0.25 m (`--park-cell`), with skaters and trees left out.
- **The coping:** grind curves along each bowl's coping, from the bowl outlines OpenStreetMap maps as holes in the park. Where there are none, it finds bowls in the ground.
- **The output:** a Studio-ready mesh. Then `studio_prep.py` lays it out as a `.blend` for ReSkate Studio (CUSTOM-MAPS.md).
- **Limits:** aerial LiDAR gets bowl shapes and depths, but not the coping lip: see [examples/venice-beach-skatepark.md](examples/venice-beach-skatepark.md).

| Option | |
|---|---|
| `--sources osm` / `overture` | use only one map source (default both) |
| `--lidar none` / `<EPT url or folder>` | skip LiDAR, or read another EPT survey |
| `--overpass <url or file>` | another Overpass server, or a saved answer |
| `--ground-cell 0.5` | ground grid size (metres) |
| `--tile 64` | mesh ground tile size (metres) |
| `--turn 30` | turn the whole place before placing it |

## Street-level imagery (`imagery`)

`python -m reskate_lidar imagery --centre 33.9870851,-118.4754707 --radius 100` lists the openly
licensed street-level photos around a place:
- **Sources:** Mapillary, Panoramax and KartaView.
- **What it reports:** how many, when taken, under which licence, and how many come with a point cloud.
- **Mapillary** needs your own client token in `MAPILLARY_TOKEN`.
- See DATA-SOURCES.md for what the photos can and can't add, and their licences.

**The Mapillary token.** Keep it in a password manager, not in a file or the repo. With 1Password's
CLI, the token is injected only for the one command:

```
op run --env MAPILLARY_TOKEN="op://Private/Mapillary/client_token" -- \
    python -m reskate_lidar imagery --centre 33.9870851,-118.4754707 --radius 100 --verbose
```

The tool sends the token only in a request header. It never goes in a URL, the cache, or the report.

## Tests

```
python -m pytest tests
```

`test_city.py` builds a synthetic city block from a known scene and runs the city pipeline on it,
offline:
- what it reads: a small EPT survey, an Overpass answer and an Overture Parquet file, all written by the tests;
- what it checks: buildings, stairs, rails, benches, walls, ledges and banks come back where they are;
- what it rules out: no car, hedge or grass becomes a spot;
- what it writes: the park and meshes, both valid.

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
