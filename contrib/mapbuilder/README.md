# ReSkate map builder

Builds custom skate maps from plain geometry (for example the 50 m tiles that
`contrib/lidar-parks` writes with `city --level`), in the game's own package formats,
with no third-party tools.

It is pure Python, so it runs under the signed `python.exe` on Windows (Smart App
Control does not block it) and is tested on Linux CI.

## Status

| piece | state |
| --- | --- |
| Superbundle TOC writer and reader, perfect hashes | done, byte-identical to ReSkate's C++ (`tests/test_crosscheck.py`) |
| Bundle region and binary manifest | done, byte-identical to ReSkate's C++ |
| CAS archive | raw blocks written; raw, zlib and (on Windows, via the game's own Oodle DLL) Oodle read |
| Game data access (layout.toc, archives, Huffman-named TOCs) | done; not yet run against the real game |
| Mod folder (layout, stamp, superbundle TOCs, cas) | done for resource overrides |
| Collision (`PhysicsResource`, half-edge shapes, edge data, zero tree) | written and unit-tested; in-game check pending |
| Visible meshes (rigid `MeshSet` and geometry chunks) | layout verified on a PC; writer next |
| EBX document reader and writer | done, byte-identical to ReSkate's C++ (`tests/test_ebx_crosscheck.py`) |
| Level registration (layout, level TOC, description, on-demand entry, detached sublevel) | written as a clone of bam, tested on a synthetic game; not yet run on the PC |
| Own ground (tile -> layer -> placement of a copy of util_groundplane, its 50 m plane mesh reused, own flat collision found by name) | `new-ground-level`: loads in game, drawn and textured (own mesh cloned from the skatepark box), collision ours |
| Own MeshSet (`--mesh own`, a patched copy of the template's) | written, tested on a synthetic game; not yet run in game |
| A real place (lidar-parks tiles v2, `new-place-level`): collision and textured visuals | tested in game on Embarcadero Plaza 130 x 20: readings within 0.1 m, ground drawn and textured |
| World-partition tiles of our own | not started (FORMAT-NOTES 6.3) |

What has been verified in the game so far: a mod written this way that overrides one
collision resource loads; ground raised by exactly the amount written; zeroed
collision tree nodes still work; and a collision shape written from scratch (a ramp)
can be stood on.

## Try it on the PC

    python -m reskate_mapbuilder inspect --game "<Skate>" --superbundle <path under Data/Win32> \
        --bundle <bundle> --resource <...staticmodelgroup.physics> --near X Z
    python -m reskate_mapbuilder raise-ground --game "<Skate>" --mods <out> --name zz-... \
        --superbundle ... --bundle ... --resource ... --parts 157,161,165 --by 1.0

    python -m reskate_mapbuilder new-level --game "<Skate>" --name zz-new-level \
        --level dingolevel_reskate_test --dry-run
    python -m reskate_mapbuilder new-level --game "<Skate>" --mods "<Skate>\Mods" --name zz-new-level \
        --level dingolevel_reskate_test --display-name "ReSkate test level"
    python -m reskate_mapbuilder new-ground-level --game "<Skate>" --name zz-ground-level \
        --level dingolevel_reskate_ground --bam all --dry-run
    python -m reskate_mapbuilder list-assets --game "<Skate>" --superbundle <path> [--bundle B] [--contains TEXT]
    python -m reskate_mapbuilder inspect-meshset --game "<Skate>" --superbundle S --bundle B --resource R
    python -m reskate_mapbuilder new-place-level --game "<Skate>" --name zz-place-level \
        --level dingolevel_reskate_place --tiles <DIR from city --level> --dry-run
    python -m reskate_mapbuilder place-info --tiles <DIR> [--max-height 20]
    python -m reskate_mapbuilder material-donors --game "<Skate>" [--contains skatepark]
    python -m reskate_mapbuilder scan-materials --game "<Skate>" [--superbundle S] [--bundle-contains T] \
        [--class wood] [--both] [--every]
    python -m reskate_mapbuilder mesh-refs --game "<Skate>" [--mod <mod>] --superbundle S --bundle B --mesh EBX
    python -m reskate_mapbuilder ebx-roundtrip --game "<Skate>" --superbundle globals \
        --ebx levels/game/dingolevel_root/dingolevel_root/description
    python -m reskate_mapbuilder ebx-arrayhash --game "<Skate>" --superbundle ... --ebx ...
    python -m reskate_mapbuilder dump-ebx --game "<Skate>" [--mod "<a mod folder>"] \
        --superbundle globals --ebx levels/game/dingolevel_root/dingolevel_root/description

`raise-ground` repeats the collision test already proven in game with the C++ tools, written
entirely by this package. `new-level` adds a level that is, for now, a clone of the base
map (FORMAT-NOTES 6.2); `--dry-run` reads and reports only. `new-ground-level` adds a level
with its own flat ground (FORMAT-NOTES 6.3). `dump-ebx` prints an EBX document; `ebx-roundtrip` says where and why rewriting one unchanged
changes its bytes.

## A real place, end to end

Tested in game with the Embarcadero Plaza 130 x 20 export (FORMAT-NOTES 6.4). Export the place
with `contrib/lidar-parks` (`city --level DIR`), then, with `--game` the game folder and `T` that
directory:

    python -m reskate_mapbuilder place-info --tiles T --max-height 20 --views
    python -m reskate_mapbuilder new-place-level --game "<Skate>" --name zz-place-level \
        --level dingolevel_reskate_place --tiles T --bam 333 --max-height 20 --visuals --dry-run
    python -m reskate_mapbuilder new-place-level --game "<Skate>" --mods "<Skate>\Mods" --name zz-place-level \
        --level dingolevel_reskate_place --tiles T --bam 333 --max-height 20 --visuals

Keep one level mod in `Mods`. In game: `load levels/game/dingolevel_reskate_place/dingolevel_reskate_place`,
then the `trainer ground X Z` spots `place-info` prints (the export's spawn is at 0 0). The
collision is the place's collision chunks merged into util_groundplane's one part; each visual
object is its own copy of the skatepark box with our geometry, textured with the box's surface.
`--visuals-only NAME` adds objects one at a time, `--skip-visual buildings` leaves the buildings
(their own class, `building`) out; `material-donors --game "<Skate>"` lists stock
sections for `--material-donor wood=MESH:N` and the like. For materials of other levels,
`scan-materials --game "<Skate>"` ranks the bundles holding them (`--both`: concrete or stone and
wood in one bundle), and `--material-bundle SUPERBUNDLE:BUNDLE` with `--material-donor
ground=MESH:N` (a mesh of that bundle) draws objects with them (tested in game: concrete ground
and wood benches from one of bam's tile sublevels). `--material-bundle auto` picks a world-partition
tile sublevel of bam's with distinct poured concrete, paver or stone ground and wood (vistas and
proxies left out), and donors from it (FORMAT-NOTES 6.4). New resource ids are odd, confirmed in
game (`--resource-ids clear` for the earlier form).

The format reference is `contrib/mapbuilder/FORMAT-NOTES.md`.

## Tests

    ci/local.sh mapbuilder

The cross-check builds a small C++ harness from `Engine/Resource/toc.cpp`,
`binary_bundle.cpp` and `binary_io.cpp` and compares its output with the Python port's,
byte for byte. The EBX cross-check does the same with `ebx_document.cpp` and `ebx_writer.cpp`
on synthetic documents. Both are skipped when no C++20 compiler is available.
