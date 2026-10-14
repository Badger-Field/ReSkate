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
| CAS archive (raw blocks) | done |
| Mod folder (layout, stamp, superbundle TOCs, cas) | next |
| Collision (`PhysicsResource`, half-edge shapes, edge data) | layout verified on a PC; writer next |
| Visible meshes (rigid `MeshSet` and geometry chunks) | layout verified on a PC; writer next |
| Level registration, start point, world-partition tiles (EBX) | not started |

What has been verified in the game so far: a mod written this way that overrides one
collision resource loads; ground raised by exactly the amount written; zeroed
collision tree nodes still work; and a collision shape written from scratch (a ramp)
can be stood on.

## Tests

    ci/local.sh mapbuilder

The cross-check builds a small C++ harness from `Engine/Resource/toc.cpp`,
`binary_bundle.cpp` and `binary_io.cpp` and compares its output with the Python port's,
byte for byte. It is skipped when no C++20 compiler is available.
