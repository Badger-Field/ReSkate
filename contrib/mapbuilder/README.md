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
| Own ground (tile -> layer -> placement of a copy of util_groundplane, its 50 m plane mesh reused, own flat collision found by name) | written as `new-ground-level`, tested on a synthetic game; not yet run in game |
| World-partition tiles of our own, own MeshSet | not started (FORMAT-NOTES 6.3) |

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

The format reference is `contrib/mapbuilder/FORMAT-NOTES.md`.

## Tests

    ci/local.sh mapbuilder

The cross-check builds a small C++ harness from `Engine/Resource/toc.cpp`,
`binary_bundle.cpp` and `binary_io.cpp` and compares its output with the Python port's,
byte for byte. The EBX cross-check does the same with `ebx_document.cpp` and `ebx_writer.cpp`
on synthetic documents. Both are skipped when no C++20 compiler is available.
