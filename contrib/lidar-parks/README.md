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
| kerbs | where a road meets a sidewalk or plaza 8-30 cm above it (the 12 longest, after everything else) |
| buildings | footprints, heights measured in the points (or tagged) |

- **Where spots come from:** only public, hard, car-free space: squares, plazas, parks and footways. Grass is left out.
- **Park objects:** they follow the anchor, as with `convert`. Real north is 90° to the left of the stick's facing (its facing is east). Each spot sits on the base map's ground; `--keep-elevation` keeps real heights.
- **Budget:** `--max-objects` (default 256) keeps the mod within the Object Dropper's default. Stairs and rails go first.
- **Meshes:** `--mesh` writes the visual GLB and a `-collision.glb`, as data for viewing and checking a place. They were meant for custom maps, which this project no longer builds ([CUSTOM-MAPS.md](CUSTOM-MAPS.md)). Places reach the game as park mods.
- **Attribution:** every output carries the data's credit. Keep it when you share. See [DATA-SOURCES.md](DATA-SOURCES.md) for licences and server rules.
- **Limits:** areas are capped at 4 km². Downloads are cached in `~/.cache/reskate-lidar`, which `--cache` changes.
- **Plans:** what's next, and what to check in game, is in [ROADMAP.md](ROADMAP.md).

### LiDAR outside the US

3DEP covers the US. Elsewhere, download the national survey's tiles over the area, then pass the file or folder:

```
python -m reskate_lidar city --centre 52.3573,4.8818 --size 160 --lidar ~/ahn-tiles \
    --lidar-credit "AHN, CC0" --anchor ... --mods ...
```

- **Reading:** only tiles whose bounds reach the area are read. A `.copc.laz` is queried for just the area; other tiles are cropped as they are read.
- **CRS:** coordinates in a national grid (RD, British National Grid, UTM) are reprojected with `pyproj`. The CRS comes from the tile header, or from `--lidar-crs`.
- **Credit:** each survey's credit is in [DATA-SOURCES.md](DATA-SOURCES.md). Without `--lidar-credit`, the outputs ask you to add it.

### Is the anchor on open, flat ground? (`anchor`)

A city park spreads tens of metres round its anchor. The base map's ground there may be a road, a sea wall or a slope. The game can measure it: the trainer's `trainer ground <x> <z>` finds the topmost surface at a map position and logs it.

```
python -m reskate_lidar anchor probe --park "<Skate>/Mods/Embarcadero Plaza/parks/bam.park.json"
```

1. **Probe:** this prints a grid of probes over the park's footprint, with 10 m spacing and at most 100 probes. They come as a `RESKATE_STARTUP_COMMANDS` value, and as console lines (`--out` writes those to a file).
2. **Run them in game:** turn the park mod off and load the map alone, otherwise the probes land on the park.
3. **Check:**

   ```
   python -m reskate_lidar anchor check --park "<...>/bam.park.json" --log "<Skate>/ReSkate.log"
   ```

   It reports:
   - what share of the footprint is within 0.3 m (`--tolerance`) of the park's ground, which counts as flat and clear;
   - what is higher (walls, buildings) or lower (drops, beach), and by how much;
   - which objects would be buried or float.

   A good anchor has at least 90% level and no buried or floating objects. Otherwise, move the stick, or make the park smaller with `city --size`.

**Finding open ground (`anchor search`).** When the anchor fails, search round it:

```
python -m reskate_lidar anchor search --map bam --around 627.1 1062.8 --radius 200 --spacing 20 --out-dir probes
```

1. **Batches:** this writes the spots as batches. Each batch is one `RESKATE_STARTUP_COMMANDS` value under the launcher's 4,000 characters: about 55 spots, about 9 minutes. `--per-batch` caps it lower.
   - **The route:** the spots run as a serpentine of short hops, so each lands next to ground that has already streamed in.
   - **Two probes a spot:** `trainer ground x z;wait 8;trainer ground x z`. The first answers within milliseconds of the teleport, before a far spot's collision has loaded. On bam an unloaded spot reads 191.0, the same as the sea. The second, 8 s later (`--wait`), reads the loaded ground under the skater.
   - **`--fast`:** one probe a spot, `trainer ground x z;wait 4`, read from the landing watch's log lines. It takes half the time and fits twice the spots a batch. Keep the default two probes until a fast run and a settled run of the same spots agree.
2. **Run each batch:** set the value, start the game, load the map with no park mods, and keep each run's `ReSkate.log` as its own file. It can run unattended.
3. **Rank:**

   ```
   python -m reskate_lidar anchor search --map bam --log batch-01.log --log batch-02.log ... \
       --size 160 --anchor-out new-anchor.park.json --anchor-template "<old anchor>.park.json"
   ```

   - **What it ranks:** every square of `--size` round a probed point, by the share of it that is level with its own median height. It lists the best few that don't overlap, as anchor `x, y, z`.
   - **Squares left out:** those with under 85% of their probes read.
   - **Each spot's height**, most trusted first:
     1. the second probe;
     2. the landing watch's "sank to … under the surface at H; put back on it", which gives H, the loaded surface, exactly;
     3. the last "landed at" or "landing watch ended at" within 30 m of the spot, less 1.0 m (the skater's height there, measured on bam). Over water the skater lands below the water level, so the reading is below it;
     4. the first, early reading. It is reported as unsettled, to probe again.
   - **Water and out of bounds:** the game's probe gives a height, not what the surface is.
     - When the game sends the skater back to the start (a watch line more than 30 m away), the spot is unusable.
     - When many spots settle at exactly the lowest height seen, that height is taken as water.
     - Both count against a square.
     - `--water-level` (or `--water-height`) sets the water height. On bam it is 191.0; pass it with `--fast`. `--no-water` turns this off.
   - **Street level first:** places within 10 m (`--height-window`) of a reference height come first. The reference is the median of the land probes within 30 m of the search's centre (the middle of the probed area), or the nearest land probe to it, unless `--near-height` sets it. On bam, street level is about 199.4-199.9.
   - **Suspicious squares:** a square exactly flat over 20 or more probes is flagged and ranked last. Real ground has kerbs and falls; the sea doesn't.
   - **Widening:** search further out without probing the middle again with `--radius 400 --skip-radius 200`.
   - **Next:** it prints a finer `--around` search round the best one.
   - **The anchor file:** `--anchor-out` writes the best place as an anchor file, keeping the old stick's facing. Pass it to `city --anchor`, then confirm with `anchor probe` and `anchor check`.

### A skatepark's real shape (`--skatepark`)

For a park of bowls and transitions, which the Build Kit can't make:

```
python -m reskate_lidar city --centre 33.9870851,-118.4754707 --size 120 --skatepark --mesh venice.glb
python studio_prep.py venice.glb venice.blend      # optional, for ReSkate Studio (not pursued)
```

- **The park:** it finds the mapped skatepark at the centre (`leisure=skatepark`, or `sport=skateboard`).
- **The surface:** builds it from the LiDAR's ground at 0.25 m (`--park-cell`), with skaters and trees left out.
- **The coping:** grind curves along each bowl's coping, from the bowl outlines OpenStreetMap maps as holes in the park. Where there are none, it finds bowls in the ground.
- **As a park mod** (`--mods` / `--park`, with `--anchor`): each bowl becomes a ring of Build Kit quarter pipes, rising out of the ground to its measured depth.
  - The coping lies on the bowl's real coping line.
  - Quarters stop at 2.4 m. In a narrow bowl they are lower, to leave a flat bottom.
  - Quarters that would mostly overlap are left out, so round ends are approximate.
  - See [examples/venice-beach-skatepark.md](examples/venice-beach-skatepark.md).
- **The mesh** (`--mesh`): the bowls' measured shapes, depths and coping lines, as data. It was made for ReSkate Studio custom maps, a route this project no longer pursues (CUSTOM-MAPS.md).
- **Limits:** aerial LiDAR gets bowl shapes and depths, but not the coping lip: see [examples/venice-beach-skatepark.md](examples/venice-beach-skatepark.md).

| Option | |
|---|---|
| `--sources osm` / `overture` | use only one map source (default both) |
| `--lidar none` / `<EPT url or folder>` | skip LiDAR, or read another EPT survey |
| `--lidar <tile or folder>` | LAS, LAZ or COPC tiles on disk, in any CRS: AHN, the UK Environment Agency, Denmark, Finland (see below) |
| `--lidar-crs EPSG:28992` | the tiles' CRS, where their headers give none |
| `--lidar-credit "…"` | the credit the LiDAR's licence asks for; it goes into every output |
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
